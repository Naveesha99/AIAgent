"""Bounded-memory helpers for incoming file uploads."""

from fastapi import UploadFile

CHUNK_SIZE_BYTES = 64 * 1024


class UploadTooLargeError(Exception):
    """Raised when the bytes read from an upload exceed the configured limit."""


class UploadReadError(Exception):
    """Raised when an upload cannot be read completely."""


async def count_upload_bytes(
    upload: UploadFile,
    max_size_bytes: int,
) -> int:
    """Count actual uploaded bytes incrementally, stopping at the first excess byte."""
    size_bytes = 0

    try:
        while True:
            remaining_with_overflow_byte = max_size_bytes - size_bytes + 1
            chunk = await upload.read(
                min(CHUNK_SIZE_BYTES, remaining_with_overflow_byte)
            )
            if not chunk:
                return size_bytes

            size_bytes += len(chunk)
            if size_bytes > max_size_bytes:
                raise UploadTooLargeError
    except OSError as exc:
        raise UploadReadError from exc
