"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.services.llm.base import LLMClient
from app.services.llm.factory import create_llm_client


def get_llm_client(
    settings: Settings = Depends(get_settings),
) -> LLMClient:
    """Return the configured provider client without making a request."""
    return create_llm_client(settings)
