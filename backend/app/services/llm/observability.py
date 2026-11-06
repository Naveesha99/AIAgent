"""Structured LLM logging that never records prompts, keys, or CSV data."""

from __future__ import annotations

import logging
from typing import Optional

from app.services.llm.base import LLMError, LLMErrorCode, LLMResponse

logger = logging.getLogger("app.llm")


def _token(value: Optional[int]) -> str:
    return "unknown" if value is None else str(value)


def log_llm_success(
    operation: str,
    response: LLMResponse,
) -> None:
    """Log safe request metadata for a completed model call."""
    logger.info(
        "llm_operation_completed operation=%s provider=%s model=%s "
        "duration_seconds=%.3f retry_count=%d input_tokens=%s "
        "output_tokens=%s total_tokens=%s request_id=%s",
        operation,
        response.provider,
        response.model,
        response.request_duration_seconds or 0.0,
        response.retry_count,
        _token(response.input_tokens),
        _token(response.output_tokens),
        _token(response.total_tokens),
        response.request_id or "unknown",
    )


def log_llm_failure(
    operation: str,
    error: LLMError,
    *,
    provider: Optional[str],
    model: Optional[str],
) -> None:
    """Log a safe error code without exposing provider payloads or prompts."""
    logger.warning(
        "llm_operation_failed operation=%s provider=%s model=%s "
        "error_code=%s error_type=%s temporary=%s",
        operation,
        provider or "unknown",
        model or "unknown",
        getattr(error, "code", LLMErrorCode.UNKNOWN),
        type(error).__name__,
        bool(getattr(error, "temporary", False)),
    )


def log_mock_response(
    scenario: str,
    call_number: int,
    response_kind: str,
) -> None:
    """Identify deterministic mock provider activity explicitly."""
    logger.info(
        "mock_llm_response provider=mock scenario=%s call_number=%d "
        "response_kind=%s",
        scenario,
        call_number,
        response_kind,
    )
