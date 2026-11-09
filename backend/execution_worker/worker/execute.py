"""Run one generated analysis program and emit bounded, framed output."""

from __future__ import annotations

import argparse
import json
import math
import resource
import sys
import time
from typing import Any, Iterator

import numpy as np
import pandas as pd

_OUTPUT_CHUNK_CHARS = 2048
_RESULT_CHUNK_CHARS = 2048
_MAX_RESULT_DEPTH = 64


class ResultSerializationError(ValueError):
    """The analysis result is not supported by the JSON result contract."""


class ResultLimitError(ResultSerializationError):
    """The analysis result exceeds a configured result bound."""


class ResultRowLimitError(ResultLimitError):
    """The analysis result contains more rows than the configured maximum."""


def _emit(stream: Any, event: dict[str, Any]) -> None:
    encoded = json.dumps(
        event,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    stream.write(encoded + "\n")
    stream.flush()


class ProtocolTextWriter:
    """Capture one Python text stream and forward bounded framed output."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(
        self,
        protocol_stream: Any,
        stream_name: str,
        max_bytes: int,
    ) -> None:
        self._protocol_stream = protocol_stream
        self._stream_name = stream_name
        self._max_bytes = max_bytes
        self._total_bytes = 0
        self._emitted_bytes = 0
        self._truncated = False

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("write() argument must be str")
        for offset in range(0, len(text), _OUTPUT_CHUNK_CHARS):
            raw = text[offset : offset + _OUTPUT_CHUNK_CHARS].encode(
                "utf-8",
                errors="replace",
            )
            self._total_bytes += len(raw)
            remaining = self._max_bytes - self._emitted_bytes
            if remaining > 0:
                retained = raw[:remaining]
                content = retained.decode("utf-8", errors="ignore")
                if content:
                    emitted = content.encode("utf-8")
                    _emit(
                        self._protocol_stream,
                        {
                            "type": "output",
                            "stream": self._stream_name,
                            "content": content,
                        },
                    )
                    self._emitted_bytes += len(emitted)
            if self._total_bytes > self._max_bytes:
                self._truncated = True
        return len(text)

    def flush(self) -> None:
        self._protocol_stream.flush()

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def finish(self) -> None:
        _emit(
            self._protocol_stream,
            {
                "type": "stream_end",
                "stream": self._stream_name,
                "size_bytes": self._total_bytes,
                "truncated": self._truncated,
            },
        )


def _json_string(value: str) -> Iterator[str]:
    if not value.isprintable() and len(value) <= _RESULT_CHUNK_CHARS:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        yield encoded
        return
    yield '"'
    for offset in range(0, len(value), _RESULT_CHUNK_CHARS):
        part = value[offset : offset + _RESULT_CHUNK_CHARS]
        encoded = json.dumps(part, ensure_ascii=False, allow_nan=False)
        yield encoded[1:-1]
    yield '"'


def _column_name(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)) and (
        not isinstance(value, float) or math.isfinite(value)
    ):
        return str(value)
    raise ResultSerializationError("Table column names must be scalar values.")


def _json_fragments(value: Any, *, max_rows: int, depth: int = 0) -> Iterator[str]:
    if depth > _MAX_RESULT_DEPTH:
        raise ResultSerializationError("The result is nested too deeply.")
    if value is None or value is pd.NA or value is pd.NaT:
        yield "null"
    elif isinstance(value, (bool, np.bool_)):
        yield "true" if bool(value) else "false"
    elif isinstance(value, (int, np.integer)):
        yield str(int(value))
    elif isinstance(value, (float, np.floating)):
        number = float(value)
        if not math.isfinite(number):
            raise ResultSerializationError(
                "The result contains a non-finite number."
            )
        yield repr(number)
    elif isinstance(value, str):
        yield from _json_string(value)
    elif isinstance(value, pd.Timestamp):
        if pd.isna(value):
            yield "null"
        else:
            yield from _json_string(value.isoformat())
    elif isinstance(value, np.generic):
        yield from _json_fragments(
            value.item(),
            max_rows=max_rows,
            depth=depth + 1,
        )
    elif isinstance(value, pd.DataFrame):
        if len(value.index) > max_rows:
            raise ResultRowLimitError(
                "The tabular result exceeds the configured row limit."
            )
        columns = [_column_name(column) for column in value.columns]
        if len(set(columns)) != len(columns):
            raise ResultSerializationError(
                "Table column names must be unique after conversion."
            )
        yield '{"columns":['
        for index, column in enumerate(columns):
            if index:
                yield ","
            yield from _json_string(column)
        yield '],"records":['
        for row_index, row in enumerate(
            value.itertuples(index=False, name=None)
        ):
            if row_index:
                yield ","
            yield "{"
            for column_index, (column, item) in enumerate(zip(columns, row)):
                if column_index:
                    yield ","
                yield from _json_string(column)
                yield ":"
                yield from _json_fragments(
                    item,
                    max_rows=max_rows,
                    depth=depth + 1,
                )
            yield "}"
        yield "]}"
    elif isinstance(value, pd.Series):
        if len(value.index) > max_rows:
            raise ResultRowLimitError(
                "The tabular result exceeds the configured row limit."
            )
        name = None if value.name is None else _column_name(value.name)
        yield '{"name":'
        yield from _json_fragments(name, max_rows=max_rows, depth=depth + 1)
        yield ',"values":['
        for index, item in enumerate(value.array):
            if index:
                yield ","
            yield from _json_fragments(
                item,
                max_rows=max_rows,
                depth=depth + 1,
            )
        yield "]}"
    elif isinstance(value, dict):
        yield "{"
        for index, (key, item) in enumerate(value.items()):
            if not isinstance(key, str):
                raise ResultSerializationError(
                    "Result object keys must be strings."
                )
            if index:
                yield ","
            yield from _json_string(key)
            yield ":"
            yield from _json_fragments(
                item,
                max_rows=max_rows,
                depth=depth + 1,
            )
        yield "}"
    elif isinstance(value, (list, tuple)):
        yield "["
        for index, item in enumerate(value):
            if index:
                yield ","
            yield from _json_fragments(
                item,
                max_rows=max_rows,
                depth=depth + 1,
            )
        yield "]"
    else:
        raise ResultSerializationError(
            "The analysis result contains an unsupported value."
        )


def bounded_result_json(value: Any, *, max_bytes: int, max_rows: int) -> str:
    """Serialize only supported JSON and tabular values under a byte limit."""
    output = bytearray()
    try:
        for fragment in _json_fragments(value, max_rows=max_rows):
            encoded = fragment.encode("utf-8")
            if len(output) + len(encoded) > max_bytes:
                raise ResultLimitError(
                    "The analysis result exceeds the configured size limit."
                )
            output.extend(encoded)
    except UnicodeEncodeError:
        raise ResultSerializationError(
            "The analysis result contains invalid Unicode text."
        ) from None
    return output.decode("utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-stdout-bytes", type=int, required=True)
    parser.add_argument("--max-stderr-bytes", type=int, required=True)
    parser.add_argument("--max-result-bytes", type=int, required=True)
    parser.add_argument("--max-result-rows", type=int, required=True)
    return parser


def _resource_snapshot() -> tuple[float, int]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return (
        (usage.ru_utime + usage.ru_stime) * 1000,
        int(usage.ru_maxrss) * 1024,
    )


def main() -> int:
    args = _build_parser().parse_args()
    protocol_stream = sys.stdout
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    stdout_writer = ProtocolTextWriter(
        protocol_stream,
        "stdout",
        args.max_stdout_bytes,
    )
    stderr_writer = ProtocolTextWriter(
        protocol_stream,
        "stderr",
        args.max_stderr_bytes,
    )
    started_cpu_ms, _ = _resource_snapshot()
    started_wall = time.monotonic()
    status = "completed"
    result_payload: str | None = None

    sys.stdout = stdout_writer
    sys.stderr = stderr_writer
    try:
        request_text = original_stdin_read()
        request = json.loads(request_text)
        if not isinstance(request, dict) or set(request) != {"code"}:
            raise ValueError("Invalid execution payload.")
        code = request["code"]
        if not isinstance(code, str):
            raise ValueError("Invalid execution payload.")

        dataframe = pd.read_csv("/input/dataset.csv")
        namespace: dict[str, Any] = {"df": dataframe}
        exec(compile(code, "<generated-analysis>", "exec"), namespace, namespace)
        if "result" not in namespace:
            raise ResultSerializationError(
                "The analysis code did not assign a result."
            )
        result_payload = bounded_result_json(
            namespace["result"],
            max_bytes=args.max_result_bytes,
            max_rows=args.max_result_rows,
        )
    except ResultSerializationError as exc:
        status = "failed"
        stderr_writer.write(f"Result serialization error: {str(exc)[:1000]}\n")
        _emit(
            protocol_stream,
            {
                "type": "execution_error",
                "code": (
                    "output_limit_exceeded"
                    if isinstance(exc, ResultLimitError)
                    else "execution_failed"
                ),
                "message": "The analysis result could not be captured within its limits.",
            },
        )
    except BaseException as exc:
        status = "failed"
        stderr_writer.write(
            f"{type(exc).__name__}: {str(exc)[:1000]}\n"
        )
        _emit(
            protocol_stream,
            {
                "type": "execution_error",
                "code": "execution_failed",
                "message": "The generated analysis code failed.",
            },
        )
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        stdout_writer.finish()
        stderr_writer.finish()

    if result_payload is not None:
        _emit(protocol_stream, {"type": "result_begin"})
        for offset in range(0, len(result_payload), _RESULT_CHUNK_CHARS):
            _emit(
                protocol_stream,
                {
                    "type": "result_chunk",
                    "content": result_payload[
                        offset : offset + _RESULT_CHUNK_CHARS
                    ],
                },
            )
        _emit(
            protocol_stream,
            {
                "type": "result_end",
                "size_bytes": len(result_payload.encode("utf-8")),
            },
        )

    cpu_ms, peak_memory_bytes = _resource_snapshot()
    _emit(
        protocol_stream,
        {
            "type": "done",
            "status": status,
            "cpu_time_ms": max(0.0, cpu_ms - started_cpu_ms),
            "peak_memory_bytes": peak_memory_bytes,
            "wall_time_ms": max(0.0, (time.monotonic() - started_wall) * 1000),
        },
    )
    return 0


def original_stdin_read() -> str:
    return sys.__stdin__.read()


if __name__ == "__main__":
    raise SystemExit(main())
