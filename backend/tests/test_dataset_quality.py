"""Tests for read-only quality analysis of stored CSV datasets."""

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


@pytest.fixture
def quality_storage(tmp_path: Path) -> Iterator[Path]:
    storage_dir = tmp_path / "quality-datasets"
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


def test_quality_clean_csv_has_no_issues(quality_storage: Path) -> None:
    dataset_id = upload_csv(
        b"quantity,region,active\n"
        b"1,North,true\n"
        b"2,South,false\n"
        b"3,North,true\n"
        b"4,South,false\n"
        b"5,North,true\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")

    assert response.status_code == 200
    result = response.json()
    assert result["row_count"] == 5
    assert result["column_count"] == 3
    assert result["summary"] == {
        "total_missing_cells": 0,
        "rows_with_missing_values": 0,
        "duplicate_rows": 0,
        "duplicate_groups": 0,
        "invalid_date_values": 0,
        "type_inconsistencies": 0,
    }
    assert result["issues"] == []


def test_quality_counts_empty_and_whitespace_but_keeps_zero_false_and_na(
    quality_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"number,flag,label,mostly_empty\n"
        b"0,false,N/A,\n"
        b"1,true,valid,   \n"
        b"2,false,valid,\n"
        b"3,true,valid,\n"
        b"4,false,valid,\n"
    )
    path = DatasetStorage(quality_storage).resolve(dataset_id)
    before = path.read_bytes()

    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")

    assert response.status_code == 200
    result = response.json()
    assert result["summary"]["total_missing_cells"] == 5
    assert result["summary"]["rows_with_missing_values"] == 5
    columns = {item["name"]: item for item in result["column_quality"]}
    assert columns["number"]["missing_count"] == 0
    assert columns["flag"]["missing_count"] == 0
    assert columns["label"]["missing_count"] == 0
    assert columns["label"]["non_null_count"] == 5
    assert columns["mostly_empty"]["missing_count"] == 5
    assert columns["mostly_empty"]["missing_percentage"] == 100.0
    assert path.read_bytes() == before
    json.dumps(result, allow_nan=False)


def test_quality_duplicate_row_convention_and_column_value_repetition(
    quality_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"region,amount\n"
        b"North,1\n"
        b"North,2\n"
        b"North,1\n"
        b"North,1\n"
        b"South,1\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["duplicate_rows"] == 2
    assert summary["duplicate_groups"] == 1
    duplicate_issue = next(
        issue
        for issue in response.json()["issues"]
        if issue["category"] == "duplicate_rows"
    )
    assert "2 rows" in duplicate_issue["description"]
    assert "1 duplicate groups" in duplicate_issue["description"]


def test_quality_detects_invalid_dates_and_mixed_date_type(
    quality_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"order_date,source\n"
        b"2025-01-01,a\n"
        b"2025-01-02,b\n"
        b"2025-01-03,c\n"
        b"2025-01-04,d\n"
        b"2025-01-05,e\n"
        b"2025-13-40,f\n"
        b",g\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")

    assert response.status_code == 200
    result = response.json()
    assert result["summary"]["invalid_date_values"] == 1
    assert result["summary"]["total_missing_cells"] == 1
    column = result["column_quality"][0]
    assert column["inferred_type"] == "string"
    assert column["invalid_date_count"] == 1
    assert column["missing_count"] == 1
    issue = next(
        issue
        for issue in result["issues"]
        if issue["category"] == "invalid_dates"
    )
    assert issue["sample_values"] == ["2025-13-40"]


def test_quality_accepts_supported_day_month_year_dates(
    quality_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"order_date\n"
        b"13/02/2025\n"
        b"14/03/2025\n"
        b"15/04/2025\n"
        b"16/05/2025\n"
        b"17/06/2025\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")

    assert response.status_code == 200
    result = response.json()
    assert result["column_quality"][0]["inferred_type"] == "date"
    assert result["summary"]["invalid_date_values"] == 0
    assert not any(issue["category"] == "invalid_dates" for issue in result["issues"])


def test_quality_does_not_flag_valid_values_in_mixed_supported_date_formats(
    quality_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"order_date\n"
        b"13/02/2025\n"
        b"14/03/2025\n"
        b"15/04/2025\n"
        b"16/05/2025\n"
        b"17/06/2025\n"
        b"2025-07-18\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")

    assert response.status_code == 200
    result = response.json()
    assert result["summary"]["invalid_date_values"] == 0
    assert not any(issue["category"] == "invalid_dates" for issue in result["issues"])


def test_quality_caps_invalid_date_samples(
    quality_storage: Path,
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        dataset_storage_dir=quality_storage,
        data_quality_sample_limit=1,
    )
    rows = [
        [f"2025-01-{day:02d}"]
        for day in range(1, 11)
    ] + [["not-a-date"], ["still-not-a-date"]]
    dataset_id = upload_csv(make_csv(["order_date"], rows))

    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")

    assert response.status_code == 200
    issue = next(
        issue
        for issue in response.json()["issues"]
        if issue["category"] == "invalid_dates"
    )
    assert issue["count"] == 2
    assert len(issue["sample_values"]) == 1


def test_quality_flags_numeric_and_boolean_inconsistencies(
    quality_storage: Path,
) -> None:
    numeric_values = ["1", "2", "3", "4", "5", "6", "oops"]
    boolean_values = ["true", "false", "true", "false", "true", "yes"]
    rows = [
        [numeric_values[index], boolean_values[index]]
        for index in range(6)
    ]
    rows.append(["oops", "true"])
    dataset_id = upload_csv(make_csv(["amount", "consent"], rows))

    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")

    assert response.status_code == 200
    result = response.json()
    columns = {item["name"]: item for item in result["column_quality"]}
    assert columns["amount"]["inferred_type"] == "string"
    assert columns["amount"]["type_inconsistency_count"] == 1
    assert columns["consent"]["inferred_type"] == "categorical"
    assert columns["consent"]["type_inconsistency_count"] == 1
    assert result["summary"]["type_inconsistencies"] == 2
    issues = {
        issue["column"]: issue
        for issue in result["issues"]
        if issue["category"] == "type_inconsistency"
    }
    assert "oops" in issues["amount"]["sample_values"]
    assert issues["consent"]["sample_values"] == ["yes"]


def test_quality_does_not_flag_leading_zero_identifier_and_limits_samples(
    quality_storage: Path,
) -> None:
    rows = [
        [
            f"{index:05d}",
            f"{index:05d}",
            "not-a-number" if index >= 11 else str(index),
        ]
        for index in range(1, 13)
    ]
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        dataset_storage_dir=quality_storage,
        data_quality_sample_limit=2,
    )
    dataset_id = upload_csv(
        make_csv(["customer_id", "postal_code", "amount"], rows)
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")

    assert response.status_code == 200
    result = response.json()
    columns = {item["name"]: item for item in result["column_quality"]}
    assert columns["customer_id"]["inferred_type"] == "string"
    assert columns["customer_id"]["type_inconsistency_count"] == 0
    assert columns["postal_code"]["inferred_type"] == "string"
    assert columns["postal_code"]["type_inconsistency_count"] == 0
    amount_issue = next(
        issue
        for issue in result["issues"]
        if issue["category"] == "type_inconsistency"
        and issue["column"] == "amount"
    )
    assert amount_issue["count"] == 2
    assert len(amount_issue["sample_values"]) == 2


def test_quality_counts_duplicate_groups_and_empty_columns(
    quality_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"empty,value\n,alpha\n,alpha\n,alpha\n,alpha\n,alpha\n"
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")

    assert response.status_code == 200
    result = response.json()
    assert result["summary"]["duplicate_rows"] == 4
    assert result["summary"]["duplicate_groups"] == 1
    empty_column = result["column_quality"][0]
    assert empty_column["inferred_type"] == "string"
    assert empty_column["missing_count"] == 5


@pytest.mark.parametrize(
    ("dataset_id", "status_code"),
    [
        ("not-a-uuid", 422),
        ("../etc/passwd", 404),
        ("00000000-0000-4000-8000-000000000000", 404),
    ],
)
def test_quality_handles_invalid_and_missing_ids_safely(
    quality_storage: Path,
    dataset_id: str,
    status_code: int,
) -> None:
    response = client.get(f"/api/v1/datasets/{dataset_id}/quality")
    assert response.status_code == status_code
    assert "Traceback" not in response.text
    assert str(quality_storage) not in response.text


def test_quality_handles_expired_and_corrupted_datasets(
    quality_storage: Path,
) -> None:
    expired_id = upload_csv(b"value\n1\n")
    expired_path = DatasetStorage(quality_storage).resolve(expired_id)
    old_time = time.time() - 90_000
    os.utime(expired_path, (old_time, old_time))
    assert client.get(
        f"/api/v1/datasets/{expired_id}/quality"
    ).status_code == 404

    corrupted_id = upload_csv(b"value\n1\n")
    corrupted_path = DatasetStorage(quality_storage).resolve(corrupted_id)
    corrupted_path.write_bytes(b'value\n"unterminated')
    response = client.get(f"/api/v1/datasets/{corrupted_id}/quality")
    assert response.status_code == 500
    assert response.json()["detail"] == (
        "The stored dataset could not be read safely."
    )
    assert str(quality_storage) not in response.text
