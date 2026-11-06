"""Google Gemini implementation of the provider-independent LLM client."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Optional, Sequence

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.services.llm.base import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMError,
    LLMInvalidRequestError,
    LLMMessage,
    LLMModelUnavailableError,
    LLMPermissionError,
    LLMProviderError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    LLMTransientError,
)

_RETRYABLE_ERRORS = (
    LLMConnectionError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMTransientError,
)
_TRANSIENT_STATUS_CODES = {408, 409, 500, 502, 503, 504}
_RATE_LIMIT_STATUSES = {
    "RATE_LIMIT_EXCEEDED",
    "RESOURCE_EXHAUSTED",
    "QUOTA_EXCEEDED",
}
_DELAY_PATTERN = re.compile(r"^(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>ms|s)?$")


class GeminiClient:
    """Generate text with the official Google Gen AI SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: Optional[Any] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retry_backoff_seconds: float = 0.5,
        max_retry_delay_seconds: float = 30.0,
    ) -> None:
        clean_key = api_key.strip() if isinstance(api_key, str) else ""
        if not clean_key:
            raise LLMConfigurationError(
                "GEMINI_API_KEY is required when LLM_PROVIDER is 'gemini'."
            )

        clean_model = model.strip() if isinstance(model, str) else ""
        if not clean_model:
            raise LLMConfigurationError(
                "LLM_MODEL must be a non-empty Gemini model identifier."
            )
        if timeout_seconds <= 0:
            raise LLMConfigurationError(
                "LLM_TIMEOUT_SECONDS must be greater than zero."
            )
        if not 0 <= max_retries <= 5:
            raise LLMConfigurationError(
                "LLM_MAX_RETRIES must be between 0 and 5."
            )

        self._model = clean_model
        self._timeout_seconds = float(timeout_seconds)
        self._max_retries = max_retries
        self._sleep = sleep
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._max_retry_delay_seconds = max(0.0, max_retry_delay_seconds)

        if client is None:
            http_options = genai_types.HttpOptions(
                timeout=max(1, int(self._timeout_seconds * 1000))
            )
            client = genai.Client(api_key=clean_key, http_options=http_options)
        self._client = client

    @property
    def provider(self) -> str:
        """Return the provider name."""
        return "gemini"

    @property
    def model(self) -> str:
        """Return the configured Gemini model identifier."""
        return self._model

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        system_instruction: Optional[str] = None,
    ) -> LLMResponse:
        """Generate text from provider-independent messages and instructions."""
        contents = self._build_contents(messages)
        config = self._build_config(system_instruction)
        retry_count = 0

        while True:
            try:
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=self._model,
                        contents=contents,
                        config=config,
                    ),
                    timeout=self._timeout_seconds,
                )
                return self._normalize_response(response)
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                error: LLMError = LLMTimeoutError(
                    "The Gemini request timed out."
                )
            except httpx.TimeoutException:
                error = LLMTimeoutError("The Gemini request timed out.")
            except httpx.TransportError:
                error = LLMConnectionError(
                    "The Gemini provider could not be reached."
                )
            except genai_errors.APIError as exc:
                error = self._map_api_error(exc)
            except LLMError:
                raise
            except (ConnectionError, OSError):
                error = LLMConnectionError(
                    "The Gemini provider could not be reached."
                )
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                raise LLMResponseError(
                    "The Gemini provider returned an unexpected response."
                ) from None
            except Exception:
                raise LLMProviderError(
                    "The Gemini provider request failed."
                ) from None

            if (
                not isinstance(error, _RETRYABLE_ERRORS)
                or retry_count >= self._max_retries
            ):
                raise error from None

            delay = self._retry_delay(error, retry_count)
            retry_count += 1
            await self._sleep(delay)

    @staticmethod
    def _build_contents(
        messages: Sequence[LLMMessage],
    ) -> list[genai_types.Content]:
        if not messages:
            raise LLMInvalidRequestError(
                "At least one message is required."
            )

        contents: list[genai_types.Content] = []
        for message in messages:
            if not isinstance(message, LLMMessage):
                raise LLMInvalidRequestError(
                    "Messages must be LLMMessage values."
                )
            role = "user" if message.role == "user" else "model"
            contents.append(
                genai_types.Content(
                    role=role,
                    parts=[genai_types.Part(text=message.content)],
                )
            )
        return contents

    @staticmethod
    def _build_config(
        system_instruction: Optional[str],
    ) -> Optional[genai_types.GenerateContentConfig]:
        if system_instruction is None:
            return None
        if (
            not isinstance(system_instruction, str)
            or not system_instruction.strip()
        ):
            raise LLMInvalidRequestError(
                "The system instruction must be a non-empty string."
            )
        return genai_types.GenerateContentConfig(
            system_instruction=system_instruction
        )

    def _normalize_response(self, response: Any) -> LLMResponse:
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise LLMResponseError(
                "The Gemini provider returned an empty or malformed response."
            )

        candidates = getattr(response, "candidates", None)
        finish_reason: Optional[str] = None
        if isinstance(candidates, (list, tuple)) and candidates:
            finish_reason = self._enum_text(
                getattr(candidates[0], "finish_reason", None)
            )

        usage = getattr(response, "usage_metadata", None)
        response_id = self._safe_text(getattr(response, "response_id", None))
        model_version = self._safe_text(
            getattr(response, "model_version", None)
        )

        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self._model,
            finish_reason=finish_reason,
            prompt_tokens=self._token_count(usage, "prompt_token_count"),
            completion_tokens=self._token_count(
                usage, "candidates_token_count"
            ),
            total_tokens=self._token_count(usage, "total_token_count"),
            response_id=response_id,
            model_version=model_version,
        )

    def _map_api_error(self, error: genai_errors.APIError) -> LLMError:
        code = self._safe_int(getattr(error, "code", None))
        status = self._safe_text(getattr(error, "status", None))
        normalized_status = status.upper() if status else ""

        if (
            code == 401
            or normalized_status
            in {
                "API_KEY_EXPIRED",
                "API_KEY_INVALID",
                "UNAUTHENTICATED",
            }
            or self._is_invalid_api_key(error)
        ):
            return LLMAuthenticationError(
                "The Gemini API rejected the configured credentials."
            )
        if code == 403 or normalized_status == "PERMISSION_DENIED":
            return LLMPermissionError(
                "The Gemini API denied access to the requested resource."
            )
        if code == 404 or normalized_status == "NOT_FOUND":
            return LLMModelUnavailableError(
                "The configured Gemini model is unavailable."
            )
        if (
            code == 429
            or normalized_status in _RATE_LIMIT_STATUSES
        ):
            if self._is_quota_exhaustion(error, normalized_status):
                return LLMQuotaExceededError(
                    "The Gemini API quota is exhausted."
                )
            return LLMRateLimitError(
                "The Gemini API rate limit was reached.",
                retry_after=self._retry_after(error),
            )
        if (
            code in _TRANSIENT_STATUS_CODES
            or 500 <= code < 600
            or normalized_status
            in {
                "ABORTED",
                "DEADLINE_EXCEEDED",
                "INTERNAL",
                "UNAVAILABLE",
            }
        ):
            return LLMTransientError(
                "The Gemini provider is temporarily unavailable."
            )
        if 400 <= code < 500:
            return LLMInvalidRequestError(
                "The Gemini API rejected the request."
            )
        return LLMProviderError("The Gemini provider request failed.")

    def _is_quota_exhaustion(
        self,
        error: genai_errors.APIError,
        status: str,
    ) -> bool:
        details = self._error_details_text(error)
        compact = "".join(
            character
            for character in details.lower()
            if character.isalnum()
        )
        rate_limit_markers = (
            "ratelimit",
            "perminute",
            "requestsperminute",
            "toomanyrequests",
            "rpm",
            "tpm",
        )
        quota_markers = (
            "billing",
            "credit",
            "daily",
            "perday",
            "quota",
            "quotafailure",
        )

        if status == "QUOTA_EXCEEDED":
            return True
        if (
            status == "RATE_LIMIT_EXCEEDED"
            or any(marker in compact for marker in rate_limit_markers)
        ):
            return False
        if any(marker in compact for marker in quota_markers):
            return True
        if self._retry_after(error) is not None:
            return False
        return status == "RESOURCE_EXHAUSTED"

    def _is_invalid_api_key(
        self,
        error: genai_errors.APIError,
    ) -> bool:
        details = self._error_details_text(error)
        compact = "".join(
            character
            for character in details.lower()
            if character.isalnum()
        )
        return any(
            marker in compact
            for marker in (
                "apikeyexpired",
                "apikeyinvalid",
                "apikeynotvalid",
                "invalidapikey",
            )
        )

    def _retry_after(
        self,
        error: genai_errors.APIError,
    ) -> Optional[float]:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            header_value = headers.get("retry-after")
            parsed_header = self._parse_delay(header_value)
            if parsed_header is not None:
                return parsed_header

        return self._find_retry_delay(getattr(error, "details", None))

    def _find_retry_delay(
        self,
        value: Any,
        depth: int = 0,
    ) -> Optional[float]:
        if depth > 5:
            return None
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in {"retrydelay", "retry_delay"}:
                    parsed = self._parse_delay(item)
                    if parsed is not None:
                        return parsed
                parsed = self._find_retry_delay(item, depth + 1)
                if parsed is not None:
                    return parsed
        elif isinstance(value, (list, tuple)):
            for item in value:
                parsed = self._find_retry_delay(item, depth + 1)
                if parsed is not None:
                    return parsed
        return None

    def _retry_delay(self, error: LLMError, retry_count: int) -> float:
        retry_after = (
            error.retry_after
            if isinstance(error, LLMRateLimitError)
            else None
        )
        if retry_after is not None:
            return min(
                max(0.0, retry_after),
                self._max_retry_delay_seconds,
            )
        return min(
            self._retry_backoff_seconds * (2**retry_count),
            self._max_retry_delay_seconds,
        )

    @staticmethod
    def _parse_delay(value: Any) -> Optional[float]:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, float(value))
        if not isinstance(value, str):
            return None

        stripped = value.strip()
        if not stripped:
            return None

        match = _DELAY_PATTERN.fullmatch(stripped)
        if match is not None:
            amount = float(match.group("amount"))
            if match.group("unit") == "ms":
                amount /= 1000
            return max(0.0, amount)

        try:
            parsed_date = parsedate_to_datetime(stripped)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed_date.tzinfo is None:
            parsed_date = parsed_date.replace(tzinfo=timezone.utc)
        delta = (parsed_date - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)

    @staticmethod
    def _error_details_text(error: genai_errors.APIError) -> str:
        payload = {
            "status": getattr(error, "status", None),
            "message": getattr(error, "message", None),
            "details": getattr(error, "details", None),
        }
        try:
            return json.dumps(payload, default=str).lower()
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _enum_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        candidate = getattr(value, "value", None)
        if not isinstance(candidate, str):
            candidate = getattr(value, "name", None)
        return candidate if isinstance(candidate, str) else None

    @staticmethod
    def _safe_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _token_count(self, usage: Any, field: str) -> Optional[int]:
        value = getattr(usage, field, None)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if value >= 0 else None

    @staticmethod
    def _safe_text(value: Any) -> Optional[str]:
        if isinstance(value, str) and value.strip():
            return value
        return None
