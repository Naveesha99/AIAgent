"""Reusable CSV fixtures with independently calculated expected results."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class KnownDataset:
    filename: str
    row_count: int
    column_count: int
    data_types: dict[str, str]
    non_null_counts: dict[str, int]
    null_counts: dict[str, int]
    numeric_columns: tuple[str, ...] = ()
    date_columns: tuple[str, ...] = ()
    categorical_columns: tuple[str, ...] = ()
    quality_missing_counts: dict[str, int] = field(default_factory=dict)
    quality_summary: dict[str, int] = field(default_factory=dict)
    numeric_statistics: dict[str, dict[str, float | int | None]] = field(
        default_factory=dict
    )
    date_ranges: dict[str, tuple[str, str]] = field(default_factory=dict)
    distinct_counts: dict[str, int] = field(default_factory=dict)
    modes: dict[str, tuple[str, int]] = field(default_factory=dict)
    value_frequencies: dict[str, list[tuple[str, int]]] = field(
        default_factory=dict
    )
    invalid_date_counts: dict[str, int] = field(default_factory=dict)
    type_inconsistency_counts: dict[str, int] = field(
        default_factory=dict
    )


def _summary(
    total_missing_cells: int,
    rows_with_missing_values: int,
    duplicate_rows: int = 0,
    duplicate_groups: int = 0,
    invalid_date_values: int = 0,
    type_inconsistencies: int = 0,
) -> dict[str, int]:
    return {
        "total_missing_cells": total_missing_cells,
        "rows_with_missing_values": rows_with_missing_values,
        "duplicate_rows": duplicate_rows,
        "duplicate_groups": duplicate_groups,
        "invalid_date_values": invalid_date_values,
        "type_inconsistencies": type_inconsistencies,
    }


NORMAL = KnownDataset(
    filename="normal.csv",
    row_count=6,
    column_count=7,
    data_types={
        "order_id": "string",
        "quantity": "integer",
        "unit_price": "float",
        "active": "boolean",
        "order_date": "date",
        "region": "categorical",
        "note": "string",
    },
    non_null_counts={
        "order_id": 6,
        "quantity": 6,
        "unit_price": 6,
        "active": 6,
        "order_date": 6,
        "region": 6,
        "note": 6,
    },
    null_counts={
        "order_id": 0,
        "quantity": 0,
        "unit_price": 0,
        "active": 0,
        "order_date": 0,
        "region": 0,
        "note": 0,
    },
    numeric_columns=("quantity", "unit_price"),
    date_columns=("order_date",),
    categorical_columns=("region",),
    quality_missing_counts={
        "order_id": 0,
        "quantity": 0,
        "unit_price": 0,
        "active": 0,
        "order_date": 0,
        "region": 0,
        "note": 0,
    },
    quality_summary=_summary(0, 0),
    numeric_statistics={
        "quantity": {
            "count": 6,
            "mean": 35.0,
            "median": 35.0,
            "standard_deviation": 18.708286933869708,
            "variance": 350.0,
            "minimum": 10.0,
            "maximum": 60.0,
            "q1": 22.5,
            "q3": 47.5,
            "iqr": 25.0,
            "sum": 210.0,
        },
        "unit_price": {
            "count": 6,
            "mean": 7.25,
            "median": 7.25,
            "standard_deviation": 3.6708309685955305,
            "variance": 13.475,
            "minimum": 2.5,
            "maximum": 12.0,
            "q1": 4.625,
            "q3": 9.875,
            "iqr": 5.25,
            "sum": 43.5,
        },
    },
    date_ranges={"order_date": ("2025-01-01", "2025-01-06")},
    distinct_counts={
        "order_id": 6,
        "active": 2,
        "order_date": 6,
        "region": 3,
        "note": 6,
    },
    modes={
        "active": ("false", 3),
        "region": ("North", 3),
    },
    value_frequencies={
        "region": [("North", 3), ("South", 2), ("East", 1)],
    },
    invalid_date_counts={},
    type_inconsistency_counts={},
)

MISSING_VALUES = KnownDataset(
    filename="missing_values.csv",
    row_count=6,
    column_count=6,
    data_types={
        "amount": "integer",
        "price": "float",
        "order_date": "date",
        "region": "categorical",
        "flag": "boolean",
        "note": "string",
    },
    non_null_counts={
        "amount": 6,
        "price": 5,
        "order_date": 5,
        "region": 5,
        "flag": 6,
        "note": 4,
    },
    null_counts={
        "amount": 0,
        "price": 1,
        "order_date": 1,
        "region": 1,
        "flag": 0,
        "note": 2,
    },
    numeric_columns=("amount", "price"),
    date_columns=("order_date",),
    categorical_columns=("region",),
    quality_missing_counts={
        "amount": 0,
        "price": 1,
        "order_date": 1,
        "region": 1,
        "flag": 0,
        "note": 3,
    },
    quality_summary=_summary(6, 4),
    numeric_statistics={
        "amount": {
            "count": 6,
            "mean": 2.5,
            "median": 2.5,
            "standard_deviation": 1.8708286933869707,
            "variance": 3.5,
            "minimum": 0.0,
            "maximum": 5.0,
            "q1": 1.25,
            "q3": 3.75,
            "iqr": 2.5,
            "sum": 15.0,
        },
        "price": {
            "count": 5,
            "mean": 4.3,
            "median": 4.5,
            "standard_deviation": 1.9235384061671346,
            "variance": 3.7,
            "minimum": 1.5,
            "maximum": 6.5,
            "q1": 3.5,
            "q3": 5.5,
            "iqr": 2.0,
            "sum": 21.5,
        },
    },
    date_ranges={"order_date": ("2025-01-01", "2025-01-06")},
    distinct_counts={
        "order_date": 5,
        "region": 2,
        "flag": 2,
        "note": 4,
    },
    modes={
        "flag": ("false", 3),
        "region": ("North", 3),
    },
    value_frequencies={
        "region": [("North", 3), ("South", 2)],
    },
    invalid_date_counts={
        "amount": 0,
        "price": 0,
        "order_date": 0,
        "region": 0,
        "flag": 0,
        "note": 0,
    },
    type_inconsistency_counts={
        "amount": 0,
        "price": 0,
        "order_date": 0,
        "region": 0,
        "flag": 0,
        "note": 0,
    },
)

ALL_MISSING = KnownDataset(
    filename="all_missing.csv",
    row_count=3,
    column_count=4,
    data_types={
        "id": "string",
        "empty_text": "string",
        "empty_number": "string",
        "empty_date": "string",
    },
    non_null_counts={
        "id": 3,
        "empty_text": 0,
        "empty_number": 0,
        "empty_date": 0,
    },
    null_counts={
        "id": 0,
        "empty_text": 3,
        "empty_number": 3,
        "empty_date": 3,
    },
    quality_missing_counts={
        "id": 0,
        "empty_text": 3,
        "empty_number": 3,
        "empty_date": 3,
    },
    quality_summary=_summary(9, 3),
    distinct_counts={
        "id": 3,
        "empty_text": 0,
        "empty_number": 0,
        "empty_date": 0,
    },
    invalid_date_counts={
        "id": 0,
        "empty_text": 0,
        "empty_number": 0,
        "empty_date": 0,
    },
    type_inconsistency_counts={
        "id": 0,
        "empty_text": 0,
        "empty_number": 0,
        "empty_date": 0,
    },
)

DUPLICATES = KnownDataset(
    filename="duplicates.csv",
    row_count=6,
    column_count=3,
    data_types={
        "name": "categorical",
        "value": "integer",
        "note": "categorical",
    },
    non_null_counts={"name": 6, "value": 6, "note": 6},
    null_counts={"name": 0, "value": 0, "note": 0},
    numeric_columns=("value",),
    categorical_columns=("name", "note"),
    quality_missing_counts={"name": 0, "value": 0, "note": 0},
    quality_summary=_summary(0, 0, duplicate_rows=2, duplicate_groups=2),
    numeric_statistics={
        "value": {
            "count": 6,
            "mean": 1.5,
            "median": 1.5,
            "standard_deviation": 0.5477225575051661,
            "variance": 0.3,
            "minimum": 1.0,
            "maximum": 2.0,
            "q1": 1.0,
            "q3": 2.0,
            "iqr": 1.0,
            "sum": 9.0,
        }
    },
    distinct_counts={"name": 2, "note": 3},
    modes={
        "name": ("alpha", 4),
        "note": ("first", 4),
    },
    value_frequencies={
        "name": [("alpha", 4), ("beta", 2)],
        "note": [("first", 4), ("other", 1), ("second", 1)],
    },
    invalid_date_counts={"name": 0, "value": 0, "note": 0},
    type_inconsistency_counts={"name": 0, "value": 0, "note": 0},
)

INCONSISTENT_TYPES = KnownDataset(
    filename="inconsistent_types.csv",
    row_count=8,
    column_count=6,
    data_types={
        "amount": "string",
        "order_date": "string",
        "consent": "categorical",
        "customer_id": "string",
        "postal_code": "string",
        "ambiguous_date": "string",
    },
    non_null_counts={
        "amount": 8,
        "order_date": 8,
        "consent": 8,
        "customer_id": 8,
        "postal_code": 8,
        "ambiguous_date": 8,
    },
    null_counts={
        "amount": 0,
        "order_date": 0,
        "consent": 0,
        "customer_id": 0,
        "postal_code": 0,
        "ambiguous_date": 0,
    },
    categorical_columns=("consent",),
    quality_missing_counts={
        "amount": 0,
        "order_date": 0,
        "consent": 0,
        "customer_id": 0,
        "postal_code": 0,
        "ambiguous_date": 0,
    },
    quality_summary=_summary(
        0,
        0,
        invalid_date_values=1,
        type_inconsistencies=2,
    ),
    distinct_counts={
        "amount": 8,
        "order_date": 8,
        "consent": 3,
        "customer_id": 8,
        "postal_code": 8,
        "ambiguous_date": 8,
    },
    modes={"consent": ("true", 4)},
    value_frequencies={
        "consent": [("true", 4), ("false", 3), ("yes", 1)]
    },
    invalid_date_counts={
        "amount": 0,
        "order_date": 1,
        "consent": 0,
        "customer_id": 0,
        "postal_code": 0,
        "ambiguous_date": 0,
    },
    type_inconsistency_counts={
        "amount": 1,
        "order_date": 1,
        "consent": 1,
        "customer_id": 0,
        "postal_code": 0,
        "ambiguous_date": 0,
    },
)

NUMERIC_EDGE_CASES = KnownDataset(
    filename="numeric_edge_cases.csv",
    row_count=5,
    column_count=5,
    data_types={
        "zeros": "integer",
        "only_one": "integer",
        "negative": "float",
        "decimal": "float",
        "large": "float",
    },
    non_null_counts={
        "zeros": 5,
        "only_one": 1,
        "negative": 5,
        "decimal": 5,
        "large": 5,
    },
    null_counts={
        "zeros": 0,
        "only_one": 4,
        "negative": 0,
        "decimal": 0,
        "large": 0,
    },
    numeric_columns=(
        "zeros",
        "only_one",
        "negative",
        "decimal",
        "large",
    ),
    quality_missing_counts={
        "zeros": 0,
        "only_one": 4,
        "negative": 0,
        "decimal": 0,
        "large": 0,
    },
    quality_summary=_summary(4, 4),
    numeric_statistics={
        "zeros": {
            "count": 5,
            "mean": 0.0,
            "median": 0.0,
            "standard_deviation": 0.0,
            "variance": 0.0,
            "minimum": 0.0,
            "maximum": 0.0,
            "q1": 0.0,
            "q3": 0.0,
            "iqr": 0.0,
            "sum": 0.0,
        },
        "only_one": {
            "count": 1,
            "mean": 5.0,
            "median": 5.0,
            "standard_deviation": None,
            "variance": None,
            "minimum": 5.0,
            "maximum": 5.0,
            "q1": 5.0,
            "q3": 5.0,
            "iqr": 0.0,
            "sum": 5.0,
        },
        "negative": {
            "count": 5,
            "mean": 0.05,
            "median": 0.0,
            "standard_deviation": 7.049822692805827,
            "variance": 49.7,
            "minimum": -10.0,
            "maximum": 8.75,
            "q1": -2.5,
            "q3": 4.0,
            "iqr": 6.5,
            "sum": 0.25,
        },
        "decimal": {
            "count": 5,
            "mean": 2.5,
            "median": 2.5,
            "standard_deviation": 1.5811388300841898,
            "variance": 2.5,
            "minimum": 0.5,
            "maximum": 4.5,
            "q1": 1.5,
            "q3": 3.5,
            "iqr": 2.0,
            "sum": 12.5,
        },
        "large": {
            "count": 5,
            "mean": 1e308,
            "median": 1e308,
            "standard_deviation": 0.0,
            "variance": 0.0,
            "minimum": 1e308,
            "maximum": 1e308,
            "q1": 1e308,
            "q3": 1e308,
            "iqr": 0.0,
            "sum": None,
        },
    },
    invalid_date_counts={
        "zeros": 0,
        "only_one": 0,
        "negative": 0,
        "decimal": 0,
        "large": 0,
    },
    type_inconsistency_counts={
        "zeros": 0,
        "only_one": 0,
        "negative": 0,
        "decimal": 0,
        "large": 0,
    },
)

EDGE_CASES = KnownDataset(
    filename="edge_cases.csv",
    row_count=3,
    column_count=3,
    data_types={
        "id": "string",
        "name": "string",
        "description": "string",
    },
    non_null_counts={"id": 3, "name": 3, "description": 2},
    null_counts={"id": 0, "name": 0, "description": 1},
    quality_missing_counts={"id": 0, "name": 0, "description": 1},
    quality_summary=_summary(1, 1),
    distinct_counts={"id": 3, "name": 3, "description": 2},
    invalid_date_counts={"id": 0, "name": 0, "description": 0},
    type_inconsistency_counts={
        "id": 0,
        "name": 0,
        "description": 0,
    },
)

KNOWN_DATASETS = (
    NORMAL,
    MISSING_VALUES,
    ALL_MISSING,
    DUPLICATES,
    INCONSISTENT_TYPES,
    NUMERIC_EDGE_CASES,
    EDGE_CASES,
)

DATASETS_BY_NAME = {
    dataset.filename: dataset for dataset in KNOWN_DATASETS
}

INVALID_DATASETS = {
    "empty.csv": "The CSV file is empty.",
    "header_only.csv": "at least one non-empty data row",
    "empty_header.csv": "header names must be non-empty",
    "duplicate_headers.csv": "duplicate column names",
    "inconsistent_widths.csv": "different number of fields",
    "malformed_quotes.csv": "syntax is malformed",
}


def fixture_path(filename: str) -> Path:
    path = FIXTURES_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Unknown CSV fixture: {filename}")
    return path


def read_fixture(filename: str) -> bytes:
    return fixture_path(filename).read_bytes()
