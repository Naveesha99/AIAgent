"""Tests for inferred metadata from stored CSV datasets."""

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
def metadata_storage(tmp_path: Path) -> Iterator[Path]:
    storage_dir = tmp_path / "metadata-datasets"
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


def test_metadata_returns_ordered_schema_and_inferred_types(
    metadata_storage: Path,
) -> None:
    headers = [
        "order_id",
        "quantity",
        "unit_price",
        "active",
        "order_date",
        "created_at",
        "region",
        "description",
        "mixed",
        "ambiguous_date",
        "empty",
        "special",
    ]
    rows = [
        [
            f"{index:05d}",
            str(index - 4),
            "" if index == 3 else f"{index}.25",
            "true" if index % 2 else "false",
            f"2025-01-{index:02d}",
            f"2025-01-{index:02d}T10:15:30",
            ("North", "South", "West", "North")[index % 4],
            f"Unique description number {index}",
            ("1", "two", "3", "four", "5", "six", "7", "eight")[index - 1],
            f"{index:02d}/{index + 1:02d}/2025",
            "",
            ("NaN", "Infinity", "-Infinity", "NaN")[index % 4],
        ]
        for index in range(1, 9)
    ]
    dataset_id = upload_csv(make_csv(headers, rows), "sales.csv")

    response = client.get(f"/api/v1/datasets/{dataset_id}/metadata")

    assert response.status_code == 200
    result = response.json()
    assert result["dataset_id"] == dataset_id
    assert result["filename"] == "sales.csv"
    assert result["row_count"] == 8
    assert result["column_count"] == len(headers)
    assert [column["name"] for column in result["columns"]] == headers
    assert result["numeric_columns"] == ["quantity", "unit_price"]
    assert result["date_columns"] == ["order_date", "created_at"]
    assert result["categorical_columns"] == ["region"]

    columns = {column["name"]: column for column in result["columns"]}
    assert columns["quantity"]["data_type"] == "integer"
    assert columns["quantity"]["is_numeric"] is True
    assert columns["unit_price"]["data_type"] == "float"
    assert columns["active"]["data_type"] == "boolean"
    assert columns["order_date"]["data_type"] == "date"
    assert columns["created_at"]["data_type"] == "datetime"
    assert columns["region"]["data_type"] == "categorical"
    assert columns["description"]["data_type"] == "string"
    assert columns["mixed"]["data_type"] == "string"
    assert columns["ambiguous_date"]["data_type"] == "string"
    assert columns["order_id"]["data_type"] == "string"
    assert columns["empty"]["data_type"] == "string"
    assert columns["empty"]["non_null_count"] == 0
    assert columns["empty"]["null_count"] == 8
    assert columns["unit_price"]["non_null_count"] == 7
    assert columns["unit_price"]["null_count"] == 1
    assert columns["special"]["data_type"] == "string"
    assert columns["special"]["is_numeric"] is False
    assert "NaN" not in response.text
    assert "Infinity" not in response.text


def test_metadata_detects_day_month_year_only_when_unambiguous(
    metadata_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"date\n13/02/2025\n14/03/2025\n15/04/2025\n"
        b"16/05/2025\n17/06/2025\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/metadata")

    assert response.status_code == 200
    assert response.json()["date_columns"] == ["date"]
    assert response.json()["columns"][0]["data_type"] == "date"


def test_metadata_recognizes_consistent_yes_no_booleans(
    metadata_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"consent\nYes\nNo\nyes\nno\nYes\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/metadata")

    assert response.status_code == 200
    assert response.json()["columns"][0]["data_type"] == "boolean"


def test_numeric_identifier_column_name_remains_string(
    metadata_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"order_id,quantity\n1001,2\n1002,3\n1003,4\n1004,5\n1005,6\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/metadata")

    assert response.status_code == 200
    result = response.json()
    assert result["columns"][0]["data_type"] == "string"
    assert result["numeric_columns"] == ["quantity"]


@pytest.mark.parametrize(
    ("dataset_id", "expected_status"),
    [
        ("not-a-uuid", 422),
        ("../etc/passwd", 404),
    ],
)
def test_metadata_rejects_invalid_dataset_ids(
    metadata_storage: Path,
    dataset_id: str,
    expected_status: int,
) -> None:
    response = client.get(f"/api/v1/datasets/{dataset_id}/metadata")

    assert response.status_code == expected_status
    assert "Traceback" not in response.text
    assert str(metadata_storage) not in response.text


def test_metadata_returns_not_found_for_missing_and_expired_datasets(
    metadata_storage: Path,
) -> None:
    missing = client.get(
        "/api/v1/datasets/00000000-0000-4000-8000-000000000000/metadata"
    )
    assert missing.status_code == 404

    dataset_id = upload_csv(b"value\nrow\n")
    storage = DatasetStorage(metadata_storage)
    dataset_path = storage.resolve(dataset_id)
    old_time = time.time() - 90_000
    os.utime(dataset_path, (old_time, old_time))

    expired = client.get(f"/api/v1/datasets/{dataset_id}/metadata")
    assert expired.status_code == 404


def test_metadata_reports_corrupted_csv_safely(
    metadata_storage: Path,
) -> None:
    dataset_id = upload_csv(b"value\nrow\n")
    dataset_path = DatasetStorage(metadata_storage).resolve(dataset_id)
    dataset_path.write_bytes(b"value\n\"unterminated")

    response = client.get(f"/api/v1/datasets/{dataset_id}/metadata")

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "The stored dataset could not be read safely."
    )
    assert "Traceback" not in response.text
    assert str(metadata_storage) not in response.text


def test_metadata_preserves_logical_rows_with_quoted_newlines(
    metadata_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"category,details\nNorth,\"first line\nsecond line\"\nSouth,short\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/metadata")

    assert response.status_code == 200
    assert response.json()["row_count"] == 2
    assert response.json()["column_count"] == 2
