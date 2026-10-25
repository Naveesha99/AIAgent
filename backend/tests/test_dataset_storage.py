"""Tests for private temporary dataset storage and lifecycle handling."""

import os
import time
import uuid
from io import BytesIO
from pathlib import Path

import pytest

from app.services.dataset_storage import (
    DatasetInUseError,
    DatasetNotFoundError,
    DatasetStorage,
    DatasetStorageError,
    InvalidDatasetIdError,
)
from app.services.uploads import UploadTooLargeError
from app.core.config import Settings


@pytest.fixture
def storage(tmp_path: Path) -> DatasetStorage:
    return DatasetStorage(tmp_path / "private", ttl_hours=24)


def test_storage_settings_default_to_private_backend_data_directory() -> None:
    settings = Settings(_env_file=None)

    assert settings.dataset_storage_dir == (
        Path(__file__).resolve().parents[1] / "data" / "datasets"
    )
    assert settings.dataset_ttl_hours == 24


def test_stored_upload_resolves_and_preserves_bytes(storage: DatasetStorage) -> None:
    contents = b"name,value\nalpha,1\n"
    staged = storage.stage_upload(BytesIO(contents), max_size_bytes=100)
    stored = storage.finalize(staged)

    assert stored.dataset_id == staged.dataset_id
    assert stored.size_bytes == len(contents)
    assert storage.exists(str(stored.dataset_id))
    path = storage.resolve(str(stored.dataset_id))
    assert path.parent == storage._root
    assert path.name == f"{stored.dataset_id}.csv"
    assert path.stat().st_mode & 0o777 == 0o600
    with storage.open_dataset(stored.dataset_id) as dataset_file:
        assert dataset_file.read() == contents


def test_staging_uses_bounded_reads_and_rejects_oversize(
    storage: DatasetStorage,
) -> None:
    class TrackingReader(BytesIO):
        requested_sizes: list[int]

        def __init__(self, contents: bytes) -> None:
            super().__init__(contents)
            self.requested_sizes = []

        def read(self, size: int = -1) -> bytes:
            self.requested_sizes.append(size)
            return super().read(size)

    source = TrackingReader(b"x" * 100)
    with pytest.raises(UploadTooLargeError):
        storage.stage_upload(source, max_size_bytes=8)

    assert source.requested_sizes == [9]
    assert list(storage._root.glob(".upload-*.part")) == []


def test_invalid_ids_and_traversal_never_resolve_outside_storage(
    storage: DatasetStorage,
) -> None:
    outside = storage._root.parent / "secret.csv"
    outside.write_bytes(b"private")

    for identifier in ("../secret", "../../etc/passwd", "not-a-uuid"):
        with pytest.raises(InvalidDatasetIdError):
            storage.resolve(identifier)
    with pytest.raises(InvalidDatasetIdError):
        storage.resolve(f"../{outside}")


def test_symlink_dataset_is_not_resolved_deleted_or_cleaned(
    storage: DatasetStorage,
) -> None:
    storage._ensure_root()
    outside = storage._root.parent / "external.csv"
    outside.write_bytes(b"do not delete")
    identifier = uuid.uuid4()
    link = storage._root / f"{identifier}.csv"
    link.symlink_to(outside)

    with pytest.raises(DatasetStorageError):
        storage.resolve(identifier)
    with pytest.raises(DatasetStorageError):
        storage.delete(identifier)
    assert storage.cleanup_expired(now=time.time() + 10 * 24 * 60 * 60) == 0
    assert outside.read_bytes() == b"do not delete"
    assert link.is_symlink()


def test_finalization_never_overwrites_existing_dataset(
    storage: DatasetStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = storage.stage_upload(BytesIO(b"first"), max_size_bytes=20)
    storage.finalize(original)

    monkeypatch.setattr(
        "app.services.dataset_storage.uuid.uuid4",
        lambda: original.dataset_id,
    )
    duplicate = storage.stage_upload(BytesIO(b"second"), max_size_bytes=20)
    with pytest.raises(DatasetStorageError):
        storage.finalize(duplicate)
    storage.discard(duplicate)

    with storage.open_dataset(original.dataset_id) as dataset_file:
        assert dataset_file.read() == b"first"


def test_cleanup_removes_expired_files_and_stale_partial_files(
    storage: DatasetStorage,
) -> None:
    now = time.time()
    expired = storage.stage_upload(BytesIO(b"expired"), 20)
    expired_record = storage.finalize(expired)
    current = storage.stage_upload(BytesIO(b"current"), 20)
    current_record = storage.finalize(current)
    expired_path = storage.resolve(expired_record.dataset_id)
    current_path = storage.resolve(current_record.dataset_id)
    os.utime(expired_path, (now - 90_000, now - 90_000))
    os.utime(current_path, (now - 60, now - 60))
    stale_partial = storage._root / ".upload-abandoned.part"
    stale_partial.write_bytes(b"partial")
    os.utime(stale_partial, (now - 90_000, now - 90_000))

    assert storage.cleanup_expired(now=now) == 2
    assert not storage.exists(expired_record.dataset_id)
    assert storage.exists(current_record.dataset_id)
    assert not stale_partial.exists()
    assert storage.cleanup_expired(now=now) == 0


def test_cleanup_and_delete_protect_open_dataset(
    storage: DatasetStorage,
) -> None:
    staged = storage.stage_upload(BytesIO(b"active"), 20)
    stored = storage.finalize(staged)
    path = storage.resolve(stored.dataset_id)
    old_time = time.time() - 90_000
    os.utime(path, (old_time, old_time))

    with storage.open_dataset(stored.dataset_id) as dataset_file:
        with pytest.raises(DatasetInUseError):
            storage.delete(stored.dataset_id)
        assert storage.cleanup_expired(now=time.time()) == 0
        assert dataset_file.read() == b"active"

    assert storage.cleanup_expired(now=time.time()) == 1
    assert not storage.exists(stored.dataset_id)


def test_cleanup_does_not_remove_an_active_staging_upload(
    storage: DatasetStorage,
) -> None:
    staged = storage.stage_upload(BytesIO(b"active upload"), 100)
    old_time = time.time() - 90_000
    os.utime(staged._path, (old_time, old_time))

    assert storage.cleanup_expired(now=time.time()) == 0
    assert staged._path.exists()

    storage.discard(staged)
    assert not staged._path.exists()


def test_missing_dataset_operations_are_safe(storage: DatasetStorage) -> None:
    missing_id = uuid.uuid4()
    assert not storage.exists(missing_id)
    with pytest.raises(DatasetNotFoundError):
        storage.resolve(missing_id)
    with pytest.raises(DatasetNotFoundError):
        storage.delete(missing_id)
    assert storage.cleanup_expired() == 0


def test_storage_root_symlink_is_rejected(tmp_path: Path) -> None:
    actual_directory = tmp_path / "actual"
    actual_directory.mkdir()
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(actual_directory, target_is_directory=True)
    storage = DatasetStorage(linked_directory)

    with pytest.raises(DatasetStorageError):
        storage.cleanup_expired()


def test_public_frontend_directory_cannot_be_used_for_storage() -> None:
    project_root = Path(__file__).resolve().parents[2]
    public_directory = project_root / "frontend" / "public"
    storage_path = public_directory / "phase23-test-data"
    storage = DatasetStorage(storage_path)

    with pytest.raises(DatasetStorageError):
        storage.cleanup_expired()

    assert not storage_path.exists()
