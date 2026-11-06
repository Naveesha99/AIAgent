"""Configured LLM provider construction."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from app.core.config import Settings
from app.services.llm.base import LLMClient, LLMConfigurationError
from app.services.llm.gemini_client import GeminiClient
from app.services.llm.mock_client import MockLLMClient

SUPPORTED_PROVIDERS = frozenset({"gemini", "mock"})


def create_llm_client(
    settings: Settings,
    *,
    client: Optional[Any] = None,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
) -> LLMClient:
    """Build the configured provider without performing a network request."""
    provider = (settings.llm_provider or "").strip().lower()
    if not provider:
        raise LLMConfigurationError("LLM_PROVIDER must not be empty.")
    if provider not in SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise LLMConfigurationError(
            f"Unsupported LLM provider '{provider}'. Supported providers: "
            f"{supported}."
        )

    if provider == "mock":
        return MockLLMClient(
            model=settings.llm_mock_model,
            scenario=settings.llm_mock_scenario,
        )

    api_key = (
        settings.gemini_api_key.get_secret_value()
        if settings.gemini_api_key is not None
        else ""
    )
    if not api_key.strip():
        raise LLMConfigurationError(
            "GEMINI_API_KEY is required when LLM_PROVIDER is 'gemini'."
        )

    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "model": settings.llm_model,
        "timeout_seconds": settings.llm_timeout_seconds,
        "max_retries": settings.llm_max_retries,
        "retry_backoff_seconds": settings.llm_retry_base_delay_seconds,
        "max_retry_delay_seconds": settings.llm_retry_max_delay_seconds,
        "retry_jitter_ratio": settings.llm_retry_jitter_ratio,
        "client": client,
    }
    if sleep is not None:
        client_kwargs["sleep"] = sleep
    return GeminiClient(**client_kwargs)
