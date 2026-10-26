"""Provider-independent LLM types, errors, and client protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Protocol, Sequence, runtime_checkable

MessageRole = Literal["user", "assistant"]


class LLMError(Exception):
    """Base class for safe, provider-independent LLM failures."""


class LLMConfigurationError(LLMError):
    """The LLM provider configuration is missing or invalid."""


class LLMTimeoutError(LLMError):
    """The provider did not respond before the configured timeout."""


class LLMConnectionError(LLMError):
    """The provider could not be reached."""


class LLMAuthenticationError(LLMError):
    """The provider rejected the configured credentials."""


class LLMPermissionError(LLMError):
    """The configured credentials cannot use the requested resource."""


class LLMRateLimitError(LLMError):
    """The provider rate limit was reached."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMQuotaExceededError(LLMError):
    """The provider quota is exhausted and retrying will not help."""


class LLMModelUnavailableError(LLMError):
    """The configured model is not available to the provider project."""


class LLMInvalidRequestError(LLMError):
    """The provider rejected the request as invalid."""


class LLMTransientError(LLMError):
    """The provider is temporarily unavailable."""


class LLMResponseError(LLMError):
    """The provider returned an empty or malformed response."""


class LLMProviderError(LLMError):
    """The provider failed in an unexpected way."""


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
