"""Provider-independent LLM types, errors, and client protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Protocol, Sequence, runtime_checkable

MessageRole = Literal["user", "assistant"]


class LLMErrorCode:
    """Stable, provider-independent error codes for logging and diagnostics."""

    CONFIGURATION = "configuration_error"
    TIMEOUT = "timeout"
    CONNECTION = "connection_error"
    AUTHENTICATION = "authentication_error"
    PERMISSION = "permission_denied"
    RATE_LIMIT = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    MODEL_UNAVAILABLE = "model_unavailable"
    INVALID_REQUEST = "invalid_request"
    TRANSIENT = "transient_provider_error"
    MALFORMED_RESPONSE = "malformed_response"
    EMPTY_RESPONSE = "empty_response"
    VALIDATION = "validation_error"
    UNEXPECTED_RESPONSE = "unexpected_provider_response"
    PROVIDER = "provider_error"
    UNKNOWN = "llm_error"


class LLMError(Exception):
    """Base class for safe, provider-independent LLM failures."""

    code = LLMErrorCode.UNKNOWN
    temporary = False

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or type(self).code
        self.retry_after = retry_after


class LLMConfigurationError(LLMError):
    """The LLM provider configuration is missing or invalid."""

    code = LLMErrorCode.CONFIGURATION


class LLMTimeoutError(LLMError):
    """The provider did not respond before the configured timeout."""

    code = LLMErrorCode.TIMEOUT
    temporary = True


class LLMConnectionError(LLMError):
    """The provider could not be reached."""

    code = LLMErrorCode.CONNECTION
    temporary = True


class LLMAuthenticationError(LLMError):
    """The provider rejected the configured credentials."""

    code = LLMErrorCode.AUTHENTICATION


class LLMPermissionError(LLMError):
    """The configured credentials cannot use the requested resource."""

    code = LLMErrorCode.PERMISSION


class LLMRateLimitError(LLMError):
    """The provider rate limit was reached."""

    code = LLMErrorCode.RATE_LIMIT
    temporary = True

    def __init__(
        self,
        message: str,
        retry_after: Optional[float] = None,
        *,
        code: Optional[str] = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            retry_after=retry_after,
        )


class LLMQuotaExceededError(LLMError):
    """The provider quota is exhausted and retrying will not help."""

    code = LLMErrorCode.QUOTA_EXCEEDED


class LLMModelUnavailableError(LLMError):
    """The configured model is not available to the provider project."""

    code = LLMErrorCode.MODEL_UNAVAILABLE


class LLMInvalidRequestError(LLMError):
    """The provider rejected the request as invalid."""

    code = LLMErrorCode.INVALID_REQUEST


class LLMTransientError(LLMError):
    """The provider is temporarily unavailable."""

    code = LLMErrorCode.TRANSIENT
    temporary = True


class LLMResponseError(LLMError):
    """The provider returned an empty or malformed response."""

    code = LLMErrorCode.MALFORMED_RESPONSE


class LLMEmptyResponseError(LLMResponseError):
    """The provider returned no usable text."""

    code = LLMErrorCode.EMPTY_RESPONSE


class LLMValidationError(LLMResponseError):
    """Structured model output failed application validation."""

    code = LLMErrorCode.VALIDATION


class LLMProviderError(LLMError):
    """The provider failed in an unexpected way."""

    code = LLMErrorCode.PROVIDER


class LLMUnexpectedResponseError(LLMProviderError):
    """The provider response could not be interpreted safely."""

    code = LLMErrorCode.UNEXPECTED_RESPONSE


@dataclass(frozen=True)
class LLMMessage:
    """One conversation message independent of any provider schema."""

    role: MessageRole
    content: str

    def __post_init__(self) -> None:
        if self.role not in ("user", "assistant"):
            raise ValueError("LLM message role must be 'user' or 'assistant'.")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("LLM message content must be a non-empty string.")


@dataclass(frozen=True)
class LLMResponse:
    """Normalized generated text and optional provider metadata."""

    text: str
    provider: str
    model: str
    finish_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    response_id: Optional[str] = None
    model_version: Optional[str] = None
    retry_count: int = 0
    request_duration_seconds: Optional[float] = field(
        default=None,
        compare=False,
    )

    @property
    def input_tokens(self) -> Optional[int]:
        """Return the provider prompt token count using normalized naming."""
        return self.prompt_tokens

    @property
    def output_tokens(self) -> Optional[int]:
        """Return the provider completion token count using normalized naming."""
        return self.completion_tokens

    @property
    def request_id(self) -> Optional[str]:
        """Return the provider request identifier when available."""
        return self.response_id


@runtime_checkable
class LLMClient(Protocol):
    """Asynchronous text-generation interface implemented by every provider."""

    @property
    def provider(self) -> str:
        """Return the configured provider name."""
        ...

    @property
    def model(self) -> str:
        """Return the configured model identifier."""
        ...

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        system_instruction: Optional[str] = None,
    ) -> LLMResponse:
        """Generate text from ordered messages and an optional system instruction."""
        ...
