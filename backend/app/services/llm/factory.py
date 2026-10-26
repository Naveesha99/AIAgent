"""Configured LLM provider construction."""

from __future__ import annotations

from typing import Any, Optional

from app.core.config import Settings
from app.services.llm.base import LLMClient, LLMConfigurationError
from app.services.llm.gemini_client import GeminiClient

SUPPORTED_PROVIDERS = frozenset({"gemini"})


def create_llm_client(
    settings: Settings,
    *,
    client: Optional[Any] = None,
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

    api_key = (
        settings.gemini_api_key.get_secret_value()
        if settings.gemini_api_key is not None
        else ""
    )
    if not api_key.strip():
        raise LLMConfigurationError(
            "GEMINI_API_KEY is required when LLM_PROVIDER is 'gemini'."
        )

    return GeminiClient(
        api_key=api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        client=client,
    )
