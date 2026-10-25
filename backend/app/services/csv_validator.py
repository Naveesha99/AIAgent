"""Bounded, streaming validation for UTF-8 CSV uploads."""

from __future__ import annotations

import codecs
import csv
import threading
from dataclasses import dataclass
from typing import BinaryIO, Iterator, Literal, Optional

from app.core.config import Settings

_FIELD_SIZE_LOCK = threading.Lock()
_UTF8_BOM = b"\xef\xbb\xbf"


class CsvValidationError(Exception):
    """A safe validation failure suitable for returning to an API caller."""

    def __init__(self, message: str, *, resource_limit: bool = False) -> None:
        super().__init__(message)
        self.resource_limit = resource_limit


@dataclass(frozen=True)
class CsvValidationResult:
    """Small summary of a CSV schema, with no row values retained."""

    encoding: Literal["utf-8", "utf-8-sig"]
    column_count: int
    data_row_count: int
    column_names: tuple[str, ...]
    preview_rows: tuple[tuple[Optional[str], ...], ...] = ()


class _ValidationLines(Iterator[str]):
    """Count physical lines and CSV fields before csv.reader builds a row list."""

    def __init__(
        self,
        lines: Iterator[str],
        *,
        max_columns: int,
        max_validation_lines: int,
    ) -> None:
        self._lines = lines
        self._max_columns = max_columns
        self._max_validation_lines = max_validation_lines
        self._line_count = 0
        self._field_count = 1
        self._in_quotes = False
        self._at_field_start = True

    def __iter__(self) -> "_ValidationLines":
        return self

    def __next__(self) -> str:
        line = next(self._lines)
        self._line_count += 1
        if self._line_count > self._max_validation_lines:
            raise CsvValidationError(
                "The CSV exceeds the maximum number of lines allowed for validation.",
                resource_limit=True,
            )

        index = 0
        while index < len(line):
            character = line[index]
            if character == "\x00":
                raise CsvValidationError(
                    "The CSV contains unsupported binary data."
                )
            if self._in_quotes:
                if character == '"':
                    if index + 1 < len(line) and line[index + 1] == '"':
                        index += 2
                        continue
                    self._in_quotes = False
                index += 1
                continue

            if character == '"' and self._at_field_start:
                self._in_quotes = True
                self._at_field_start = False
            elif character == ",":
                self._field_count += 1
                if self._field_count > self._max_columns:
                    raise CsvValidationError(
                        "The CSV exceeds the maximum number of columns allowed.",
                        resource_limit=True,
                    )
                self._at_field_start = True
            elif character in "\r\n":
                self._field_count = 1
                self._at_field_start = True
            elif self._at_field_start:
                self._at_field_start = False
            index += 1

        return line


def validate_csv(
    binary_file: BinaryIO,
    settings: Settings,
    *,
    preview_page: Optional[int] = None,
    preview_page_size: Optional[int] = None,
) -> CsvValidationResult:
    """Validate CSV and optionally retain only one bounded preview page."""
    if (preview_page is None) != (preview_page_size is None):
        raise ValueError("Both preview pagination values must be supplied together.")
    if preview_page is not None and preview_page < 1:
        raise ValueError("Preview page must be positive.")
    if preview_page_size is not None and preview_page_size < 1:
        raise ValueError("Preview page size must be positive.")
    try:
        prefix = binary_file.read(len(_UTF8_BOM))
        binary_file.seek(0)
    except (OSError, ValueError) as exc:
        raise CsvValidationError("The uploaded file could not be read.") from exc

    if not prefix:
        raise CsvValidationError("The CSV file is empty.")

    encoding: Literal["utf-8", "utf-8-sig"] = (
        "utf-8-sig" if prefix.startswith(_UTF8_BOM) else "utf-8"
    )
    text_file = codecs.getreader(encoding)(binary_file, errors="strict")
    with _FIELD_SIZE_LOCK:
        previous_field_size_limit = csv.field_size_limit()
        csv.field_size_limit(settings.max_csv_field_size)
        try:
            lines = _ValidationLines(
                iter(text_file),
                max_columns=settings.max_csv_columns,
                max_validation_lines=settings.max_csv_validation_lines,
            )
            reader = csv.reader(lines, strict=True)
            try:
                header = _next_nonempty_record(reader)
                if header is None:
                    raise CsvValidationError(
                        "The CSV must contain a header row."
                    )
                if len(header) > settings.max_csv_columns:
                    raise CsvValidationError(
                        "The CSV exceeds the maximum number of columns allowed.",
                        resource_limit=True,
                    )
                if any(not name.strip() for name in header):
                    raise CsvValidationError(
                        "CSV header names must be non-empty."
                    )
                if len(set(header)) != len(header):
                    raise CsvValidationError(
                        "The CSV contains duplicate column names."
                    )

                data_row_count = 0
                preview_rows: list[tuple[Optional[str], ...]] = []
                first_preview_row = (
                    (preview_page - 1) * preview_page_size
                    if preview_page is not None and preview_page_size is not None
                    else None
                )
                for row in reader:
                    if not row or all(not value.strip() for value in row):
                        continue
                    if len(row) != len(header):
                        raise CsvValidationError(
                            "A CSV data row has a different number of fields than the header."
                        )
                    if data_row_count >= settings.max_csv_rows:
                        raise CsvValidationError(
                            "The CSV exceeds the maximum number of data rows allowed.",
                            resource_limit=True,
                        )
                    if (
                        first_preview_row is not None
                        and preview_page_size is not None
                        and first_preview_row
                        <= data_row_count
                        < first_preview_row + preview_page_size
                    ):
                        preview_rows.append(
                            tuple(value if value != "" else None for value in row)
                        )
                    data_row_count += 1

                if data_row_count == 0:
                    raise CsvValidationError(
                        "The CSV must contain at least one non-empty data row."
                    )

                return CsvValidationResult(
                    encoding=encoding,
                    column_count=len(header),
                    data_row_count=data_row_count,
                    column_names=tuple(header),
                    preview_rows=tuple(preview_rows),
                )
            except UnicodeDecodeError as exc:
                raise CsvValidationError(
                    "The CSV must use valid UTF-8 encoding."
                ) from exc
            except csv.Error as exc:
                message = str(exc)
                if message.startswith("field larger than field limit"):
                    raise CsvValidationError(
                        "A CSV field exceeds the maximum field size allowed.",
                        resource_limit=True,
                    ) from exc
                raise CsvValidationError(
                    "The CSV syntax is malformed."
                ) from exc
            except (OSError, ValueError) as exc:
                raise CsvValidationError(
                    "The uploaded file could not be read."
                ) from exc
        finally:
            csv.field_size_limit(previous_field_size_limit)


def _next_nonempty_record(
    reader: Iterator[list[str]],
) -> Optional[list[str]]:
    for row in reader:
        if row:
            return row
    return None
