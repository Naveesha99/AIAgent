"""Provider-independent LLM client interfaces and implementations."""

from app.services.llm.base import (
    LLMAuthenticationError,
    LLMClient,
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
from app.services.llm.factory import SUPPORTED_PROVIDERS, create_llm_client
from app.services.llm.gemini_client import GeminiClient

__all__ = [
    "GeminiClient",
    "LLMAuthenticationError",
    "LLMClient",
    "LLMConfigurationError",
    "LLMConnectionError",
    "LLMError",
    "LLMInvalidRequestError",
    "LLMMessage",
    "LLMModelUnavailableError",
    "LLMPermissionError",
    "LLMProviderError",
    "LLMQuotaExceededError",
    "LLMRateLimitError",
    "LLMResponse",
    "LLMResponseError",
    "LLMTimeoutError",
    "LLMTransientError",
    "SUPPORTED_PROVIDERS",
    "create_llm_client",
]
