"""Technology-independent execution request, status, result, and error schemas."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Optional
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.core.execution_limits import (
    ABSOLUTE_EXECUTION_MAX_CODE_SIZE_BYTES,
    ABSOLUTE_EXECUTION_MAX_STDERR_BYTES,
    ABSOLUTE_EXECUTION_MAX_STDOUT_BYTES,
    ABSOLUTE_EXECUTION_MAX_TIMEOUT_SECONDS,
    DEFAULT_EXECUTION_MAX_CODE_SIZE_BYTES,
    DEFAULT_EXECUTION_MAX_STDERR_BYTES,
    DEFAULT_EXECUTION_MAX_STDOUT_BYTES,
    DEFAULT_EXECUTION_MAX_TIMEOUT_SECONDS,
    MAX_EXECUTION_ERROR_DETAILS_BYTES,
    MAX_EXECUTION_OUTPUT_TEXT_BYTES,
    MAX_EXECUTION_RESULT_BYTES,
)
from app.schemas.code_generation import CodeOperation
from app.schemas.common import DatasetId

_MAX_QUESTION_LENGTH = 2_000
_MAX_ERROR_MESSAGE_LENGTH = 2_000
_MAX_DETAIL_DEPTH = 4
_MAX_DETAIL_ITEMS = 50
_MAX_DETAIL_KEY_LENGTH = 100
_MAX_DETAIL_STRING_LENGTH = 1_000
_MAX_DETAILS_BYTES = MAX_EXECUTION_ERROR_DETAILS_BYTES

_UNSAFE_TEXT_PATTERNS = (
    re.compile(r"traceback \(most recent call last\)", re.IGNORECASE),
    re.compile(
        r"(?<![\w/])/(?:Users|Volumes|home|tmp|var|etc|root|opt|usr|private|"
        r"mnt|proc|sys)/",
        re.IGNORECASE,
    ),
    re.compile(r"\b[A-Za-z]:[\\/]"),
    re.compile(r"\b[A-Z][A-Z0-9_]{2,}\s*=\s*\S+"),
)

_FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "command",
        "cwd",
        "env",
        "environment",
        "executable",
        "file",
        "filename",
        "filepath",
        "password",
        "path",
        "secret",
        "shell",
        "stderr_path",
        "stdout_path",
        "token",
        "traceback",
        "workdir",
    }
)

_REDACTED_MESSAGE = "Execution error details were redacted for safety."
_OMITTED_VALUE = "[omitted]"
_REDACTED_VALUE = "[redacted]"


def validate_execution_id(value: object) -> UUID:
    """Return only a canonical server-generated execution identifier."""
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("Invalid execution identifier.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Invalid execution identifier.") from None
    if str(parsed) != value:
        raise ValueError("Invalid execution identifier.")
    return parsed


ExecutionId = Annotated[UUID, BeforeValidator(validate_execution_id)]


def _limit_from_context(
    info: ValidationInfo,
    *,
    setting_name: str,
    context_name: str,
    default: float,
) -> float:
    context = info.context if isinstance(info.context, dict) else {}
    settings = context.get("settings")
    if settings is not None:
        value = getattr(settings, setting_name, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    value = context.get(context_name)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _byte_length(value: str, label: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError(f"{label} must contain valid Unicode text.") from None


def _contains_unsafe_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in _UNSAFE_TEXT_PATTERNS)


def sanitize_execution_message(message: str) -> str:
    """Return a bounded message without stack traces, paths, or secrets."""
    text = message.strip()
    if not text:
        return "The execution failed."
    if _contains_unsafe_text(text):
        return _REDACTED_MESSAGE
    if len(text) > _MAX_ERROR_MESSAGE_LENGTH:
        return text[: _MAX_ERROR_MESSAGE_LENGTH - 3] + "..."
    return text


def _sanitize_detail_string(value: str) -> str:
    text = value[:_MAX_DETAIL_STRING_LENGTH]
    if _contains_unsafe_text(text):
        return _REDACTED_VALUE
    return text


def _sanitize_detail_value(value: Any, depth: int) -> JsonValue:
    if depth > _MAX_DETAIL_DEPTH:
        return _OMITTED_VALUE
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Execution error details must be finite.")
        return value
    if isinstance(value, str):
        return _sanitize_detail_string(value)
    if isinstance(value, dict):
        sanitized: dict[str, JsonValue] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_DETAIL_ITEMS:
                break
            if not isinstance(key, str):
                continue
            clean_key = key.strip()[:_MAX_DETAIL_KEY_LENGTH]
            if not clean_key or clean_key.lower() in _FORBIDDEN_DETAIL_KEYS:
                continue
            sanitized[clean_key] = _sanitize_detail_value(item, depth + 1)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_detail_value(item, depth + 1)
            for item in value[:_MAX_DETAIL_ITEMS]
        ]
    return _OMITTED_VALUE


def sanitize_execution_details(value: Any) -> Optional[dict[str, JsonValue]]:
    """Recursively remove or redact unsafe structured error details."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Execution error details must be an object.")
    sanitized = _sanitize_detail_value(value, 0)
    if not isinstance(sanitized, dict):
        raise ValueError("Execution error details must be an object.")
    return sanitized


class ExecutionMetadata(BaseModel):
    """Limited, non-executable context attached to an execution request."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    question: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_QUESTION_LENGTH,
    )
    operation: Optional[CodeOperation] = None


class ExecutionRequest(BaseModel):
    """A validated request for a future execution engine.

    This schema does not execute code. It only binds the candidate code to a
    dataset, a bounded timeout, and optional descriptive metadata.
    """

    model_config = ConfigDict(extra="forbid")

    execution_id: ExecutionId = Field(default_factory=uuid4)
    dataset_id: DatasetId
    code: str = Field(
        min_length=1,
        max_length=ABSOLUTE_EXECUTION_MAX_CODE_SIZE_BYTES,
    )
    timeout_seconds: float = Field(
        default=DEFAULT_EXECUTION_MAX_TIMEOUT_SECONDS,
        gt=0,
        le=ABSOLUTE_EXECUTION_MAX_TIMEOUT_SECONDS,
    )
    metadata: Optional[ExecutionMetadata] = None

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Execution code must not be empty.")
        return value

    @model_validator(mode="after")
    def validate_limits(self, info: ValidationInfo) -> "ExecutionRequest":
        max_code_bytes = _limit_from_context(
            info,
            setting_name="execution_max_code_size_bytes",
            context_name="max_code_size_bytes",
            default=DEFAULT_EXECUTION_MAX_CODE_SIZE_BYTES,
        )
        max_timeout = _limit_from_context(
            info,
            setting_name="execution_max_timeout_seconds",
            context_name="max_timeout_seconds",
            default=DEFAULT_EXECUTION_MAX_TIMEOUT_SECONDS,
        )
        code_size = _byte_length(self.code, "Execution code")
        if code_size > max_code_bytes:
            raise ValueError(
                "Execution code exceeds the configured size limit."
            )
        if self.timeout_seconds > max_timeout:
            raise ValueError(
                "Execution timeout exceeds the configured maximum."
            )
        return self

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        settings: Optional[Any] = None,
    ) -> "ExecutionRequest":
        """Validate request data against optional server settings."""
        context = {"settings": settings} if settings is not None else {}
        return cls.model_validate(payload, context=context)


class ExecutionStatus(str, Enum):
    """Lifecycle states shared by execution APIs and future workers."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

    @property
    def terminal(self) -> bool:
        """Return whether the status has no further normal transitions."""
        return not EXECUTION_STATUS_TRANSITIONS[self]

    def can_transition_to(self, target: "ExecutionStatus") -> bool:
        """Return whether this status may transition to the target status."""
        return target in EXECUTION_STATUS_TRANSITIONS[self]

    def require_transition_to(self, target: "ExecutionStatus") -> None:
        """Raise when a requested lifecycle transition is invalid."""
        validate_execution_status_transition(self, target)


EXECUTION_STATUS_TRANSITIONS: dict[
    ExecutionStatus,
    frozenset[ExecutionStatus],
] = {
    ExecutionStatus.PENDING: frozenset(
        {
            ExecutionStatus.QUEUED,
            ExecutionStatus.RUNNING,
            ExecutionStatus.REJECTED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.FAILED,
        }
    ),
    ExecutionStatus.QUEUED: frozenset(
        {
            ExecutionStatus.RUNNING,
            ExecutionStatus.REJECTED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.FAILED,
        }
    ),
    ExecutionStatus.RUNNING: frozenset(
        {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
            ExecutionStatus.CANCELLED,
        }
    ),
    ExecutionStatus.COMPLETED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
    ExecutionStatus.TIMED_OUT: frozenset(),
    ExecutionStatus.CANCELLED: frozenset(),
    ExecutionStatus.REJECTED: frozenset(),
}


def validate_execution_status_transition(
    current: ExecutionStatus,
    target: ExecutionStatus,
) -> None:
    """Reject lifecycle changes that are not defined by the shared model."""
    if not current.can_transition_to(target):
        raise ValueError(
            f"Execution status cannot transition from '{current.value}' "
            f"to '{target.value}'."
        )


def can_transition_execution_status(
    current: ExecutionStatus,
    target: ExecutionStatus,
) -> bool:
    """Return whether a lifecycle transition is allowed."""
    return current.can_transition_to(target)


class ExecutionStatusTransition(BaseModel):
    """A validated transition between two execution lifecycle states."""

    model_config = ConfigDict(extra="forbid")

    current: ExecutionStatus
    target: ExecutionStatus

    @model_validator(mode="after")
    def validate_transition(self) -> "ExecutionStatusTransition":
        validate_execution_status_transition(self.current, self.target)
        return self


class ExecutionErrorCode(str, Enum):
    """Stable application-level execution error identifiers."""

    INVALID_EXECUTION_REQUEST = "invalid_execution_request"
    DATASET_NOT_FOUND = "dataset_not_found"
    INVALID_GENERATED_CODE = "invalid_generated_code"
    EXECUTION_ENGINE_UNAVAILABLE = "execution_engine_unavailable"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_TIMEOUT = "execution_timeout"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    EXECUTION_REJECTED_BY_SECURITY_POLICY = (
        "execution_rejected_by_security_policy"
    )
    INTERNAL_EXECUTION_ERROR = "internal_execution_error"
    EXECUTION_CANCELLED = "execution_cancelled"


class ExecutionErrorCategory(str, Enum):
    """Stable classifications for execution failures and rejections."""

    INVALID_EXECUTION_REQUEST = "invalid_execution_request"
    DATASET_NOT_FOUND = "dataset_not_found"
    INVALID_GENERATED_CODE = "invalid_generated_code"
    EXECUTION_ENGINE_UNAVAILABLE = "execution_engine_unavailable"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_TIMEOUT = "execution_timeout"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    EXECUTION_REJECTED_BY_SECURITY_POLICY = (
        "execution_rejected_by_security_policy"
    )
    INTERNAL_EXECUTION_ERROR = "internal_execution_error"
    EXECUTION_CANCELLED = "execution_cancelled"


EXECUTION_ERROR_CATEGORY_BY_CODE: dict[
    ExecutionErrorCode,
    ExecutionErrorCategory,
] = {
    ExecutionErrorCode.INVALID_EXECUTION_REQUEST: (
        ExecutionErrorCategory.INVALID_EXECUTION_REQUEST
    ),
    ExecutionErrorCode.DATASET_NOT_FOUND: (
        ExecutionErrorCategory.DATASET_NOT_FOUND
    ),
    ExecutionErrorCode.INVALID_GENERATED_CODE: (
        ExecutionErrorCategory.INVALID_GENERATED_CODE
    ),
    ExecutionErrorCode.EXECUTION_ENGINE_UNAVAILABLE: (
        ExecutionErrorCategory.EXECUTION_ENGINE_UNAVAILABLE
    ),
    ExecutionErrorCode.EXECUTION_FAILED: (
        ExecutionErrorCategory.EXECUTION_FAILED
    ),
    ExecutionErrorCode.EXECUTION_TIMEOUT: (
        ExecutionErrorCategory.EXECUTION_TIMEOUT
    ),
    ExecutionErrorCode.OUTPUT_LIMIT_EXCEEDED: (
        ExecutionErrorCategory.OUTPUT_LIMIT_EXCEEDED
    ),
    ExecutionErrorCode.RESOURCE_LIMIT_EXCEEDED: (
        ExecutionErrorCategory.RESOURCE_LIMIT_EXCEEDED
    ),
    ExecutionErrorCode.EXECUTION_REJECTED_BY_SECURITY_POLICY: (
        ExecutionErrorCategory.EXECUTION_REJECTED_BY_SECURITY_POLICY
    ),
    ExecutionErrorCode.INTERNAL_EXECUTION_ERROR: (
        ExecutionErrorCategory.INTERNAL_EXECUTION_ERROR
    ),
    ExecutionErrorCode.EXECUTION_CANCELLED: (
        ExecutionErrorCategory.EXECUTION_CANCELLED
    ),
}


class ExecutionError(BaseModel):
    """A safe, structured execution error shared by future APIs."""

    model_config = ConfigDict(extra="forbid")

    code: ExecutionErrorCode
    message: str = Field(
        min_length=1,
        max_length=_MAX_ERROR_MESSAGE_LENGTH,
    )
    category: ExecutionErrorCategory
    details: Optional[dict[str, JsonValue]] = None
    retryable: bool = False

    @field_validator("message", mode="before")
    @classmethod
    def sanitize_message(cls, value: object) -> object:
        if isinstance(value, str):
            return sanitize_execution_message(value)
        return value

    @field_validator("details", mode="before")
    @classmethod
    def sanitize_details(cls, value: object) -> object:
        return sanitize_execution_details(value)

    @model_validator(mode="after")
    def validate_error(self) -> "ExecutionError":
        expected = EXECUTION_ERROR_CATEGORY_BY_CODE[self.code]
        if self.category is not expected:
            raise ValueError(
                "Execution error category does not match the error code."
            )
        if self.details is not None:
            payload = self.model_dump(mode="json", include={"details"})
            try:
                serialized = json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError):
                raise ValueError(
                    "Execution error details must be JSON-serializable."
                ) from None
            if len(serialized.encode("utf-8")) > _MAX_DETAILS_BYTES:
                raise ValueError("Execution error details are too large.")
        return self


class ExecutionResourceUsage(BaseModel):
    """Optional, non-negative resource measurements from an execution engine."""

    model_config = ConfigDict(extra="forbid")

    cpu_time_ms: Optional[float] = Field(default=None, ge=0)
    wall_time_ms: Optional[float] = Field(default=None, ge=0)
    peak_memory_bytes: Optional[int] = Field(default=None, ge=0)


class ExecutionOutput(BaseModel):
    """A bounded standard-output or standard-error representation."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(
        default="",
        max_length=MAX_EXECUTION_OUTPUT_TEXT_BYTES,
    )
    truncated: bool = False
    original_size_bytes: Optional[int] = Field(default=None, ge=0)

    @property
    def size_bytes(self) -> int:
        """Return the current UTF-8 content size."""
        return len(self.content.encode("utf-8"))

    @model_validator(mode="after")
    def validate_output(self) -> "ExecutionOutput":
        size = _byte_length(self.content, "Execution output")
        if size > MAX_EXECUTION_OUTPUT_TEXT_BYTES:
            raise ValueError("Execution output is too large.")
        if self.original_size_bytes is not None:
            if self.truncated and self.original_size_bytes <= size:
                raise ValueError(
                    "Truncated output must report a larger original size."
                )
            if not self.truncated and self.original_size_bytes != size:
                raise ValueError(
                    "Original output size must match untruncated content."
                )
        return self


def bounded_execution_output(content: str, max_bytes: int) -> ExecutionOutput:
    """Build a truncated output object without splitting UTF-8 sequences."""
    if max_bytes < 0:
        raise ValueError("Output limits must not be negative.")
    raw = content.encode("utf-8")
    limit = min(max_bytes, MAX_EXECUTION_OUTPUT_TEXT_BYTES)
    if len(raw) <= limit:
        return ExecutionOutput(
            content=content,
            truncated=False,
            original_size_bytes=len(raw),
        )
    clipped = raw[:limit].decode("utf-8", errors="ignore")
    return ExecutionOutput(
        content=clipped,
        truncated=True,
        original_size_bytes=len(raw),
    )


_PRE_EXECUTION_ERROR_CODES = frozenset(
    {
        ExecutionErrorCode.INVALID_EXECUTION_REQUEST,
        ExecutionErrorCode.DATASET_NOT_FOUND,
        ExecutionErrorCode.INVALID_GENERATED_CODE,
        ExecutionErrorCode.EXECUTION_ENGINE_UNAVAILABLE,
        ExecutionErrorCode.EXECUTION_REJECTED_BY_SECURITY_POLICY,
    }
)


class ExecutionResult(BaseModel):
    """A normalized execution result independent of any execution technology."""

    model_config = ConfigDict(extra="forbid")

    execution_id: ExecutionId
    dataset_id: DatasetId
    status: ExecutionStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = Field(default=None, ge=0)
    stdout: ExecutionOutput = Field(default_factory=ExecutionOutput)
    stderr: ExecutionOutput = Field(default_factory=ExecutionOutput)
    result: Optional[JsonValue] = None
    error: Optional[ExecutionError] = None
    resource_usage: Optional[ExecutionResourceUsage] = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def require_timezone(
        cls,
        value: Optional[datetime],
    ) -> Optional[datetime]:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("Execution timestamps must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def validate_result(self, info: ValidationInfo) -> "ExecutionResult":
        max_stdout = _limit_from_context(
            info,
            setting_name="execution_max_stdout_bytes",
            context_name="max_stdout_bytes",
            default=DEFAULT_EXECUTION_MAX_STDOUT_BYTES,
        )
        max_stderr = _limit_from_context(
            info,
            setting_name="execution_max_stderr_bytes",
            context_name="max_stderr_bytes",
            default=DEFAULT_EXECUTION_MAX_STDERR_BYTES,
        )
        if self.stdout.size_bytes > max_stdout:
            raise ValueError(
                "Standard output exceeds the configured size limit."
            )
        if self.stderr.size_bytes > max_stderr:
            raise ValueError(
                "Standard error exceeds the configured size limit."
            )
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError(
                "Execution completion time cannot precede its start time."
            )

        non_terminal = {
            ExecutionStatus.PENDING,
            ExecutionStatus.QUEUED,
            ExecutionStatus.RUNNING,
        }
        if self.status in non_terminal:
            if self.error is not None or self.result is not None:
                raise ValueError(
                    "An unfinished execution cannot have a result or error."
                )
            if self.completed_at is not None or self.duration_ms is not None:
                raise ValueError(
                    "An unfinished execution cannot have completion data."
                )
        if self.status in {
            ExecutionStatus.PENDING,
            ExecutionStatus.QUEUED,
            ExecutionStatus.REJECTED,
        } and self.started_at is not None:
            raise ValueError(
                "A pre-execution status cannot have a start time."
            )

        failure_statuses = {
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.REJECTED,
        }
        if self.status in failure_statuses and self.result is not None:
            raise ValueError(
                "A non-completed execution cannot include a result."
            )

        if self.status is ExecutionStatus.COMPLETED:
            if self.result is None:
                raise ValueError(
                    "A completed execution must include a result."
                )
            if self.error is not None:
                raise ValueError(
                    "A completed execution cannot include an error."
                )

        if self.status in {
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
            ExecutionStatus.REJECTED,
        } and self.error is None:
            raise ValueError(
                "A failed, timed-out, or rejected execution must include "
                "a structured error."
            )

        if self.status is ExecutionStatus.TIMED_OUT:
            if (
                self.error is None
                or self.error.code is not ExecutionErrorCode.EXECUTION_TIMEOUT
                or self.error.category
                is not ExecutionErrorCategory.EXECUTION_TIMEOUT
            ):
                raise ValueError(
                    "A timed-out execution must report an execution timeout."
                )
        elif self.status is ExecutionStatus.FAILED and self.error is not None:
            if self.error.code in {
                ExecutionErrorCode.EXECUTION_TIMEOUT,
                ExecutionErrorCode.EXECUTION_CANCELLED,
            }:
                raise ValueError(
                    "A failed execution cannot report a timeout or "
                    "cancellation."
                )
        elif self.status is ExecutionStatus.REJECTED and self.error is not None:
            if self.error.code not in _PRE_EXECUTION_ERROR_CODES:
                raise ValueError(
                    "A rejected execution must report a pre-execution error."
                )
        elif self.status is ExecutionStatus.CANCELLED and self.error is not None:
            if self.error.code is not ExecutionErrorCode.EXECUTION_CANCELLED:
                raise ValueError(
                    "A cancelled execution may only report a cancellation "
                    "error."
                )

        payload = self.model_dump(mode="json")
        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            raise ValueError(
                "Execution results must contain only JSON-serializable values."
            ) from None
        if len(serialized.encode("utf-8")) > MAX_EXECUTION_RESULT_BYTES:
            raise ValueError("Execution result is too large.")
        return self

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        settings: Optional[Any] = None,
    ) -> "ExecutionResult":
        """Validate result data against optional server settings."""
        context = {"settings": settings} if settings is not None else {}
        return cls.model_validate(payload, context=context)
