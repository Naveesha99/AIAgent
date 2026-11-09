"""Docker-backed execution service with bounded output and lifecycle tracking."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.core.config import Settings
from app.core.sandbox import (
    SandboxConfigurationError,
    SandboxContainerSpec,
    classify_sandbox_termination,
    sandbox_cleanup_args,
)
from app.schemas.execution import (
    ExecutionError,
    ExecutionErrorCode,
    ExecutionOutput,
    ExecutionRequest,
    ExecutionResourceUsage,
    ExecutionResult,
    ExecutionStatus,
    validate_execution_status_transition,
)
from app.services.dataset_storage import (
    DatasetNotFoundError,
    DatasetStorage,
    DatasetStorageError,
)
from app.services.execution_service import ExecutionService, ExecutionServiceError
from app.services.sandbox_config import SandboxConfigurationService

logger = logging.getLogger(__name__)

_DOCKER_COMMAND_TIMEOUT_SECONDS = 10.0
_DOCKER_DIAGNOSTIC_LIMIT_BYTES = 16_384
_MAX_PROTOCOL_FRAME_BYTES = 64 * 1024
_MAX_TRACKED_EXECUTIONS = 1_000
_OUTPUT_PROTOCOL_OVERHEAD = 64 * 1024


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _error(
    code: ExecutionErrorCode,
    message: str,
    *,
    retryable: bool = False,
) -> ExecutionError:
    from app.schemas.execution import EXECUTION_ERROR_CATEGORY_BY_CODE

    return ExecutionError(
        code=code,
        category=EXECUTION_ERROR_CATEGORY_BY_CODE[code],
        message=message,
        retryable=retryable,
    )


@dataclass
class _CapturedStream:
    max_bytes: int
    content: bytearray = field(default_factory=bytearray)
    original_size_bytes: Optional[int] = None
    truncated: bool = False

    def append(self, text: str) -> None:
        encoded = text.encode("utf-8", errors="replace")
        remaining = self.max_bytes - len(self.content)
        if remaining > 0:
            retained = encoded[:remaining]
            self.content.extend(retained.decode("utf-8", errors="ignore").encode("utf-8"))
        if len(encoded) > remaining:
            self.truncated = True

    def finish(self, size_bytes: object, truncated: object) -> None:
        if isinstance(size_bytes, int) and not isinstance(size_bytes, bool):
            self.original_size_bytes = max(0, size_bytes)
        if isinstance(truncated, bool):
            self.truncated = self.truncated or truncated

    def to_model(self) -> ExecutionOutput:
        return ExecutionOutput(
            content=self.content.decode("utf-8", errors="ignore"),
            truncated=self.truncated,
            original_size_bytes=self.original_size_bytes,
        )


class _ProtocolCapture:
    """Incrementally parse bounded worker frames without retaining raw logs."""

    def __init__(
        self,
        *,
        stdout_limit: int,
        stderr_limit: int,
        result_limit: int,
    ) -> None:
        self.streams = {
            "stdout": _CapturedStream(stdout_limit),
            "stderr": _CapturedStream(stderr_limit),
        }
        self.result_limit = result_limit
        self.result_bytes = bytearray()
        self.has_result = False
        self.done: Optional[dict[str, Any]] = None
        self.execution_error: Optional[dict[str, Any]] = None
        self.protocol_error = False
        self._buffer = bytearray()
        self._result_started = False
        self._result_overflow = False

    def feed(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) > _MAX_PROTOCOL_FRAME_BYTES:
                    self.protocol_error = True
                    self._buffer.clear()
                return
            frame = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if len(frame) > _MAX_PROTOCOL_FRAME_BYTES:
                self.protocol_error = True
                continue
            self._consume_frame(frame)

    def finish(self) -> None:
        if self._buffer:
            self.protocol_error = True

    def _consume_frame(self, frame: bytes) -> None:
        try:
            event = json.loads(frame)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.protocol_error = True
            return
        if not isinstance(event, dict):
            self.protocol_error = True
            return
        event_type = event.get("type")
        if event_type == "output":
            stream_name = event.get("stream")
            content = event.get("content")
            stream = self.streams.get(stream_name)
            if stream is None or not isinstance(content, str):
                self.protocol_error = True
                return
            stream.append(content)
        elif event_type == "stream_end":
            stream = self.streams.get(event.get("stream"))
            if stream is None:
                self.protocol_error = True
                return
            stream.finish(event.get("size_bytes"), event.get("truncated"))
        elif event_type == "result_begin":
            self._result_started = True
            self.result_bytes.clear()
        elif event_type == "result_chunk":
            content = event.get("content")
            if not self._result_started or not isinstance(content, str):
                self.protocol_error = True
                return
            encoded = content.encode("utf-8")
            if len(self.result_bytes) + len(encoded) > self.result_limit:
                self._result_overflow = True
                return
            self.result_bytes.extend(encoded)
        elif event_type == "result_end":
            if not self._result_started or self._result_overflow:
                self.protocol_error = self.protocol_error or self._result_overflow
                return
            try:
                self._result = json.loads(
                    self.result_bytes.decode("utf-8"),
                    parse_constant=lambda _value: (_ for _ in ()).throw(
                        ValueError("Non-finite JSON value.")
                    ),
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                self.protocol_error = True
                return
            reported_size = event.get("size_bytes")
            if (
                isinstance(reported_size, bool)
                or not isinstance(reported_size, int)
                or reported_size != len(self.result_bytes)
            ):
                self.protocol_error = True
                return
            self.has_result = True
            self._result_started = False
        elif event_type == "execution_error":
            self.execution_error = event
        elif event_type == "done":
            self.done = event
        else:
            self.protocol_error = True

    @property
    def result(self) -> Any:
        return getattr(self, "_result", None)


@dataclass
class _ExecutionRecord:
    execution_id: UUID
    dataset_id: UUID
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[ExecutionResult] = None
    container_name: Optional[str] = None
    process: Optional[asyncio.subprocess.Process] = None
    cancel_requested: bool = False
    completed: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task[ExecutionResult]] = None


class DockerSandboxExecutionService(ExecutionService):
    """Execute one request per isolated Docker container."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sandbox = SandboxConfigurationService(settings)
        self._storage = DatasetStorage(
            settings.dataset_storage_dir,
            ttl_hours=settings.dataset_ttl_hours,
        )
        self._records: dict[UUID, _ExecutionRecord] = {}
        self._record_order: list[UUID] = []
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        execution_id = uuid4()
        record = _ExecutionRecord(
            execution_id=execution_id,
            dataset_id=request.dataset_id,
        )
        await self._register(record)
        return await self._run_registered(request, record)

    async def submit(self, request: ExecutionRequest) -> UUID:
        """Queue a request using a server-generated identifier."""
        execution_id = uuid4()
        record = _ExecutionRecord(
            execution_id=execution_id,
            dataset_id=request.dataset_id,
        )
        await self._register(record)
        record.task = asyncio.create_task(self._run_registered(request, record))
        return execution_id

    async def _register(self, record: _ExecutionRecord) -> None:
        async with self._get_lock():
            self._records[record.execution_id] = record
            self._record_order.append(record.execution_id)
            self._prune_records()

    async def _run_registered(
        self,
        request: ExecutionRequest,
        record: _ExecutionRecord,
    ) -> ExecutionResult:
        if record.status.terminal and record.result is not None:
            return record.result
        if record.cancel_requested:
            return await self._finish(
                record,
                ExecutionStatus.CANCELLED,
                error=_error(
                    ExecutionErrorCode.EXECUTION_CANCELLED,
                    "The execution was cancelled.",
                ),
            )
        normalized_request = request.model_copy(
            update={"execution_id": record.execution_id}
        )
        try:
            normalized_request = ExecutionRequest.from_payload(
                normalized_request.model_dump(mode="python"),
                self._settings,
            )
        except ValidationError:
            return await self._finish(
                record,
                ExecutionStatus.REJECTED,
                error=_error(
                    ExecutionErrorCode.INVALID_EXECUTION_REQUEST,
                    "The execution request exceeds configured limits.",
                ),
            )

        await self._transition(record, ExecutionStatus.QUEUED)
        if record.cancel_requested:
            return await self._finish(
                record,
                ExecutionStatus.CANCELLED,
                error=_error(
                    ExecutionErrorCode.EXECUTION_CANCELLED,
                    "The execution was cancelled.",
                ),
            )

        staged_directory: Optional[tempfile.TemporaryDirectory[str]] = None
        try:
            try:
                staged_directory, dataset_file = await asyncio.to_thread(
                    self._stage_dataset,
                    normalized_request.dataset_id,
                    record.execution_id,
                )
            except DatasetNotFoundError:
                return await self._finish(
                    record,
                    ExecutionStatus.REJECTED,
                    error=_error(
                        ExecutionErrorCode.DATASET_NOT_FOUND,
                        "The dataset does not exist or has expired.",
                    ),
                )
            except (DatasetStorageError, OSError):
                return await self._finish(
                    record,
                    ExecutionStatus.REJECTED,
                    error=_error(
                        ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
                        "The dataset could not be staged for execution.",
                    ),
                )

            spec = self._sandbox.build_container_spec(
                dataset_file=dataset_file,
                dataset_root=Path(staged_directory.name),
                execution_id=record.execution_id,
            )
            if record.cancel_requested:
                return await self._finish(
                    record,
                    ExecutionStatus.CANCELLED,
                    error=_error(
                        ExecutionErrorCode.EXECUTION_CANCELLED,
                        "The execution was cancelled.",
                    ),
                )
            timeout_seconds = self._sandbox.effective_timeout_seconds(
                normalized_request.timeout_seconds
            )
            return await self._run_container(
                record,
                normalized_request,
                spec,
                timeout_seconds,
            )
        except SandboxConfigurationError:
            return await self._finish(
                record,
                ExecutionStatus.REJECTED,
                error=_error(
                    ExecutionErrorCode.EXECUTION_REJECTED_BY_SECURITY_POLICY,
                    "The execution was rejected by sandbox policy.",
                ),
            )
        except asyncio.CancelledError:
            record.cancel_requested = True
            if record.container_name is not None:
                await self._kill_container(record.container_name)
            return await self._finish(
                record,
                ExecutionStatus.CANCELLED,
                error=_error(
                    ExecutionErrorCode.EXECUTION_CANCELLED,
                    "The execution was cancelled.",
                ),
            )
        except Exception:
            logger.exception(
                "Sandbox execution failed unexpectedly for execution %s.",
                record.execution_id,
            )
            if not record.status.terminal:
                return await self._finish(
                    record,
                    ExecutionStatus.FAILED,
                    started_at=record.started_at,
                    error=_error(
                        ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
                        "The execution failed because of an internal error.",
                    ),
                )
            raise
        finally:
            if staged_directory is not None:
                await asyncio.to_thread(staged_directory.cleanup)

    def _stage_dataset(
        self,
        dataset_id: UUID,
        execution_id: UUID,
    ) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        directory = tempfile.TemporaryDirectory(
            prefix=f"ai-data-analyst-{execution_id}-"
        )
        root = Path(directory.name)
        path = root / "dataset.csv"
        try:
            with self._storage.open_dataset(dataset_id) as source:
                with path.open("xb") as destination:
                    shutil.copyfileobj(source, destination, length=64 * 1024)
            path.chmod(0o444)
            return directory, path
        except BaseException:
            directory.cleanup()
            raise

    async def _run_container(
        self,
        record: _ExecutionRecord,
        request: ExecutionRequest,
        spec: SandboxContainerSpec,
        timeout_seconds: float,
    ) -> ExecutionResult:
        record.container_name = spec.name
        capture = _ProtocolCapture(
            stdout_limit=min(
                spec.limits.max_stdout_bytes,
                self._settings.execution_max_stdout_bytes,
            ),
            stderr_limit=min(
                spec.limits.max_stderr_bytes,
                self._settings.execution_max_stderr_bytes,
            ),
            result_limit=self._settings.execution_max_result_bytes,
        )
        docker_stderr = bytearray()
        diagnostic_limit = _DOCKER_DIAGNOSTIC_LIMIT_BYTES
        start_monotonic = asyncio.get_running_loop().time()
        timed_out = False
        infrastructure_error: Optional[ExecutionError] = None
        state: dict[str, Any] = {}
        stdout_task: Optional[asyncio.Task[None]] = None
        stderr_task: Optional[asyncio.Task[None]] = None
        process: Optional[asyncio.subprocess.Process] = None
        await self._transition(record, ExecutionStatus.RUNNING, started_at=_utcnow())

        command = [
            "docker",
            "run",
            "--interactive",
            "--no-healthcheck",
            *spec.docker_run_options(),
            "--entrypoint",
            "python",
            spec.image,
            "-m",
            "worker.execute",
            "--max-stdout-bytes",
            str(capture.streams["stdout"].max_bytes),
            "--max-stderr-bytes",
            str(capture.streams["stderr"].max_bytes),
            "--max-result-bytes",
            str(self._settings.execution_max_result_bytes),
            "--max-result-rows",
            str(self._settings.execution_max_result_rows),
        ]
        protocol_limit = (
            6
            * (
                capture.streams["stdout"].max_bytes
                + capture.streams["stderr"].max_bytes
                + self._settings.execution_max_result_bytes
            )
            + _OUTPUT_PROTOCOL_OVERHEAD
        )

        try:
            async with self._get_lock():
                cancelled_before_start = record.cancel_requested
                if not cancelled_before_start:
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        limit=min(protocol_limit, 4_194_304),
                    )
                    record.process = process
            if cancelled_before_start:
                return await self._finish(
                    record,
                    ExecutionStatus.CANCELLED,
                    error=_error(
                        ExecutionErrorCode.EXECUTION_CANCELLED,
                        "The execution was cancelled.",
                    ),
                )

            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None

            async def read_protocol() -> None:
                read_total = 0
                while True:
                    chunk = await process.stdout.read(64 * 1024)
                    if not chunk:
                        break
                    read_total += len(chunk)
                    if read_total <= protocol_limit:
                        capture.feed(chunk)
                    else:
                        capture.protocol_error = True
                capture.finish()

            async def read_docker_stderr() -> None:
                while True:
                    chunk = await process.stderr.read(64 * 1024)
                    if not chunk:
                        break
                    remaining = diagnostic_limit - len(docker_stderr)
                    if remaining > 0:
                        docker_stderr.extend(chunk[:remaining])

            stdout_task = asyncio.create_task(read_protocol())
            stderr_task = asyncio.create_task(read_docker_stderr())
            process.stdin.write(
                json.dumps(
                    {"code": request.code},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            await process.stdin.drain()
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                timed_out = True
                await self._kill_container(spec.name)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            await asyncio.gather(stdout_task, stderr_task)
            state = await self._inspect_container(spec.name)
        except FileNotFoundError:
            infrastructure_error = _error(
                ExecutionErrorCode.EXECUTION_ENGINE_UNAVAILABLE,
                "The Docker execution engine is unavailable.",
                retryable=True,
            )
        except (OSError, asyncio.SubprocessError, asyncio.TimeoutError):
            infrastructure_error = _error(
                ExecutionErrorCode.EXECUTION_ENGINE_UNAVAILABLE,
                "The Docker execution engine could not complete the request.",
                retryable=True,
            )
        finally:
            if process is not None and process.returncode is None:
                if not timed_out:
                    await self._kill_container(spec.name)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            for task in (stdout_task, stderr_task):
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            cleanup_ok = await self._remove_container(spec.name)
            record.process = None

        elapsed_ms = max(
            0.0,
            (asyncio.get_running_loop().time() - start_monotonic) * 1000,
        )
        exit_code = state.get("ExitCode")
        oom_killed = state.get("OOMKilled") is True
        cancelled = record.cancel_requested
        if infrastructure_error is not None:
            status = ExecutionStatus.FAILED
            error = infrastructure_error
        elif cancelled:
            status = ExecutionStatus.CANCELLED
            error = _error(
                ExecutionErrorCode.EXECUTION_CANCELLED,
                "The execution was cancelled.",
            )
        elif timed_out:
            status = ExecutionStatus.TIMED_OUT
            error = _error(
                ExecutionErrorCode.EXECUTION_TIMEOUT,
                "The execution exceeded the configured time limit.",
                retryable=True,
            )
        elif oom_killed:
            termination = classify_sandbox_termination(
                exit_code=exit_code if isinstance(exit_code, int) else 137,
                oom_killed=True,
            )
            status = ExecutionStatus.FAILED
            error = self._sandbox.error_for_termination(termination)
        elif (
            process is not None
            and process.returncode == 0
            and capture.done is not None
            and capture.done.get("status") == "completed"
            and capture.execution_error is None
            and capture.has_result
            and not capture.protocol_error
        ):
            status = ExecutionStatus.COMPLETED
            error = None
        elif (
            process is not None
            and process.returncode == 0
            and capture.execution_error is not None
            and not capture.protocol_error
        ):
            status = ExecutionStatus.FAILED
            code = capture.execution_error.get("code")
            if code == ExecutionErrorCode.OUTPUT_LIMIT_EXCEEDED.value:
                error = _error(
                    ExecutionErrorCode.OUTPUT_LIMIT_EXCEEDED,
                    "The analysis result exceeded a configured capture limit.",
                )
            else:
                error = _error(
                    ExecutionErrorCode.EXECUTION_FAILED,
                    "The generated analysis code failed.",
                )
        elif not state:
            status = ExecutionStatus.FAILED
            error = _error(
                ExecutionErrorCode.EXECUTION_ENGINE_UNAVAILABLE,
                "Docker could not start the execution container.",
                retryable=True,
            )
        else:
            status = ExecutionStatus.FAILED
            error = _error(
                ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
                "The execution container stopped unexpectedly.",
            )

        if not cleanup_ok:
            logger.warning(
                "Could not remove sandbox container for execution %s.",
                record.execution_id,
            )
            if error is None:
                status = ExecutionStatus.FAILED
                error = _error(
                    ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
                    "The execution completed but container cleanup failed.",
                )

        resource_usage = ExecutionResourceUsage(
            cpu_time_ms=_optional_number(
                (capture.done or {}).get("cpu_time_ms")
            ),
            wall_time_ms=elapsed_ms,
            peak_memory_bytes=_optional_int(
                (capture.done or {}).get("peak_memory_bytes")
            ),
            exit_code=exit_code if _is_exit_code(exit_code) else None,
            termination_reason=_termination_reason(
                status=status,
                oom_killed=oom_killed,
                exit_code=exit_code,
                infrastructure_error=infrastructure_error is not None,
            ),
        )
        completed_at = _utcnow()
        return await self._finish(
            record,
            status,
            started_at=record.started_at,
            completed_at=completed_at,
            duration_ms=elapsed_ms,
            stdout=capture.streams["stdout"].to_model(),
            stderr=capture.streams["stderr"].to_model(),
            result=capture.result if status is ExecutionStatus.COMPLETED else None,
            error=error,
            resource_usage=resource_usage,
        )

    async def _inspect_container(self, name: str) -> dict[str, Any]:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "--format",
                "{{json .State}}",
                name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=_DOCKER_COMMAND_TIMEOUT_SECONDS,
            )
            if process.returncode != 0:
                return {}
            state = json.loads(stdout)
            return state if isinstance(state, dict) else {}
        except (FileNotFoundError, OSError, asyncio.TimeoutError, json.JSONDecodeError):
            return {}

    async def _kill_container(self, name: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "kill",
                "--signal=KILL",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(
                process.wait(),
                timeout=_DOCKER_COMMAND_TIMEOUT_SECONDS,
            )
        except (FileNotFoundError, OSError, asyncio.TimeoutError):
            logger.warning("Docker kill request failed for a sandbox container.")

    async def _remove_container(self, name: str) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                *sandbox_cleanup_args(self._execution_id_from_name(name)),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return_code = await asyncio.wait_for(
                process.wait(),
                timeout=_DOCKER_COMMAND_TIMEOUT_SECONDS,
            )
            return return_code == 0
        except (FileNotFoundError, OSError, asyncio.TimeoutError):
            return False

    @staticmethod
    def _execution_id_from_name(name: str) -> UUID:
        from app.core.sandbox import canonical_execution_id

        prefix = "ai-data-analyst-sandbox-"
        if not name.startswith(prefix):
            raise SandboxConfigurationError("Invalid generated container name.")
        return canonical_execution_id(name[len(prefix) :])

    async def _transition(
        self,
        record: _ExecutionRecord,
        target: ExecutionStatus,
        *,
        started_at: Optional[datetime] = None,
    ) -> None:
        async with self._get_lock():
            validate_execution_status_transition(record.status, target)
            record.status = target
            if started_at is not None:
                record.started_at = started_at

    async def _finish(
        self,
        record: _ExecutionRecord,
        status: ExecutionStatus,
        *,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        duration_ms: Optional[float] = None,
        stdout: Optional[ExecutionOutput] = None,
        stderr: Optional[ExecutionOutput] = None,
        result: Any = None,
        error: Optional[ExecutionError] = None,
        resource_usage: Optional[ExecutionResourceUsage] = None,
    ) -> ExecutionResult:
        async with self._get_lock():
            target_status = record.status if record.status.terminal else status
            if not record.status.terminal:
                validate_execution_status_transition(record.status, target_status)
            resolved_completed_at = completed_at or _utcnow()
            payload: dict[str, Any] = {
                "execution_id": record.execution_id,
                "dataset_id": record.dataset_id,
                "status": target_status,
                "started_at": started_at or record.started_at,
                "completed_at": resolved_completed_at,
                "duration_ms": duration_ms,
                "stdout": stdout or ExecutionOutput(),
                "stderr": stderr or ExecutionOutput(),
                "error": error,
                "resource_usage": resource_usage,
            }
            if target_status is ExecutionStatus.COMPLETED:
                payload["result"] = result
            execution_result = ExecutionResult.from_payload(
                payload,
                self._settings,
            )
            record.status = target_status
            record.completed_at = resolved_completed_at
            record.result = execution_result
            record.completed.set()
            return execution_result

    async def get_status(self, execution_id: UUID) -> ExecutionStatus:
        async with self._get_lock():
            record = self._records.get(execution_id)
            if record is None:
                raise ExecutionServiceError(
                    _error(
                        ExecutionErrorCode.INVALID_EXECUTION_REQUEST,
                        "The execution identifier is unknown or expired.",
                    )
                )
            return record.status

    async def get_result(
        self,
        execution_id: UUID,
    ) -> Optional[ExecutionResult]:
        async with self._get_lock():
            record = self._records.get(execution_id)
            if record is None:
                raise ExecutionServiceError(
                    _error(
                        ExecutionErrorCode.INVALID_EXECUTION_REQUEST,
                        "The execution identifier is unknown or expired.",
                    )
                )
            return record.result

    async def cancel(self, execution_id: UUID) -> ExecutionResult:
        async with self._get_lock():
            record = self._records.get(execution_id)
            if record is None:
                raise ExecutionServiceError(
                    _error(
                        ExecutionErrorCode.INVALID_EXECUTION_REQUEST,
                        "The execution identifier is unknown or expired.",
                    )
                )
            if record.status.terminal and record.result is not None:
                return record.result
            record.cancel_requested = True
            name = record.container_name
            process = record.process
        if name is not None:
            await self._kill_container(name)
        if not record.completed.is_set():
            try:
                await asyncio.wait_for(record.completed.wait(), timeout=15)
            except asyncio.TimeoutError:
                logger.error(
                    "Execution %s did not complete after cancellation.",
                    record.execution_id,
                )
        if record.result is not None:
            return record.result
        return await self._finish(
            record,
            ExecutionStatus.CANCELLED,
            error=_error(
                ExecutionErrorCode.EXECUTION_CANCELLED,
                "The execution was cancelled.",
            ),
        )

    def _prune_records(self) -> None:
        while len(self._record_order) > _MAX_TRACKED_EXECUTIONS:
            candidate = self._record_order[0]
            record = self._records.get(candidate)
            if record is not None and not record.status.terminal:
                self._record_order.append(self._record_order.pop(0))
                if all(
                    not item.status.terminal for item in self._records.values()
                ):
                    break
                continue
            self._record_order.pop(0)
            self._records.pop(candidate, None)


def _optional_number(value: object) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    return None


def _optional_int(value: object) -> Optional[int]:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return None


def _is_exit_code(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255


def _termination_reason(
    *,
    status: ExecutionStatus,
    oom_killed: bool,
    exit_code: object,
    infrastructure_error: bool,
) -> str:
    if infrastructure_error:
        return "docker_unavailable"
    if status is ExecutionStatus.TIMED_OUT:
        return "timeout"
    if status is ExecutionStatus.CANCELLED:
        return "cancelled"
    if oom_killed:
        return "out_of_memory"
    if not _is_exit_code(exit_code):
        return "container_error"
    if exit_code == 0:
        return "normal_exit"
    return "nonzero_exit"
