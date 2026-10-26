"""Tests for reusable dataset analytics operations."""

import csv
import io
import json
import os
import time
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app
from app.services.dataset_storage import DatasetStorage

client = TestClient(app)

HEADERS = [
    "amount",
    "price",
    "name",
    "region",
    "active",
    "order_date",
    "note",
]
ROWS = [
    ["10", "1.5", "Alpha", "North", "true", "2025-01-01", ""],
    ["20", "2.5", "Beta", "South", "false", "2025-01-02", "x"],
    ["30", "", "Gamma", "North", "true", "2025-01-03", ""],
    ["40", "4.5", "Delta", "", "false", "2025-01-04", "y"],
    ["50", "5.5", "Epsilon", "South", "true", "2025-01-05", ""],
    ["60", "6.5", "Zeta", "North", "false", "2025-01-06", "z"],
]


@pytest.fixture
def analytics_storage(tmp_path: Path) -> Iterator[Path]:
    storage_dir = tmp_path / "analytics-datasets"
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        dataset_storage_dir=storage_dir,
    )
    yield storage_dir
    app.dependency_overrides.pop(get_settings, None)


def make_csv(headers: list[str], rows: list[list[str]]) -> bytes:
    contents = io.StringIO(newline="")
    writer = csv.writer(contents, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return contents.getvalue().encode("utf-8")


def upload_csv(
    contents: bytes,
    filename: str = "records.csv",
) -> str:
    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": (filename, contents, "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["dataset_id"]


def upload_primary_dataset() -> str:
    return upload_csv(make_csv(HEADERS, ROWS), "analytics.csv")


def post_analytics(dataset_id: str, payload: dict[str, object]):
    return client.post(
        f"/api/v1/datasets/{dataset_id}/analytics",
        json=payload,
    )


def names(response) -> list[str]:
    result = response.json()
    return [row["name"] for row in result["rows"]]


def filter_payload(
    conditions: list[dict[str, object]],
    *,
    combine: str = "and",
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "operation": "filter",
        "filters": {"combine": combine, "conditions": conditions},
    }
    payload.update(extra)
    return payload


@pytest.mark.parametrize(
    ("condition", "expected_names"),
    [
        (
            {"column": "amount", "operator": "equals", "value": 30},
            ["Gamma"],
        ),
        (
            {
                "column": "price",
                "operator": "greater_than",
                "value": "2.5",
            },
            ["Delta", "Epsilon", "Zeta"],
        ),
        (
            {
                "column": "name",
                "operator": "contains",
                "value": "ta",
            },
            ["Beta", "Delta", "Zeta"],
        ),
        (
            {
                "column": "name",
                "operator": "starts_with",
                "value": "Ga",
            },
            ["Gamma"],
        ),
        (
            {
                "column": "name",
                "operator": "ends_with",
                "value": "ta",
            },
            ["Beta", "Delta", "Zeta"],
        ),
        (
            {
                "column": "region",
                "operator": "equals",
                "value": "North",
            },
            ["Alpha", "Gamma", "Zeta"],
        ),
        (
            {
                "column": "active",
                "operator": "equals",
                "value": True,
            },
            ["Alpha", "Gamma", "Epsilon"],
        ),
        (
            {
                "column": "order_date",
                "operator": "greater_than",
                "value": "2025-01-03",
            },
            ["Delta", "Epsilon", "Zeta"],
        ),
        (
            {"column": "note", "operator": "is_null"},
            ["Alpha", "Gamma", "Epsilon"],
        ),
        (
            {"column": "note", "operator": "is_not_null"},
            ["Beta", "Delta", "Zeta"],
        ),
        (
            {
                "column": "note",
                "operator": "not_equals",
                "value": "x",
            },
            ["Delta", "Zeta"],
        ),
        (
            {"column": "region", "operator": "is_null"},
            ["Delta"],
        ),
    ],
)
def test_filter_supports_typed_comparisons_and_missing_values(
    analytics_storage: Path,
    condition: dict[str, object],
    expected_names: list[str],
) -> None:
    dataset_id = upload_primary_dataset()

    response = post_analytics(dataset_id, filter_payload([condition]))

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["operation"] == "filter"
    assert result["row_count"] == 6
    assert result["matched_count"] == len(expected_names)
    assert result["result_count"] == len(expected_names)
    assert result["truncated"] is False
    assert names(response) == expected_names
    assert "NaN" not in response.text
    assert "Infinity" not in response.text


def test_filter_combines_conditions_with_explicit_and_or(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    or_response = post_analytics(
        dataset_id,
        filter_payload(
            [
                {
                    "column": "amount",
                    "operator": "less_than",
                    "value": "15",
                },
                {
                    "column": "region",
                    "operator": "equals",
                    "value": "North",
                },
            ],
            combine="or",
        ),
    )
    and_response = post_analytics(
        dataset_id,
        filter_payload(
            [
                {
                    "column": "amount",
                    "operator": "greater_than",
                    "value": "15",
                },
                {
                    "column": "region",
                    "operator": "equals",
                    "value": "North",
                },
            ],
            combine="and",
        ),
    )

    assert or_response.status_code == 200, or_response.text
    assert names(or_response) == ["Alpha", "Gamma", "Zeta"]
    assert and_response.status_code == 200, and_response.text
    assert names(and_response) == ["Gamma", "Zeta"]


def test_filter_projection_and_empty_results(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    response = post_analytics(
        dataset_id,
        filter_payload(
            [
                {
                    "column": "region",
                    "operator": "equals",
                    "value": "Nowhere",
                }
            ],
            columns=["name", "amount"],
        ),
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["columns"] == ["name", "amount"]
    assert result["rows"] == []
    assert result["result_count"] == 0
    assert result["matched_count"] == 0
    assert result["truncated"] is False


@pytest.mark.parametrize(
    ("condition", "detail_fragment"),
    [
        (
            {
                "column": "missing",
                "operator": "equals",
                "value": "x",
            },
            "Unknown column",
        ),
        (
            {
                "column": "amount",
                "operator": "contains",
                "value": "1",
            },
            "requires a string or categorical column",
        ),
        (
            {
                "column": "name",
                "operator": "greater_than",
                "value": "A",
            },
            "not supported",
        ),
        (
            {"column": "amount", "operator": "is_null", "value": "1"},
            "does not accept a comparison value",
        ),
        (
            {"column": "amount", "operator": "equals"},
            "requires a comparison value",
        ),
        (
            {
                "column": "amount",
                "operator": "equals",
                "value": "abc",
            },
            "numeric comparison value",
        ),
        (
            {
                "column": "active",
                "operator": "less_than",
                "value": True,
            },
            "not supported",
        ),
        (
            {
                "column": "order_date",
                "operator": "equals",
                "value": "01/02/2025",
            },
            "ambiguous",
        ),
        (
            {
                "column": "order_date",
                "operator": "equals",
                "value": "not-a-date",
            },
            "invalid",
        ),
        (
            {
                "column": "order_date",
                "operator": "equals",
                "value": "2025-01-01T00:00:00",
            },
            "invalid",
        ),
        (
            {"column": "   ", "operator": "equals", "value": "x"},
            "non-empty",
        ),
    ],
)
def test_filter_rejects_invalid_columns_operators_and_values(
    analytics_storage: Path,
    condition: dict[str, object],
    detail_fragment: str,
) -> None:
    dataset_id = upload_primary_dataset()

    response = post_analytics(dataset_id, filter_payload([condition]))

    assert response.status_code == 422
    assert detail_fragment.lower() in response.text.lower()
    assert "Traceback" not in response.text
    assert str(analytics_storage) not in response.text


def test_invalid_operation_schema_returns_422(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    response = post_analytics(
        dataset_id,
        {
            "operation": "filter",
            "filters": {
                "conditions": [
                    {
                        "column": "amount",
                        "operator": "between",
                        "value": "1",
                    }
                ]
            },
        },
    )

    assert response.status_code == 422
    assert "Traceback" not in response.text


def test_group_by_one_and_multiple_columns_preserves_missing_groups(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    single = post_analytics(
        dataset_id,
        {
            "operation": "group",
            "group_by": ["region"],
            "aggregations": [
                {"function": "sum", "column": "amount"},
                {"function": "count", "column": "price"},
            ],
        },
    )
    multiple = post_analytics(
        dataset_id,
        {
            "operation": "group",
            "group_by": ["region", "active"],
        },
    )
    numeric_order = post_analytics(
        dataset_id,
        {
            "operation": "group",
            "group_by": ["amount"],
        },
    )

    assert single.status_code == 200, single.text
    result = single.json()
    assert result["columns"] == ["region", "sum_amount", "count_price"]
    assert result["row_count"] == 6
    assert result["matched_count"] == 6
    assert result["result_count"] == 3
    assert result["total_groups"] == 3
    assert result["truncated"] is False
    assert result["rows"] == [
        {"region": None, "sum_amount": 40.0, "count_price": 1},
        {"region": "North", "sum_amount": 100.0, "count_price": 2},
        {"region": "South", "sum_amount": 70.0, "count_price": 2},
    ]

    assert multiple.status_code == 200, multiple.text
    multiple_result = multiple.json()
    assert multiple_result["columns"] == [
        "region",
        "active",
        "count_rows",
    ]
    assert multiple_result["total_groups"] == 5
    assert {
        (
            row["region"],
            row["active"],
            row["count_rows"],
        )
        for row in multiple_result["rows"]
    } == {
        (None, "false", 1),
        ("North", "true", 2),
        ("North", "false", 1),
        ("South", "false", 1),
        ("South", "true", 1),
    }

    assert numeric_order.status_code == 200, numeric_order.text
    assert [
        row["amount"] for row in numeric_order.json()["rows"]
    ] == ["10", "20", "30", "40", "50", "60"]


def test_group_limits_and_truncation_metadata(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    limited = post_analytics(
        dataset_id,
        {
            "operation": "group",
            "group_by": ["region"],
            "limit": 1,
        },
    )
    assert limited.status_code == 200, limited.text
    limited_result = limited.json()
    assert limited_result["result_count"] == 1
    assert limited_result["total_groups"] == 3
    assert limited_result["truncated"] is True

    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        dataset_storage_dir=analytics_storage,
        analytics_max_groups=2,
    )
    configured = post_analytics(
        dataset_id,
        {
            "operation": "group",
            "group_by": ["region"],
        },
    )
    assert configured.status_code == 200, configured.text
    result = configured.json()
    assert result["result_count"] == 2
    assert result["total_groups"] == 3
    assert result["truncated"] is True
    assert [row["region"] for row in result["rows"]] == [None, "North"]


def test_aggregate_without_grouping_and_count_semantics(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    response = post_analytics(
        dataset_id,
        {
            "operation": "aggregate",
            "aggregations": [
                {"function": "count"},
                {"function": "count", "column": "amount"},
                {"function": "count", "column": "price"},
                {"function": "sum", "column": "amount"},
                {"function": "mean", "column": "amount"},
                {"function": "median", "column": "amount"},
                {"function": "min", "column": "amount"},
                {"function": "max", "column": "amount"},
                {"function": "std", "column": "amount"},
                {"function": "nunique", "column": "region"},
                {"function": "min", "column": "name"},
                {"function": "max", "column": "name"},
                {"function": "count", "column": "region"},
                {"function": "nunique", "column": "name"},
            ],
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["operation"] == "aggregate"
    assert result["row_count"] == 6
    assert result["result_count"] == 1
    assert result["total_groups"] == 1
    assert result["truncated"] is False
    row = result["rows"][0]
    assert row["count_rows"] == 6
    assert row["count_amount"] == 6
    assert row["count_price"] == 5
    assert row["count_region"] == 5
    assert row["sum_amount"] == 210.0
    assert row["mean_amount"] == 35.0
    assert row["median_amount"] == 35.0
    assert row["min_amount"] == 10.0
    assert row["max_amount"] == 60.0
    assert row["std_amount"] == pytest.approx(18.708286933869708)
    assert row["nunique_region"] == 2
    assert row["nunique_name"] == 6
    assert row["min_name"] == "Alpha"
    assert row["max_name"] == "Zeta"
    json.dumps(result, allow_nan=False)


def test_aggregate_empty_filter_result_and_grouped_aggregation(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    empty = post_analytics(
        dataset_id,
        {
            "operation": "aggregate",
            "filters": {
                "conditions": [
                    {
                        "column": "region",
                        "operator": "equals",
                        "value": "Nowhere",
                    }
                ]
            },
            "aggregations": [
                {"function": "count"},
                {"function": "sum", "column": "amount"},
            ],
        },
    )
    grouped = post_analytics(
        dataset_id,
        {
            "operation": "aggregate",
            "group_by": ["region"],
            "aggregations": [
                {"function": "sum", "column": "amount"},
                {"function": "count", "column": "price"},
            ],
        },
    )

    assert empty.status_code == 200, empty.text
    assert empty.json()["rows"] == [
        {"count_rows": 0, "sum_amount": None}
    ]
    assert grouped.status_code == 200, grouped.text
    grouped_result = grouped.json()
    assert grouped_result["total_groups"] == 3
    assert grouped_result["result_count"] == 3
    assert grouped_result["rows"] == [
        {"region": None, "sum_amount": 40.0, "count_price": 1},
        {"region": "North", "sum_amount": 100.0, "count_price": 2},
        {"region": "South", "sum_amount": 70.0, "count_price": 2},
    ]


@pytest.mark.parametrize(
    ("payload", "detail_fragment"),
    [
        (
            {
                "operation": "aggregate",
                "aggregations": [
                    {"function": "sum", "column": "region"}
                ],
            },
            "numeric column",
        ),
        (
            {
                "operation": "aggregate",
                "aggregations": [
                    {"function": "mean", "column": "name"}
                ],
            },
            "numeric column",
        ),
        (
            {
                "operation": "aggregate",
                "aggregations": [
                    {"function": "min", "column": "active"}
                ],
            },
            "not supported for boolean",
        ),
        (
            {
                "operation": "aggregate",
                "aggregations": [{"function": "nunique"}],
            },
            "requires a column",
        ),
        (
            {
                "operation": "aggregate",
                "aggregations": [
                    {
                        "function": "count",
                        "column": "missing",
                    }
                ],
            },
            "Unknown column",
        ),
        (
            {
                "operation": "group",
                "group_by": ["missing"],
            },
            "Unknown column",
        ),
        (
            {
                "operation": "aggregate",
                "aggregations": [
                    {
                        "function": "count",
                        "column": "amount",
                        "alias": "total",
                    },
                    {
                        "function": "sum",
                        "column": "amount",
                        "alias": "total",
                    },
                ],
            },
            "Duplicate aggregation alias",
        ),
        (
            {
                "operation": "group",
                "group_by": ["region"],
                "aggregations": [
                    {
                        "function": "count",
                        "column": "amount",
                        "alias": "region",
                    }
                ],
            },
            "Duplicate aggregation alias",
        ),
    ],
)
def test_aggregation_rejects_invalid_functions_and_columns(
    analytics_storage: Path,
    payload: dict[str, object],
    detail_fragment: str,
) -> None:
    dataset_id = upload_primary_dataset()

    response = post_analytics(dataset_id, payload)

    assert response.status_code == 422
    assert detail_fragment.lower() in response.text.lower()
    assert "Traceback" not in response.text


def test_sorting_multiple_columns_directions_and_missing_order(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    region_amount = post_analytics(
        dataset_id,
        {
            "operation": "sort",
            "sort_by": [
                {"column": "region", "direction": "asc"},
                {"column": "amount", "direction": "desc"},
            ],
        },
    )
    note_asc = post_analytics(
        dataset_id,
        {
            "operation": "sort",
            "sort_by": [{"column": "note", "direction": "asc"}],
        },
    )
    date_desc = post_analytics(
        dataset_id,
        {
            "operation": "sort",
            "sort_by": [
                {"column": "order_date", "direction": "desc"}
            ],
        },
    )

    assert region_amount.status_code == 200, region_amount.text
    assert names(region_amount) == [
        "Zeta",
        "Gamma",
        "Alpha",
        "Epsilon",
        "Beta",
        "Delta",
    ]
    assert note_asc.status_code == 200, note_asc.text
    assert names(note_asc) == [
        "Beta",
        "Delta",
        "Zeta",
        "Alpha",
        "Gamma",
        "Epsilon",
    ]
    assert date_desc.status_code == 200, date_desc.text
    assert names(date_desc) == [
        "Zeta",
        "Epsilon",
        "Delta",
        "Gamma",
        "Beta",
        "Alpha",
    ]


def test_sorting_is_stable_and_applies_projection_and_limits(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    stable = post_analytics(
        dataset_id,
        {
            "operation": "sort",
            "sort_by": [{"column": "active", "direction": "asc"}],
        },
    )
    projected = post_analytics(
        dataset_id,
        {
            "operation": "sort",
            "sort_by": [{"column": "amount", "direction": "desc"}],
            "columns": ["name", "amount"],
            "limit": 2,
        },
    )

    assert stable.status_code == 200, stable.text
    assert names(stable) == [
        "Beta",
        "Delta",
        "Zeta",
        "Alpha",
        "Gamma",
        "Epsilon",
    ]
    assert projected.status_code == 200, projected.text
    result = projected.json()
    assert result["columns"] == ["name", "amount"]
    assert result["rows"] == [
        {"name": "Zeta", "amount": "60"},
        {"name": "Epsilon", "amount": "50"},
    ]
    assert result["matched_count"] == 6
    assert result["result_count"] == 2
    assert result["truncated"] is True


def test_datetime_comparisons_handle_timezone_aware_and_naive_values(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_csv(
        make_csv(
            ["label", "when"],
            [
                ["first", "2025-01-01T00:00:00Z"],
                ["second", "2025-01-02T00:00:00+05:00"],
                ["third", "2025-01-03T00:00:00"],
            ],
        ),
        "datetimes.csv",
    )

    sorted_result = post_analytics(
        dataset_id,
        {
            "operation": "sort",
            "sort_by": [{"column": "when", "direction": "asc"}],
        },
    )
    filtered = post_analytics(
        dataset_id,
        filter_payload(
            [
                {
                    "column": "when",
                    "operator": "greater_than",
                    "value": "2025-01-02T00:00:00Z",
                }
            ]
        ),
    )
    aggregated = post_analytics(
        dataset_id,
        {
            "operation": "aggregate",
            "aggregations": [
                {"function": "min", "column": "when"},
                {"function": "max", "column": "when"},
            ],
        },
    )

    assert sorted_result.status_code == 200, sorted_result.text
    assert [
        row["label"] for row in sorted_result.json()["rows"]
    ] == ["first", "second", "third"]
    assert filtered.status_code == 200, filtered.text
    assert [row["label"] for row in filtered.json()["rows"]] == ["third"]
    assert aggregated.status_code == 200, aggregated.text
    assert aggregated.json()["rows"] == [
        {
            "min_when": "2025-01-01T00:00:00+00:00",
            "max_when": "2025-01-03T00:00:00",
        }
    ]


def test_summarize_reuses_statistics_and_serializes_safely(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    response = post_analytics(dataset_id, {"operation": "summarize"})
    statistics = client.get(
        f"/api/v1/datasets/{dataset_id}/statistics"
    )

    assert response.status_code == 200, response.text
    assert statistics.status_code == 200, statistics.text
    result = response.json()
    summary = result["summary"]
    assert result["operation"] == "summarize"
    assert result["row_count"] == 6
    assert result["result_count"] == 0
    assert result["rows"] == []
    assert summary["row_count"] == 6
    assert summary["column_count"] == 7
    assert summary["columns"] == [
        {"name": "amount", "data_type": "integer"},
        {"name": "price", "data_type": "float"},
        {"name": "name", "data_type": "string"},
        {"name": "region", "data_type": "categorical"},
        {"name": "active", "data_type": "boolean"},
        {"name": "order_date", "data_type": "date"},
        {"name": "note", "data_type": "string"},
    ]

    numeric = {item["column"]: item for item in summary["numeric"]}
    assert numeric["amount"] == {
        "column": "amount",
        "count": 6,
        "mean": 35.0,
        "median": 35.0,
        "minimum": 10.0,
        "maximum": 60.0,
        "standard_deviation": pytest.approx(18.708286933869708),
    }
    assert numeric["price"]["count"] == 5
    assert numeric["price"]["mean"] == 4.1
    assert numeric["price"]["median"] == 4.5
    assert numeric["price"]["minimum"] == 1.5
    assert numeric["price"]["maximum"] == 6.5

    statistics_amount = next(
        column
        for column in statistics.json()["column_statistics"]
        if column["name"] == "amount"
    )
    assert numeric["amount"]["mean"] == statistics_amount["numeric"]["mean"]
    assert (
        numeric["amount"]["standard_deviation"]
        == statistics_amount["numeric"]["standard_deviation"]
    )

    categorical = {
        item["column"]: item for item in summary["categorical"]
    }
    assert categorical["region"]["distinct_count"] == 2
    assert categorical["region"]["top_values"] == [
        {"value": "North", "count": 3},
        {"value": "South", "count": 2},
    ]
    assert categorical["region"]["truncated"] is False

    missing = {item["column"]: item for item in summary["missing"]}
    assert missing["amount"]["missing_count"] == 0
    assert missing["price"]["missing_count"] == 1
    assert missing["region"]["missing_count"] == 1
    assert missing["note"]["missing_count"] == 3
    assert missing["note"]["missing_percentage"] == 50.0
    json.dumps(result, allow_nan=False)


def test_summarize_top_value_limits_filtering_and_grouping(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    limited = post_analytics(
        dataset_id,
        {"operation": "summarize", "top_values_limit": 1},
    )
    filtered = post_analytics(
        dataset_id,
        {
            "operation": "summarize",
            "filters": {
                "conditions": [
                    {
                        "column": "region",
                        "operator": "equals",
                        "value": "North",
                    }
                ]
            },
        },
    )
    grouped = post_analytics(
        dataset_id,
        {
            "operation": "summarize",
            "group_by": ["region"],
            "aggregations": [
                {"function": "sum", "column": "amount"},
                {"function": "count", "column": "price"},
            ],
        },
    )

    assert limited.status_code == 200, limited.text
    assert limited.json()["truncated"] is True
    region = next(
        item
        for item in limited.json()["summary"]["categorical"]
        if item["column"] == "region"
    )
    assert region["top_values"] == [{"value": "North", "count": 3}]
    assert region["truncated"] is True

    assert filtered.status_code == 200, filtered.text
    filtered_result = filtered.json()
    filtered_summary = filtered_result["summary"]
    assert filtered_result["row_count"] == 6
    assert filtered_result["matched_count"] == 3
    assert filtered_summary["row_count"] == 3
    amount = next(
        item
        for item in filtered_summary["numeric"]
        if item["column"] == "amount"
    )
    assert amount["mean"] == pytest.approx(33.333333333333336)
    assert amount["median"] == 30.0
    assert amount["minimum"] == 10.0
    assert amount["maximum"] == 60.0
    price = next(
        item
        for item in filtered_summary["numeric"]
        if item["column"] == "price"
    )
    assert price["count"] == 2
    assert price["mean"] == 4.0
    region_summary = next(
        item
        for item in filtered_summary["categorical"]
        if item["column"] == "region"
    )
    assert region_summary["distinct_count"] == 1
    assert region_summary["top_values"] == [
        {"value": "North", "count": 3}
    ]
    missing = {
        item["column"]: item
        for item in filtered_summary["missing"]
    }
    assert missing["price"]["missing_count"] == 1
    assert missing["note"]["missing_count"] == 2
    assert missing["note"]["missing_percentage"] == pytest.approx(
        66.66666666666666
    )

    assert grouped.status_code == 200, grouped.text
    grouped_summary = grouped.json()["summary"]
    assert grouped.json()["result_count"] == 3
    assert grouped.json()["total_groups"] == 3
    assert grouped_summary["group_by"] == ["region"]
    assert grouped_summary["total_groups"] == 3
    assert grouped_summary["groups_truncated"] is False
    assert grouped_summary["groups"] == [
        {"region": None, "sum_amount": 40.0, "count_price": 1},
        {"region": "North", "sum_amount": 100.0, "count_price": 2},
        {"region": "South", "sum_amount": 70.0, "count_price": 2},
    ]


def test_configured_result_limits_are_enforced(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        dataset_storage_dir=analytics_storage,
        analytics_max_result_rows=2,
        analytics_max_groups=1,
        analytics_max_value_frequencies=1,
    )

    filtered = post_analytics(
        dataset_id,
        filter_payload(
            [
                {
                    "column": "amount",
                    "operator": "greater_than",
                    "value": "0",
                }
            ],
            limit=100,
        ),
    )
    grouped = post_analytics(
        dataset_id,
        {
            "operation": "group",
            "group_by": ["region"],
        },
    )
    summarized = post_analytics(
        dataset_id,
        {"operation": "summarize", "top_values_limit": 10},
    )

    assert filtered.status_code == 200, filtered.text
    filtered_result = filtered.json()
    assert filtered_result["result_count"] == 2
    assert filtered_result["matched_count"] == 6
    assert filtered_result["truncated"] is True

    assert grouped.status_code == 200, grouped.text
    grouped_result = grouped.json()
    assert grouped_result["result_count"] == 1
    assert grouped_result["total_groups"] == 3
    assert grouped_result["truncated"] is True

    assert summarized.status_code == 200, summarized.text
    assert summarized.json()["truncated"] is True
    region = next(
        item
        for item in summarized.json()["summary"]["categorical"]
        if item["column"] == "region"
    )
    assert region["top_values"] == [{"value": "North", "count": 3}]
    assert region["truncated"] is True


def test_analytics_never_modifies_original_dataset(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()
    dataset_path = DatasetStorage(analytics_storage).resolve(dataset_id)
    before = dataset_path.read_bytes()

    payloads = [
        filter_payload(
            [
                {
                    "column": "amount",
                    "operator": "greater_than",
                    "value": "15",
                }
            ]
        ),
        {
            "operation": "sort",
            "sort_by": [{"column": "amount", "direction": "desc"}],
        },
        {
            "operation": "group",
            "group_by": ["region"],
            "aggregations": [{"function": "sum", "column": "amount"}],
        },
        {
            "operation": "aggregate",
            "aggregations": [
                {"function": "mean", "column": "price"}
            ],
        },
        {"operation": "summarize"},
    ]
    for payload in payloads:
        response = post_analytics(dataset_id, payload)
        assert response.status_code == 200, response.text

    assert dataset_path.read_bytes() == before


@pytest.mark.parametrize(
    ("dataset_id", "status_code"),
    [
        ("not-a-uuid", 422),
        ("../etc/passwd", 404),
        ("00000000-0000-4000-8000-000000000000", 404),
    ],
)
def test_analytics_handles_invalid_and_missing_dataset_ids(
    analytics_storage: Path,
    dataset_id: str,
    status_code: int,
) -> None:
    response = post_analytics(dataset_id, {"operation": "summarize"})

    assert response.status_code == status_code
    assert "Traceback" not in response.text
    assert str(analytics_storage) not in response.text


def test_analytics_handles_expired_and_corrupted_datasets(
    analytics_storage: Path,
) -> None:
    expired_id = upload_primary_dataset()
    expired_path = DatasetStorage(analytics_storage).resolve(expired_id)
    old_time = time.time() - 90_000
    os.utime(expired_path, (old_time, old_time))
    expired = post_analytics(expired_id, {"operation": "summarize"})
    assert expired.status_code == 404

    corrupted_id = upload_primary_dataset()
    corrupted_path = DatasetStorage(analytics_storage).resolve(corrupted_id)
    corrupted_path.write_bytes(b'amount\n"unterminated')
    corrupted = post_analytics(
        corrupted_id,
        filter_payload(
            [
                {
                    "column": "amount",
                    "operator": "is_not_null",
                }
            ]
        ),
    )
    assert corrupted.status_code == 500
    assert corrupted.json()["detail"] == (
        "The stored dataset could not be read safely."
    )
    assert "Traceback" not in corrupted.text
    assert str(analytics_storage) not in corrupted.text


def test_existing_dataset_endpoints_still_work(
    analytics_storage: Path,
) -> None:
    dataset_id = upload_primary_dataset()

    responses = [
        client.get("/api/v1/health"),
        client.get(
            f"/api/v1/datasets/{dataset_id}/preview?page=1&page_size=2"
        ),
        client.get(f"/api/v1/datasets/{dataset_id}/metadata"),
        client.get(f"/api/v1/datasets/{dataset_id}/statistics"),
        client.get(f"/api/v1/datasets/{dataset_id}/quality"),
    ]

    for response in responses:
        assert response.status_code == 200, response.text
    preview = responses[1].json()
    assert preview["row_count"] == 6
    assert preview["page"] == 1
    assert preview["total_pages"] == 3
