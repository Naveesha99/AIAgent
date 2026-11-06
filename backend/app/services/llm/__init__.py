"""Provider-independent LLM client interfaces and implementations."""

from app.services.llm.base import (
    LLMAuthenticationError,
    LLMClient,
    LLMConfigurationError,
    LLMConnectionError,
    LLMEmptyResponseError,
    LLMError,
    LLMErrorCode,
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
    LLMUnexpectedResponseError,
    LLMValidationError,
)
from app.services.llm.error_mapping import LLMHTTPError, map_llm_error
from app.services.llm.factory import SUPPORTED_PROVIDERS, create_llm_client
from app.services.llm.gemini_client import GeminiClient
from app.services.llm.mock_client import MockLLMClient
from app.services.llm.usage import combine_usage, usage_from_response

__all__ = [
    "GeminiClient",
    "LLMHTTPError",
    "MockLLMClient",
    "LLMAuthenticationError",
    "LLMClient",
    "LLMConfigurationError",
    "LLMConnectionError",
    "LLMEmptyResponseError",
    "LLMError",
    "LLMErrorCode",
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
    "LLMUnexpectedResponseError",
    "LLMValidationError",
    "SUPPORTED_PROVIDERS",
    "combine_usage",
    "create_llm_client",
    "map_llm_error",
    "usage_from_response",
]
