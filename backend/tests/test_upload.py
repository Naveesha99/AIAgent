"""Tests for the non-persistent CSV upload endpoint."""

import asyncio
from io import BytesIO
from typing import Callable, Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.config import Settings, get_settings
from app.main import app
from app.schemas.dataset import DatasetUploadResponse
from app.services.uploads import (
    UploadReadError,
    UploadTooLargeError,
    count_upload_bytes,
)

client = TestClient(app)


@pytest.fixture
def upload_limit() -> Iterator[Callable[[int], None]]:
    """Override the upload limit without modifying process environment."""

    def set_limit(limit: int) -> None:
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            max_upload_size_bytes=limit,
        )

    yield set_limit
    app.dependency_overrides.pop(get_settings, None)


def test_upload_accepts_csv_and_returns_declared_schema() -> None:
    contents = b"name,value\nalpha,1\n"

    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("sample.CSV", contents, "text/csv")},
    )

    assert response.status_code == 201
    result = DatasetUploadResponse.model_validate(response.json())
    assert UUID(str(result.dataset_id))
    assert result.filename == "sample.CSV"
    assert result.size_bytes == len(contents)
    assert result.content_type == "text/csv"
    assert result.status == "uploaded"
    assert set(response.json()) == {
        "dataset_id",
        "filename",
        "size_bytes",
        "content_type",
        "status",
    }


def test_upload_generates_unique_dataset_ids() -> None:
    upload = {"file": ("sample.csv", b"a\n1\n", "text/csv")}

    first = client.post("/api/v1/datasets/upload", files=upload)
    second = client.post("/api/v1/datasets/upload", files=upload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["dataset_id"] != second.json()["dataset_id"]


def test_upload_requires_file_field() -> None:
    response = client.post("/api/v1/datasets/upload")

    assert response.status_code == 422


def test_malformed_multipart_request_returns_client_error() -> None:
    response = client.post(
        "/api/v1/datasets/upload",
        content=b"--broken",
        headers={"Content-Type": "multipart/form-data; boundary=broken"},
    )

    assert response.status_code in {400, 422}


def test_upload_rejects_unsupported_extension() -> None:
    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("sample.txt", b"not a csv", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Only files with a .csv filename extension are accepted."
    )


def test_upload_rejects_empty_filename() -> None:
    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("", b"private-upload-content", "text/csv")},
    )

    assert response.status_code == 422
    assert "private-upload-content" not in response.text


def test_upload_rejects_whitespace_filename() -> None:
    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("   ", b"a\n1\n", "text/csv")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "A non-empty CSV filename is required."


@pytest.mark.parametrize("size_bytes", [7, 8])
def test_upload_accepts_file_at_or_below_limit(
    size_bytes: int,
    upload_limit: Callable[[int], None],
) -> None:
    upload_limit(8)
    contents = b"x" * size_bytes

    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("sample.csv", contents, "text/csv")},
    )

    assert response.status_code == 201
    assert response.json()["size_bytes"] == size_bytes


def test_upload_rejects_first_byte_over_limit(
    upload_limit: Callable[[int], None],
) -> None:
    upload_limit(8)

    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("sample.csv", b"x" * 9, "text/csv")},
    )

    assert response.status_code == 413
    assert "maximum size of 8 bytes" in response.json()["detail"]
    assert "Traceback" not in response.text
    assert "backend/" not in response.text


def test_upload_size_defaults_to_10_mib() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_upload_size_bytes == 10 * 1024 * 1024


@pytest.mark.parametrize(
    "status_code",
    [201, 400, 413],
)
def test_uploaded_file_is_closed_after_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
    upload_limit: Callable[[int], None],
    status_code: int,
) -> None:
    original_close = StarletteUploadFile.close
    closed_uploads: list[StarletteUploadFile] = []

    async def track_close(upload: StarletteUploadFile) -> None:
        closed_uploads.append(upload)
        await original_close(upload)

    monkeypatch.setattr(StarletteUploadFile, "close", track_close)
    if status_code == 413:
        upload_limit(1)
        filename = "sample.csv"
        contents = b"too large"
    elif status_code == 400:
        filename = "sample.txt"
        contents = b"invalid extension"
    else:
        filename = "sample.csv"
        contents = b"ok"

    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": (filename, contents, "text/csv")},
    )

    assert response.status_code == status_code
    assert closed_uploads
    assert all(upload.file.closed for upload in closed_uploads)


def test_upload_read_failure_returns_client_error_and_closes_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_close = StarletteUploadFile.close
    closed_uploads: list[StarletteUploadFile] = []

    async def fail_read(upload: StarletteUploadFile, size: int) -> int:
        raise UploadReadError

    async def track_close(upload: StarletteUploadFile) -> None:
        closed_uploads.append(upload)
        await original_close(upload)

    from app.api.routes import datasets

    monkeypatch.setattr(datasets, "count_upload_bytes", fail_read)
    monkeypatch.setattr(StarletteUploadFile, "close", track_close)

    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("sample.csv", b"a\n1\n", "text/csv")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "The uploaded file could not be read."
    assert closed_uploads
    assert all(upload.file.closed for upload in closed_uploads)


def test_upload_read_error_is_converted_to_upload_read_error() -> None:
    class InterruptedReader(BytesIO):
        def read(self, size: int = -1) -> bytes:
            raise OSError("simulated interrupted read")

    upload = StarletteUploadFile(
        file=InterruptedReader(b"contents"),
        filename="sample.csv",
    )

    with pytest.raises(UploadReadError):
        asyncio.run(count_upload_bytes(upload, max_size_bytes=100))
    asyncio.run(upload.close())


def test_upload_size_count_stops_on_first_excess_byte() -> None:
    class TrackingReader(BytesIO):
        def __init__(self, contents: bytes) -> None:
            super().__init__(contents)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return super().read(size)

    reader = TrackingReader(b"x" * 100)
    upload = StarletteUploadFile(file=reader, filename="sample.csv")

    with pytest.raises(UploadTooLargeError):
        asyncio.run(count_upload_bytes(upload, max_size_bytes=8))

    assert reader.read_sizes == [9]
    asyncio.run(upload.close())
