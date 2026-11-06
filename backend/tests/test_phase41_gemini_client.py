"""Mocked tests for the Gemini LLM client."""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Optional

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.services.llm import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMInvalidRequestError,
    LLMMessage,
    LLMModelUnavailableError,
    LLMProviderError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    LLMTransientError,
)
from app.services.llm.gemini_client import GeminiClient

API_KEY = "test-api-key-not-real"
MODEL = "gemini-3.5-flash-lite"


class FakeModels:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("Unexpected provider call.")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeAio:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


class FakeGeminiSdkClient:
    def __init__(self, models: FakeModels) -> None:
        self.aio = FakeAio(models)


class SlowModels:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls += 1
        await asyncio.sleep(0.2)
        return make_success()


class SlowAio:
    def __init__(self, models: SlowModels) -> None:
        self.models = models


class SlowGeminiSdkClient:
    def __init__(self, models: SlowModels) -> None:
        self.aio = SlowAio(models)


def make_success(
    text: str = "Generated answer",
    *,
    finish_reason: Any = genai_types.FinishReason.STOP,
    response_id: Optional[str] = "response-123",
    model_version: Optional[str] = "gemini-version-001",
) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        candidates=[
            SimpleNamespace(finish_reason=finish_reason),
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=11,
            candidates_token_count=7,
            total_token_count=18,
        ),
        response_id=response_id,
        model_version=model_version,
    )


def make_api_error(
    code: int,
    status: str,
    message: str,
    *,
    headers: Optional[dict[str, str]] = None,
    details: Optional[Any] = None,
) -> genai_errors.APIError:
    error_payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "status": status,
    }
    if details is not None:
        error_payload["details"] = details
    payload = {"error": error_payload}
    response = None
    if headers is not None:
        response = httpx.Response(
            code,
            headers=headers,
            json=payload,
            request=httpx.Request(
                "POST",
                "https://generativelanguage.googleapis.com/v1beta/models",
            ),
        )
    return genai_errors.APIError(code, payload, response)


def make_client(
    outcomes: list[Any],
    *,
    max_retries: int = 0,
    timeout_seconds: float = 5.0,
) -> tuple[GeminiClient, FakeModels, list[float]]:
    models = FakeModels(outcomes)
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = GeminiClient(
        api_key=API_KEY,
        model=MODEL,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        client=FakeGeminiSdkClient(models),
        sleep=record_sleep,
    )
    return client, models, sleeps


def test_successful_response_is_normalized_with_metadata() -> None:
    client, models, sleeps = make_client([make_success()])

    result = asyncio.run(
        client.generate(
            [LLMMessage("user", "Summarize this.")],
            system_instruction="Use only facts.",
        )
    )

    assert isinstance(result, LLMResponse)
    assert result == LLMResponse(
        text="Generated answer",
        provider="gemini",
        model=MODEL,
        finish_reason="STOP",
        prompt_tokens=11,
        completion_tokens=7,
        total_tokens=18,
        response_id="response-123",
        model_version="gemini-version-001",
    )
    assert sleeps == []
    call = models.calls[0]
    assert call["model"] == MODEL
    assert call["config"].system_instruction == "Use only facts."
    assert call["contents"][0].role == "user"
    assert call["contents"][0].parts[0].text == "Summarize this."


def test_messages_use_gemini_roles_and_optional_system_instruction() -> None:
    client, models, _ = make_client([make_success()])

    asyncio.run(
        client.generate(
            [
                LLMMessage("user", "First"),
                LLMMessage("assistant", "Second"),
                LLMMessage("user", "Third"),
            ]
        )
    )

    call = models.calls[0]
    assert call["config"] is None
    assert [content.role for content in call["contents"]] == [
        "user",
        "model",
        "user",
    ]
    assert [content.parts[0].text for content in call["contents"]] == [
        "First",
        "Second",
        "Third",
    ]


@pytest.mark.parametrize(
    "messages,system_instruction",
    [
        ([], None),
        ([LLMMessage("user", "Hello")], "   "),
    ],
)
def test_invalid_local_requests_do_not_call_the_provider(
    messages: list[LLMMessage],
    system_instruction: Optional[str],
) -> None:
    client, models, _ = make_client([make_success()])

    with pytest.raises(LLMInvalidRequestError):
        asyncio.run(
            client.generate(
                messages,
                system_instruction=system_instruction,
            )
        )

    assert models.calls == []


def test_actual_timeout_cancels_the_provider_request() -> None:
    models = SlowModels()
    client = GeminiClient(
        api_key=API_KEY,
        model=MODEL,
        timeout_seconds=0.01,
        max_retries=0,
        client=SlowGeminiSdkClient(models),
    )

    with pytest.raises(LLMTimeoutError) as error:
        asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert "timed out" in str(error.value).lower()
    assert models.calls == 1


def test_timeout_is_retried_with_bounded_backoff() -> None:
    client, models, sleeps = make_client(
        [asyncio.TimeoutError(), make_success()],
        max_retries=1,
    )

    result = asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert result.text == "Generated answer"
    assert len(models.calls) == 2
    assert sleeps == [0.5]


def test_connection_failure_is_retried() -> None:
    connection_error = httpx.ConnectError(
        "connection refused",
        request=httpx.Request("POST", "https://example.test"),
    )
    client, models, sleeps = make_client(
        [connection_error, make_success()],
        max_retries=1,
    )

    result = asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert result.text == "Generated answer"
    assert len(models.calls) == 2
    assert sleeps == [0.5]


def test_connection_failure_exhausts_bounded_retries() -> None:
    connection_error = httpx.ConnectError(
        "connection refused",
        request=httpx.Request("POST", "https://example.test"),
    )
    client, models, sleeps = make_client(
        [connection_error, connection_error, connection_error],
        max_retries=2,
    )

    with pytest.raises(LLMConnectionError):
        asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert len(models.calls) == 3
    assert sleeps == [0.5, 1.0]


def test_rate_limit_retry_after_header_is_respected() -> None:
    rate_limit_error = make_api_error(
        429,
        "RESOURCE_EXHAUSTED",
        "Rate limit exceeded.",
        headers={"retry-after": "2"},
    )
    client, models, sleeps = make_client(
        [rate_limit_error, make_success()],
        max_retries=2,
    )

    result = asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert result.text == "Generated answer"
    assert len(models.calls) == 2
    assert sleeps == [2.0]


def test_rate_limit_retry_delay_detail_is_respected() -> None:
    rate_limit_error = make_api_error(
        429,
        "RESOURCE_EXHAUSTED",
        "Too many requests.",
        details=[
            {
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": "1.5s",
            }
        ],
    )
    client, models, sleeps = make_client(
        [rate_limit_error, make_success()],
        max_retries=1,
    )

    asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert len(models.calls) == 2
    assert sleeps == [1.5]


def test_generic_resource_exhaustion_with_retry_info_is_retried() -> None:
    rate_limit_error = make_api_error(
        429,
        "RESOURCE_EXHAUSTED",
        "Resource exhausted.",
        details=[
            {
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": "2s",
            }
        ],
    )
    client, models, sleeps = make_client(
        [rate_limit_error, make_success()],
        max_retries=1,
    )

    result = asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert result.text == "Generated answer"
    assert len(models.calls) == 2
    assert sleeps == [2.0]


def test_exhausted_quota_is_not_retried() -> None:
    quota_error = make_api_error(
        429,
        "RESOURCE_EXHAUSTED",
        "You exceeded your current quota. Check billing details.",
    )
    client, models, sleeps = make_client(
        [quota_error, make_success()],
        max_retries=3,
    )

    with pytest.raises(LLMQuotaExceededError):
        asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert len(models.calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "code,status,message",
    [
        (401, "UNAUTHENTICATED", "Request had invalid authentication credentials."),
        (400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."),
    ],
)
def test_invalid_api_credentials_are_not_retried(
    code: int,
    status: str,
    message: str,
) -> None:
    authentication_error = make_api_error(code, status, message)
    client, models, sleeps = make_client(
        [authentication_error, make_success()],
        max_retries=3,
    )

    with pytest.raises(LLMAuthenticationError):
        asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert len(models.calls) == 1
    assert sleeps == []


def test_unavailable_model_is_not_retried() -> None:
    model_error = make_api_error(
        404,
        "NOT_FOUND",
        "The model was not found.",
    )
    client, models, sleeps = make_client(
        [model_error, make_success()],
        max_retries=3,
    )

    with pytest.raises(LLMModelUnavailableError):
        asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert len(models.calls) == 1
    assert sleeps == []


def test_invalid_request_is_not_retried_and_does_not_leak_details() -> None:
    invalid_error = make_api_error(
        400,
        "INVALID_ARGUMENT",
        "Rejected CUSTOMER_SECRET_ROW with key sk-super-secret",
    )
    client, models, sleeps = make_client(
        [invalid_error, make_success()],
        max_retries=3,
    )

    with pytest.raises(LLMInvalidRequestError) as error:
        asyncio.run(
            client.generate(
                [LLMMessage("user", "CUSTOMER_SECRET_ROW")],
            )
        )

    assert len(models.calls) == 1
    assert sleeps == []
    assert "CUSTOMER_SECRET_ROW" not in str(error.value)
    assert "sk-super-secret" not in str(error.value)


def test_transient_server_errors_use_bounded_retries() -> None:
    transient_error = make_api_error(
        503,
        "UNAVAILABLE",
        "The service is temporarily unavailable.",
    )
    client, models, sleeps = make_client(
        [transient_error, transient_error, transient_error],
        max_retries=2,
    )

    with pytest.raises(LLMTransientError):
        asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert len(models.calls) == 3
    assert sleeps == [0.5, 1.0]


@pytest.mark.parametrize(
    "bad_response",
    [
        SimpleNamespace(text=None),
        SimpleNamespace(text=123),
        SimpleNamespace(text="   "),
        object(),
    ],
)
def test_malformed_responses_are_reported_safely(
    bad_response: Any,
) -> None:
    client, models, sleeps = make_client([bad_response], max_retries=2)

    with pytest.raises(LLMResponseError):
        asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert len(models.calls) == 1
    assert sleeps == []


def test_unexpected_provider_failure_is_wrapped_safely() -> None:
    client, models, sleeps = make_client(
        [RuntimeError("raw provider failure")],
        max_retries=2,
    )

    with pytest.raises(LLMProviderError) as error:
        asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert "raw provider failure" not in str(error.value)
    assert len(models.calls) == 1
    assert sleeps == []


def test_rate_limit_error_carries_retry_guidance() -> None:
    rate_limit_error = make_api_error(
        429,
        "RATE_LIMIT_EXCEEDED",
        "Rate limit exceeded.",
        headers={"retry-after": "4"},
    )
    client, _, _ = make_client([rate_limit_error], max_retries=0)

    with pytest.raises(LLMRateLimitError) as error:
        asyncio.run(client.generate([LLMMessage("user", "Hello")]))

    assert error.value.retry_after == 4.0


def test_api_key_and_prompt_are_not_exposed_in_logs_or_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    invalid_error = make_api_error(
        400,
        "INVALID_ARGUMENT",
        f"Rejected CUSTOMER_SECRET_ROW using key {API_KEY}",
    )
    client, _, _ = make_client([invalid_error], max_retries=0)

    with pytest.raises(LLMInvalidRequestError) as error:
        asyncio.run(
            client.generate([LLMMessage("user", "CUSTOMER_SECRET_ROW")])
        )

    combined = f"{caplog.text}\n{error.value}"
    assert API_KEY not in combined
    assert "CUSTOMER_SECRET_ROW" not in combined
