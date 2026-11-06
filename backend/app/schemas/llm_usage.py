"""Normalized, non-billing LLM usage metadata."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class LLMUsage(BaseModel):
    """Usage and request metadata returned by one or more LLM calls.

    Token values are provider-reported or, for the deterministic mock provider,
    clearly simulated. They are not cost estimates.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    total_tokens: Optional[int] = Field(default=None, ge=0)
    request_id: Optional[str] = None
    request_duration_seconds: Optional[float] = Field(default=None, ge=0)
    retry_count: int = Field(default=0, ge=0)
    request_count: int = Field(default=1, ge=1)
    requests: list["LLMUsage"] = Field(default_factory=list)
