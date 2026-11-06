"""Deterministic mock LLM scenario names shared by settings and providers."""

from __future__ import annotations

MOCK_SCENARIOS = frozenset(
    {
        "authentication_error",
        "candidate_code",
        "clarification",
        "empty",
        "empty_code",
        "malformed",
        "malformed_code",
        "network_error",
        "quota_exceeded",
        "rate_limit",
        "server_error",
        "success",
        "timeout",
        "unsupported_model",
    }
)

DEFAULT_MOCK_SCENARIO = "success"
