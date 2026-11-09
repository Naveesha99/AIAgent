"""Technology-independent execution service contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from app.core.config import Settings
from app.schemas.execution import (
    ExecutionError,
    ExecutionErrorCategory,
    ExecutionErrorCode,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)


class ExecutionServiceError(Exception):
    """A service failure carrying a safe structured execution error."""

    def __init__(self, error: ExecutionError) -> None:
        super().__init__(error.message)
        self.error = error


class ExecutionEngineUnavailableError(ExecutionServiceError):
    """No execution engine is implemented or enabled by configuration."""


def _engine_unavailable_error(message: str) -> ExecutionError:
    return ExecutionError(
        code=ExecutionErrorCode.EXECUTION_ENGINE_UNAVAILABLE,
        message=message,
        category=ExecutionErrorCategory.EXECUTION_ENGINE_UNAVAILABLE,
        retryable=False,
    )


class ExecutionService(ABC):
    """Interface implemented by future local and sandbox execution engines."""

    @abstractmethod
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute one validated request and return a validated result."""

    async def submit(self, request: ExecutionRequest) -> UUID:
        """Submit work and return its generated identifier when supported."""
        raise ExecutionEngineUnavailableError(
            _engine_unavailable_error(
                "Asynchronous execution submission is not supported by this engine."
            )
        )

    async def get_status(self, execution_id: UUID) -> ExecutionStatus:
        """Return status when the implementation supports status lookup."""
        raise ExecutionEngineUnavailableError(
            _engine_unavailable_error(
                "Execution status lookup is not supported by this engine."
            )
        )

    async def get_result(
        self,
        execution_id: UUID,
    ) -> Optional[ExecutionResult]:
        """Return a terminal result, or None while execution is in progress."""
        raise ExecutionEngineUnavailableError(
            _engine_unavailable_error(
                "Execution result lookup is not supported by this engine."
            )
        )

    async def cancel(self, execution_id: UUID) -> ExecutionResult:
        """Request cancellation when the implementation supports it."""
        raise ExecutionEngineUnavailableError(
            _engine_unavailable_error(
                "Execution cancellation is not supported by this engine."
            )
        )


class UnavailableExecutionService(ExecutionService):
    """An explicit placeholder that never claims to execute code."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def _error(self, operation: str) -> ExecutionError:
        return _engine_unavailable_error(
            f"{operation} is unavailable. {self._reason}"
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raise ExecutionEngineUnavailableError(
            self._error("Code execution")
        )

    async def get_status(self, execution_id: UUID) -> ExecutionStatus:
        raise ExecutionEngineUnavailableError(
            self._error("Execution status lookup")
        )

    async def get_result(
        self,
        execution_id: UUID,
    ) -> Optional[ExecutionResult]:
        raise ExecutionEngineUnavailableError(
            self._error("Execution result lookup")
        )

    async def cancel(self, execution_id: UUID) -> ExecutionResult:
        raise ExecutionEngineUnavailableError(
            self._error("Execution cancellation")
        )


def create_execution_service(
    settings: Settings,
    *,
    engine: Optional[ExecutionService] = None,
) -> ExecutionService:
    """Return an injected engine or an explicit unavailable placeholder."""
    if engine is not None:
        return engine
    if not settings.execution_enabled:
        return UnavailableExecutionService(
            "Execution is disabled by server configuration."
        )
    from app.services.docker_execution_service import (
        DockerSandboxExecutionService,
    )

    return DockerSandboxExecutionService(settings)


ExecutionEngine = ExecutionService
