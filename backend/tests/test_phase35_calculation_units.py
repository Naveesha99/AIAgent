"""Independent unit checks for statistical and date-format calculations."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.dataset_profiler import date_formats_for_value
from app.services.statistical_profiler import (
    _numeric_statistics,
    _quantile,
)

NUMERIC_CASES = [
    (
        "empty",
        [],
        {
            "count": 0,
            "mean": None,
            "median": None,
            "standard_deviation": None,
            "variance": None,
            "minimum": None,
            "maximum": None,
            "q1": None,
            "q3": None,
            "iqr": None,
            "sum": None,
        },
    ),
    (
        "single",
        ["5"],
        {
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
    ),
    (
        "zeros",
        ["0", "0", "0", "0", "0"],
        {
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
    ),
    (
        "negative",
        ["-10", "-2.5", "0", "4", "8.75"],
        {
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
    ),
    (
        "decimal",
        ["0.5", "1.5", "2.5", "3.5", "4.5"],
        {
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
    ),
    (
        "two_values",
        ["1", "3"],
        {
            "count": 2,
            "mean": 2.0,
            "median": 2.0,
            "standard_deviation": 1.4142135623730951,
            "variance": 2.0,
            "minimum": 1.0,
            "maximum": 3.0,
            "q1": 1.5,
            "q3": 2.5,
            "iqr": 1.0,
            "sum": 4.0,
        },
    ),
    (
        "very_large",
        ["1e308", "1e308", "1e308", "1e308", "1e308"],
        {
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
    ),
]


@pytest.mark.parametrize(
    ("case_name", "values", "expected"),
    NUMERIC_CASES,
    ids=[case[0] for case in NUMERIC_CASES],
)
def test_numeric_statistics_match_independent_expected_values(
    case_name: str,
    values: list[str],
    expected: dict[str, float | int | None],
) -> None:
    result = _numeric_statistics([Decimal(value) for value in values])
    actual = result.model_dump()

    for field_name, expected_value in expected.items():
        actual_value = actual[field_name]
        if expected_value is None:
            assert actual_value is None
        elif isinstance(expected_value, int):
            assert actual_value == expected_value
        else:
            assert actual_value == pytest.approx(
                expected_value,
                rel=1e-12,
                abs=1e-12,
            )


@pytest.mark.parametrize(
    ("values", "probability", "expected"),
    [
        (["-2", "0", "4", "8"], "0.25", "-0.5"),
        (["-2", "0", "4", "8"], "0.5", "2"),
        (["-2", "0", "4", "8"], "0.75", "5"),
        (["5"], "0.25", "5"),
        (["5"], "0.5", "5"),
        (["5"], "0.75", "5"),
        (["0", "10"], "0.25", "2.5"),
        (["0", "10"], "0.5", "5"),
        (["0", "10"], "0.75", "7.5"),
    ],
)
def test_quantiles_use_linear_interpolation_at_rank(
    values: list[str],
    probability: str,
    expected: str,
) -> None:
    result = _quantile(
        sorted(Decimal(value) for value in values),
        Decimal(probability),
    )

    assert result == Decimal(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("01/02/2025", {"dmy_slash", "mdy_slash"}),
        ("13/02/2025", {"dmy_slash"}),
        ("02/13/2025", {"mdy_slash"}),
        ("01-02-2025", {"dmy_dash", "mdy_dash"}),
        ("2025-02-30", set()),
        ("not-a-date", set()),
        ("2025-01-01", {"iso_date"}),
        ("2025-01-01T10:30:00Z", {"iso_datetime"}),
    ],
)
def test_date_format_detection_preserves_ambiguity(
    value: str,
    expected: set[str],
) -> None:
    assert date_formats_for_value(value) == expected
