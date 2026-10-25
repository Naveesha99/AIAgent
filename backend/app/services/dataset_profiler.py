"""Bounded-memory schema inference for validated stored CSV datasets."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Callable, Optional
from uuid import UUID

from app.core.config import Settings
from app.schemas.dataset import DatasetColumnMetadata, DatasetMetadataResponse
from app.services.csv_validator import CsvValidationResult, validate_csv
from app.services.dataset_storage import DatasetStorage

_INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
_NUMBER_PATTERN = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
_SLASH_DATE_PATTERN = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_DASH_DATE_PATTERN = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:")
_MAX_CATEGORICAL_VALUES = 50
_MAX_CATEGORICAL_VALUE_LENGTH = 128

_TRUE_FALSE = {"true", "false"}
_YES_NO = {"yes", "no"}
_NONFINITE_NUMBERS = {
    "nan",
    "+nan",
    "-nan",
    "inf",
    "+inf",
    "-inf",
    "infinity",
    "+infinity",
    "-infinity",
}


class _ColumnProfile:
    """Accumulate only bounded inference state for one column."""

    def __init__(self) -> None:
        self.non_null_count = 0
        self.numeric_possible = True
        self.has_float = False
        self.boolean_possible = True
        self.boolean_values: set[str] = set()
        self.boolean_vocabulary: Optional[frozenset[str]] = None
        self.date_formats: Optional[set[str]] = None
        self.distinct_values: set[str] = set()
        self.too_many_distinct_values = False
        self.has_long_value = False
        self.has_nonfinite_number = False
        self.value_counts: dict[str, int] = {}

    def add(self, value: str) -> None:
        self.non_null_count += 1
        self._inspect_numeric(value)
        self._inspect_boolean(value)
        self._inspect_date(value)
        self._inspect_categorical(value)

    def _inspect_numeric(self, value: str) -> None:
        if value.casefold() in _NONFINITE_NUMBERS:
            self.has_nonfinite_number = True
        if not self.numeric_possible:
            return
        if _INTEGER_PATTERN.fullmatch(value):
            digits = value.lstrip("+-")
            if len(digits) > 1 and digits.startswith("0"):
                self.numeric_possible = False
            return
        if not _NUMBER_PATTERN.fullmatch(value):
            self.numeric_possible = False
            return
        try:
            number = float(value)
        except (OverflowError, ValueError):
            self.numeric_possible = False
            return
        if not math.isfinite(number):
            self.numeric_possible = False
            return
        self.has_float = True

    def _inspect_boolean(self, value: str) -> None:
        if not self.boolean_possible:
            return
        normalized = value.casefold()
        if normalized in _TRUE_FALSE:
            vocabulary = frozenset(_TRUE_FALSE)
        elif normalized in _YES_NO:
            vocabulary = frozenset(_YES_NO)
        else:
            self.boolean_possible = False
            return
        if self.boolean_vocabulary is None:
            self.boolean_vocabulary = vocabulary
        elif self.boolean_vocabulary != vocabulary:
            self.boolean_possible = False
            return
        self.boolean_values.add(normalized)

    def _inspect_date(self, value: str) -> None:
        possible_formats = date_formats_for_value(value)
        if self.date_formats is None:
            self.date_formats = possible_formats
        else:
            self.date_formats.intersection_update(possible_formats)

    def _inspect_categorical(self, value: str) -> None:
        if len(value) > _MAX_CATEGORICAL_VALUE_LENGTH:
            self.has_long_value = True
            return
        if value in self.distinct_values:
            self.value_counts[value] += 1
            return
        if len(self.distinct_values) >= _MAX_CATEGORICAL_VALUES:
            self.too_many_distinct_values = True
            return
        self.distinct_values.add(value)
        self.value_counts[value] = 1

    def infer_type(self, name: str) -> str:
        if self.non_null_count == 0:
            return "string"
        if _is_identifier_name(name):
            return "string"
        if (
            self.boolean_possible
            and len(self.boolean_values) == 2
            and self.boolean_vocabulary is not None
        ):
            return "boolean"
        if self.date_formats and len(self.date_formats) == 1:
            date_format = next(iter(self.date_formats))
            return "datetime" if date_format == "iso_datetime" else "date"
        if self.numeric_possible:
            return "float" if self.has_float else "integer"
        if self._is_categorical():
            return "categorical"
        return "string"

    def _is_categorical(self) -> bool:
        distinct_count = len(self.distinct_values)
        if (
            self.non_null_count < 5
            or self.too_many_distinct_values
            or self.has_long_value
            or self.has_nonfinite_number
            or distinct_count == 0
        ):
            return False
        distinct_limit = min(
            _MAX_CATEGORICAL_VALUES,
            max(5, math.isqrt(self.non_null_count)),
        )
        return (
            distinct_count <= distinct_limit
            and distinct_count * 2 <= self.non_null_count
            and any(count >= 2 for count in self.value_counts.values())
        )


def date_formats_for_value(value: str) -> set[str]:
    """Return valid, explicit formats; ambiguous slash dates retain both orders."""
    if _ISO_DATE_PATTERN.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return set()
        return {"iso_date"}

    if _ISO_DATETIME_PATTERN.match(value):
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            datetime.fromisoformat(candidate)
        except ValueError:
            return set()
        return {"iso_datetime"}

    for pattern, separator in (
        (_SLASH_DATE_PATTERN, "slash"),
        (_DASH_DATE_PATTERN, "dash"),
    ):
        match = pattern.fullmatch(value)
        if match is None:
            continue
        first, second, year = (int(part) for part in match.groups())
        valid_formats: set[str] = set()
        for first_is_day, name in (
            (True, f"dmy_{separator}"),
            (False, f"mdy_{separator}"),
        ):
            month, day = (second, first) if first_is_day else (first, second)
            try:
                date(year, month, day)
            except ValueError:
                continue
            valid_formats.add(name)
        return valid_formats
    return set()


def _is_identifier_name(name: str) -> bool:
    spaced_name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name.strip())
    words = re.findall(r"[a-z0-9]+", spaced_name.casefold())
    return bool(words and words[-1] in {"id", "identifier", "uuid"})


def _build_metadata(
    validation: CsvValidationResult,
    profiles: list[_ColumnProfile],
    *,
    dataset_id: str,
    filename: str,
) -> DatasetMetadataResponse:
    columns: list[DatasetColumnMetadata] = []
    for name, profile in zip(validation.column_names, profiles):
        data_type = profile.infer_type(name)
        is_numeric = data_type in {"integer", "float"}
        is_date = data_type in {"date", "datetime"}
        columns.append(
            DatasetColumnMetadata(
                name=name,
                data_type=data_type,
                non_null_count=profile.non_null_count,
                null_count=validation.data_row_count - profile.non_null_count,
                is_numeric=is_numeric,
                is_date=is_date,
                is_categorical=data_type == "categorical",
            )
        )
    return DatasetMetadataResponse(
        dataset_id=UUID(dataset_id),
        filename=filename,
        row_count=validation.data_row_count,
        column_count=validation.column_count,
        columns=columns,
        numeric_columns=[
            column.name for column in columns if column.is_numeric
        ],
        date_columns=[column.name for column in columns if column.is_date],
        categorical_columns=[
            column.name for column in columns if column.is_categorical
        ],
    )


def create_dataset_metadata(
    storage: DatasetStorage,
    settings: Settings,
    dataset_id: str,
    *,
    row_consumer: Optional[Callable[[tuple[str, ...]], None]] = None,
) -> DatasetMetadataResponse:
    """Validate and profile a stored CSV in one bounded-memory pass."""
    profiles: list[_ColumnProfile] = []

    def consume_row(row: tuple[str, ...]) -> None:
        for profile, value in zip(profiles, row):
            if value != "":
                profile.add(value)
        if row_consumer is not None:
            row_consumer(row)

    with storage.open_dataset(dataset_id) as dataset_file:
        filename = storage.get_filename(dataset_id)

        def consume_columns(row: tuple[str, ...]) -> None:
            if not profiles:
                profiles.extend(_ColumnProfile() for _ in row)
            consume_row(row)

        validation = validate_csv(
            dataset_file,
            settings,
            row_consumer=consume_columns,
        )

    return _build_metadata(
        validation,
        profiles,
        dataset_id=dataset_id,
        filename=filename,
    )
