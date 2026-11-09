"""Tests for Phase 5.1 execution contracts and service boundaries."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.execution_limits import (
    DEFAULT_EXECUTION_MAX_CODE_SIZE_BYTES,
    DEFAULT_EXECUTION_MAX_STDERR_BYTES,
    DEFAULT_EXECUTION_MAX_STDOUT_BYTES,
    DEFAULT_EXECUTION_MAX_TIMEOUT_SECONDS,
)
from app.main import app
from app.schemas.execution import (
    EXECUTION_ERROR_CATEGORY_BY_CODE,
    EXECUTION_STATUS_TRANSITIONS,
    ExecutionError,
    ExecutionErrorCategory,
    ExecutionErrorCode,
    ExecutionOutput,
    ExecutionRequest,
    ExecutionResourceUsage,
    ExecutionResult,
    ExecutionStatus,
    ExecutionStatusTransition,
    bounded_execution_output,
    validate_execution_status_transition,
)
from app.services.execution_service import (
    ExecutionEngineUnavailableError,
    ExecutionService,
    UnavailableExecutionService,
    create_execution_service,
)
from app.services.docker_execution_service import DockerSandboxExecutionService

client = TestClient(app)

EXECUTION_ENVIRONMENT_VARIABLES = (
    "EXECUTION_ENABLED",
    "EXECUTION_MAX_TIMEOUT_SECONDS",
    "EXECUTION_MAX_CODE_SIZE_BYTES",
    "EXECUTION_MAX_STDOUT_BYTES",
    "EXECUTION_MAX_STDERR_BYTES",
)


@pytest.fixture(autouse=True)
def clear_execution_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in EXECUTION_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def dataset_id() -> str:
    return str(uuid4())


def execution_id() -> str:
    return str(uuid4())


def request_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dataset_id": dataset_id(),
        "code": "result = df.shape",
        "timeout_seconds": 10,
        "metadata": {
            "question": "What shape is the dataset?",
            "operation": "summarize",
        },
    }
    payload.update(overrides)
    return payload


def error_for(
    code: ExecutionErrorCode = ExecutionErrorCode.EXECUTION_FAILED,
    *,
    message: str = "The execution failed.",
    details: Optional[dict[str, Any]] = None,
    retryable: bool = False,
) -> ExecutionError:
    return ExecutionError(
        code=code,
        message=message,
        category=EXECUTION_ERROR_CATEGORY_BY_CODE[code],
        details=details,
        retryable=retryable,
    )


def result_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "execution_id": execution_id(),
        "dataset_id": dataset_id(),
        "status": "completed",
        "started_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:00:01Z",
        "duration_ms": 1000,
        "stdout": {"content": "done\n", "truncated": False},
        "stderr": {"content": "", "truncated": False},
        "result": {"rows": 2},
    }
    payload.update(overrides)
    return payload


def test_execution_configuration_defaults_and_bounds() -> None:
    settings = Settings(_env_file=None)

    assert settings.execution_enabled is False
    assert (
        settings.execution_max_timeout_seconds
        == DEFAULT_EXECUTION_MAX_TIMEOUT_SECONDS
    )
    assert (
        settings.execution_max_code_size_bytes
        == DEFAULT_EXECUTION_MAX_CODE_SIZE_BYTES
    )
    assert (
        settings.execution_max_stdout_bytes
        == DEFAULT_EXECUTION_MAX_STDOUT_BYTES
    )
    assert (
        settings.execution_max_stderr_bytes
        == DEFAULT_EXECUTION_MAX_STDERR_BYTES
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"execution_max_timeout_seconds": 0},
        {"execution_max_timeout_seconds": 601},
        {"execution_max_code_size_bytes": 0},
        {"execution_max_code_size_bytes": 1_000_001},
        {"execution_max_stdout_bytes": 0},
        {"execution_max_stdout_bytes": 2_000_001},
        {"execution_max_stderr_bytes": 0},
        {"execution_max_stderr_bytes": 2_000_001},
    ],
)
def test_invalid_execution_configuration_is_rejected(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_valid_execution_request_generates_identifier() -> None:
    request = ExecutionRequest.from_payload(
        request_payload(),
        Settings(_env_file=None),
    )

    assert isinstance(request.execution_id, UUID)
    assert request.execution_id != request.dataset_id
    assert request.code == "result = df.shape"
    assert request.metadata is not None
    assert request.metadata.operation == "summarize"


@pytest.mark.parametrize("missing", ["dataset_id", "code"])
def test_execution_request_requires_core_fields(missing: str) -> None:
    payload = request_payload()
    payload.pop(missing)

    with pytest.raises(ValidationError):
        ExecutionRequest.from_payload(payload)


@pytest.mark.parametrize(
    "invalid_id",
    [
        "not-a-uuid",
        "{dataset_id}",
    ],
)
def test_execution_request_rejects_invalid_dataset_ids(
    invalid_id: str,
) -> None:
    payload = request_payload(
        dataset_id=invalid_id.format(dataset_id=str(uuid4()).upper())
    )

    with pytest.raises(ValidationError):
        ExecutionRequest.from_payload(payload)


@pytest.mark.parametrize("code", ["", "   ", "\n\t"])
def test_execution_request_rejects_empty_code(code: str) -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest.from_payload(request_payload(code=code))


def test_execution_request_rejects_oversized_code_by_default() -> None:
    payload = request_payload(
        code="x" * (DEFAULT_EXECUTION_MAX_CODE_SIZE_BYTES + 1)
    )

    with pytest.raises(ValidationError):
        ExecutionRequest.from_payload(payload)


def test_execution_request_uses_configured_code_limit() -> None:
    settings = Settings(
        _env_file=None,
        execution_max_code_size_bytes=10,
    )

    request = ExecutionRequest.from_payload(
        request_payload(code="x" * 10),
        settings,
    )
    assert request.code == "x" * 10

    with pytest.raises(ValidationError):
        ExecutionRequest.from_payload(
            request_payload(code="x" * 11),
            settings,
        )


@pytest.mark.parametrize("timeout", [0, -1, 601])
def test_execution_request_rejects_invalid_timeouts(timeout: float) -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest.from_payload(
            request_payload(timeout_seconds=timeout)
        )


def test_execution_request_uses_configured_timeout_limit() -> None:
    settings = Settings(
        _env_file=None,
        execution_max_timeout_seconds=5,
    )

    request = ExecutionRequest.from_payload(
        request_payload(timeout_seconds=5),
        settings,
    )
    assert request.timeout_seconds == 5

    with pytest.raises(ValidationError):
        ExecutionRequest.from_payload(
            request_payload(timeout_seconds=5.1),
            settings,
        )


@pytest.mark.parametrize(
    "extra",
    [
        {"shell": "bash"},
        {"env": {"SECRET": "value"}},
        {"workdir": "/tmp"},
        {"docker_image": "python:latest"},
        {"memory_limit_bytes": 100},
        {"network_enabled": True},
        {"command": "rm -rf /"},
    ],
)
def test_execution_request_rejects_unrestricted_settings(
    extra: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest.from_payload(request_payload(**extra))


def test_execution_metadata_rejects_unexpected_fields() -> None:
    payload = request_payload(
        metadata={
            "question": "Summarize.",
            "operation": "summarize",
            "environment": {"TOKEN": "secret"},
        }
    )

    with pytest.raises(ValidationError):
        ExecutionRequest.from_payload(payload)


def test_execution_request_rejects_noncanonical_execution_id() -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest.from_payload(
            request_payload(execution_id=execution_id().upper())
        )


def test_all_execution_statuses_and_terminals_are_defined() -> None:
    assert {status.value for status in ExecutionStatus} == {
        "pending",
        "queued",
        "running",
        "completed",
        "failed",
        "timed_out",
        "cancelled",
        "rejected",
    }
    assert set(EXECUTION_STATUS_TRANSITIONS) == set(ExecutionStatus)
    assert ExecutionStatus.PENDING.terminal is False
    assert ExecutionStatus.RUNNING.terminal is False
    for status in {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.REJECTED,
    }:
        assert status.terminal is True
        assert EXECUTION_STATUS_TRANSITIONS[status] == frozenset()


def test_invalid_execution_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ExecutionStatusTransition(current="running", target="unknown")


def test_valid_execution_status_transitions() -> None:
    assert ExecutionStatus.PENDING.can_transition_to(
        ExecutionStatus.QUEUED
    )
    assert ExecutionStatus.QUEUED.can_transition_to(
        ExecutionStatus.RUNNING
    )
    assert ExecutionStatus.RUNNING.can_transition_to(
        ExecutionStatus.COMPLETED
    )
    assert ExecutionStatus.RUNNING.can_transition_to(
        ExecutionStatus.TIMED_OUT
    )
    transition = ExecutionStatusTransition(
        current="queued",
        target="running",
    )
    assert transition.current is ExecutionStatus.QUEUED
    assert transition.target is ExecutionStatus.RUNNING


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ExecutionStatus.COMPLETED, ExecutionStatus.RUNNING),
        (ExecutionStatus.FAILED, ExecutionStatus.COMPLETED),
        (ExecutionStatus.REJECTED, ExecutionStatus.QUEUED),
        (ExecutionStatus.RUNNING, ExecutionStatus.PENDING),
        (ExecutionStatus.PENDING, ExecutionStatus.COMPLETED),
        (ExecutionStatus.RUNNING, ExecutionStatus.RUNNING),
    ],
)
def test_invalid_execution_status_transitions(
    current: ExecutionStatus,
    target: ExecutionStatus,
) -> None:
    assert current.can_transition_to(target) is False
    with pytest.raises(ValueError):
        validate_execution_status_transition(current, target)
    with pytest.raises(ValidationError):
        ExecutionStatusTransition(current=current, target=target)


def test_successful_execution_result() -> None:
    result = ExecutionResult.from_payload(result_payload())

    assert result.status is ExecutionStatus.COMPLETED
    assert result.result == {"rows": 2}
    assert result.stdout.content == "done\n"
    assert result.stdout.size_bytes == 5
    assert result.started_at is not None
    assert result.started_at.tzinfo is not None
    assert result.duration_ms == 1000
    assert result.error is None


def test_failed_execution_result() -> None:
    result = ExecutionResult(
        execution_id=execution_id(),
        dataset_id=dataset_id(),
        status=ExecutionStatus.FAILED,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        duration_ms=10,
        stderr=ExecutionOutput(content="division by zero\n"),
        error=error_for(message="The execution failed."),
    )

    assert result.error is not None
    assert result.error.code is ExecutionErrorCode.EXECUTION_FAILED
    assert result.result is None


def test_timed_out_execution_result() -> None:
    result = ExecutionResult(
        execution_id=execution_id(),
        dataset_id=dataset_id(),
        status=ExecutionStatus.TIMED_OUT,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        duration_ms=30_000,
        error=error_for(
            ExecutionErrorCode.EXECUTION_TIMEOUT,
            message="The execution exceeded its timeout.",
            retryable=True,
        ),
    )

    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.EXECUTION_TIMEOUT


def test_rejected_execution_result() -> None:
    result = ExecutionResult(
        execution_id=execution_id(),
        dataset_id=dataset_id(),
        status=ExecutionStatus.REJECTED,
        error=error_for(
            ExecutionErrorCode.EXECUTION_REJECTED_BY_SECURITY_POLICY,
            message="The execution request was rejected.",
        ),
    )

    assert result.started_at is None
    assert result.completed_at is None
    assert result.error is not None


def test_execution_result_allows_missing_optional_fields() -> None:
    result = ExecutionResult(
        execution_id=execution_id(),
        dataset_id=dataset_id(),
        status=ExecutionStatus.COMPLETED,
        result={},
    )

    assert result.started_at is None
    assert result.completed_at is None
    assert result.duration_ms is None
    assert result.resource_usage is None
    assert result.stdout.content == ""
    assert result.stderr.content == ""


@pytest.mark.parametrize("field_name", ["started_at", "completed_at"])
def test_execution_result_rejects_naive_timestamps(field_name: str) -> None:
    payload = result_payload(**{field_name: "2026-01-01T00:00:00"})

    with pytest.raises(ValidationError):
        ExecutionResult.from_payload(payload)


def test_execution_result_rejects_completion_before_start() -> None:
    payload = result_payload(
        completed_at="2025-12-31T23:59:59Z",
    )

    with pytest.raises(ValidationError):
        ExecutionResult.from_payload(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"duration_ms": -1},
        {"resource_usage": {"cpu_time_ms": -1}},
        {"resource_usage": {"wall_time_ms": -1}},
        {"resource_usage": {"peak_memory_bytes": -1}},
    ],
)
def test_execution_result_rejects_invalid_durations_and_usage(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ExecutionResult.from_payload(result_payload(**overrides))


def test_execution_output_truncation_metadata() -> None:
    stdout = ExecutionOutput(
        content="line one\nline two\n",
        truncated=True,
        original_size_bytes=10_000,
    )

    assert stdout.content == "line one\nline two\n"
    assert stdout.truncated is True
    assert stdout.original_size_bytes == 10_000


def test_bounded_execution_output_preserves_content_and_utf8() -> None:
    ordinary = bounded_execution_output("one\ntwo\n", 100)
    assert ordinary.content == "one\ntwo\n"
    assert ordinary.truncated is False
    assert ordinary.original_size_bytes == 8

    truncated = bounded_execution_output("abcdef\n", 3)
    assert truncated.content == "abc"
    assert truncated.truncated is True
    assert truncated.original_size_bytes == 7
    assert truncated.size_bytes <= 3


@pytest.mark.parametrize(
    "field_name",
    [
        "stdout",
        "stderr",
    ],
)
def test_execution_result_rejects_oversized_output(field_name: str) -> None:
    limit = (
        DEFAULT_EXECUTION_MAX_STDOUT_BYTES
        if field_name == "stdout"
        else DEFAULT_EXECUTION_MAX_STDERR_BYTES
    )
    payload = result_payload(
        **{field_name: {"content": "x" * (limit + 1)}}
    )

    with pytest.raises(ValidationError):
        ExecutionResult.from_payload(payload)


def test_execution_result_uses_configured_output_limits() -> None:
    settings = Settings(
        _env_file=None,
        execution_max_stdout_bytes=5,
        execution_max_stderr_bytes=4,
    )

    valid = ExecutionResult.from_payload(
        result_payload(
            stdout={"content": "12345"},
            stderr={"content": "1234"},
        ),
        settings,
    )
    assert valid.stdout.content == "12345"

    with pytest.raises(ValidationError):
        ExecutionResult.from_payload(
            result_payload(stdout={"content": "123456"}),
            settings,
        )
    with pytest.raises(ValidationError):
        ExecutionResult.from_payload(
            result_payload(stderr={"content": "12345"}),
            settings,
        )


def test_execution_output_rejects_inconsistent_metadata() -> None:
    with pytest.raises(ValidationError):
        ExecutionOutput(
            content="short",
            truncated=False,
            original_size_bytes=100,
        )
    with pytest.raises(ValidationError):
        ExecutionOutput(
            content="short",
            truncated=True,
            original_size_bytes=5,
        )


def test_execution_result_is_json_serializable() -> None:
    result = ExecutionResult.from_payload(
        result_payload(
            result={
                "rows": 2,
                "values": [1, 2.5, True, None],
            },
            resource_usage={
                "cpu_time_ms": 12.5,
                "wall_time_ms": 20,
                "peak_memory_bytes": 1_024,
            },
        )
    )

    payload = result.model_dump(mode="json")
    serialized = json.dumps(payload, allow_nan=False)

    assert payload["status"] == "completed"
    assert payload["resource_usage"]["peak_memory_bytes"] == 1_024
    assert '"rows": 2' in serialized


def test_execution_result_rejects_non_json_values() -> None:
    with pytest.raises(ValidationError):
        ExecutionResult.from_payload(
            result_payload(result=float("nan"))
        )


def test_completed_execution_may_return_json_null() -> None:
    result = ExecutionResult.from_payload(result_payload(result=None))

    assert result.status is ExecutionStatus.COMPLETED
    assert result.result is None


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "status": "completed",
            "result": {},
            "error": error_for().model_dump(mode="json"),
        },
        {
            "status": "failed",
            "result": None,
            "error": None,
        },
        {
            "status": "failed",
            "result": {"partial": True},
            "error": error_for().model_dump(mode="json"),
        },
        {
            "status": "rejected",
            "error": None,
        },
        {
            "status": "timed_out",
            "error": error_for().model_dump(mode="json"),
        },
        {
            "status": "pending",
            "result": None,
            "completed_at": "2026-01-01T00:00:01Z",
        },
        {
            "status": "running",
            "result": {"premature": True},
        },
        {
            "status": "pending",
            "started_at": "2026-01-01T00:00:00Z",
        },
    ],
)
def test_contradictory_execution_results_are_rejected(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ExecutionResult.from_payload(result_payload(**overrides))


def test_cancelled_execution_result_can_omit_error() -> None:
    result = ExecutionResult(
        execution_id=execution_id(),
        dataset_id=dataset_id(),
        status=ExecutionStatus.CANCELLED,
    )

    assert result.error is None
    assert result.result is None


@pytest.mark.parametrize(
    ("code", "category"),
    list(EXECUTION_ERROR_CATEGORY_BY_CODE.items()),
)
def test_each_execution_error_category_and_code(
    code: ExecutionErrorCode,
    category: ExecutionErrorCategory,
) -> None:
    error = error_for(code)

    assert error.code is code
    assert error.category is category
    assert error.retryable is False
    assert json.dumps(error.model_dump(mode="json"))


def test_execution_error_retryable_flag() -> None:
    error = error_for(
        ExecutionErrorCode.EXECUTION_ENGINE_UNAVAILABLE,
        message="The execution engine is temporarily unavailable.",
        retryable=True,
    )

    assert error.retryable is True


def test_execution_error_rejects_mismatched_category() -> None:
    with pytest.raises(ValidationError):
        ExecutionError(
            code=ExecutionErrorCode.EXECUTION_FAILED,
            category=ExecutionErrorCategory.INVALID_EXECUTION_REQUEST,
            message="The execution failed.",
        )


def test_execution_error_sanitizes_messages_and_details() -> None:
    error = ExecutionError(
        code=ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
        category=ExecutionErrorCategory.INTERNAL_EXECUTION_ERROR,
        message=(
            "Traceback (most recent call last): "
            "/Users/example/secret.py API_KEY=super-secret"
        ),
        details={
            "path": "/Users/example/private.csv",
            "token": "super-secret",
            "reason": "/Users/example/private.csv",
            "safe": "value",
            "nested": {"workdir": "/tmp", "count": 2},
        },
    )

    payload = error.model_dump(mode="json")

    assert "Traceback" not in payload["message"]
    assert "/Users/" not in payload["message"]
    assert "super-secret" not in payload["message"]
    assert payload["details"] == {
        "reason": "[redacted]",
        "safe": "value",
        "nested": {"count": 2},
    }


def test_execution_error_rejects_nonfinite_or_oversized_details() -> None:
    with pytest.raises(ValidationError):
        error_for(
            details={"metric": float("inf")},
        )

    with pytest.raises(ValidationError):
        error_for(
            details={
                f"field_{index}": "x" * 1_000
                for index in range(25)
            },
        )


class MinimalExecutionService(ExecutionService):
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            execution_id=request.execution_id,
            dataset_id=request.dataset_id,
            status=ExecutionStatus.COMPLETED,
            result={"accepted": True},
        )


def test_execution_interface_contract() -> None:
    request = ExecutionRequest.from_payload(request_payload())
    service = MinimalExecutionService()

    result = asyncio.run(service.execute(request))

    assert result.execution_id == request.execution_id
    assert result.dataset_id == request.dataset_id
    assert result.status is ExecutionStatus.COMPLETED
    assert result.result == {"accepted": True}


def test_execution_interface_rejects_unimplemented_operations() -> None:
    service = MinimalExecutionService()

    with pytest.raises(ExecutionEngineUnavailableError):
        asyncio.run(service.get_status(uuid4()))
    with pytest.raises(ExecutionEngineUnavailableError):
        asyncio.run(service.cancel(uuid4()))


@pytest.mark.parametrize("operation", ["execute", "status", "cancel"])
def test_unavailable_execution_service_is_explicit(operation: str) -> None:
    service = UnavailableExecutionService("No engine is installed.")
    request = ExecutionRequest.from_payload(request_payload())

    with pytest.raises(ExecutionEngineUnavailableError) as caught:
        if operation == "execute":
            asyncio.run(service.execute(request))
        elif operation == "status":
            asyncio.run(service.get_status(request.execution_id))
        else:
            asyncio.run(service.cancel(request.execution_id))

    assert (
        caught.value.error.code
        is ExecutionErrorCode.EXECUTION_ENGINE_UNAVAILABLE
    )
    assert (
        caught.value.error.category
        is ExecutionErrorCategory.EXECUTION_ENGINE_UNAVAILABLE
    )
    assert "No engine is installed" in caught.value.error.message


def test_execution_is_disabled_by_default_and_factory_is_unavailable() -> None:
    settings = Settings(_env_file=None)
    service = create_execution_service(settings)
    request = ExecutionRequest.from_payload(request_payload())

    assert settings.execution_enabled is False
    assert isinstance(service, UnavailableExecutionService)
    with pytest.raises(ExecutionEngineUnavailableError):
        asyncio.run(service.execute(request))


def test_enabling_configuration_creates_the_docker_executor() -> None:
    settings = Settings(_env_file=None, execution_enabled=True)
    service = create_execution_service(settings)

    assert isinstance(service, DockerSandboxExecutionService)


def test_execution_factory_accepts_an_future_engine() -> None:
    settings = Settings(_env_file=None)
    engine = MinimalExecutionService()

    assert create_execution_service(settings, engine=engine) is engine


def test_execution_api_route_is_gated_when_execution_is_disabled() -> None:
    response = client.post(
        "/api/v1/executions",
        json={
            "dataset_id": dataset_id(),
            "code": "result = df.shape",
            "timeout_seconds": 10,
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "execution_engine_unavailable"
    assert client.post(
        f"/api/v1/datasets/{dataset_id()}/execute"
    ).status_code == 404
