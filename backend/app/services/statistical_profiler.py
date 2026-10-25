"""Descriptive statistics for stored CSV datasets."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, DecimalException, localcontext
from typing import Optional

from app.core.config import Settings
from app.schemas.dataset import (
    CategoryFrequency,
    DatasetColumnStatistics,
    DatasetStatisticsResponse,
    DatasetLogicalType,
    NumericColumnStatistics,
)
from app.services.dataset_profiler import create_dataset_metadata
from app.services.dataset_storage import DatasetStorage

_NUMBER_PATTERN = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
_MAX_VALUE_FREQUENCIES = 20
_MAX_TRACKED_FREQUENCIES = 50
_DATE_FORMATS = {
    "iso_date": "%Y-%m-%d",
    "dmy_slash": "%d/%m/%Y",
    "mdy_slash": "%m/%d/%Y",
    "dmy_dash": "%d-%m-%Y",
    "mdy_dash": "%m-%d-%Y",
}


class _ColumnStatistics:
    """Bounded dataset-limit accumulator for one column."""

    def __init__(self) -> None:
        self.numeric_values: list[Decimal] = []
        self.distinct_values: set[str] = set()
        self.frequencies: dict[str, int] = {}
        self.track_frequencies = True
        self.date_counts: dict[str, int] = {}
        self.date_ranges: dict[str, tuple[date | datetime, date | datetime]] = {}
        self.incompatible_date_ranges: set[str] = set()
        self.date_counts: dict[str, int] = {}

    def add(self, value: str) -> None:
        self.distinct_values.add(value)
        if self.track_frequencies:
            if value in self.frequencies:
                self.frequencies[value] += 1
            elif len(self.frequencies) < _MAX_TRACKED_FREQUENCIES:
                self.frequencies[value] = 1
            else:
                self.frequencies.clear()
                self.track_frequencies = False
        if _NUMBER_PATTERN.fullmatch(value):
            try:
                number = Decimal(value)
            except DecimalException:
                number = Decimal("NaN")
            if number.is_finite():
                self.numeric_values.append(number)
        self._add_date(value)

    def _add_date(self, value: str) -> None:
        possible_dates: dict[str, date | datetime] = {}
        for format_name, date_format in _DATE_FORMATS.items():
            try:
                possible_dates[format_name] = datetime.strptime(
                    value, date_format
                ).date()
            except ValueError:
                continue
        if _looks_like_iso_datetime(value):
            candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
            try:
                possible_dates["iso_datetime"] = datetime.fromisoformat(candidate)
            except ValueError:
                pass

        for format_name, parsed in possible_dates.items():
            self.date_counts[format_name] = (
                self.date_counts.get(format_name, 0) + 1
            )
            if format_name in self.incompatible_date_ranges:
                continue
            current = self.date_ranges.get(format_name)
            if current is None:
                self.date_ranges[format_name] = (parsed, parsed)
            else:
                try:
                    self.date_ranges[format_name] = (
                        min(current[0], parsed),
                        max(current[1], parsed),
                    )
                except TypeError:
                    self.date_ranges.pop(format_name, None)
                    self.incompatible_date_ranges.add(format_name)


def _looks_like_iso_datetime(value: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:", value))


def _as_json_number(value: Decimal) -> Optional[float]:
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _quantile(
    sorted_values: list[Decimal],
    probability: Decimal,
) -> Decimal:
    """Use pandas' default linear interpolation at rank (n - 1) * q."""
    position = Decimal(len(sorted_values) - 1) * probability
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    lower = sorted_values[lower_index]
    upper = sorted_values[upper_index]
    return lower + (upper - lower) * fraction


def _numeric_statistics(
    values: list[Decimal],
) -> NumericColumnStatistics:
    count = len(values)
    if count == 0:
        return NumericColumnStatistics(
            count=0,
            mean=None,
            median=None,
            standard_deviation=None,
            variance=None,
            minimum=None,
            maximum=None,
            q1=None,
            q3=None,
            iqr=None,
            sum=None,
        )

    ordered = sorted(values)
    total: Optional[Decimal] = None
    mean: Optional[Decimal] = None
    median: Optional[Decimal] = None
    q1: Optional[Decimal] = None
    q3: Optional[Decimal] = None
    iqr: Optional[Decimal] = None
    variance: Optional[Decimal] = None
    standard_deviation: Optional[Decimal] = None
    try:
        with localcontext() as context:
            context.prec = 50
            total = sum(ordered, Decimal(0))
            mean = total / count
            median = _quantile(ordered, Decimal("0.5"))
            q1 = _quantile(ordered, Decimal("0.25"))
            q3 = _quantile(ordered, Decimal("0.75"))
            iqr = q3 - q1
            if count >= 2:
                variance = sum(
                    ((value - mean) ** 2 for value in ordered),
                    Decimal(0),
                ) / (count - 1)
                standard_deviation = variance.sqrt()
    except DecimalException:
        total = mean = median = q1 = q3 = iqr = None
        variance = standard_deviation = None

    return NumericColumnStatistics(
        count=count,
        mean=_as_json_number(mean) if mean is not None else None,
        median=_as_json_number(median) if median is not None else None,
        standard_deviation=(
            _as_json_number(standard_deviation)
            if standard_deviation is not None
            else None
        ),
        variance=_as_json_number(variance) if variance is not None else None,
        minimum=_as_json_number(ordered[0]),
        maximum=_as_json_number(ordered[-1]),
        q1=_as_json_number(q1) if q1 is not None else None,
        q3=_as_json_number(q3) if q3 is not None else None,
        iqr=_as_json_number(iqr) if iqr is not None else None,
        sum=_as_json_number(total) if total is not None else None,
    )


def _column_statistics(
    name: str,
    data_type: DatasetLogicalType,
    non_null_count: int,
    null_count: int,
    accumulator: _ColumnStatistics,
) -> DatasetColumnStatistics:
    result = DatasetColumnStatistics(
        name=name,
        data_type=data_type,  # validated by the Phase 3.1 metadata model
        non_null_count=non_null_count,
        null_count=null_count,
    )
    if data_type in {"integer", "float"}:
        result.numeric = _numeric_statistics(accumulator.numeric_values)
    elif data_type in {"categorical", "boolean"}:
        result.distinct_count = len(accumulator.distinct_values)
        ordered_frequencies = sorted(
            accumulator.frequencies.items(),
            key=lambda item: (-item[1], item[0]),
        )
        if ordered_frequencies:
            result.mode = ordered_frequencies[0][0]
            result.mode_frequency = ordered_frequencies[0][1]
        if data_type == "categorical":
            result.value_frequencies = [
                CategoryFrequency(value=value, count=count)
                for value, count in ordered_frequencies[:_MAX_VALUE_FREQUENCIES]
            ]
            result.value_frequencies_truncated = (
                len(ordered_frequencies) > _MAX_VALUE_FREQUENCIES
            )
    elif data_type in {"date", "datetime"}:
        result.distinct_count = len(accumulator.distinct_values)
        format_name = "iso_datetime" if data_type == "datetime" else None
        if format_name is None:
            matching_formats = [
                candidate
                for candidate, count in accumulator.date_counts.items()
                if candidate != "iso_datetime" and count == non_null_count
            ]
            if len(matching_formats) == 1:
                format_name = matching_formats[0]
        elif accumulator.date_counts.get(format_name) != non_null_count:
            format_name = None
        date_range = None
        if (
            format_name is not None
            and format_name not in accumulator.incompatible_date_ranges
        ):
            date_range = accumulator.date_ranges.get(format_name)
        if date_range is not None:
            result.earliest = date_range[0].isoformat()
            result.latest = date_range[1].isoformat()
    elif data_type == "string":
        result.distinct_count = len(accumulator.distinct_values)
    return result


def create_dataset_statistics(
    storage: DatasetStorage,
    settings: Settings,
    dataset_id: str,
) -> DatasetStatisticsResponse:
    """Calculate statistics in the same validated pass as type inference."""
    accumulators: list[_ColumnStatistics] = []

    def consume_row(row: tuple[str, ...]) -> None:
        if not accumulators:
            accumulators.extend(_ColumnStatistics() for _ in row)
        for accumulator, value in zip(accumulators, row):
            if value != "":
                accumulator.add(value)

    metadata = create_dataset_metadata(
        storage,
        settings,
        dataset_id,
        row_consumer=consume_row,
    )
    column_statistics = [
        _column_statistics(
            column.name,
            column.data_type,
            column.non_null_count,
            column.null_count,
            accumulator,
        )
        for column, accumulator in zip(metadata.columns, accumulators)
    ]
    return DatasetStatisticsResponse(
        dataset_id=metadata.dataset_id,
        filename=metadata.filename,
        row_count=metadata.row_count,
        column_count=metadata.column_count,
        column_statistics=column_statistics,
    )
