"""Tests for the Docker execution lifecycle and bounded result protocol."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import pytest
from fastapi.responses import JSONResponse

from app.api.routes.executions import get_execution_result
from app.core.config import Settings
from app.schemas.execution import (
    ExecutionErrorCode,
    ExecutionRequest,
    ExecutionStatus,
)
from app.core.sandbox import sandbox_container_name
from app.services.dataset_storage import DatasetStorage
from app.services.docker_execution_service import DockerSandboxExecutionService

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOCKER_IMAGE = "ai-data-analyst-worker:dev"


class _FakeReader:
    def __init__(self, value: bytes) -> None:
        self._value = value

    async def read(self, _size: int = -1) -> bytes:
        value, self._value = self._value, b""
        return value


class _FakeStdin:
    def write(self, _value: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        returncode: Optional[int] = 0,
        wait_event: Optional[asyncio.Event] = None,
    ) -> None:
        self.stdout = _FakeReader(stdout)
        self.stderr = _FakeReader(b"")
        self.stdin = _FakeStdin()
        self.returncode = returncode
        self._wait_event = wait_event

    async def wait(self) -> int:
        if self._wait_event is not None:
            await self._wait_event.wait()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return await self.stdout.read(), await self.stderr.read()

    def kill(self) -> None:
        self.returncode = -9
        if self._wait_event is not None:
            self._wait_event.set()


def _frame(**event: Any) -> bytes:
    return (
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _worker_success_frames(
    *,
    stdout: str = "stdout line\n",
    stderr: str = "stderr line\n",
    result: Any = None,
    include_result: bool = True,
) -> bytes:
    output = bytearray()
    for name, value in (("stdout", stdout), ("stderr", stderr)):
        if value:
            output.extend(_frame(type="output", stream=name, content=value))
        output.extend(
            _frame(
                type="stream_end",
                stream=name,
                size_bytes=len(value.encode("utf-8")),
                truncated=False,
            )
        )
    if include_result:
        encoded_result = json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        output.extend(_frame(type="result_begin"))
        output.extend(
            _frame(type="result_chunk", content=encoded_result)
        )
        output.extend(
            _frame(
                type="result_end",
                size_bytes=len(encoded_result.encode("utf-8")),
            )
        )
    output.extend(
        _frame(
            type="done",
            status="completed" if include_result else "failed",
            cpu_time_ms=12.5,
            peak_memory_bytes=4_096,
            wall_time_ms=25.0,
        )
    )
    return bytes(output)


def _stored_dataset(tmp_path: Path) -> tuple[UUID, Settings]:
    storage_dir = tmp_path / "datasets"
    storage = DatasetStorage(storage_dir)
    staged = storage.stage_upload(
        BytesIO(b"value,label\n1,a\n2,b\n"),
        max_size_bytes=100,
    )
    stored = storage.finalize(staged)
    return stored.dataset_id, Settings(
        _env_file=None,
        dataset_storage_dir=storage_dir,
    )


def _request(dataset_id: UUID, **overrides: Any) -> ExecutionRequest:
    values: dict[str, Any] = {
        "dataset_id": dataset_id,
        "code": "result = df.shape",
        "timeout_seconds": 5,
    }
    values.update(overrides)
    return ExecutionRequest(**values)


def test_docker_execution_captures_streams_result_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, settings = _stored_dataset(tmp_path)
    service = DockerSandboxExecutionService(settings)
    calls: list[list[str]] = []
    frames = _worker_success_frames(
        result={
            "table": {
                "columns": ["value", "label"],
                "records": [
                    {"value": 1, "label": "a"},
                    {"value": 2, "label": "b"},
                ],
            },
            "none": None,
        }
    )

    async def fake_create(*args: str, **kwargs: Any) -> _FakeProcess:
        command = list(args)
        calls.append(command)
        if command[1] == "run":
            return _FakeProcess(stdout=frames, returncode=0)
        if command[1] == "inspect":
            return _FakeProcess(
                stdout=b'{"ExitCode":0,"OOMKilled":false}\n',
                returncode=0,
            )
        if command[1] == "rm":
            return _FakeProcess(returncode=0)
        raise AssertionError(f"Unexpected Docker operation: {command[1]}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    result = asyncio.run(service.execute(_request(dataset_id)))

    assert result.status is ExecutionStatus.COMPLETED
    assert result.execution_id != _request(dataset_id).execution_id
    assert result.stdout.content == "stdout line\n"
    assert result.stderr.content == "stderr line\n"
    assert result.stdout.original_size_bytes == len("stdout line\n")
    assert result.result == {
        "table": {
            "columns": ["value", "label"],
            "records": [
                {"value": 1, "label": "a"},
                {"value": 2, "label": "b"},
            ],
        },
        "none": None,
    }
    assert result.started_at is not None
    assert result.completed_at is not None
    assert result.duration_ms is not None
    assert result.resource_usage is not None
    assert result.resource_usage.cpu_time_ms == 12.5
    assert result.resource_usage.peak_memory_bytes == 4_096
    assert result.resource_usage.exit_code == 0
    assert result.resource_usage.termination_reason == "normal_exit"
    assert any(command[1:3] == ["rm", "--force"] for command in calls)
    assert asyncio.run(
        get_execution_result(result.execution_id, service)
    ) == result
    run_command = next(command for command in calls if command[1] == "run")
    assert "--network=none" in run_command
    assert "--read-only" in run_command
    assert "--cap-drop=ALL" in run_command


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_output_is_truncated_at_the_server_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream_name: str,
) -> None:
    dataset_id, base_settings = _stored_dataset(tmp_path)
    settings = base_settings.model_copy(
        update={
            f"execution_max_{stream_name}_bytes": 4,
            f"sandbox_max_{stream_name}_bytes": 100,
        }
    )
    service = DockerSandboxExecutionService(settings)
    frames = bytearray()
    frames.extend(
        _frame(
            type="output",
            stream=stream_name,
            content="abcdefgh",
        )
    )
    for current_stream in ("stdout", "stderr"):
        is_truncated_stream = current_stream == stream_name
        frames.extend(
            _frame(
                type="stream_end",
                stream=current_stream,
                size_bytes=8 if is_truncated_stream else 0,
                truncated=is_truncated_stream,
            )
        )
    frames.extend(_frame(type="result_begin"))
    frames.extend(_frame(type="result_chunk", content="true"))
    frames.extend(_frame(type="result_end", size_bytes=4))
    frames.extend(
        _frame(
            type="done",
            status="completed",
            cpu_time_ms=1,
            peak_memory_bytes=1_024,
            wall_time_ms=2,
        )
    )

    async def fake_create(*args: str, **_kwargs: Any) -> _FakeProcess:
        if args[1] == "run":
            return _FakeProcess(stdout=bytes(frames), returncode=0)
        if args[1] == "inspect":
            return _FakeProcess(
                stdout=b'{"ExitCode":0,"OOMKilled":false}\n',
                returncode=0,
            )
        return _FakeProcess(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    result = asyncio.run(service.execute(_request(dataset_id)))

    assert result.status is ExecutionStatus.COMPLETED
    captured_stream = getattr(result, stream_name)
    assert captured_stream.content == "abcd"
    assert captured_stream.truncated is True
    assert captured_stream.original_size_bytes == 8


def test_out_of_memory_termination_is_a_resource_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, settings = _stored_dataset(tmp_path)
    service = DockerSandboxExecutionService(settings)

    async def fake_create(*args: str, **_kwargs: Any) -> _FakeProcess:
        if args[1] == "run":
            return _FakeProcess(
                stdout=_worker_success_frames(result=1),
                returncode=137,
            )
        if args[1] == "inspect":
            return _FakeProcess(
                stdout=b'{"ExitCode":137,"OOMKilled":true}\n',
                returncode=0,
            )
        if args[1] == "rm":
            return _FakeProcess(returncode=0)
        raise AssertionError(f"Unexpected Docker operation: {args[1]}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    result = asyncio.run(service.execute(_request(dataset_id)))

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.code is ExecutionErrorCode.RESOURCE_LIMIT_EXCEEDED
    assert result.resource_usage is not None
    assert result.resource_usage.termination_reason == "out_of_memory"


def test_timeout_kills_and_cleans_the_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, base_settings = _stored_dataset(tmp_path)
    settings = base_settings.model_copy(
        update={"sandbox_execution_timeout_seconds": 0.05}
    )
    service = DockerSandboxExecutionService(settings)
    wait_event: Optional[asyncio.Event] = None
    run_process: Optional[_FakeProcess] = None
    operations: list[str] = []
    partial_output = _frame(
        type="output",
        stream="stdout",
        content="before timeout\n",
    )

    async def fake_create(*args: str, **_kwargs: Any) -> _FakeProcess:
        nonlocal run_process
        operation = args[1]
        operations.append(operation)
        if operation == "run":
            run_process = _FakeProcess(
                stdout=partial_output,
                returncode=None,
                wait_event=wait_event,
            )
            return run_process
        if operation == "kill":
            assert run_process is not None
            run_process.kill()
            return _FakeProcess(returncode=0)
        if operation == "inspect":
            return _FakeProcess(
                stdout=b'{"ExitCode":137,"OOMKilled":false}\n',
                returncode=0,
            )
        if operation == "rm":
            return _FakeProcess(returncode=0)
        raise AssertionError(f"Unexpected Docker operation: {operation}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    async def scenario() -> Any:
        nonlocal wait_event
        wait_event = asyncio.Event()
        return await service.execute(_request(dataset_id))

    result = asyncio.run(scenario())

    assert result.status is ExecutionStatus.TIMED_OUT
    assert result.error is not None
    assert result.error.code is ExecutionErrorCode.EXECUTION_TIMEOUT
    assert result.stdout.content == "before timeout\n"
    assert "kill" in operations
    assert "rm" in operations
    assert result.resource_usage is not None
    assert result.resource_usage.termination_reason == "timeout"


def test_cancelled_execution_reaches_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, settings = _stored_dataset(tmp_path)
    service = DockerSandboxExecutionService(settings)
    wait_event: Optional[asyncio.Event] = None
    run_process: Optional[_FakeProcess] = None
    operations: list[str] = []

    async def fake_create(*args: str, **_kwargs: Any) -> _FakeProcess:
        nonlocal run_process
        operation = args[1]
        operations.append(operation)
        if operation == "run":
            run_process = _FakeProcess(
                stdout=b"",
                returncode=None,
                wait_event=wait_event,
            )
            return run_process
        if operation == "kill":
            assert run_process is not None
            run_process.kill()
            return _FakeProcess(returncode=0)
        if operation == "inspect":
            return _FakeProcess(
                stdout=b'{"ExitCode":137,"OOMKilled":false}\n',
                returncode=0,
            )
        if operation == "rm":
            return _FakeProcess(returncode=0)
        raise AssertionError(f"Unexpected Docker operation: {operation}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    async def scenario() -> Any:
        nonlocal wait_event
        wait_event = asyncio.Event()
        execution_id = await service.submit(_request(dataset_id))
        for _ in range(100):
            if service._records[execution_id].process is not None:
                break
            await asyncio.sleep(0.005)
        pending_response = await get_execution_result(execution_id, service)
        assert isinstance(pending_response, JSONResponse)
        assert pending_response.status_code == 202
        assert json.loads(pending_response.body)["execution_id"] == str(
            execution_id
        )
        return await service.cancel(execution_id)

    result = asyncio.run(scenario())

    assert result.status is ExecutionStatus.CANCELLED
    assert result.error is not None
    assert result.error.code is ExecutionErrorCode.EXECUTION_CANCELLED
    assert "kill" in operations
    assert "rm" in operations


def test_docker_startup_failure_is_a_structured_infrastructure_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id, settings = _stored_dataset(tmp_path)
    service = DockerSandboxExecutionService(settings)

    async def fail_start(*_args: str, **_kwargs: Any) -> _FakeProcess:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_start)

    result = asyncio.run(service.execute(_request(dataset_id)))

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.code is ExecutionErrorCode.EXECUTION_ENGINE_UNAVAILABLE
    assert result.resource_usage is not None
    assert result.resource_usage.termination_reason == "docker_unavailable"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    completed = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return completed.returncode == 0


def _image_available() -> bool:
    completed = subprocess.run(
        ["docker", "image", "inspect", DOCKER_IMAGE],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return completed.returncode == 0


requires_docker = pytest.mark.skipif(
    not _docker_available() or not _image_available(),
    reason="Docker and the worker image are required for integration tests.",
)


@requires_docker
def test_real_docker_execution_and_timeout(
    tmp_path: Path,
) -> None:
    dataset_id, settings = _stored_dataset(tmp_path)
    service = DockerSandboxExecutionService(settings)

    completed = asyncio.run(
        service.execute(
            _request(
                dataset_id,
                code=(
                    "import sys\n"
                    "print('out line')\n"
                    "print('err line', file=sys.stderr)\n"
                    "result = {'total': int(df['value'].sum()), "
                    "'ok': True, 'nothing': None, 'items': [1, 'x'], "
                    "'table': df.head(1)}\n"
                ),
            )
        )
    )
    assert completed.status is ExecutionStatus.COMPLETED, completed.model_dump(
        mode="json"
    )
    assert completed.stdout.content == "out line\n"
    assert completed.stderr.content == "err line\n"
    assert completed.result == {
        "total": 3,
        "ok": True,
        "nothing": None,
        "items": [1, "x"],
        "table": {
            "columns": ["value", "label"],
            "records": [{"value": 1, "label": "a"}],
        },
    }

    failed = asyncio.run(
        service.execute(
            _request(
                dataset_id,
                code="raise ValueError('controlled failure')",
            )
        )
    )
    assert failed.status is ExecutionStatus.FAILED
    assert failed.error is not None
    assert failed.error.code is ExecutionErrorCode.EXECUTION_FAILED
    assert "ValueError: controlled failure" in failed.stderr.content

    timed_settings = settings.model_copy(
        update={
            "execution_max_timeout_seconds": 1.0,
            "sandbox_execution_timeout_seconds": 1.0,
        }
    )
    timed_service = DockerSandboxExecutionService(timed_settings)
    timed_out = asyncio.run(
        timed_service.execute(
            _request(
                dataset_id,
                timeout_seconds=1,
                code=(
                    "import subprocess, sys, time\n"
                    "subprocess.Popen([sys.executable, '-c', "
                    "'import time; time.sleep(20)'])\n"
                    "print('before timeout', flush=True)\n"
                    "time.sleep(20)\n"
                    "result = 1\n"
                ),
            )
        )
    )
    assert timed_out.status is ExecutionStatus.TIMED_OUT
    assert timed_out.stdout.content == "before timeout\n"
    assert timed_out.error is not None
    assert timed_out.error.code is ExecutionErrorCode.EXECUTION_TIMEOUT
    inspect_after_cleanup = subprocess.run(
        ["docker", "inspect", sandbox_container_name(timed_out.execution_id)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert inspect_after_cleanup.returncode != 0


@requires_docker
def test_real_docker_rejects_non_finite_and_oversized_results(
    tmp_path: Path,
) -> None:
    dataset_id, settings = _stored_dataset(tmp_path)
    service = DockerSandboxExecutionService(settings)

    non_finite = asyncio.run(
        service.execute(
            _request(dataset_id, code="result = float('nan')")
        )
    )
    assert non_finite.status is ExecutionStatus.FAILED
    assert non_finite.error is not None
    assert non_finite.error.code is ExecutionErrorCode.EXECUTION_FAILED

    row_limited_settings = settings.model_copy(
        update={"execution_max_result_rows": 1}
    )
    row_limited = asyncio.run(
        DockerSandboxExecutionService(row_limited_settings).execute(
            _request(dataset_id, code="result = df")
        )
    )
    assert row_limited.status is ExecutionStatus.FAILED
    assert row_limited.error is not None
    assert row_limited.error.code is ExecutionErrorCode.OUTPUT_LIMIT_EXCEEDED

    size_limited_settings = settings.model_copy(
        update={"execution_max_result_bytes": 64}
    )
    size_limited = asyncio.run(
        DockerSandboxExecutionService(size_limited_settings).execute(
            _request(dataset_id, code="result = 'x' * 100")
        )
    )
    assert size_limited.status is ExecutionStatus.FAILED
    assert size_limited.error is not None
    assert (
        size_limited.error.code
        is ExecutionErrorCode.OUTPUT_LIMIT_EXCEEDED
    )
