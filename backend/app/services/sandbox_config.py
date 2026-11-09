"""Reusable service wrapper for the centralized sandbox policy.

This service resolves application settings into a validated Docker sandbox
policy and defines bounded timeout and structured-error behavior for a future
execution runtime. It does not launch containers or execute code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import UUID

from app.core.sandbox import (
    SandboxContainerSpec,
    SandboxLimits,
    SandboxPolicy,
    SandboxTermination,
    SandboxConfigurationError,
)
from app.schemas.execution import (
    ExecutionError,
    ExecutionErrorCategory,
    ExecutionErrorCode,
)


class SandboxConfigurationService:
    """Central access point for execution sandbox restrictions."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._policy = SandboxPolicy.from_settings(settings)

    @property
    def policy(self) -> SandboxPolicy:
        """Return the validated sandbox policy."""
        return self._policy

    @property
    def limits(self) -> SandboxLimits:
        """Return the configured per-container resource limits."""
        return self._policy.limits

    def build_container_spec(
        self,
        *,
        dataset_file: Path,
        dataset_root: Path,
        execution_id: Optional[object] = None,
        environment: Optional[Mapping[str, str]] = None,
    ) -> SandboxContainerSpec:
        """Build a container specification from server-side values only."""
        return self._policy.container_spec(
            dataset_file=dataset_file,
            dataset_root=dataset_root,
            execution_id=execution_id,
            environment=environment,
        )

    def effective_timeout_seconds(self, requested_seconds: float) -> float:
        """Clamp a request timeout to the sandbox and server hard limits."""
        if (
            not isinstance(requested_seconds, (int, float))
            or isinstance(requested_seconds, bool)
        ):
            raise SandboxConfigurationError(
                "Requested execution timeout must be a number."
            )
        requested = float(requested_seconds)
        if requested <= 0:
            raise SandboxConfigurationError(
                "Requested execution timeout must be positive."
            )
        caps = [self._policy.limits.execution_timeout_seconds]
        configured_max = getattr(
            self._settings,
            "execution_max_timeout_seconds",
            None,
        )
        if isinstance(configured_max, (int, float)) and not isinstance(
            configured_max,
            bool,
        ):
            caps.append(float(configured_max))
        return min([requested, *caps])

    def error_for_termination(
        self,
        termination: SandboxTermination,
    ) -> Optional[ExecutionError]:
        """Return a safe structured error for a non-successful termination."""
        if termination is SandboxTermination.COMPLETED:
            return None
        if termination is SandboxTermination.TIMED_OUT:
            return ExecutionError(
                code=ExecutionErrorCode.EXECUTION_TIMEOUT,
                category=ExecutionErrorCategory.EXECUTION_TIMEOUT,
                message="The execution exceeded the sandbox time limit.",
                retryable=True,
            )
        if termination is SandboxTermination.RESOURCE_EXHAUSTED:
            return ExecutionError(
                code=ExecutionErrorCode.RESOURCE_LIMIT_EXCEEDED,
                category=ExecutionErrorCategory.RESOURCE_LIMIT_EXCEEDED,
                message="The execution exceeded a sandbox resource limit.",
                retryable=False,
            )
        if termination is SandboxTermination.CANCELLED:
            return ExecutionError(
                code=ExecutionErrorCode.EXECUTION_CANCELLED,
                category=ExecutionErrorCategory.EXECUTION_CANCELLED,
                message="The execution was cancelled.",
                retryable=False,
            )
        return ExecutionError(
            code=ExecutionErrorCode.EXECUTION_FAILED,
            category=ExecutionErrorCategory.EXECUTION_FAILED,
            message="The execution failed inside the sandbox.",
            retryable=False,
        )
