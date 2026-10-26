"""Known-result verification for the dataset profiling and analytics APIs."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.core.config import Settings, get_settings
from app.main import app
from app.schemas.analytics import AnalyticsRequest
from app.services.analytics_service import analyze_dataset
from app.services.dataset_storage import DatasetStorage
from fixtures.known_datasets import (
    ALL_MISSING,
    EDGE_CASES,
    INCONSISTENT_TYPES,
    INVALID_DATASETS,
    KNOWN_DATASETS,
    MISSING_VALUES,
    NORMAL,
    read_fixture,
)

client = TestClient(app)
ANALYTICS_ADAPTER = TypeAdapter(AnalyticsRequest)


@pytest.fixture
def phase35_settings(tmp_path: Path) -> Iterator[Settings]:
    settings = Settings(
        _env_file=None,
        dataset_storage_dir=tmp_path / "phase35-datasets",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def phase35_storage(phase35_settings: Settings) -> Path:
    return phase35_settings.dataset_storage_dir


def upload_fixture(filename: str) -> str:
    response = client.post(
        "/api/v1/datasets/upload",
        files={
            "file": (
                filename,
                read_fixture(filename),
                "text/csv",
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["dataset_id"]


def upload_known(known) -> str:
    return upload_fixture(known.filename)


def analytics_payloads() -> list[dict[str, object]]:
    return [
        {
            "operation": "filter",
            "filters": {
                "combine": "and",
                "conditions": [
                    {
                        "column": "region",
                        "operator": "equals",
                        "value": "North",
                    }
                ],
            },
            "columns": ["order_id", "quantity"],
        },
        {
            "operation": "sort",
            "sort_by": [
                {"column": "region", "direction": "asc"},
                {"column": "quantity", "direction": "desc"},
            ],
        },
        {
            "operation": "group",
            "group_by": ["region"],
            "aggregations": [
                {"function": "count"},
                {"function": "sum", "column": "quantity"},
            ],
        },
        {
            "operation": "aggregate",
            "aggregations": [
                {"function": "mean", "column": "quantity"},
                {"function": "std", "column": "unit_price"},
            ],
        },
        {"operation": "summarize", "top_values_limit": 2},
    ]


@pytest.mark.parametrize(
    "known",
    KNOWN_DATASETS,
    ids=[dataset.filename for dataset in KNOWN_DATASETS],
)
def test_known_dataset_metadata_matches_expected_values(
    phase35_storage: Path,
    known,
) -> None:
    dataset_id = upload_known(known)

    response = client.get(f"/api/v1/datasets/{dataset_id}/metadata")

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["filename"] == known.filename
    assert result["row_count"] == known.row_count
    assert result["column_count"] == known.column_count
    assert [column["name"] for column in result["columns"]] == list(
        known.data_types
    )
    assert result["numeric_columns"] == list(known.numeric_columns)
    assert result["date_columns"] == list(known.date_columns)
    assert result["categorical_columns"] == list(
        known.categorical_columns
    )

    columns = {
        column["name"]: column for column in result["columns"]
    }
    for name, expected_type in known.data_types.items():
        column = columns[name]
        assert column["data_type"] == expected_type
        assert column["non_null_count"] == known.non_null_counts[name]
        assert column["null_count"] == known.null_counts[name]
        assert column["is_numeric"] == (name in known.numeric_columns)
        assert column["is_date"] == (name in known.date_columns)
        assert column["is_categorical"] == (
            name in known.categorical_columns
        )

    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "known",
    KNOWN_DATASETS,
    ids=[dataset.filename for dataset in KNOWN_DATASETS],
)
def test_known_dataset_statistics_match_expected_values(
    phase35_storage: Path,
    known,
) -> None:
    dataset_id = upload_known(known)

    response = client.get(f"/api/v1/datasets/{dataset_id}/statistics")

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["row_count"] == known.row_count
    assert result["column_count"] == known.column_count
    columns = {
        column["name"]: column
        for column in result["column_statistics"]
    }

    for name, expected_stats in known.numeric_statistics.items():
        numeric = columns[name]["numeric"]
        assert numeric is not None
        for field_name, expected_value in expected_stats.items():
            actual_value = numeric[field_name]
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

    for name, (earliest, latest) in known.date_ranges.items():
        assert columns[name]["earliest"] == earliest
        assert columns[name]["latest"] == latest

    for name, expected_distinct in known.distinct_counts.items():
        assert columns[name]["distinct_count"] == expected_distinct

    for name, (expected_mode, expected_frequency) in known.modes.items():
        assert columns[name]["mode"] == expected_mode
        assert columns[name]["mode_frequency"] == expected_frequency

    for name, expected_frequencies in known.value_frequencies.items():
        assert columns[name]["value_frequencies"] == [
            {"value": value, "count": count}
            for value, count in expected_frequencies
        ]

    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "known",
    KNOWN_DATASETS,
    ids=[dataset.filename for dataset in KNOWN_DATASETS],
)
def test_known_dataset_quality_matches_expected_values(
    phase35_storage: Path,
    known,
) -> None:
    dataset_id = upload_known(known)

    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["row_count"] == known.row_count
    assert result["column_count"] == known.column_count
    assert result["summary"] == known.quality_summary

    columns = {
        column["name"]: column for column in result["column_quality"]
    }
    for name, expected_missing in known.quality_missing_counts.items():
        column = columns[name]
        assert column["inferred_type"] == known.data_types[name]
        assert column["missing_count"] == expected_missing
        assert column["non_null_count"] == (
            known.row_count - expected_missing
        )
        expected_percentage = (
            expected_missing / known.row_count * 100
            if known.row_count
            else 0.0
        )
        assert column["missing_percentage"] == pytest.approx(
            expected_percentage
        )

    for name, expected_invalid in known.invalid_date_counts.items():
        assert columns[name]["invalid_date_count"] == expected_invalid

    for name, expected_inconsistent in (
        known.type_inconsistency_counts.items()
    ):
        assert (
            columns[name]["type_inconsistency_count"]
            == expected_inconsistent
        )

    expected_categories: set[str] = set()
    if known.quality_summary["total_missing_cells"]:
        expected_categories.add("missing_values")
    if known.quality_summary["duplicate_rows"]:
        expected_categories.add("duplicate_rows")
    if known.quality_summary["invalid_date_values"]:
        expected_categories.add("invalid_dates")
    if known.quality_summary["type_inconsistencies"]:
        expected_categories.add("type_inconsistency")
    assert {
        issue["category"] for issue in result["issues"]
    } == expected_categories

    json.dumps(result, allow_nan=False)


def test_known_edge_case_preview_preserves_quoted_and_multiline_values(
    phase35_storage: Path,
) -> None:
    dataset_id = upload_known(EDGE_CASES)

    first_page = client.get(
        f"/api/v1/datasets/{dataset_id}/preview",
        params={"page": 1, "page_size": 2},
    )
    second_page = client.get(
        f"/api/v1/datasets/{dataset_id}/preview",
        params={"page": 2, "page_size": 2},
    )

    assert first_page.status_code == 200, first_page.text
    assert second_page.status_code == 200, second_page.text
    assert first_page.json()["row_count"] == 3
    assert first_page.json()["total_pages"] == 2
    assert first_page.json()["rows"] == [
        {
            "id": "001",
            "name": "Acme, Inc.",
            "description": 'He said "hello"',
        },
        {
            "id": "002",
            "name": "café 東京",
            "description": "line one\nline two",
        },
    ]
    assert second_page.json()["rows"] == [
        {"id": "003", "name": "plain", "description": None}
    ]


def test_known_normal_preview_pagination_matches_expected_slice(
    phase35_storage: Path,
) -> None:
    dataset_id = upload_known(NORMAL)

    response = client.get(
        f"/api/v1/datasets/{dataset_id}/preview",
        params={"page": 2, "page_size": 2},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["row_count"] == 6
    assert result["total_pages"] == 3
    assert result["page"] == 2
    assert result["page_size"] == 2
    assert result["rows"] == [
        {
            "order_id": "3",
            "quantity": "30",
            "unit_price": "6.5",
            "active": "true",
            "order_date": "2025-01-03",
            "region": "North",
            "note": "gamma",
        },
        {
            "order_id": "4",
            "quantity": "40",
            "unit_price": "8.0",
            "active": "false",
            "order_date": "2025-01-04",
            "region": "East",
            "note": "delta",
        },
    ]


def test_invalid_fixtures_are_rejected_without_partial_storage(
    phase35_storage: Path,
) -> None:
    valid_id = upload_known(NORMAL)
    storage = DatasetStorage(phase35_storage)
    valid_path = storage.resolve(valid_id)
    before = valid_path.read_bytes()

    for filename, expected_message in INVALID_DATASETS.items():
        response = client.post(
            "/api/v1/datasets/upload",
            files={
                "file": (
                    filename,
                    read_fixture(filename),
                    "text/csv",
                )
            },
        )

        assert response.status_code == 400, (filename, response.text)
        assert expected_message.lower() in response.json()["detail"].lower()
        assert valid_path.read_bytes() == before

    stored_csv_files = sorted(
        path.name for path in phase35_storage.glob("*.csv")
    )
    assert stored_csv_files == [f"{valid_id}.csv"]
    assert list(phase35_storage.glob(".upload-*.part")) == []
    assert list(phase35_storage.glob(".metadata-*.part")) == []


def test_whitespace_only_cells_follow_documented_endpoint_contracts(
    phase35_storage: Path,
) -> None:
    dataset_id = upload_known(MISSING_VALUES)

    metadata = client.get(
        f"/api/v1/datasets/{dataset_id}/metadata"
    ).json()
    statistics = client.get(
        f"/api/v1/datasets/{dataset_id}/statistics"
    ).json()
    quality = client.get(
        f"/api/v1/datasets/{dataset_id}/quality"
    ).json()
    summarize = client.post(
        f"/api/v1/datasets/{dataset_id}/analytics",
        json={"operation": "summarize"},
    ).json()
    preview = client.get(
        f"/api/v1/datasets/{dataset_id}/preview",
        params={"page": 1, "page_size": 10},
    ).json()

    metadata_note = next(
        column
        for column in metadata["columns"]
        if column["name"] == "note"
    )
    statistics_note = next(
        column
        for column in statistics["column_statistics"]
        if column["name"] == "note"
    )
    quality_note = next(
        column
        for column in quality["column_quality"]
        if column["name"] == "note"
    )
    summary_note = next(
        item
        for item in summarize["summary"]["missing"]
        if item["column"] == "note"
    )

    assert metadata_note["null_count"] == 2
    assert metadata_note["non_null_count"] == 4
    assert statistics_note["null_count"] == 2
    assert statistics_note["non_null_count"] == 4
    assert quality_note["missing_count"] == 3
    assert quality_note["non_null_count"] == 3
    assert summary_note["missing_count"] == 2
    assert preview["rows"][2]["note"] == "   "


def test_direct_analytics_service_matches_api_responses(
    phase35_settings: Settings,
    phase35_storage: Path,
) -> None:
    dataset_id = upload_known(NORMAL)
    storage = DatasetStorage(phase35_storage)

    for payload in analytics_payloads():
        response = client.post(
            f"/api/v1/datasets/{dataset_id}/analytics",
            json=payload,
        )
        assert response.status_code == 200, response.text

        request = ANALYTICS_ADAPTER.validate_python(payload)
        service_result = analyze_dataset(
            storage,
            phase35_settings,
            dataset_id,
            request,
        )

        assert (
            service_result.model_dump(mode="json")
            == response.json()
        )


def test_known_dataset_bytes_survive_all_read_only_operations(
    phase35_settings: Settings,
    phase35_storage: Path,
) -> None:
    normal_id = upload_known(NORMAL)
    missing_id = upload_known(MISSING_VALUES)
    storage = DatasetStorage(phase35_storage)
    normal_path = storage.resolve(normal_id)
    missing_path = storage.resolve(missing_id)
    normal_before = normal_path.read_bytes()
    missing_before = missing_path.read_bytes()

    endpoints = [
        f"/api/v1/datasets/{normal_id}/preview",
        f"/api/v1/datasets/{normal_id}/metadata",
        f"/api/v1/datasets/{normal_id}/statistics",
        f"/api/v1/datasets/{normal_id}/quality",
        f"/api/v1/datasets/{missing_id}/preview",
        f"/api/v1/datasets/{missing_id}/metadata",
        f"/api/v1/datasets/{missing_id}/statistics",
        f"/api/v1/datasets/{missing_id}/quality",
    ]
    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200, (endpoint, response.text)

    for payload in analytics_payloads():
        response = client.post(
            f"/api/v1/datasets/{normal_id}/analytics",
            json=payload,
        )
        assert response.status_code == 200, response.text

    assert normal_path.read_bytes() == normal_before
    assert missing_path.read_bytes() == missing_before
    assert normal_path.exists()
    assert missing_path.exists()


def test_corrupted_dataset_fails_safely_without_affecting_other_datasets(
    phase35_settings: Settings,
    phase35_storage: Path,
) -> None:
    corrupted_id = upload_known(NORMAL)
    healthy_id = upload_known(MISSING_VALUES)
    storage = DatasetStorage(phase35_storage)
    corrupted_path = storage.resolve(corrupted_id)
    healthy_path = storage.resolve(healthy_id)
    healthy_before = healthy_path.read_bytes()
    corrupted_path.write_bytes(b'order_id,quantity\n"unterminated')

    analytics_request = {
        "operation": "filter",
        "filters": {
            "conditions": [
                {
                    "column": "order_id",
                    "operator": "is_not_null",
                }
            ]
        },
    }
    failing_responses = [
        client.get(f"/api/v1/datasets/{corrupted_id}/preview"),
        client.get(f"/api/v1/datasets/{corrupted_id}/metadata"),
        client.get(f"/api/v1/datasets/{corrupted_id}/statistics"),
        client.get(f"/api/v1/datasets/{corrupted_id}/quality"),
        client.post(
            f"/api/v1/datasets/{corrupted_id}/analytics",
            json=analytics_request,
        ),
    ]

    for response in failing_responses:
        assert response.status_code == 500
        assert response.json()["detail"] == (
            "The stored dataset could not be read safely."
        )
        assert str(phase35_storage) not in response.text
        assert "Traceback" not in response.text

    healthy_metadata = client.get(
        f"/api/v1/datasets/{healthy_id}/metadata"
    )
    healthy_quality = client.get(
        f"/api/v1/datasets/{healthy_id}/quality"
    )
    assert healthy_metadata.status_code == 200
    assert healthy_quality.status_code == 200
    assert healthy_path.read_bytes() == healthy_before


def test_cleanup_removes_only_expired_dataset_files(
    phase35_settings: Settings,
    phase35_storage: Path,
) -> None:
    expired_id = upload_known(NORMAL)
    retained_id = upload_known(MISSING_VALUES)
    storage = DatasetStorage(
        phase35_storage,
        ttl_hours=phase35_settings.dataset_ttl_hours,
    )
    expired_path = storage.resolve(expired_id)
    retained_path = storage.resolve(retained_id)
    retained_before = retained_path.read_bytes()
    old_time = time.time() - 90_000
    os.utime(expired_path, (old_time, old_time))

    removed = storage.cleanup_expired()

    assert removed == 2
    assert not expired_path.exists()
    assert not (phase35_storage / f"{expired_id}.json").exists()
    assert retained_path.read_bytes() == retained_before
    assert (phase35_storage / f"{retained_id}.json").exists()
    response = client.get(
        f"/api/v1/datasets/{retained_id}/metadata"
    )
    assert response.status_code == 200


def test_all_missing_columns_have_well_defined_statistics_and_analytics(
    phase35_settings: Settings,
    phase35_storage: Path,
) -> None:
    dataset_id = upload_known(ALL_MISSING)

    summarize = client.post(
        f"/api/v1/datasets/{dataset_id}/analytics",
        json={"operation": "summarize"},
    )
    count = client.post(
        f"/api/v1/datasets/{dataset_id}/analytics",
        json={
            "operation": "aggregate",
            "aggregations": [
                {"function": "count", "column": "empty_number"},
                {"function": "nunique", "column": "empty_text"},
                {"function": "min", "column": "empty_number"},
                {"function": "max", "column": "empty_date"},
            ],
        },
    )

    assert summarize.status_code == 200, summarize.text
    result = summarize.json()
    assert result["summary"]["numeric"] == []
    assert result["summary"]["categorical"] == []
    assert {
        item["column"]: item["missing_count"]
        for item in result["summary"]["missing"]
    } == {
        "id": 0,
        "empty_text": 3,
        "empty_number": 3,
        "empty_date": 3,
    }
    assert count.status_code == 200, count.text
    assert count.json()["rows"] == [
        {
            "count_empty_number": 0,
            "nunique_empty_text": 0,
            "min_empty_number": None,
            "max_empty_date": None,
        }
    ]


def test_inconsistent_and_ambiguous_dates_remain_uncorrupted(
    phase35_settings: Settings,
    phase35_storage: Path,
) -> None:
    dataset_id = upload_known(INCONSISTENT_TYPES)
    storage = DatasetStorage(phase35_storage)
    path = storage.resolve(dataset_id)
    before = path.read_bytes()

    metadata = client.get(
        f"/api/v1/datasets/{dataset_id}/metadata"
    ).json()
    quality = client.get(
        f"/api/v1/datasets/{dataset_id}/quality"
    ).json()
    columns = {
        column["name"]: column for column in metadata["columns"]
    }
    quality_columns = {
        column["name"]: column
        for column in quality["column_quality"]
    }

    assert columns["order_date"]["data_type"] == "string"
    assert columns["ambiguous_date"]["data_type"] == "string"
    assert quality_columns["order_date"]["invalid_date_count"] == 1
    assert quality_columns["ambiguous_date"]["invalid_date_count"] == 0
    assert quality_columns["customer_id"]["type_inconsistency_count"] == 0
    assert quality_columns["postal_code"]["type_inconsistency_count"] == 0
    assert path.read_bytes() == before
