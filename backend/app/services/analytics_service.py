"""Reusable filtering, grouping, aggregation, sorting, and summary services."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, DecimalException, localcontext
from typing import Optional, Union

from app.core.config import Settings
from app.schemas.analytics import (
    AggregationSpec,
    AnalyticsCategoricalSummary,
    AnalyticsColumnSummary,
    AnalyticsFrequency,
    AnalyticsMissingSummary,
    AnalyticsNumericSummary,
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsSummary,
    FilterGroup,
    FilterRequest,
    SortRequest,
    SortSpec,
    SummarizeRequest,
)
from app.schemas.dataset import (
    DatasetColumnMetadata,
    DatasetLogicalType,
    DatasetMetadataResponse,
    DatasetStatisticsResponse,
)
from app.services.csv_validator import validate_csv
from app.services.dataset_profiler import (
    create_dataset_metadata,
    date_formats_for_value,
)
from app.services.dataset_storage import DatasetStorage
from app.services.statistical_profiler import (
    _as_json_number,
    _numeric_statistics,
    _quantile,
    create_dataset_statistics,
)

_NUMBER_PATTERN = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
_ISO_DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:")
_TRUE_TOKENS = {"true", "yes"}
_FALSE_TOKENS = {"false", "no"}
_NUMERIC_TYPES = {"integer", "float"}
_DATE_TYPES = {"date", "datetime"}
_STRING_TYPES = {"string", "categorical"}
_ORDERING_OPERATORS = {
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
}
_STRING_OPERATORS = {"contains", "starts_with", "ends_with"}
_DATE_FORMATS = {
    "iso_date": "%Y-%m-%d",
    "dmy_slash": "%d/%m/%Y",
    "mdy_slash": "%m/%d/%Y",
    "dmy_dash": "%d-%m-%Y",
    "mdy_dash": "%m-%d-%Y",
}
_NUMERIC_FUNCTIONS = {"sum", "mean", "median", "std"}

TypedValue = Union[Decimal, date, datetime, bool, str]


class AnalyticsValidationError(ValueError):
    """An invalid analytics request that can be reported to an API caller."""


@dataclass(frozen=True)
class _CompiledCondition:
    index: int
    operator: str
    data_type: DatasetLogicalType
    value: object = None


@dataclass(frozen=True)
class _ResolvedSort:
    index: int
    direction: str
    data_type: DatasetLogicalType


@dataclass(frozen=True)
class _ResolvedAggregation:
    function: str
    alias: str
    column: Optional[str]
    index: Optional[int]
    data_type: Optional[DatasetLogicalType]


def analyze_dataset(
    storage: DatasetStorage,
    settings: Settings,
    dataset_id: str,
    request: AnalyticsRequest,
) -> AnalyticsResponse:
    """Run one validated analytics operation against a stored dataset."""
    if request.operation == "summarize":
        return _summarize(storage, settings, dataset_id, request)

    metadata = create_dataset_metadata(storage, settings, dataset_id)
    if request.operation == "filter":
        return _execute_rows(
            storage,
            settings,
            dataset_id,
            metadata,
            request,
        )
    if request.operation == "sort":
        return _execute_rows(
            storage,
            settings,
            dataset_id,
            metadata,
            request,
        )
    return _execute_grouping(
        storage,
        settings,
        dataset_id,
        metadata,
        request.group_by,
        request.aggregations,
        request.filters,
        request.operation,
        request.limit,
    )


def _column_index(metadata: DatasetMetadataResponse) -> dict[str, int]:
    return {
        column.name: index
        for index, column in enumerate(metadata.columns)
    }


def _columns_by_name(
    metadata: DatasetMetadataResponse,
) -> dict[str, DatasetColumnMetadata]:
    return {column.name: column for column in metadata.columns}


def _require_column(
    name: str,
    columns: dict[str, DatasetColumnMetadata],
) -> str:
    if not name.strip():
        raise AnalyticsValidationError("Column names must be non-empty.")
    if name not in columns:
        raise AnalyticsValidationError(f"Unknown column '{name}'.")
    return name


def _unique_columns(
    names: list[str],
    columns: dict[str, DatasetColumnMetadata],
    field_name: str,
) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for name in names:
        required = _require_column(name, columns)
        if required in seen:
            raise AnalyticsValidationError(
                f"The {field_name} list contains duplicate column "
                f"'{required}'."
            )
        seen.add(required)
        cleaned.append(required)
    return cleaned


def _parse_decimal(value: str) -> Optional[Decimal]:
    if not _NUMBER_PATTERN.fullmatch(value):
        return None
    try:
        parsed = Decimal(value)
    except DecimalException:
        return None
    return parsed if parsed.is_finite() else None


def _require_decimal(value: object, column: str) -> Decimal:
    if isinstance(value, bool):
        raise AnalyticsValidationError(
            f"Column '{column}' requires a numeric comparison value."
        )
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise AnalyticsValidationError(
                f"Column '{column}' requires a finite numeric comparison value."
            )
        try:
            parsed = Decimal(str(value))
        except DecimalException as exc:
            raise AnalyticsValidationError(
                f"Column '{column}' requires a numeric comparison value."
            ) from exc
    elif isinstance(value, str):
        parsed = _parse_decimal(value)
        if parsed is None:
            raise AnalyticsValidationError(
                f"Column '{column}' requires a numeric comparison value."
            )
    else:
        raise AnalyticsValidationError(
            f"Column '{column}' requires a numeric comparison value."
        )
    return parsed


def _parse_bool(value: str) -> Optional[bool]:
    normalized = value.strip().casefold()
    if normalized in _TRUE_TOKENS:
        return True
    if normalized in _FALSE_TOKENS:
        return False
    return None


def _require_bool(value: object, column: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        parsed = _parse_bool(value)
        if parsed is not None:
            return parsed
    raise AnalyticsValidationError(
        f"Column '{column}' requires a boolean comparison value."
    )


def _datetime_key(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_date_value(
    value: str,
    data_type: DatasetLogicalType,
    *,
    strict: bool,
) -> Optional[Union[date, datetime]]:
    if data_type == "datetime":
        if not _ISO_DATETIME_PATTERN.match(value):
            if strict:
                raise AnalyticsValidationError(
                    "Datetime comparison values must use an ISO datetime."
                )
            return None
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            return datetime.fromisoformat(candidate)
        except ValueError as exc:
            if strict:
                raise AnalyticsValidationError(
                    "The datetime comparison value is invalid."
                ) from exc
            return None

    formats = date_formats_for_value(value)
    if len(formats) != 1:
        if strict:
            if not formats:
                raise AnalyticsValidationError(
                    "The date comparison value is invalid."
                )
            raise AnalyticsValidationError(
                "The date comparison value is ambiguous; use an unambiguous "
                "date such as YYYY-MM-DD."
            )
        return None
    format_name = next(iter(formats))
    if format_name not in _DATE_FORMATS:
        if strict:
            raise AnalyticsValidationError(
                "The date comparison value is invalid."
            )
        return None
    try:
        if format_name == "iso_date":
            return date.fromisoformat(value)
        return datetime.strptime(
            value,
            _DATE_FORMATS[format_name],
        ).date()
    except ValueError as exc:
        if strict:
            raise AnalyticsValidationError(
                "The date comparison value is invalid."
            ) from exc
        return None


def _compile_condition(
    index: int,
    column: DatasetColumnMetadata,
    operator: str,
    value: object,
) -> _CompiledCondition:
    name = column.name
    data_type = column.data_type

    if operator in {"is_null", "is_not_null"}:
        if value is not None:
            raise AnalyticsValidationError(
                f"The '{operator}' operator does not accept a comparison value."
            )
        return _CompiledCondition(index, operator, data_type)

    if operator in _STRING_OPERATORS:
        if data_type not in _STRING_TYPES:
            raise AnalyticsValidationError(
                f"The '{operator}' operator requires a string or categorical "
                f"column; column '{name}' is {data_type}."
            )
        if not isinstance(value, str):
            raise AnalyticsValidationError(
                f"The '{operator}' operator requires a string comparison value."
            )
        return _CompiledCondition(index, operator, data_type, value)

    if operator in _ORDERING_OPERATORS:
        if data_type in _NUMERIC_TYPES:
            expected = _require_decimal(value, name)
        elif data_type in _DATE_TYPES:
            if not isinstance(value, str):
                raise AnalyticsValidationError(
                    f"Column '{name}' requires a date comparison value."
                )
            expected = _parse_date_value(value, data_type, strict=True)
        else:
            raise AnalyticsValidationError(
                f"The '{operator}' operator is not supported for {data_type} "
                f"column '{name}'."
            )
        return _CompiledCondition(index, operator, data_type, expected)

    if value is None:
        raise AnalyticsValidationError(
            f"The '{operator}' operator requires a comparison value; use "
            "'is_null' or 'is_not_null' to test missing values."
        )

    if data_type in _NUMERIC_TYPES:
        expected = _require_decimal(value, name)
    elif data_type in _DATE_TYPES:
        if not isinstance(value, str):
            raise AnalyticsValidationError(
                f"Column '{name}' requires a date comparison value."
            )
        expected = _parse_date_value(value, data_type, strict=True)
    elif data_type == "boolean":
        expected = _require_bool(value, name)
    else:
        if not isinstance(value, str):
            raise AnalyticsValidationError(
                f"Column '{name}' requires a string comparison value."
            )
        expected = value
    return _CompiledCondition(index, operator, data_type, expected)


def _compile_filters(
    filter_group: FilterGroup,
    metadata: DatasetMetadataResponse,
) -> tuple[str, tuple[_CompiledCondition, ...]]:
    columns = _columns_by_name(metadata)
    index_by_name = _column_index(metadata)
    conditions: list[_CompiledCondition] = []
    for condition in filter_group.conditions:
        name = _require_column(condition.column, columns)
        conditions.append(
            _compile_condition(
                index_by_name[name],
                columns[name],
                condition.operator,
                condition.value,
            )
        )
    return filter_group.combine, tuple(conditions)


def _compare(
    operator: str,
    left: object,
    right: object,
) -> bool:
    if operator == "equals":
        return left == right
    if operator == "not_equals":
        return left != right
    if operator == "greater_than":
        return left > right
    if operator == "greater_than_or_equal":
        return left >= right
    if operator == "less_than":
        return left < right
    return left <= right


def _matches_condition(value: str, condition: _CompiledCondition) -> bool:
    operator = condition.operator
    if operator == "is_null":
        return value == ""
    if operator == "is_not_null":
        return value != ""
    if value == "":
        return False

    if condition.data_type in _NUMERIC_TYPES:
        cell = _parse_decimal(value)
        if cell is None:
            return False
        return _compare(operator, cell, condition.value)
    if condition.data_type in _DATE_TYPES:
        cell_date = _parse_date_value(
            value,
            condition.data_type,
            strict=False,
        )
        if cell_date is None:
            return False
        if condition.data_type == "datetime":
            return _compare(
                operator,
                _datetime_key(cell_date),
                _datetime_key(condition.value),
            )
        return _compare(operator, cell_date, condition.value)
    if condition.data_type == "boolean":
        cell_bool = _parse_bool(value)
        if cell_bool is None:
            return False
        return _compare(operator, cell_bool, condition.value)

    if operator == "equals":
        return value == condition.value
    if operator == "not_equals":
        return value != condition.value
    if operator == "contains":
        return str(condition.value) in value
    if operator == "starts_with":
        return value.startswith(str(condition.value))
    if operator == "ends_with":
        return value.endswith(str(condition.value))
    return False


def _row_matches(
    row: tuple[str, ...],
    combine: str,
    conditions: tuple[_CompiledCondition, ...],
) -> bool:
    if not conditions:
        return True
    if combine == "and":
        return all(
            _matches_condition(row[condition.index], condition)
            for condition in conditions
        )
    return any(
        _matches_condition(row[condition.index], condition)
        for condition in conditions
    )


def _validate_projection(
    requested: Optional[list[str]],
    metadata: DatasetMetadataResponse,
) -> list[str]:
    if requested is None:
        return [column.name for column in metadata.columns]
    return _unique_columns(
        requested,
        _columns_by_name(metadata),
        "columns",
    )


def _resolve_sort(
    specs: list[SortSpec],
    metadata: DatasetMetadataResponse,
) -> list[_ResolvedSort]:
    columns = _columns_by_name(metadata)
    index_by_name = _column_index(metadata)
    resolved: list[_ResolvedSort] = []
    seen: set[str] = set()
    for spec in specs:
        name = _require_column(spec.column, columns)
        if name in seen:
            raise AnalyticsValidationError(
                f"The sort list contains duplicate column '{name}'."
            )
        seen.add(name)
        resolved.append(
            _ResolvedSort(
                index_by_name[name],
                spec.direction,
                columns[name].data_type,
            )
        )
    return resolved


def _typed_value(
    value: str,
    data_type: DatasetLogicalType,
) -> Optional[TypedValue]:
    if data_type in _NUMERIC_TYPES:
        return _parse_decimal(value)
    if data_type in _DATE_TYPES:
        return _parse_date_value(value, data_type, strict=False)
    if data_type == "boolean":
        return _parse_bool(value)
    if data_type in _STRING_TYPES:
        return value
    return value


def _sort_key(value: str, data_type: DatasetLogicalType) -> tuple[object, ...]:
    if value == "":
        return (1, 0)
    typed = _typed_value(value, data_type)
    if typed is None:
        if data_type in _NUMERIC_TYPES:
            typed = Decimal(0)
        elif data_type == "datetime":
            typed = datetime.min.replace(tzinfo=timezone.utc)
        elif data_type == "date":
            typed = date.min
        elif data_type == "boolean":
            typed = False
        else:
            typed = ""
    elif data_type == "datetime" and isinstance(typed, datetime):
        typed = _datetime_key(typed)
    return (0, typed)


def _sort_rows(
    rows: list[tuple[str, ...]],
    sorts: list[_ResolvedSort],
) -> None:
    for sort in reversed(sorts):
        rows.sort(
            key=lambda row, current=sort: _sort_key(
                row[current.index],
                current.data_type,
            ),
            reverse=sort.direction == "desc",
        )


def _project_row(
    row: tuple[str, ...],
    column_names: list[str],
    indices: list[int],
) -> dict[str, Optional[str]]:
    return {
        name: row[index] if row[index] != "" else None
        for name, index in zip(column_names, indices)
    }


def _effective_limit(
    requested: Optional[int],
    maximum: int,
) -> int:
    if requested is None:
        return maximum
    return min(requested, maximum)


def _execute_rows(
    storage: DatasetStorage,
    settings: Settings,
    dataset_id: str,
    metadata: DatasetMetadataResponse,
    request: Union[FilterRequest, SortRequest],
) -> AnalyticsResponse:
    columns = _columns_by_name(metadata)
    index_by_name = _column_index(metadata)
    projection = _validate_projection(request.columns, metadata)
    projection_indices = [index_by_name[name] for name in projection]
    sorts = _resolve_sort(request.sort_by, metadata)
    compiled = (
        _compile_filters(request.filters, metadata)
        if request.filters is not None
        else None
    )
    limit = _effective_limit(
        request.limit,
        settings.analytics_max_result_rows,
    )
    collect_all = bool(sorts)
    matched_count = 0
    collected: list[tuple[str, ...]] = []

    def consume(row: tuple[str, ...]) -> None:
        nonlocal matched_count
        if compiled is not None and not _row_matches(row, *compiled):
            return
        matched_count += 1
        if collect_all:
            collected.append(row)
        elif len(collected) < limit + 1:
            collected.append(row)

    with storage.open_dataset(dataset_id) as dataset_file:
        validation = validate_csv(
            dataset_file,
            settings,
            row_consumer=consume,
        )

    if collect_all:
        _sort_rows(collected, sorts)
    selected = collected[:limit]
    rows = [
        _project_row(row, projection, projection_indices)
        for row in selected
    ]
    return AnalyticsResponse(
        dataset_id=metadata.dataset_id,
        operation=request.operation,
        columns=projection,
        rows=rows,
        row_count=validation.data_row_count,
        result_count=len(rows),
        truncated=matched_count > len(rows),
        matched_count=matched_count,
    )


def _resolve_aggregations(
    specs: list[AggregationSpec],
    metadata: DatasetMetadataResponse,
    group_columns: list[str],
    *,
    allow_default: bool,
) -> list[_ResolvedAggregation]:
    columns = _columns_by_name(metadata)
    index_by_name = _column_index(metadata)
    if not specs and allow_default:
        specs = [AggregationSpec(function="count")]

    resolved: list[_ResolvedAggregation] = []
    aliases: set[str] = set(group_columns)
    for spec in specs:
        function = spec.function
        column_name: Optional[str] = None
        index: Optional[int] = None
        data_type: Optional[DatasetLogicalType] = None

        if function == "count" and spec.column is None:
            alias = spec.alias.strip() if spec.alias is not None else ""
            alias = alias or "count_rows"
        else:
            if spec.column is None:
                raise AnalyticsValidationError(
                    f"The '{function}' aggregation requires a column."
                )
            column_name = _require_column(spec.column, columns)
            column = columns[column_name]
            data_type = column.data_type
            index = index_by_name[column_name]
            if function in _NUMERIC_FUNCTIONS and data_type not in _NUMERIC_TYPES:
                raise AnalyticsValidationError(
                    f"The '{function}' aggregation requires a numeric column; "
                    f"column '{column_name}' is {data_type}."
                )
            if function in {"min", "max"} and data_type == "boolean":
                raise AnalyticsValidationError(
                    f"The '{function}' aggregation is not supported for "
                    f"boolean column '{column_name}'."
                )
            default_alias = f"{function}_{column_name}"
            alias = (
                spec.alias.strip()
                if spec.alias is not None
                else default_alias
            )
            alias = alias or default_alias

        if not alias.strip():
            raise AnalyticsValidationError(
                "Aggregation aliases must be non-empty."
            )
        if alias in aliases:
            raise AnalyticsValidationError(
                f"Duplicate aggregation alias or group column '{alias}'."
            )
        aliases.add(alias)
        resolved.append(
            _ResolvedAggregation(
                function=function,
                alias=alias,
                column=column_name,
                index=index,
                data_type=data_type,
            )
        )
    return resolved


class _AggregationState:
    """Accumulate bounded per-group aggregation results."""

    def __init__(self, spec: _ResolvedAggregation) -> None:
        self.spec = spec
        self.row_count = 0
        self.non_null_count = 0
        self.distinct_values: set[str] = set()
        self.numbers: list[Decimal] = []
        self.best: Optional[TypedValue] = None
        self.has_best = False

    def add(self, row: tuple[str, ...]) -> None:
        if self.spec.index is None:
            self.row_count += 1
            return
        value = row[self.spec.index]
        if value == "":
            return
        function = self.spec.function
        if function == "count":
            self.non_null_count += 1
            return
        if function == "nunique":
            self.distinct_values.add(value)
            return
        if function in _NUMERIC_FUNCTIONS:
            parsed = _parse_decimal(value)
            if parsed is not None:
                self.numbers.append(parsed)
            return
        if function in {"min", "max"}:
            typed = _typed_value(value, self.spec.data_type or "string")
            if typed is None:
                return
            if not self.has_best:
                self.best = typed
                self.has_best = True
            elif function == "min" and _is_less(typed, self.best):
                self.best = typed
            elif function == "max" and _is_greater(typed, self.best):
                self.best = typed

    def finalize(self) -> object:
        function = self.spec.function
        if function == "count":
            if self.spec.index is None:
                return self.row_count
            return self.non_null_count
        if function == "nunique":
            return len(self.distinct_values)
        if function == "sum":
            return self._numeric_total(
                lambda values: sum(values, Decimal(0))
            )
        if function == "mean":
            return self._numeric_total(
                lambda values: sum(values, Decimal(0)) / len(values)
            )
        if function == "median":
            return self._numeric_total(
                lambda values: _quantile(
                    sorted(values),
                    Decimal("0.5"),
                )
            )
        if function == "std":
            if len(self.numbers) < 2:
                return None
            return self._numeric_total(self._standard_deviation)

        if not self.has_best or self.best is None:
            return None
        if self.spec.data_type in _NUMERIC_TYPES:
            return _as_json_number(self.best)
        if self.spec.data_type in _DATE_TYPES:
            return self.best.isoformat()
        return str(self.best)

    def _numeric_total(self, operation) -> Optional[float]:
        if not self.numbers:
            return None
        try:
            with localcontext() as context:
                context.prec = 50
                result = operation(self.numbers)
        except (DecimalException, ZeroDivisionError):
            return None
        return _as_json_number(result)

    def _standard_deviation(self, values: list[Decimal]) -> Decimal:
        count = len(values)
        mean = sum(values, Decimal(0)) / count
        variance = sum(
            ((value - mean) ** 2 for value in values),
            Decimal(0),
        ) / (count - 1)
        return variance.sqrt()


def _group_sort_key(
    key: tuple[Optional[str], ...],
    data_types: list[DatasetLogicalType],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (0, 0)
        if value is None
        else (1, _sort_key(value, data_type)[1])
        for value, data_type in zip(key, data_types)
    )


def _is_less(left: TypedValue, right: TypedValue) -> bool:
    if isinstance(left, datetime) and isinstance(right, datetime):
        return _datetime_key(left) < _datetime_key(right)
    return left < right


def _is_greater(left: TypedValue, right: TypedValue) -> bool:
    if isinstance(left, datetime) and isinstance(right, datetime):
        return _datetime_key(left) > _datetime_key(right)
    return left > right


def _execute_grouping(
    storage: DatasetStorage,
    settings: Settings,
    dataset_id: str,
    metadata: DatasetMetadataResponse,
    group_by: list[str],
    aggregations: list[AggregationSpec],
    filters: Optional[FilterGroup],
    operation: str,
    requested_limit: Optional[int],
) -> AnalyticsResponse:
    columns = _columns_by_name(metadata)
    index_by_name = _column_index(metadata)
    group_columns = _unique_columns(group_by, columns, "group_by")
    group_indices = [index_by_name[name] for name in group_columns]
    group_data_types = [columns[name].data_type for name in group_columns]
    resolved = _resolve_aggregations(
        aggregations,
        metadata,
        group_columns,
        allow_default=True,
    )
    compiled = (
        _compile_filters(filters, metadata)
        if filters is not None
        else None
    )
    groups: dict[
        tuple[Optional[str], ...],
        list[_AggregationState],
    ] = {}
    matched_count = 0

    def consume(row: tuple[str, ...]) -> None:
        nonlocal matched_count
        if compiled is not None and not _row_matches(row, *compiled):
            return
        matched_count += 1
        key = tuple(
            row[index] if row[index] != "" else None
            for index in group_indices
        )
        states = groups.get(key)
        if states is None:
            states = [_AggregationState(spec) for spec in resolved]
            groups[key] = states
        for state in states:
            state.add(row)

    with storage.open_dataset(dataset_id) as dataset_file:
        validation = validate_csv(
            dataset_file,
            settings,
            row_consumer=consume,
        )

    if group_columns:
        total_groups = len(groups)
        ordered_keys = sorted(
            groups,
            key=lambda key: _group_sort_key(key, group_data_types),
        )
        maximum = settings.analytics_max_groups
        limit = _effective_limit(requested_limit, maximum)
        selected_keys = ordered_keys[:limit]
        truncated = total_groups > len(selected_keys)
    else:
        if () not in groups:
            groups[()] = [
                _AggregationState(spec) for spec in resolved
            ]
        total_groups = 1
        selected_keys = [()]
        truncated = False

    rows: list[dict[str, object]] = []
    for key in selected_keys:
        row: dict[str, object] = {
            column: value
            for column, value in zip(group_columns, key)
        }
        for spec, state in zip(resolved, groups[key]):
            row[spec.alias] = state.finalize()
        rows.append(row)

    output_columns = group_columns + [spec.alias for spec in resolved]
    return AnalyticsResponse(
        dataset_id=metadata.dataset_id,
        operation=operation,
        columns=output_columns,
        rows=rows,
        row_count=validation.data_row_count,
        result_count=len(rows),
        truncated=truncated,
        matched_count=matched_count,
        total_groups=total_groups,
    )


def _percentage(count: int, total: int) -> float:
    return count / total * 100 if total else 0.0


def _build_summary_from_statistics(
    statistics: DatasetStatisticsResponse,
    top_values_limit: int,
) -> tuple[AnalyticsSummary, list[str]]:
    columns: list[AnalyticsColumnSummary] = []
    numeric: list[AnalyticsNumericSummary] = []
    categorical: list[AnalyticsCategoricalSummary] = []
    missing: list[AnalyticsMissingSummary] = []

    for column in statistics.column_statistics:
        columns.append(
            AnalyticsColumnSummary(
                name=column.name,
                data_type=column.data_type,
            )
        )
        if column.data_type in _NUMERIC_TYPES and column.numeric is not None:
            numeric.append(
                AnalyticsNumericSummary(
                    column=column.name,
                    count=column.numeric.count,
                    mean=column.numeric.mean,
                    median=column.numeric.median,
                    minimum=column.numeric.minimum,
                    maximum=column.numeric.maximum,
                    standard_deviation=column.numeric.standard_deviation,
                )
            )
        if column.data_type == "categorical":
            top_values = [
                AnalyticsFrequency(value=value.value, count=value.count)
                for value in column.value_frequencies[:top_values_limit]
            ]
            categorical.append(
                AnalyticsCategoricalSummary(
                    column=column.name,
                    distinct_count=column.distinct_count or 0,
                    top_values=top_values,
                    truncated=(
                        column.value_frequencies_truncated
                        or len(column.value_frequencies) > top_values_limit
                    ),
                )
            )
        missing.append(
            AnalyticsMissingSummary(
                column=column.name,
                missing_count=column.null_count,
                missing_percentage=_percentage(
                    column.null_count,
                    statistics.row_count,
                ),
            )
        )

    summary = AnalyticsSummary(
        row_count=statistics.row_count,
        column_count=statistics.column_count,
        columns=columns,
        numeric=numeric,
        categorical=categorical,
        missing=missing,
    )
    return summary, [column.name for column in statistics.column_statistics]


def _summarize_filtered(
    storage: DatasetStorage,
    settings: Settings,
    dataset_id: str,
    metadata: DatasetMetadataResponse,
    filters: FilterGroup,
    top_values_limit: int,
) -> tuple[AnalyticsSummary, list[str]]:
    compiled = _compile_filters(filters, metadata)
    numeric_values: dict[str, list[Decimal]] = {
        column.name: []
        for column in metadata.columns
        if column.data_type in _NUMERIC_TYPES
    }
    categorical_values: dict[str, Counter[str]] = {
        column.name: Counter()
        for column in metadata.columns
        if column.data_type == "categorical"
    }
    missing_counts = {column.name: 0 for column in metadata.columns}
    matching_rows = 0

    def consume(row: tuple[str, ...]) -> None:
        nonlocal matching_rows
        if not _row_matches(row, *compiled):
            return
        matching_rows += 1
        for column, value in zip(metadata.columns, row):
            if value == "":
                missing_counts[column.name] += 1
                continue
            if column.data_type in _NUMERIC_TYPES:
                parsed = _parse_decimal(value)
                if parsed is not None:
                    numeric_values[column.name].append(parsed)
            elif column.data_type == "categorical":
                categorical_values[column.name][value] += 1

    with storage.open_dataset(dataset_id) as dataset_file:
        validate_csv(
            dataset_file,
            settings,
            row_consumer=consume,
        )

    columns = [
        AnalyticsColumnSummary(
            name=column.name,
            data_type=column.data_type,
        )
        for column in metadata.columns
    ]
    numeric: list[AnalyticsNumericSummary] = []
    categorical: list[AnalyticsCategoricalSummary] = []
    missing: list[AnalyticsMissingSummary] = []

    for column in metadata.columns:
        if column.data_type in _NUMERIC_TYPES:
            stats = _numeric_statistics(numeric_values[column.name])
            numeric.append(
                AnalyticsNumericSummary(
                    column=column.name,
                    count=stats.count,
                    mean=stats.mean,
                    median=stats.median,
                    minimum=stats.minimum,
                    maximum=stats.maximum,
                    standard_deviation=stats.standard_deviation,
                )
            )
        if column.data_type == "categorical":
            frequencies = categorical_values[column.name]
            ordered = sorted(
                frequencies.items(),
                key=lambda item: (-item[1], item[0]),
            )
            top_values = [
                AnalyticsFrequency(value=value, count=count)
                for value, count in ordered[:top_values_limit]
            ]
            categorical.append(
                AnalyticsCategoricalSummary(
                    column=column.name,
                    distinct_count=len(frequencies),
                    top_values=top_values,
                    truncated=len(ordered) > top_values_limit,
                )
            )
        missing.append(
            AnalyticsMissingSummary(
                column=column.name,
                missing_count=missing_counts[column.name],
                missing_percentage=_percentage(
                    missing_counts[column.name],
                    matching_rows,
                ),
            )
        )

    summary = AnalyticsSummary(
        row_count=matching_rows,
        column_count=len(metadata.columns),
        columns=columns,
        numeric=numeric,
        categorical=categorical,
        missing=missing,
    )
    return summary, [column.name for column in metadata.columns]


def _summarize(
    storage: DatasetStorage,
    settings: Settings,
    dataset_id: str,
    request: SummarizeRequest,
) -> AnalyticsResponse:
    top_values_limit = _effective_limit(
        request.top_values_limit,
        settings.analytics_max_value_frequencies,
    )

    if request.filters is None and not request.group_by:
        statistics = create_dataset_statistics(
            storage,
            settings,
            dataset_id,
            value_frequency_limit=top_values_limit,
        )
        summary, columns = _build_summary_from_statistics(
            statistics,
            top_values_limit,
        )
        return AnalyticsResponse(
            dataset_id=statistics.dataset_id,
            operation="summarize",
            columns=columns,
            row_count=statistics.row_count,
            result_count=0,
            truncated=any(
                item.truncated for item in summary.categorical
            ),
            matched_count=statistics.row_count,
            summary=summary,
        )

    metadata = create_dataset_metadata(storage, settings, dataset_id)
    if request.filters is None:
        statistics = create_dataset_statistics(
            storage,
            settings,
            dataset_id,
            value_frequency_limit=top_values_limit,
        )
        summary, columns = _build_summary_from_statistics(
            statistics,
            top_values_limit,
        )
    else:
        summary, columns = _summarize_filtered(
            storage,
            settings,
            dataset_id,
            metadata,
            request.filters,
            top_values_limit,
        )

    truncated = any(item.truncated for item in summary.categorical)
    result_count = 0
    if request.group_by:
        grouping = _execute_grouping(
            storage,
            settings,
            dataset_id,
            metadata,
            request.group_by,
            request.aggregations,
            request.filters,
            "aggregate",
            None,
        )
        summary.group_by = grouping.columns[: len(request.group_by)]
        summary.groups = grouping.rows
        summary.groups_truncated = grouping.truncated
        summary.total_groups = grouping.total_groups
        truncated = truncated or grouping.truncated
        result_count = grouping.result_count

    return AnalyticsResponse(
        dataset_id=metadata.dataset_id,
        operation="summarize",
        columns=columns,
        row_count=metadata.row_count,
        result_count=result_count,
        truncated=truncated,
        matched_count=summary.row_count,
        total_groups=summary.total_groups,
        summary=summary,
    )
