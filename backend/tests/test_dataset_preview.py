"""Tests for paginated previews of stored CSV datasets."""

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
def preview_storage(tmp_path: Path) -> Iterator[Path]:
    storage_dir = tmp_path / "preview-datasets"
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


def test_preview_returns_metadata_and_default_page(
    preview_storage: Path,
) -> None:
    dataset_id = upload_csv(
        b"\xef\xbb\xbfid,name,note\n"
        b'00123,"Acme, Inc.","say ""hello"""\n'
        b'00007,"first line\nsecond line",\n'
        b"00008,,last\n",
        filename="companies.csv",
    )

    response = client.get(f"/api/v1/datasets/{dataset_id}/preview")

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": dataset_id,
        "filename": "companies.csv",
        "row_count": 3,
        "column_count": 3,
        "columns": ["id", "name", "note"],
        "rows": [
            {
                "id": "00123",
                "name": "Acme, Inc.",
                "note": 'say "hello"',
            },
            {
                "id": "00007",
                "name": "first line\nsecond line",
                "note": None,
            },
            {"id": "00008", "name": None, "note": "last"},
        ],
        "page": 1,
        "page_size": 10,
        "total_pages": 1,
    }
    stored_files = list(preview_storage.iterdir())
    assert any(path.name == f"{dataset_id}.csv" for path in stored_files)
    assert any(path.name == f"{dataset_id}.json" for path in stored_files)


def test_preview_returns_only_requested_page_and_correct_counts(
    preview_storage: Path,
) -> None:
    dataset_id = upload_csv(b"value\nzero\none\ntwo\nthree\n")

    response = client.get(
        f"/api/v1/datasets/{dataset_id}/preview",
        params={"page": 2, "page_size": 2},
    )

    assert response.status_code == 200
    assert response.json()["row_count"] == 4
    assert response.json()["rows"] == [{"value": "two"}, {"value": "three"}]
    assert response.json()["page"] == 2
    assert response.json()["page_size"] == 2
    assert response.json()["total_pages"] == 2


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": -1},
        {"page_size": 0},
        {"page_size": 101},
    ],
)
def test_preview_rejects_invalid_pagination(
    preview_storage: Path,
    params: dict[str, int],
) -> None:
    dataset_id = upload_csv(b"value\nrow\n")

    response = client.get(
        f"/api/v1/datasets/{dataset_id}/preview",
        params=params,
    )

    assert response.status_code in {404, 422}


@pytest.mark.parametrize(
    "dataset_id",
    ["not-a-uuid", "../etc/passwd", "../../secret"],
)
def test_preview_rejects_malformed_dataset_ids(
    preview_storage: Path,
    dataset_id: str,
) -> None:
    response = client.get(f"/api/v1/datasets/{dataset_id}/preview")

    assert response.status_code in {404, 422}
    assert "Traceback" not in response.text
    assert "backend/" not in response.text


def test_preview_returns_not_found_for_missing_or_expired_dataset(
    preview_storage: Path,
) -> None:
    missing = client.get(
        "/api/v1/datasets/00000000-0000-4000-8000-000000000000/preview"
    )
    assert missing.status_code == 404

    dataset_id = upload_csv(b"value\nrow\n")
    storage = DatasetStorage(preview_storage)
    dataset_path = storage.resolve(dataset_id)
    old_time = time.time() - 90_000
    os.utime(dataset_path, (old_time, old_time))

    expired = client.get(f"/api/v1/datasets/{dataset_id}/preview")
    assert expired.status_code == 404


def test_preview_handles_missing_file_and_corrupted_stored_csv_safely(
    preview_storage: Path,
) -> None:
    dataset_id = upload_csv(b"value\nrow\n")
    storage = DatasetStorage(preview_storage)
    dataset_path = storage.resolve(dataset_id)
    dataset_path.unlink()
    missing = client.get(f"/api/v1/datasets/{dataset_id}/preview")
    assert missing.status_code == 404

    corrupted_id = upload_csv(b"value\nrow\n", filename="corrupted.csv")
    corrupted_path = storage.resolve(corrupted_id)
    corrupted_path.write_bytes(b"value\n\"unterminated")
    corrupted = client.get(f"/api/v1/datasets/{corrupted_id}/preview")

    assert corrupted.status_code == 500
    assert corrupted.json()["detail"] == (
        "The stored dataset could not be read safely."
    )
    assert "Traceback" not in corrupted.text
    assert str(preview_storage) not in corrupted.text


def test_preview_reports_metadata_corruption_without_leaking_details(
    preview_storage: Path,
) -> None:
    dataset_id = upload_csv(b"value\nrow\n")
    metadata_path = preview_storage / f"{dataset_id}.json"
    metadata_path.write_text("{malformed", encoding="utf-8")

    response = client.get(f"/api/v1/datasets/{dataset_id}/preview")

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "The stored dataset could not be read safely."
    )
    assert str(preview_storage) not in response.text
