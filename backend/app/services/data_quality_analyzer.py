"""Read-only data quality analysis for stored CSV datasets."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException

from app.core.config import Settings
from app.schemas.dataset import (
    DataQualityIssue,
    DataQualitySummary,
    DatasetColumnQuality,
    DatasetQualityResponse,
)
from app.services.dataset_profiler import (
    create_dataset_metadata,
    date_formats_for_value,
)
from app.services.dataset_storage import DatasetStorage

_NUMBER_PATTERN = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
_BOOLEAN_VALUES = {"true", "false", "yes", "no"}
_SAMPLE_LIMIT = 20
_TYPE_INCONSISTENCY_THRESHOLD = 0.8
_MIN_TYPE_EVIDENCE = 5


class _ColumnQuality:
    """Accumulate missing, candidate-type, and bounded example data."""

    def __init__(self, sample_limit: int) -> None:
        self.missing_count = 0
        self.non_null_count = 0
        self.numeric_valid_count = 0
        self.numeric_invalid_samples: list[str] = []
        self.boolean_counts: Counter[str] = Counter()
        self.boolean_samples: dict[str, list[str]] = {}
        self.non_boolean_samples: list[str] = []
        self.date_format_counts: Counter[str] = Counter()
        self.date_valid_count = 0
        self.invalid_date_count = 0
        self.type_inconsistency_count = 0
        self.invalid_date_samples: list[str] = []
        self.inconsistency_samples: list[str] = []
        self.sample_limit = sample_limit

    def add_sample(self, destination: list[str], value: str) -> None:
        if len(destination) < self.sample_limit:
            destination.append(value[:200])


@dataclass
class _DuplicateState:
    duplicate_rows: int = 0
    duplicate_groups: int = 0
    duplicate_fingerprints: set[bytes] = field(default_factory=set)
    rows_with_missing: int = 0


def _is_numeric(value: str) -> bool:
    if not _NUMBER_PATTERN.fullmatch(value):
        return False
    if re.fullmatch(r"[+-]?\d+", value):
        digits = value.lstrip("+-")
        if len(digits) > 1 and digits.startswith("0"):
            return False
    try:
        return Decimal(value).is_finite()
    except DecimalException:
        return False


def _is_identifier(name: str) -> bool:
    words = re.findall(
        r"[a-z0-9]+",
        re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name.strip()).casefold(),
    )
    return bool(words and words[-1] in {"id", "identifier", "uuid"})


def _row_fingerprint(row: tuple[str, ...]) -> bytes:
    digest = hashlib.blake2b(digest_size=16)
    for value in row:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


def _collect_row(
    row: tuple[str, ...],
    columns: list[_ColumnQuality],
    seen_rows: set[bytes],
    duplicate_state: _DuplicateState,
) -> None:
    has_missing_value = False
    for profile, value in zip(columns, row):
        if not value.strip():
            profile.missing_count += 1
            has_missing_value = True
            continue
        profile.non_null_count += 1

        if _is_numeric(value):
            profile.numeric_valid_count += 1
        else:
            profile.add_sample(profile.numeric_invalid_samples, value)

        normalized = value.casefold()
        if normalized in _BOOLEAN_VALUES:
            profile.boolean_counts[normalized] += 1
            profile.boolean_samples.setdefault(normalized, [])
            profile.add_sample(profile.boolean_samples[normalized], value)
        else:
            profile.add_sample(profile.non_boolean_samples, value)

        formats = date_formats_for_value(value)
        if formats:
            profile.date_valid_count += 1
        else:
            profile.add_sample(profile.invalid_date_samples, value)
        for format_name in (
            "iso_date",
            "dmy_slash",
            "mdy_slash",
            "dmy_dash",
            "mdy_dash",
            "iso_datetime",
        ):
            if format_name in formats:
                profile.date_format_counts[format_name] += 1

    if has_missing_value:
        duplicate_state.rows_with_missing += 1

    fingerprint = _row_fingerprint(row)
    if fingerprint in seen_rows:
        duplicate_state.duplicate_rows += 1
        if fingerprint not in duplicate_state.duplicate_fingerprints:
            duplicate_state.duplicate_groups += 1
            duplicate_state.duplicate_fingerprints.add(fingerprint)
    else:
        seen_rows.add(fingerprint)


def _get_candidate_date_format(profile: _ColumnQuality) -> str | None:
    if not profile.date_format_counts or profile.non_null_count < _MIN_TYPE_EVIDENCE:
        return None
    format_name, matching_count = max(
        profile.date_format_counts.items(),
        key=lambda item: (item[1], item[0]),
    )
    if matching_count / profile.non_null_count < _TYPE_INCONSISTENCY_THRESHOLD:
        return None
    return format_name


def _type_issue(
    profile: _ColumnQuality,
    inferred_type: str,
    column_name: str,
) -> None:
    if inferred_type in {"date", "datetime"} or _get_candidate_date_format(profile):
        candidate_format = _get_candidate_date_format(profile)
        if inferred_type in {"date", "datetime"} or candidate_format is not None:
            if inferred_type in {"date", "datetime"}:
                if candidate_format is None:
                    profile.invalid_date_count = (
                        profile.non_null_count - profile.date_valid_count
                    )
                    profile.invalid_date_samples = []
                else:
                    profile.invalid_date_count = (
                        profile.non_null_count - profile.date_valid_count
                    )
                profile.type_inconsistency_count = profile.invalid_date_count
                profile.inconsistency_samples = profile.invalid_date_samples
            else:
                assert candidate_format is not None
                profile.invalid_date_count = (
                    profile.non_null_count - profile.date_valid_count
                )
                profile.type_inconsistency_count = profile.invalid_date_count
                profile.inconsistency_samples = profile.invalid_date_samples
            return

    if inferred_type in {"integer", "float"}:
        if profile.numeric_valid_count != profile.non_null_count:
            profile.type_inconsistency_count = (
                profile.non_null_count - profile.numeric_valid_count
            )
            profile.inconsistency_samples = profile.numeric_invalid_samples
        return

    if inferred_type == "boolean":
        bool_value_count = sum(profile.boolean_counts.values())
        profile.type_inconsistency_count = (
            profile.non_null_count - bool_value_count
        )
        return

    if _is_identifier(column_name):
        return

    numeric_ratio = profile.numeric_valid_count / max(profile.non_null_count, 1)
    if (
        profile.numeric_valid_count >= _MIN_TYPE_EVIDENCE
        and numeric_ratio >= _TYPE_INCONSISTENCY_THRESHOLD
    ):
        profile.type_inconsistency_count = (
            profile.non_null_count - profile.numeric_valid_count
        )
        profile.inconsistency_samples = profile.numeric_invalid_samples
        return

    boolean_count = sum(profile.boolean_counts.values())
    if (
        boolean_count >= _MIN_TYPE_EVIDENCE
        and boolean_count / max(profile.non_null_count, 1)
        >= _TYPE_INCONSISTENCY_THRESHOLD
    ):
        true_false = sum(
            profile.boolean_counts[value] for value in ("true", "false")
        )
        yes_no = sum(profile.boolean_counts[value] for value in ("yes", "no"))
        if true_false and yes_no:
            minority_tokens = (
                ("true", "false") if true_false <= yes_no else ("yes", "no")
            )
            profile.type_inconsistency_count = (
                min(true_false, yes_no)
                + profile.non_null_count
                - boolean_count
            )
            for token in minority_tokens:
                for value in profile.boolean_samples.get(token, []):
                    profile.add_sample(profile.inconsistency_samples, value)
            for value in profile.non_boolean_samples:
                profile.add_sample(profile.inconsistency_samples, value)
        else:
            profile.type_inconsistency_count = (
                profile.non_null_count - boolean_count
            )
            profile.inconsistency_samples = profile.non_boolean_samples


def _percentage(count: int, denominator: int) -> float:
    value = count / denominator * 100 if denominator else 0.0
    return value if math.isfinite(value) else 0.0


def _create_issues(
    metadata,
    profiles: list[_ColumnQuality],
    duplicate_rows: int,
    duplicate_groups: int,
    sample_limit: int,
) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    for column, profile in zip(metadata.columns, profiles):
        if profile.missing_count:
            issues.append(
                DataQualityIssue(
                    category="missing_values",
                    severity="info",
                    column=column.name,
                    count=profile.missing_count,
                    percentage=_percentage(
                        profile.missing_count,
                        metadata.row_count,
                    ),
                    description=(
                        f"{profile.missing_count} rows have a missing value "
                        f"in column '{column.name}'."
                    ),
                )
            )

        if profile.invalid_date_count:
            issues.append(
                DataQualityIssue(
                    category="invalid_dates",
                    severity="warning",
                    column=column.name,
                    count=profile.invalid_date_count,
                    percentage=_percentage(
                        profile.invalid_date_count,
                        profile.non_null_count,
                    ),
                    description=(
                        f"{profile.invalid_date_count} non-missing date-like "
                        f"values in '{column.name}' are invalid."
                    ),
                    sample_values=profile.invalid_date_samples[:sample_limit],
                )
            )
        elif profile.type_inconsistency_count:
            issues.append(
                DataQualityIssue(
                    category="type_inconsistency",
                    severity="warning",
                    column=column.name,
                    count=profile.type_inconsistency_count,
                    percentage=_percentage(
                        profile.type_inconsistency_count,
                        profile.non_null_count,
                    ),
                    description=(
                        f"{profile.type_inconsistency_count} values in "
                        f"'{column.name}' may not match its inferred "
                        f"{column.data_type} type."
                    ),
                    sample_values=profile.inconsistency_samples[:sample_limit],
                )
            )

    if duplicate_rows:
        issues.append(
            DataQualityIssue(
                category="duplicate_rows",
                severity="warning",
                count=duplicate_rows,
                description=(
                    f"{duplicate_rows} rows repeat a previous full data row "
                    f"across {duplicate_groups} duplicate groups."
                ),
            )
        )
    return issues


def analyze_dataset_quality(
    storage: DatasetStorage,
    settings: Settings,
    dataset_id: str,
) -> DatasetQualityResponse:
    """Analyze a dataset in one validated pass without changing its bytes."""
    columns: list[_ColumnQuality] = []
    seen_rows: set[bytes] = set()
    duplicate_state = _DuplicateState()
    sample_limit = min(settings.data_quality_sample_limit, _SAMPLE_LIMIT)

    def consume_row(row: tuple[str, ...]) -> None:
        if not columns:
            columns.extend(
                _ColumnQuality(sample_limit=sample_limit) for _ in row
            )
        _collect_row(row, columns, seen_rows, duplicate_state)

    metadata = create_dataset_metadata(
        storage,
        settings,
        dataset_id,
        row_consumer=consume_row,
    )
    column_quality: list[DatasetColumnQuality] = []
    invalid_date_values = 0
    type_inconsistencies = 0

    for column, profile in zip(metadata.columns, columns):
        _type_issue(profile, column.data_type, column.name)
        invalid_date_values += profile.invalid_date_count
        if not profile.invalid_date_count:
            type_inconsistencies += profile.type_inconsistency_count
        column_quality.append(
            DatasetColumnQuality(
                name=column.name,
                inferred_type=column.data_type,
                non_null_count=profile.non_null_count,
                missing_count=profile.missing_count,
                missing_percentage=_percentage(
                    profile.missing_count,
                    metadata.row_count,
                ),
                invalid_date_count=profile.invalid_date_count,
                invalid_date_percentage=_percentage(
                    profile.invalid_date_count,
                    profile.non_null_count,
                ),
                type_inconsistency_count=profile.type_inconsistency_count,
                type_inconsistency_percentage=_percentage(
                    profile.type_inconsistency_count,
                    profile.non_null_count,
                ),
            )
        )

    duplicate_rows = duplicate_state.duplicate_rows
    duplicate_groups = duplicate_state.duplicate_groups
    issues = _create_issues(
        metadata,
        columns,
        duplicate_rows,
        duplicate_groups,
        sample_limit,
    )
    return DatasetQualityResponse(
        dataset_id=metadata.dataset_id,
        filename=metadata.filename,
        row_count=metadata.row_count,
        column_count=metadata.column_count,
        summary=DataQualitySummary(
            total_missing_cells=sum(
                profile.missing_count for profile in columns
            ),
            rows_with_missing_values=duplicate_state.rows_with_missing,
            duplicate_rows=duplicate_rows,
            duplicate_groups=duplicate_groups,
            invalid_date_values=invalid_date_values,
            type_inconsistencies=type_inconsistencies,
        ),
        column_quality=column_quality,
        issues=issues,
    )
