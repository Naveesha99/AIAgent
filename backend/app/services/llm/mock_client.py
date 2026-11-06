"""Deterministic mock LLM provider for development and tests."""

from __future__ import annotations

import json
from typing import Optional, Sequence

from app.core.llm_scenarios import MOCK_SCENARIOS
from app.services.llm.base import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMInvalidRequestError,
    LLMMessage,
    LLMModelUnavailableError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMResponse,
    LLMTimeoutError,
    LLMTransientError,
)
from app.services.llm.observability import log_mock_response

_MOCK_WARNING = (
    "This response came from the deterministic mock provider; it is not "
    "real model output."
)


def _interpretation_payload() -> dict[str, object]:
    return {
        "intent": "summarize",
        "referenced_columns": [],
        "requested_metrics": [],
        "group_by": [],
        "filters": None,
        "sort": None,
        "limit": None,
        "clarification_question": None,
        "assumptions": [],
    }


def _clarification_payload() -> dict[str, object]:
    return {
        "intent": "clarification_needed",
        "referenced_columns": [],
        "requested_metrics": [],
        "group_by": [],
        "filters": None,
        "sort": None,
        "limit": None,
        "clarification_question": (
            "Which column and metric should I use for this question?"
        ),
        "assumptions": [],
    }


def _code_payload() -> dict[str, object]:
    return {
        "code": "result = df.shape",
        "explanation": "Returns the dataset shape as a deterministic mock.",
        "operation": "summarize",
        "referenced_columns": [],
        "assumptions": [],
        "warnings": [_MOCK_WARNING],
    }


class MockLLMClient:
    """Return deterministic structured responses without network access."""

    provider = "mock"

    def __init__(
        self,
        *,
        model: str = "mock-deterministic",
        scenario: str = "success",
    ) -> None:
        clean_model = model.strip() if isinstance(model, str) else ""
        if not clean_model:
            raise LLMConfigurationError(
                "LLM_MOCK_MODEL must be a non-empty mock model identifier."
            )
        clean_scenario = (
            scenario.strip().lower() if isinstance(scenario, str) else ""
        )
        if clean_scenario not in MOCK_SCENARIOS:
            raise LLMConfigurationError(
                "LLM_MOCK_SCENARIO must be one of: "
                + ", ".join(sorted(MOCK_SCENARIOS))
            )
        self._model = clean_model
        self._scenario = clean_scenario
        self._call_count = 0

    @property
    def model(self) -> str:
        """Return the configured deterministic mock model identifier."""
        return self._model

    @property
    def scenario(self) -> str:
        """Return the selected deterministic mock scenario."""
        return self._scenario

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        system_instruction: Optional[str] = None,
    ) -> LLMResponse:
        """Return one deterministic response through the normal client shape."""
        self._validate_request(messages, system_instruction)
        self._call_count += 1
        kind = self._request_kind(system_instruction)
        scenario = self._scenario

        if scenario == "rate_limit":
            log_mock_response(scenario, self._call_count, "error:rate_limited")
            raise LLMRateLimitError(
                "The mock provider simulated a rate limit.",
                retry_after=1.0,
            )
        if scenario == "timeout":
            log_mock_response(scenario, self._call_count, "error:timeout")
            raise LLMTimeoutError("The mock provider simulated a timeout.")
        if scenario == "network_error":
            log_mock_response(scenario, self._call_count, "error:connection")
            raise LLMConnectionError(
                "The mock provider simulated a network failure."
            )
        if scenario == "server_error":
            log_mock_response(scenario, self._call_count, "error:transient")
            raise LLMTransientError(
                "The mock provider simulated a temporary server error."
            )
        if scenario == "authentication_error":
            log_mock_response(
                scenario,
                self._call_count,
                "error:authentication",
            )
            raise LLMAuthenticationError(
                "The mock provider simulated rejected credentials."
            )
        if scenario == "quota_exceeded":
            log_mock_response(scenario, self._call_count, "error:quota")
            raise LLMQuotaExceededError(
                "The mock provider simulated exhausted quota."
            )
        if scenario == "unsupported_model":
            log_mock_response(scenario, self._call_count, "error:model")
            raise LLMModelUnavailableError(
                "The mock provider simulated an unavailable model."
            )

        if scenario == "empty":
            log_mock_response(scenario, self._call_count, "empty")
            return self._response("", messages)
        if scenario == "malformed":
            log_mock_response(scenario, self._call_count, "malformed")
            return self._response("not-json", messages)
        if scenario == "empty_code" and kind == "code_generation":
            log_mock_response(scenario, self._call_count, "empty_code")
            return self._response("", messages)
        if scenario == "malformed_code" and kind == "code_generation":
            log_mock_response(scenario, self._call_count, "malformed_code")
            return self._response("not-json", messages)

        if scenario == "clarification":
            payload = _clarification_payload()
            response_kind = "clarification"
        elif kind == "code_generation":
            payload = _code_payload()
            response_kind = "candidate_code"
        else:
            payload = _interpretation_payload()
            response_kind = "interpretation"

        log_mock_response(scenario, self._call_count, response_kind)
        return self._response(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            messages,
        )

    def _response(
        self,
        text: str,
        messages: Sequence[LLMMessage],
    ) -> LLMResponse:
        input_tokens = self._estimate_tokens(
            " ".join(message.content for message in messages)
        )
        output_tokens = self._estimate_tokens(text)
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self._model,
            finish_reason="STOP",
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            response_id=f"mock-response-{self._call_count:04d}",
            model_version="mock-v1",
            retry_count=0,
            request_duration_seconds=0.0,
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4) if text else 0

    @staticmethod
    def _request_kind(system_instruction: Optional[str]) -> str:
        instruction = (
            system_instruction.casefold()
            if isinstance(system_instruction, str)
            else ""
        )
        if "pandas analysis code" in instruction:
            return "code_generation"
        return "question_interpretation"

    @staticmethod
    def _validate_request(
        messages: Sequence[LLMMessage],
        system_instruction: Optional[str],
    ) -> None:
        if not messages:
            raise LLMInvalidRequestError(
                "At least one message is required."
            )
        for message in messages:
            if not isinstance(message, LLMMessage):
                raise LLMInvalidRequestError(
                    "Messages must be LLMMessage values."
                )
        if system_instruction is not None and (
            not isinstance(system_instruction, str)
            or not system_instruction.strip()
        ):
            raise LLMInvalidRequestError(
                "The system instruction must be a non-empty string."
            )
