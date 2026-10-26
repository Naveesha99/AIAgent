"""Direct service-level verification for reusable analytics operations."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.core.config import Settings
from app.schemas.analytics import AnalyticsRequest
from app.services.analytics_service import (
    AnalyticsValidationError,
    analyze_dataset,
)
from app.services.dataset_storage import DatasetStorage
from fixtures.known_datasets import (
    ALL_MISSING,
    MISSING_VALUES,
    NORMAL,
    read_fixture,
)

ANALYTICS_ADAPTER = TypeAdapter(AnalyticsRequest)


@dataclass(frozen=True)
class ServiceContext:
    settings: Settings
    storage: DatasetStorage
    dataset_id: str
    path: Path


@pytest.fixture
def service_context(tmp_path: Path):
    counter = 0

    def create(known, **settings_values) -> ServiceContext:
        nonlocal counter
        counter += 1
        settings = Settings(
            _env_file=None,
            dataset_storage_dir=tmp_path / f"service-store-{counter}",
            **settings_values,
        )
        storage = DatasetStorage(
            settings.dataset_storage_dir,
            ttl_hours=settings.dataset_ttl_hours,
        )
        staged = storage.stage_upload(
            BytesIO(read_fixture(known.filename)),
            settings.max_upload_size_bytes,
        )
        stored = storage.finalize(staged, known.filename)
        return ServiceContext(
            settings=settings,
            storage=storage,
            dataset_id=str(stored.dataset_id),
            path=storage.resolve(stored.dataset_id),
        )

    return create


def run(context: ServiceContext, payload: dict[str, object]):
    request = ANALYTICS_ADAPTER.validate_python(payload)
    return analyze_dataset(
        context.storage,
        context.settings,
        context.dataset_id,
        request,
    ).model_dump(mode="json")


def selected_amounts(result: dict[str, object]) -> list[str]:
    return [row["amount"] for row in result["rows"]]  # type: ignore[index]


def test_service_filter_null_whitespace_and_date_semantics(
    service_context,
) -> None:
    context = service_context(MISSING_VALUES)

    is_null_price = run(
        context,
        {
            "operation": "filter",
            "filters": {
                "conditions": [
                    {
                        "column": "price",
                        "operator": "is_null",
                    }
                ]
            },
            "columns": ["amount"],
        },
    )
    is_not_null_price = run(
        context,
        {
            "operation": "filter",
            "filters": {
                "conditions": [
                    {
                        "column": "price",
                        "operator": "is_not_null",
                    }
                ]
            },
            "columns": ["amount"],
        },
    )
    not_north = run(
        context,
        {
            "operation": "filter",
            "filters": {
                "conditions": [
                    {
                        "column": "region",
                        "operator": "not_equals",
                        "value": "North",
                    }
                ]
            },
            "columns": ["amount"],
        },
    )
    whitespace_note = run(
        context,
        {
            "operation": "filter",
            "filters": {
                "conditions": [
                    {
                        "column": "note",
                        "operator": "equals",
                        "value": "   ",
                    }
                ]
            },
            "columns": ["amount"],
        },
    )
    null_note = run(
        context,
        {
            "operation": "filter",
            "filters": {
                "conditions": [
                    {
                        "column": "note",
                        "operator": "is_null",
                    }
                ]
            },
            "columns": ["amount"],
        },
    )
    late_dates = run(
        context,
        {
            "operation": "filter",
            "filters": {
                "conditions": [
                    {
                        "column": "order_date",
                        "operator": "greater_than",
                        "value": "2025-01-04",
                    }
                ]
            },
            "columns": ["amount"],
        },
    )

    assert is_null_price["rows"] == [{"amount": "1"}]
    assert is_null_price["matched_count"] == 1
    assert selected_amounts(is_not_null_price) == ["0", "2", "3", "4", "5"]
    assert selected_amounts(not_north) == ["1", "4"]
    assert selected_amounts(whitespace_note) == ["2"]
    assert selected_amounts(null_note) == ["1", "4"]
    assert selected_amounts(late_dates) == ["4", "5"]


@pytest.mark.parametrize(
    ("column", "operator", "value", "expected_amounts"),
    [
        ("amount", "equals", 0, ["0"]),
        ("price", "greater_than_or_equal", "4.5", ["3", "4", "5"]),
        ("price", "less_than_or_equal", "3.5", ["0", "2"]),
        ("price", "less_than", "3.5", ["0"]),
        ("region", "contains", "or", ["0", "2", "5"]),
        ("region", "starts_with", "N", ["0", "2", "5"]),
        ("region", "ends_with", "th", ["0", "1", "2", "4", "5"]),
    ],
)
def test_service_filter_covers_each_supported_operator(
    service_context,
    column: str,
    operator: str,
    value: object,
    expected_amounts: list[str],
) -> None:
    context = service_context(MISSING_VALUES)

    result = run(
        context,
        {
            "operation": "filter",
            "filters": {
                "conditions": [
                    {
                        "column": column,
                        "operator": operator,
                        "value": value,
                    }
                ]
            },
            "columns": ["amount"],
        },
    )

    assert selected_amounts(result) == expected_amounts


def test_service_aggregate_known_values(service_context) -> None:
    context = service_context(MISSING_VALUES)

    result = run(
        context,
        {
            "operation": "aggregate",
            "aggregations": [
                {"function": "count"},
                {"function": "count", "column": "price"},
                {"function": "sum", "column": "amount"},
                {"function": "mean", "column": "amount"},
                {"function": "median", "column": "amount"},
                {"function": "min", "column": "amount"},
                {"function": "max", "column": "amount"},
                {"function": "std", "column": "amount"},
                {"function": "nunique", "column": "region"},
                {"function": "min", "column": "order_date"},
                {"function": "max", "column": "order_date"},
            ],
        },
    )

    assert result["rows"] == [
        {
            "count_rows": 6,
            "count_price": 5,
            "sum_amount": 15.0,
            "mean_amount": 2.5,
            "median_amount": 2.5,
            "min_amount": 0.0,
            "max_amount": 5.0,
            "std_amount": pytest.approx(1.8708286933869707),
            "nunique_region": 2,
            "min_order_date": "2025-01-01",
            "max_order_date": "2025-01-06",
        }
    ]
    assert result["result_count"] == 1
    assert result["total_groups"] == 1


def test_service_grouping_preserves_missing_groups_and_order(
    service_context,
) -> None:
    context = service_context(MISSING_VALUES)

    single = run(
        context,
        {
            "operation": "group",
            "group_by": ["region"],
            "aggregations": [
                {"function": "count"},
                {"function": "sum", "column": "amount"},
                {"function": "count", "column": "price"},
            ],
        },
    )
    multiple = run(
        context,
        {
            "operation": "group",
            "group_by": ["region", "flag"],
            "aggregations": [{"function": "count"}],
        },
    )

    assert single["columns"] == [
        "region",
        "count_rows",
        "sum_amount",
        "count_price",
    ]
    assert single["rows"] == [
        {
            "region": None,
            "count_rows": 1,
            "sum_amount": 3.0,
            "count_price": 1,
        },
        {
            "region": "North",
            "count_rows": 3,
            "sum_amount": 7.0,
            "count_price": 3,
        },
        {
            "region": "South",
            "count_rows": 2,
            "sum_amount": 5.0,
            "count_price": 1,
        },
    ]
    assert single["total_groups"] == 3
    assert multiple["rows"] == [
        {"region": None, "flag": "true", "count_rows": 1},
        {"region": "North", "flag": "false", "count_rows": 2},
        {"region": "North", "flag": "true", "count_rows": 1},
        {"region": "South", "flag": "false", "count_rows": 1},
        {"region": "South", "flag": "true", "count_rows": 1},
    ]
    assert multiple["total_groups"] == 5


def test_service_sorting_known_values_and_stability(service_context) -> None:
    context = service_context(MISSING_VALUES)

    price_asc = run(
        context,
        {
            "operation": "sort",
            "sort_by": [{"column": "price", "direction": "asc"}],
            "columns": ["amount"],
        },
    )
    price_desc = run(
        context,
        {
            "operation": "sort",
            "sort_by": [{"column": "price", "direction": "desc"}],
            "columns": ["amount"],
        },
    )
    note_asc = run(
        context,
        {
            "operation": "sort",
            "sort_by": [{"column": "note", "direction": "asc"}],
            "columns": ["amount"],
        },
    )
    region_price = run(
        context,
        {
            "operation": "sort",
            "sort_by": [
                {"column": "region", "direction": "asc"},
                {"column": "price", "direction": "desc"},
            ],
            "columns": ["amount"],
        },
    )
    flag_stable = run(
        context,
        {
            "operation": "sort",
            "sort_by": [{"column": "flag", "direction": "asc"}],
            "columns": ["amount"],
        },
    )

    assert selected_amounts(price_asc) == ["0", "2", "3", "4", "5", "1"]
    assert selected_amounts(price_desc) == ["1", "5", "4", "3", "2", "0"]
    assert selected_amounts(note_asc) == ["2", "5", "0", "3", "1", "4"]
    assert selected_amounts(region_price) == ["5", "2", "0", "1", "4", "3"]
    assert selected_amounts(flag_stable) == ["0", "2", "4", "1", "3", "5"]


def test_service_sorting_all_missing_column_is_stable(
    service_context,
) -> None:
    context = service_context(ALL_MISSING)

    result = run(
        context,
        {
            "operation": "sort",
            "sort_by": [
                {"column": "empty_number", "direction": "asc"}
            ],
            "columns": ["id"],
        },
    )

    assert result["rows"] == [{"id": "a"}, {"id": "b"}, {"id": "c"}]


def test_service_group_filtered_empty_result_has_no_groups(
    service_context,
) -> None:
    context = service_context(MISSING_VALUES)

    result = run(
        context,
        {
            "operation": "group",
            "filters": {
                "conditions": [
                    {
                        "column": "region",
                        "operator": "equals",
                        "value": "Nowhere",
                    }
                ]
            },
            "group_by": ["region"],
        },
    )

    assert result["rows"] == []
    assert result["matched_count"] == 0
    assert result["result_count"] == 0
    assert result["total_groups"] == 0
    assert result["truncated"] is False


def test_service_limits_are_clamped_and_reported(service_context) -> None:
    context = service_context(
        NORMAL,
        analytics_max_result_rows=2,
        analytics_max_groups=1,
        analytics_max_value_frequencies=1,
    )

    filtered = run(
        context,
        {
            "operation": "filter",
            "filters": {
                "conditions": [
                    {
                        "column": "quantity",
                        "operator": "greater_than",
                        "value": 0,
                    }
                ]
            },
            "limit": 100,
        },
    )
    grouped = run(
        context,
        {
            "operation": "group",
            "group_by": ["region"],
        },
    )
    summarized = run(
        context,
        {
            "operation": "summarize",
            "top_values_limit": 100,
        },
    )

    assert filtered["result_count"] == 2
    assert filtered["matched_count"] == 6
    assert filtered["truncated"] is True
    assert grouped["result_count"] == 1
    assert grouped["total_groups"] == 3
    assert grouped["truncated"] is True
    region = next(
        item
        for item in summarized["summary"]["categorical"]
        if item["column"] == "region"
    )
    assert region["top_values"] == [{"value": "North", "count": 3}]
    assert region["truncated"] is True
    assert summarized["truncated"] is True


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "operation": "filter",
                "filters": {
                    "conditions": [
                        {
                            "column": "missing",
                            "operator": "equals",
                            "value": "x",
                        }
                    ]
                },
            },
            "Unknown column",
        ),
        (
            {
                "operation": "filter",
                "filters": {
                    "conditions": [
                        {
                            "column": "order_date",
                            "operator": "equals",
                            "value": "01/02/2025",
                        }
                    ]
                },
            },
            "ambiguous",
        ),
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
def test_service_rejects_invalid_requests(
    service_context,
    payload: dict[str, object],
    message: str,
) -> None:
    context = service_context(MISSING_VALUES)

    with pytest.raises(AnalyticsValidationError, match=message):
        run(context, payload)


def test_service_results_are_deterministic_and_do_not_modify_source(
    service_context,
) -> None:
    context = service_context(MISSING_VALUES)
    before = context.path.read_bytes()
    payload = {
        "operation": "summarize",
        "group_by": ["region"],
        "aggregations": [
            {"function": "sum", "column": "amount"},
            {"function": "count", "column": "price"},
        ],
        "top_values_limit": 1,
    }

    first = run(context, payload)
    second = run(context, payload)

    assert first == second
    assert context.path.read_bytes() == before
