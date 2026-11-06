"""Map provider-independent LLM failures to controlled HTTP responses."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from app.services.llm.base import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMError,
    LLMErrorCode,
    LLMInvalidRequestError,
    LLMModelUnavailableError,
    LLMPermissionError,
    LLMProviderError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    LLMTransientError,
)


@dataclass(frozen=True)
class LLMHTTPError:
    """Safe HTTP details derived from an internal LLM error."""

    status_code: int
    detail: str
    code: str
    temporary: bool
    headers: Optional[dict[str, str]] = None


def _headers(
    code: str,
    *,
    temporary: bool,
    retry_after: Optional[float] = None,
) -> dict[str, str]:
    headers = {
        "X-LLM-Error-Code": code,
        "X-LLM-Error-Temporary": "true" if temporary else "false",
    }
    if retry_after is not None and math.isfinite(retry_after):
        headers["Retry-After"] = str(max(0, math.ceil(retry_after)))
    return headers


def map_llm_error(exc: LLMError) -> Optional[LLMHTTPError]:
    """Return a safe HTTP mapping for a known LLM error."""
    code = getattr(exc, "code", LLMErrorCode.UNKNOWN)
    temporary = bool(getattr(exc, "temporary", False))

    if isinstance(exc, LLMConfigurationError):
        return LLMHTTPError(
            503,
            str(exc),
            code,
            temporary,
            _headers(code, temporary=temporary),
        )
    if isinstance(exc, LLMTimeoutError):
        return LLMHTTPError(
            504,
            "The language model request timed out.",
            code,
            temporary,
            _headers(code, temporary=temporary),
        )
    if isinstance(exc, LLMRateLimitError):
        return LLMHTTPError(
            429,
            "The language model rate limit was reached.",
            code,
            temporary,
            _headers(
                code,
                temporary=temporary,
                retry_after=exc.retry_after,
            ),
        )
    if isinstance(exc, LLMQuotaExceededError):
        return LLMHTTPError(
            429,
            "The language model quota is exhausted.",
            code,
            temporary,
            _headers(code, temporary=temporary),
        )
    if isinstance(
        exc,
        (
            LLMAuthenticationError,
            LLMPermissionError,
            LLMModelUnavailableError,
        ),
    ):
        return LLMHTTPError(
            502,
            "The configured language model is unavailable.",
            code,
            temporary,
            _headers(code, temporary=temporary),
        )
    if isinstance(exc, (LLMConnectionError, LLMTransientError)):
        return LLMHTTPError(
            503,
            "The language model provider is temporarily unavailable.",
            code,
            temporary,
            _headers(code, temporary=temporary),
        )
    if isinstance(
        exc,
        (LLMInvalidRequestError, LLMProviderError, LLMResponseError),
    ):
        return LLMHTTPError(
            502,
            "The language model returned an invalid response.",
            code,
            temporary,
            _headers(code, temporary=temporary),
        )
    return LLMHTTPError(
        502,
        "The language model request failed.",
        code,
        temporary,
        _headers(code, temporary=temporary),
    )
