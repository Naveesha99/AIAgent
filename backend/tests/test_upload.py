"""Tests for the non-persistent CSV upload endpoint."""

import asyncio
from io import BytesIO
from pathlib import Path
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


@pytest.fixture(autouse=True)
def isolate_dataset_storage(tmp_path: Path) -> Iterator[Path]:
    """Keep every endpoint test inside its own temporary storage directory."""
    storage_dir = tmp_path / "private-datasets"
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        dataset_storage_dir=storage_dir,
    )
    yield storage_dir
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def upload_limit(
    isolate_dataset_storage: Path,
) -> Iterator[Callable[[int], None]]:
    """Override the upload limit without modifying process environment."""

    def set_limit(limit: int) -> None:
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            max_upload_size_bytes=limit,
            dataset_storage_dir=isolate_dataset_storage,
        )

    yield set_limit
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def csv_settings(
    isolate_dataset_storage: Path,
) -> Iterator[Callable[..., None]]:
    """Override CSV validation settings without modifying process environment."""

    def set_settings(**values: int) -> None:
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            dataset_storage_dir=isolate_dataset_storage,
            **values,
        )

    yield set_settings
    app.dependency_overrides.pop(get_settings, None)


def test_upload_accepts_csv_and_returns_declared_schema(
    isolate_dataset_storage: Path,
) -> None:
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
    from app.services.dataset_storage import DatasetStorage

    storage = DatasetStorage(isolate_dataset_storage)
    stored_path = storage.resolve(result.dataset_id)
    assert stored_path.parent == isolate_dataset_storage.resolve()
    assert stored_path.name == f"{result.dataset_id}.csv"
    with storage.open_dataset(result.dataset_id) as stored_file:
        assert stored_file.read() == contents


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


@pytest.mark.parametrize(
    ("contents", "expected_encoding", "expected_columns", "expected_rows"),
    [
        (b"name,value\nalpha,1\n", "utf-8", 2, 1),
        (b"\xef\xbb\xbfname,value\nalpha,1\n", "utf-8-sig", 2, 1),
        (
            b'name,value\n"alpha, beta","say ""hello"""\n',
            "utf-8",
            2,
            1,
        ),
        (
            b'name,value\nalpha,"first line\nsecond line"\n',
            "utf-8",
            2,
            1,
        ),
        ("café,値\n東京,naïve\n".encode("utf-8"), "utf-8", 2, 1),
    ],
)
def test_csv_validator_accepts_valid_utf8_csv(
    contents: bytes,
    expected_encoding: str,
    expected_columns: int,
    expected_rows: int,
) -> None:
    from app.services.csv_validator import validate_csv

    result = validate_csv(BytesIO(contents), Settings(_env_file=None))

    assert result.encoding == expected_encoding
    assert result.column_count == expected_columns
    assert result.data_row_count == expected_rows
    assert len(result.column_names) == expected_columns


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (b"", "The CSV file is empty."),
        (b" \t\r\n", "CSV header names must be non-empty."),
        (b"name,value\n", "at least one non-empty data row"),
        (b",value\n1,2\n", "header names must be non-empty"),
        (b"name,name\n1,2\n", "duplicate column names"),
        (b"\xff\xfe\x00\x00", "valid UTF-8 encoding"),
        (b"a,b\n1,2\n\"unterminated", "syntax is malformed"),
        (b"a,b\n1\n", "different number of fields"),
        (b"a,b\n1,2,3\n", "different number of fields"),
        (b"binary\x00,data\n1,2\n", "unsupported binary data"),
    ],
)
def test_csv_validator_rejects_invalid_csv(
    contents: bytes,
    message: str,
) -> None:
    from app.services.csv_validator import CsvValidationError, validate_csv

    with pytest.raises(CsvValidationError, match=message):
        validate_csv(BytesIO(contents), Settings(_env_file=None))


def test_upload_rejects_invalid_csv_without_echoing_contents(
    isolate_dataset_storage: Path,
) -> None:
    private_data = b"private-row-value"
    response = client.post(
        "/api/v1/datasets/upload",
        files={
            "file": (
                "sample.csv",
                b"name,value\n" + private_data + b"\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 400
    assert "private-row-value" not in response.text
    assert "Traceback" not in response.text
    assert "backend/" not in response.text
    assert list(isolate_dataset_storage.glob("*.csv")) == []
    assert list(isolate_dataset_storage.glob(".upload-*.part")) == []


def test_upload_closes_file_after_csv_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_close = StarletteUploadFile.close
    closed_uploads: list[StarletteUploadFile] = []

    async def track_close(upload: StarletteUploadFile) -> None:
        closed_uploads.append(upload)
        await original_close(upload)

    monkeypatch.setattr(StarletteUploadFile, "close", track_close)
    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("bad.csv", b"a,b\n1\n", "text/csv")},
    )

    assert response.status_code == 400
    assert closed_uploads
    assert all(upload.file.closed for upload in closed_uploads)


def test_original_filename_does_not_influence_storage_path(
    isolate_dataset_storage: Path,
) -> None:
    from app.services.dataset_storage import DatasetStorage

    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("../../display-name.csv", b"a\n1\n", "text/csv")},
    )

    assert response.status_code == 201
    assert response.json()["filename"] == "../../display-name.csv"
    storage = DatasetStorage(isolate_dataset_storage)
    path = storage.resolve(response.json()["dataset_id"])
    assert path.parent == isolate_dataset_storage.resolve()
    assert path.name == f"{response.json()['dataset_id']}.csv"


def test_storage_failure_does_not_return_success_or_leave_partial_file(
    isolate_dataset_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.dataset_storage import DatasetStorage, DatasetStorageError

    def fail_finalize(
        storage: DatasetStorage,
        staged: object,
        filename: str,
    ) -> object:
        raise DatasetStorageError("simulated storage failure")

    monkeypatch.setattr(DatasetStorage, "finalize", fail_finalize)
    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("sample.csv", b"a\n1\n", "text/csv")},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "The uploaded dataset could not be stored safely."
    )
    assert list(isolate_dataset_storage.glob("*.csv")) == []
    assert list(isolate_dataset_storage.glob(".upload-*.part")) == []


def test_storage_write_failure_is_not_reported_as_success(
    isolate_dataset_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.dataset_storage as dataset_storage_module

    def fail_fsync(descriptor: int) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(dataset_storage_module.os, "fsync", fail_fsync)
    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("sample.csv", b"a\n1\n", "text/csv")},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "The uploaded dataset could not be stored safely."
    )
    assert list(isolate_dataset_storage.glob("*.csv")) == []
    assert list(isolate_dataset_storage.glob(".upload-*.part")) == []


def test_close_failure_prevents_finalizing_dataset(
    isolate_dataset_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_close = StarletteUploadFile.close
    close_calls = 0

    async def fail_close(upload: StarletteUploadFile) -> None:
        nonlocal close_calls
        close_calls += 1
        await original_close(upload)
        if close_calls == 1:
            raise OSError("simulated close failure")

    monkeypatch.setattr(StarletteUploadFile, "close", fail_close)
    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("sample.csv", b"a\n1\n", "text/csv")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "The uploaded file could not be closed safely."
    )
    assert list(isolate_dataset_storage.glob("*.csv")) == []
    assert list(isolate_dataset_storage.glob(".upload-*.part")) == []


@pytest.mark.parametrize("size_bytes", [7, 8])
def test_upload_accepts_file_at_or_below_limit(
    size_bytes: int,
    upload_limit: Callable[[int], None],
) -> None:
    upload_limit(8)
    contents = b"a\n1" + b" " * (size_bytes - 4) + b"\n"

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


def test_csv_validation_limits_have_expected_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_csv_columns == 200
    assert settings.max_csv_rows == 100_000
    assert settings.max_csv_field_size == 1024 * 1024
    assert settings.max_csv_validation_lines == 200_000


def test_csv_field_size_limit_is_restored_after_validation_failure() -> None:
    import csv

    from app.services.csv_validator import CsvValidationError, validate_csv

    original_limit = csv.field_size_limit()
    settings = Settings(_env_file=None, max_csv_field_size=4)

    with pytest.raises(CsvValidationError):
        validate_csv(BytesIO(b"a\n12345\n"), settings)

    assert csv.field_size_limit() == original_limit


@pytest.mark.parametrize(
    ("setting", "contents", "expected_detail"),
    [
        ("max_csv_columns", b"a,b,c\n1,2,3\n", "maximum number of columns"),
        ("max_csv_rows", b"a\n1\n2\n", "maximum number of data rows"),
        ("max_csv_field_size", b"a\n123456789\n", "maximum field size"),
        (
            "max_csv_validation_lines",
            b"a\n1\n2\n",
            "maximum number of lines",
        ),
    ],
)
def test_csv_resource_limits_return_413(
    setting: str,
    contents: bytes,
    expected_detail: str,
    csv_settings: Callable[..., None],
) -> None:
    limit = {
        "max_csv_columns": 2,
        "max_csv_rows": 1,
        "max_csv_field_size": 8,
        "max_csv_validation_lines": 2,
    }[setting]
    csv_settings(**{setting: limit})

    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("sample.csv", contents, "text/csv")},
    )

    assert response.status_code == 413
    assert expected_detail in response.json()["detail"]


def test_column_limit_is_checked_before_csv_reader_builds_a_wide_row() -> None:
    from app.services.csv_validator import (
        CsvValidationError,
        _ValidationLines,
    )

    lines = _ValidationLines(
        iter([",".join([""] * 100_000) + "\n"]),
        max_columns=3,
        max_validation_lines=10,
    )

    with pytest.raises(CsvValidationError, match="maximum number of columns"):
        next(lines)


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
        contents = b"value\nok\n"

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

    def fail_stage(
        storage: object,
        source: BytesIO,
        max_size_bytes: int,
    ) -> object:
        raise UploadReadError

    async def track_close(upload: StarletteUploadFile) -> None:
        closed_uploads.append(upload)
        await original_close(upload)

    from app.services.dataset_storage import DatasetStorage

    monkeypatch.setattr(DatasetStorage, "stage_upload", fail_stage)
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
