"""Private local storage for validated datasets."""

from __future__ import annotations

import os
import json
import stat
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Generator, Optional

from app.services.uploads import UploadReadError, UploadTooLargeError

CHUNK_SIZE_BYTES = 64 * 1024
_ACTIVE_LOCK = threading.RLock()
_ACTIVE_STAGES: set[Path] = set()
_OPEN_DATASETS: dict[tuple[Path, str], int] = {}
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class DatasetStorageError(Exception):
    """A safe storage failure that can be reported without filesystem details."""


class InvalidDatasetIdError(ValueError):
    """The supplied identifier is not a canonical server-generated UUID."""


class DatasetNotFoundError(LookupError):
    """The requested dataset does not exist as a regular stored file."""


class DatasetInUseError(Exception):
    """The requested dataset is currently open for reading."""


@dataclass(frozen=True)
class StagedDataset:
    """A private, incomplete file awaiting validation and atomic finalization."""

    dataset_id: uuid.UUID
    size_bytes: int
    _path: Path
    _root: Path


@dataclass(frozen=True)
class StoredDataset:
    """Metadata for a finalized dataset without exposing its path to clients."""

    dataset_id: uuid.UUID
    size_bytes: int
    filename: str


class DatasetStorage:
    """Manage bounded uploads and expiring files in a private local directory."""

    def __init__(self, directory: Path, ttl_hours: int = 24) -> None:
        self._configured_root = Path(directory).expanduser()
        if not self._configured_root.is_absolute():
            backend_root = Path(__file__).resolve().parents[2]
            self._configured_root = backend_root / self._configured_root
        absolute_root = Path(os.path.abspath(self._configured_root))
        self._root = absolute_root.parent.resolve() / absolute_root.name
        self._ttl_seconds = ttl_hours * 60 * 60

    def _ensure_root(self) -> Path:
        try:
            public_directory = (
                Path(__file__).resolve().parents[3]
                / "frontend"
                / "public"
            ).resolve()
            try:
                self._root.relative_to(public_directory)
            except ValueError:
                pass
            else:
                raise DatasetStorageError(
                    "Dataset storage cannot be inside a public directory."
                )
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root_stat = self._root.lstat()
            if not stat.S_ISDIR(root_stat.st_mode):
                raise DatasetStorageError(
                    "The configured dataset storage location is unsafe."
                )
            os.chmod(self._root, 0o700, follow_symlinks=False)
            return self._root.resolve(strict=True)
        except DatasetStorageError:
            raise
        except (OSError, ValueError) as exc:
            raise DatasetStorageError(
                "The configured dataset storage location is unavailable."
            ) from exc

    def _checked_root(self) -> Path:
        root = self._ensure_root()
        if root != self._root:
            raise DatasetStorageError(
                "The configured dataset storage location changed unexpectedly."
            )
        return root

    @staticmethod
    def _parse_id(dataset_id: str | uuid.UUID) -> uuid.UUID:
        try:
            parsed = dataset_id if isinstance(dataset_id, uuid.UUID) else uuid.UUID(dataset_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise InvalidDatasetIdError("Invalid dataset identifier.") from exc
        if not isinstance(dataset_id, uuid.UUID) and str(parsed) != dataset_id:
            raise InvalidDatasetIdError("Invalid dataset identifier.")
        return parsed

    def _dataset_path(self, dataset_id: str | uuid.UUID) -> tuple[str, Path, Path]:
        parsed = self._parse_id(dataset_id)
        root = self._checked_root()
        path = root / f"{parsed}.csv"
        if path.parent != root:
            raise InvalidDatasetIdError("Invalid dataset identifier.")
        return str(parsed), root, path

    def stage_upload(
        self,
        source: BinaryIO,
        max_size_bytes: int,
    ) -> StagedDataset:
        """Copy upload bytes to a private staging file, enforcing actual size."""
        root = self._checked_root()
        dataset_id = uuid.uuid4()
        staged_path: Optional[Path] = None
        descriptor: Optional[int] = None

        try:
            with _ACTIVE_LOCK:
                descriptor, raw_path = tempfile.mkstemp(
                    prefix=".upload-",
                    suffix=".part",
                    dir=str(root),
                )
                staged_path = Path(raw_path)
                _ACTIVE_STAGES.add(staged_path)

            size_bytes = 0
            with os.fdopen(descriptor, "wb") as destination:
                descriptor = None
                while True:
                    read_size = min(
                        CHUNK_SIZE_BYTES,
                        max_size_bytes - size_bytes + 1,
                    )
                    try:
                        chunk = source.read(read_size)
                    except (OSError, ValueError) as exc:
                        raise UploadReadError from exc
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > max_size_bytes:
                        raise UploadTooLargeError
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())

            assert staged_path is not None
            return StagedDataset(
                dataset_id=dataset_id,
                size_bytes=size_bytes,
                _path=staged_path,
                _root=root,
            )
        except UploadTooLargeError:
            self._discard_path(staged_path)
            raise
        except UploadReadError:
            self._discard_path(staged_path)
            raise
        except (OSError, ValueError) as exc:
            self._discard_path(staged_path)
            raise DatasetStorageError("The upload could not be stored safely.") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def open_staged(self, staged: StagedDataset) -> BinaryIO:
        """Open a staging file without following symlinks."""
        root = self._checked_root()
        if staged._root != root or staged._path.parent != root:
            raise DatasetStorageError("The staged upload is no longer available.")
        descriptor: Optional[int] = None
        try:
            descriptor = os.open(staged._path, os.O_RDONLY | _NOFOLLOW)
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                os.close(descriptor)
                descriptor = None
                raise DatasetStorageError("The staged upload is no longer available.")
            with _ACTIVE_LOCK:
                if staged._path not in _ACTIVE_STAGES:
                    os.close(descriptor)
                    descriptor = None
                    raise DatasetStorageError("The staged upload is no longer available.")
            opened_file = os.fdopen(descriptor, "rb")
            descriptor = None
            return opened_file
        except DatasetStorageError:
            raise
        except (OSError, ValueError) as exc:
            raise DatasetStorageError("The staged upload is no longer available.") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def finalize(
        self,
        staged: StagedDataset,
        filename: Optional[str] = None,
    ) -> StoredDataset:
        """Atomically publish a staged file without replacing an existing dataset."""
        root = self._checked_root()
        if staged._root != root or staged._path.parent != root:
            raise DatasetStorageError("The staged upload is no longer available.")
        final_path = root / f"{staged.dataset_id}.csv"
        metadata_path = root / f"{staged.dataset_id}.json"
        display_filename = filename or f"{staged.dataset_id}.csv"
        metadata_descriptor: Optional[int] = None
        metadata_stage: Optional[Path] = None
        metadata_created = False
        dataset_created = False
        with _ACTIVE_LOCK:
            if staged._path not in _ACTIVE_STAGES:
                raise DatasetStorageError("The staged upload is no longer available.")
            try:
                metadata_descriptor, metadata_stage_raw = tempfile.mkstemp(
                    prefix=".metadata-",
                    suffix=".part",
                    dir=str(root),
                )
                metadata_stage = Path(metadata_stage_raw)
                _ACTIVE_STAGES.add(metadata_stage)
                with os.fdopen(metadata_descriptor, "w", encoding="utf-8") as metadata_file:
                    metadata_descriptor = None
                    json.dump({"filename": display_filename}, metadata_file)
                    metadata_file.flush()
                    os.fsync(metadata_file.fileno())

                stage_stat = staged._path.lstat()
                if not stat.S_ISREG(stage_stat.st_mode):
                    raise DatasetStorageError("The staged upload is no longer available.")
                os.link(metadata_stage, metadata_path, follow_symlinks=False)
                metadata_created = True
                os.link(staged._path, final_path, follow_symlinks=False)
                dataset_created = True
                final_stat = final_path.lstat()
                if (final_stat.st_dev, final_stat.st_ino) != (
                    stage_stat.st_dev,
                    stage_stat.st_ino,
                ):
                    raise DatasetStorageError("Dataset finalization failed.")
                staged._path.unlink()
                metadata_stage.unlink()
                self._sync_directory(root)
                _ACTIVE_STAGES.discard(staged._path)
                _ACTIVE_STAGES.discard(metadata_stage)
                return StoredDataset(
                    staged.dataset_id,
                    staged.size_bytes,
                    display_filename,
                )
            except DatasetStorageError:
                self._rollback_finalization(
                    final_path,
                    metadata_path,
                    staged,
                    metadata_stage,
                    dataset_created,
                    metadata_created,
                )
                raise
            except FileExistsError as exc:
                self._rollback_finalization(
                    final_path,
                    metadata_path,
                    staged,
                    metadata_stage,
                    dataset_created,
                    metadata_created,
                )
                raise DatasetStorageError("Dataset finalization failed.") from exc
            except (OSError, ValueError) as exc:
                self._rollback_finalization(
                    final_path,
                    metadata_path,
                    staged,
                    metadata_stage,
                    dataset_created,
                    metadata_created,
                )
                raise DatasetStorageError("Dataset finalization failed.") from exc
            finally:
                if metadata_descriptor is not None:
                    os.close(metadata_descriptor)
                if metadata_stage is not None:
                    self._discard_path(metadata_stage)

    def _rollback_finalization(
        self,
        dataset_path: Path,
        metadata_path: Path,
        staged: StagedDataset,
        metadata_stage: Optional[Path],
        dataset_created: bool,
        metadata_created: bool,
    ) -> None:
        for path, created in (
            (dataset_path, dataset_created),
            (metadata_path, metadata_created),
        ):
            if not created:
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self._discard_path(staged._path)
        self._discard_path(metadata_stage)

    def get_filename(self, dataset_id: str | uuid.UUID) -> str:
        """Read the display filename from private metadata without following links."""
        canonical_id, _, _ = self._dataset_path(dataset_id)
        metadata_path = self._root / f"{canonical_id}.json"
        descriptor: Optional[int] = None
        try:
            descriptor = os.open(metadata_path, os.O_RDONLY | _NOFOLLOW)
            metadata_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata_stat.st_mode)
                or metadata_stat.st_size > 64 * 1024
            ):
                raise DatasetStorageError("Dataset metadata is unavailable.")
            with os.fdopen(descriptor, "r", encoding="utf-8") as metadata_file:
                descriptor = None
                metadata = json.load(metadata_file)
        except FileNotFoundError as exc:
            raise DatasetNotFoundError("Dataset not found.") from exc
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise DatasetStorageError("Dataset metadata is unreadable.") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        filename = metadata.get("filename") if isinstance(metadata, dict) else None
        if not isinstance(filename, str):
            raise DatasetStorageError("Dataset metadata is unreadable.")
        return filename

    def discard(self, staged: StagedDataset) -> None:
        """Remove an unfinished staging file; safe to call repeatedly."""
        if staged._root != self._root or staged._path.parent != self._root:
            raise DatasetStorageError("The staged upload is no longer available.")
        self._discard_path(staged._path)

    def _discard_path(self, path: Optional[Path]) -> None:
        if path is None:
            return
        with _ACTIVE_LOCK:
            try:
                file_stat = path.lstat()
                if stat.S_ISREG(file_stat.st_mode):
                    path.unlink()
                elif stat.S_ISLNK(file_stat.st_mode):
                    path.unlink()
                else:
                    raise DatasetStorageError(
                        "A staging file was replaced unsafely."
                    )
            except FileNotFoundError:
                pass
            except DatasetStorageError:
                raise
            except (OSError, ValueError) as exc:
                raise DatasetStorageError("A staging file could not be removed.") from exc
            finally:
                _ACTIVE_STAGES.discard(path)

    def resolve(self, dataset_id: str | uuid.UUID) -> Path:
        """Return the confined path for an existing regular dataset file."""
        _, root, path = self._dataset_path(dataset_id)
        try:
            file_stat = path.lstat()
        except FileNotFoundError as exc:
            raise DatasetNotFoundError("Dataset not found.") from exc
        except (OSError, ValueError) as exc:
            raise DatasetStorageError("The dataset could not be accessed.") from exc
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or path.parent != root
            or self._is_expired(file_stat)
        ):
            if self._is_expired(file_stat):
                raise DatasetNotFoundError("Dataset not found.")
            raise DatasetStorageError("The dataset could not be accessed.")
        return path

    def exists(self, dataset_id: str | uuid.UUID) -> bool:
        try:
            self.resolve(dataset_id)
        except DatasetNotFoundError:
            return False
        return True

    @contextmanager
    def open_dataset(
        self,
        dataset_id: str | uuid.UUID,
    ) -> Generator[BinaryIO, None, None]:
        """Open a dataset safely and prevent cleanup while the handle is active."""
        canonical_id, root, path = self._dataset_path(dataset_id)
        descriptor: Optional[int] = None
        with _ACTIVE_LOCK:
            try:
                descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
            except FileNotFoundError as exc:
                raise DatasetNotFoundError("Dataset not found.") from exc
            except (OSError, ValueError) as exc:
                raise DatasetStorageError("The dataset could not be accessed.") from exc

            try:
                file_stat = os.fstat(descriptor)
            except (OSError, ValueError) as exc:
                os.close(descriptor)
                descriptor = None
                raise DatasetStorageError(
                    "The dataset could not be accessed."
                ) from exc
            if not stat.S_ISREG(file_stat.st_mode):
                os.close(descriptor)
                descriptor = None
                raise DatasetStorageError("The dataset could not be accessed.")
            if self._is_expired(file_stat):
                os.close(descriptor)
                descriptor = None
                raise DatasetNotFoundError("Dataset not found.")

            key = (root, canonical_id)
            _OPEN_DATASETS[key] = _OPEN_DATASETS.get(key, 0) + 1

        try:
            dataset_file = os.fdopen(descriptor, "rb")
            descriptor = None
            with dataset_file:
                yield dataset_file
        finally:
            if descriptor is not None:
                os.close(descriptor)
            with _ACTIVE_LOCK:
                count = _OPEN_DATASETS.get(key, 0)
                if count <= 1:
                    _OPEN_DATASETS.pop(key, None)
                else:
                    _OPEN_DATASETS[key] = count - 1

    def delete(self, dataset_id: str | uuid.UUID) -> None:
        """Delete one dataset without following symlinks or removing active reads."""
        canonical_id, root, path = self._dataset_path(dataset_id)
        with _ACTIVE_LOCK:
            if _OPEN_DATASETS.get((root, canonical_id), 0):
                raise DatasetInUseError("The dataset is currently in use.")
            try:
                file_stat = path.lstat()
            except FileNotFoundError as exc:
                raise DatasetNotFoundError("Dataset not found.") from exc
            except (OSError, ValueError) as exc:
                raise DatasetStorageError("The dataset could not be deleted.") from exc
            if not stat.S_ISREG(file_stat.st_mode):
                raise DatasetStorageError("The dataset could not be deleted.")
            try:
                path.unlink()
                metadata_path = root / f"{canonical_id}.json"
                try:
                    metadata_stat = metadata_path.lstat()
                    if stat.S_ISREG(metadata_stat.st_mode):
                        metadata_path.unlink()
                except FileNotFoundError:
                    pass
                self._sync_directory(root)
            except (OSError, ValueError) as exc:
                raise DatasetStorageError("The dataset could not be deleted.") from exc

    def cleanup_expired(self, now: Optional[float] = None) -> int:
        """Remove expired datasets and stale partial files; skip active handles."""
        root = self._checked_root()
        current_time = now if now is not None else time.time()
        cutoff = current_time - self._ttl_seconds
        removed = 0
        try:
            with _ACTIVE_LOCK:
                with os.scandir(root) as entries:
                    for entry in entries:
                        path = Path(entry.path)
                        if path in _ACTIVE_STAGES:
                            continue
                        try:
                            file_stat = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        except OSError as exc:
                            raise DatasetStorageError(
                                "Dataset cleanup could not inspect storage."
                            ) from exc
                        if not stat.S_ISREG(file_stat.st_mode):
                            continue

                        dataset_id = self._dataset_id_from_name(entry.name)
                        is_stale_stage = (
                            entry.name.startswith(".upload-")
                            and entry.name.endswith(".part")
                        )
                        if dataset_id is None and not is_stale_stage:
                            continue
                        metadata_for_expired_dataset = False
                        if entry.name.endswith(".json"):
                            dataset_path = root / f"{dataset_id}.csv"
                            try:
                                dataset_stat = os.stat(
                                    dataset_path,
                                    follow_symlinks=False,
                                )
                            except FileNotFoundError:
                                dataset_stat = None
                            if (
                                dataset_stat is not None
                                and dataset_stat.st_mtime > cutoff
                            ):
                                continue
                            metadata_for_expired_dataset = (
                                dataset_stat is None
                                or dataset_stat.st_mtime <= cutoff
                            )
                        if (
                            file_stat.st_mtime > cutoff
                            and not metadata_for_expired_dataset
                        ):
                            continue
                        if dataset_id is not None and _OPEN_DATASETS.get(
                            (root, dataset_id), 0
                        ):
                            continue
                        try:
                            path.unlink()
                            removed += 1
                        except FileNotFoundError:
                            continue
                        except OSError as exc:
                            raise DatasetStorageError(
                                "Dataset cleanup could not remove expired files."
                            ) from exc
                if removed:
                    self._sync_directory(root)
        except DatasetStorageError:
            raise
        except (OSError, ValueError) as exc:
            raise DatasetStorageError("Dataset cleanup failed.") from exc
        return removed

    @staticmethod
    def _dataset_id_from_name(filename: str) -> Optional[str]:
        if not filename.endswith((".csv", ".json")):
            return None
        identifier = filename.rsplit(".", 1)[0]
        try:
            parsed = uuid.UUID(identifier)
        except ValueError:
            return None
        return identifier if str(parsed) == identifier else None

    def _is_expired(self, file_stat: os.stat_result) -> bool:
        return file_stat.st_mtime <= time.time() - self._ttl_seconds

    @staticmethod
    def _sync_directory(root: Path) -> None:
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
