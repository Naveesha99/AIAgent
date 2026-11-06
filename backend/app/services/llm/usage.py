"""Helpers for normalizing and combining provider usage metadata."""

from __future__ import annotations

from typing import Iterable, Optional

from app.schemas.llm_usage import LLMUsage
from app.services.llm.base import LLMResponse


def usage_from_response(response: LLMResponse) -> LLMUsage:
    """Convert one provider response into normalized API-safe usage."""
    return LLMUsage(
        provider=response.provider,
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        total_tokens=response.total_tokens,
        request_id=response.request_id,
        request_duration_seconds=response.request_duration_seconds,
        retry_count=response.retry_count,
        request_count=1,
    )


def _sum_if_complete(values: Iterable[Optional[int]]) -> Optional[int]:
    items = list(values)
    if not items or any(value is None for value in items):
        return None
    return sum(value for value in items if value is not None)


def _sum_durations(values: Iterable[Optional[float]]) -> Optional[float]:
    items = list(values)
    if not items or any(value is None for value in items):
        return None
    return sum(value for value in items if value is not None)


def combine_usage(usages: Iterable[LLMUsage]) -> Optional[LLMUsage]:
    """Combine per-call usage without inventing absent provider values."""
    items = list(usages)
    if not items:
        return None
    if len(items) == 1:
        return items[0]

    providers = {item.provider for item in items}
    models = {item.model for item in items}
    provider = providers.pop() if len(providers) == 1 else "mixed"
    model = models.pop() if len(models) == 1 else "mixed"

    return LLMUsage(
        provider=provider,
        model=model,
        input_tokens=_sum_if_complete(item.input_tokens for item in items),
        output_tokens=_sum_if_complete(
            item.output_tokens for item in items
        ),
        total_tokens=_sum_if_complete(item.total_tokens for item in items),
        request_id=None,
        request_duration_seconds=_sum_durations(
            item.request_duration_seconds for item in items
        ),
        retry_count=sum(item.retry_count for item in items),
        request_count=sum(item.request_count for item in items),
        requests=[
            item.model_copy(update={"requests": []})
            for item in items
        ],
    )
