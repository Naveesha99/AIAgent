"""Tests for statistical profiles of stored CSV datasets."""

import csv
import io
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


@pytest.fixture
def statistics_storage(tmp_path: Path) -> Iterator[Path]:
    storage_dir = tmp_path / "statistics-datasets"
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        dataset_storage_dir=storage_dir,
    )
    yield storage_dir
    app.dependency_overrides.pop(get_settings, None)


def upload_csv(contents: bytes, filename: str = "records.csv") -> str:
    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": (filename, contents, "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["dataset_id"]


def make_csv(headers: list[str], rows: list[list[str]]) -> bytes:
    contents = io.StringIO(newline="")
    writer = csv.writer(contents, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return contents.getvalue().encode("utf-8")


def get_columns(response: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        column["name"]: column
        for column in response["column_statistics"]  # type: ignore[index]
    }


def test_statistics_returns_numeric_category_boolean_and_string_summaries(
    statistics_storage: Path,
) -> None:
    rows = [
        ["-2", "1.5", "5", "5", "", "true", "red", "001"],
        ["0", "2.5", "", "5", "", "false", "blue", "002"],
        ["4", "", "", "5", "", "true", "red", "003"],
        ["8", "4.5", "", "5", "", "false", "blue", "004"],
        ["", "6.5", "", "5", "", "true", "red", "005"],
    ]
    headers = [
        "quantity",
        "price",
        "single",
        "constant",
        "empty",
        "active",
        "color",
        "customer_id",
    ]
    dataset_id = upload_csv(make_csv(headers, rows), "summary.csv")

    response = client.get(f"/api/v1/datasets/{dataset_id}/statistics")

    assert response.status_code == 200
    result = response.json()
    assert result["dataset_id"] == dataset_id
    assert result["filename"] == "summary.csv"
    assert result["row_count"] == 5
    assert result["column_count"] == 8
    columns = get_columns(result)

    quantity = columns["quantity"]
    assert quantity["data_type"] == "integer"
    assert quantity["non_null_count"] == 4
    assert quantity["null_count"] == 1
    assert quantity["numeric"] == {
        "count": 4,
        "mean": 2.5,
        "median": 2.0,
        "standard_deviation": pytest.approx(4.43471156509042),
        "variance": pytest.approx(19.666666666666668),
        "minimum": -2.0,
        "maximum": 8.0,
        "q1": -0.5,
        "q3": 5.0,
        "iqr": 5.5,
        "sum": 10.0,
    }
    price = columns["price"]["numeric"]
    assert price["count"] == 4
    assert price["mean"] == 3.75
    assert price["median"] == 3.5
    assert price["minimum"] == 1.5
    assert price["maximum"] == 6.5
    assert price["q1"] == 2.25
    assert price["q3"] == 5.0
    assert price["iqr"] == 2.75
    assert price["sum"] == 15.0

    single = columns["single"]["numeric"]
    assert single["count"] == 1
    assert single["standard_deviation"] is None
    assert single["variance"] is None
    assert single["mean"] == 5.0
    assert columns["constant"]["numeric"]["standard_deviation"] == 0.0
    assert columns["empty"]["data_type"] == "string"
    assert columns["empty"]["distinct_count"] == 0

    color = columns["color"]
    assert color["data_type"] == "categorical"
    assert color["distinct_count"] == 2
    assert color["mode"] == "red"
    assert color["mode_frequency"] == 3
    assert color["value_frequencies"] == [
        {"value": "red", "count": 3},
        {"value": "blue", "count": 2},
    ]
    assert color["value_frequencies_truncated"] is False
    active = columns["active"]
    assert active["data_type"] == "boolean"
    assert active["distinct_count"] == 2
    assert active["value_frequencies"] == []
    assert columns["customer_id"]["data_type"] == "string"
    assert columns["customer_id"]["distinct_count"] == 5
    assert columns["customer_id"]["value_frequencies"] == []

    import json

    assert "NaN" not in json.dumps(result, allow_nan=False)


def test_statistics_breaks_mode_ties_deterministically(
    statistics_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"category\nblue\nred\nblue\nred\nblue\nred\nblue\nred\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/statistics")

    assert response.status_code == 200
    category = response.json()["column_statistics"][0]
    assert category["mode"] == "blue"
    assert category["mode_frequency"] == 4
    assert category["value_frequencies"] == [
        {"value": "blue", "count": 4},
        {"value": "red", "count": 4},
    ]


def test_statistics_date_and_datetime_ranges_use_iso_serialization(
    statistics_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"date,created_at\n"
        b"2024-01-02,2024-01-02T10:30:00Z\n"
        b"2024-01-01,2024-01-01T08:00:00Z\n"
        b",2024-01-03T11:00:00Z\n"
        b"2024-01-03,\n"
        b"2024-01-04,2024-01-04T12:00:00Z\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/statistics")

    assert response.status_code == 200
    columns = get_columns(response.json())
    assert columns["date"]["data_type"] == "date"
    assert columns["date"]["non_null_count"] == 4
    assert columns["date"]["null_count"] == 1
    assert columns["date"]["distinct_count"] == 4
    assert columns["date"]["earliest"] == "2024-01-01"
    assert columns["date"]["latest"] == "2024-01-04"
    assert columns["created_at"]["data_type"] == "datetime"
    assert columns["created_at"]["earliest"] == "2024-01-01T08:00:00+00:00"
    assert columns["created_at"]["latest"] == "2024-01-04T12:00:00+00:00"


def test_statistics_handles_mixed_timezone_datetimes_safely(
    statistics_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"created_at\n"
        b"2025-02-01T10:00:00\n"
        b"2025-02-02T10:00:00Z\n"
        b"2025-02-03T10:00:00\n"
        b"2025-02-04T10:00:00Z\n"
        b"2025-02-05T10:00:00\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/statistics")

    assert response.status_code == 200
    column = response.json()["column_statistics"][0]
    assert column["data_type"] == "datetime"
    assert column["earliest"] is None
    assert column["latest"] is None
    assert column["distinct_count"] == 5


def test_statistics_truncates_frequencies_and_ties_deterministically(
    statistics_storage: Path,
) -> None:
    rows = [
        [f"category-{index:02d}"]
        for _ in range(21)
        for index in range(1, 22)
    ]
    dataset_id = upload_csv(make_csv(["category"], rows))

    response = client.get(f"/api/v1/datasets/{dataset_id}/statistics")

    assert response.status_code == 200
    category = response.json()["column_statistics"][0]
    assert category["distinct_count"] == 21
    assert category["mode"] == "category-01"
    assert category["mode_frequency"] == 21
    assert len(category["value_frequencies"]) == 20
    assert category["value_frequencies_truncated"] is True


def test_statistics_handles_extreme_numeric_magnitudes_without_invalid_json(
    statistics_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"magnitude\n1e308\n1e308\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/statistics")

    assert response.status_code == 200
    numeric = response.json()["column_statistics"][0]["numeric"]
    assert numeric["count"] == 2
    assert numeric["mean"] == 1e308
    assert numeric["sum"] is None
    assert numeric["standard_deviation"] == 0.0
    assert "Infinity" not in response.text
    assert "NaN" not in response.text


@pytest.mark.parametrize(
    ("dataset_id", "expected_status"),
    [
        ("not-a-uuid", 422),
        ("../etc/passwd", 404),
    ],
)
def test_statistics_rejects_malformed_dataset_ids(
    statistics_storage: Path,
    dataset_id: str,
    expected_status: int,
) -> None:
    response = client.get(f"/api/v1/datasets/{dataset_id}/statistics")

    assert response.status_code == expected_status
    assert "Traceback" not in response.text
    assert str(statistics_storage) not in response.text


def test_statistics_handles_missing_expired_and_corrupted_datasets(
    statistics_storage: Path,
) -> None:
    missing = client.get(
        "/api/v1/datasets/00000000-0000-4000-8000-000000000000/statistics"
    )
    assert missing.status_code == 404

    expired_id = upload_csv(b"value\n1\n")
    expired_path = DatasetStorage(statistics_storage).resolve(expired_id)
    old_time = time.time() - 90_000
    os.utime(expired_path, (old_time, old_time))
    assert client.get(
        f"/api/v1/datasets/{expired_id}/statistics"
    ).status_code == 404

    corrupted_id = upload_csv(b"value\n1\n")
    corrupted_path = DatasetStorage(statistics_storage).resolve(corrupted_id)
    corrupted_path.write_bytes(b'value\n"broken')
    response = client.get(f"/api/v1/datasets/{corrupted_id}/statistics")
    assert response.status_code == 500
    assert response.json()["detail"] == (
        "The stored dataset could not be read safely."
    )
    assert str(statistics_storage) not in response.text
