"""Tests for Phase 4.5 LLM errors, usage, retries, and mock mode."""

import asyncio
import csv
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Optional

import pytest
from fastapi.testclient import TestClient

import app.services.llm.gemini_client as gemini_module
from app.core.config import Settings, get_settings
from app.main import app
from app.schemas.llm_usage import LLMUsage
from app.services.llm import (
    GeminiClient,
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMErrorCode,
    LLMInvalidRequestError,
    LLMMessage,
    LLMModelUnavailableError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMResponse,
    LLMTimeoutError,
    LLMTransientError,
    MockLLMClient,
    combine_usage,
    create_llm_client,
    map_llm_error,
    usage_from_response,
)

client = TestClient(app)

API_KEY = "test-api-key-not-real"
MODEL = "gemini-test-model"
SCENARIO_ENVIRONMENT_VARIABLES = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "GEMINI_API_KEY",
    "LLM_TIMEOUT_SECONDS",
    "LLM_MAX_RETRIES",
    "LLM_RETRY_BASE_DELAY_SECONDS",
    "LLM_RETRY_MAX_DELAY_SECONDS",
    "LLM_RETRY_JITTER_RATIO",
    "LLM_MOCK_SCENARIO",
    "LLM_MOCK_MODEL",
)

HEADERS = ["region", "amount"]
ROWS = [["North", "10"], ["South", "20"]]


class FakeModels:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls += 1
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


class CancelledModels:
    async def generate_content(self, **kwargs: Any) -> Any:
        raise asyncio.CancelledError()


class CancelledAio:
    def __init__(self) -> None:
        self.models = CancelledModels()


class CancelledGeminiSdkClient:
    def __init__(self) -> None:
        self.aio = CancelledAio()


def make_csv() -> bytes:
    contents = io.StringIO(newline="")
    writer = csv.writer(contents, lineterminator="\n")
    writer.writerow(HEADERS)
    writer.writerows(ROWS)
    return contents.getvalue().encode("utf-8")


def make_success(
    *,
    usage_metadata: Any = None,
    text: str = "Generated answer",
) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason="STOP")],
        usage_metadata=usage_metadata,
        response_id="response-123",
        model_version="gemini-version-001",
    )


def make_client(
    outcomes: list[Any],
    *,
    max_retries: int = 0,
    retry_backoff_seconds: float = 0.5,
    retry_jitter_ratio: float = 0.0,
    random_uniform: Any = None,
) -> tuple[GeminiClient, FakeModels, list[float]]:
    models = FakeModels(outcomes)
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    kwargs: dict[str, Any] = {}
    if random_uniform is not None:
        kwargs["random_uniform"] = random_uniform
    gemini = GeminiClient(
        api_key=API_KEY,
        model=MODEL,
        timeout_seconds=5.0,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        retry_jitter_ratio=retry_jitter_ratio,
        client=FakeGeminiSdkClient(models),
        sleep=record_sleep,
        **kwargs,
    )
    return gemini, models, sleeps


@pytest.fixture(autouse=True)
def clear_llm_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SCENARIO_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mock_settings(tmp_path: Path) -> Iterator[Settings]:
    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        gemini_api_key=None,
        llm_mock_scenario="success",
        dataset_storage_dir=tmp_path / "phase45-datasets",
        dataset_ttl_hours=1,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def stored_dataset(mock_settings: Settings) -> str:
    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("sales.csv", make_csv(), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["dataset_id"]


def ask(dataset_id: str) -> Any:
    return client.post(
        f"/api/v1/datasets/{dataset_id}/questions",
        json={"question": "Summarize this dataset."},
    )


def generate(dataset_id: str) -> Any:
    return client.post(
        f"/api/v1/datasets/{dataset_id}/generate-code",
        json={"question": "Summarize this dataset."},
    )


# Configuration and mock provider


def test_retry_and_mock_defaults_are_bounded() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_retry_base_delay_seconds == 1.0
    assert settings.llm_retry_max_delay_seconds == 30.0
    assert settings.llm_retry_jitter_ratio == 0.2
    assert settings.llm_mock_scenario == "success"
    assert settings.llm_mock_model == "mock-deterministic"


def test_invalid_mock_scenario_and_delay_range_are_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, llm_mock_scenario="not-a-scenario")
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            llm_retry_base_delay_seconds=10,
            llm_retry_max_delay_seconds=5,
        )


def test_mock_provider_requires_no_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_initialized(*args: object, **kwargs: object) -> None:
        raise AssertionError("Gemini must not be initialized in mock mode.")

    monkeypatch.setattr(gemini_module.genai, "Client", fail_if_initialized)
    settings = Settings(_env_file=None, llm_provider="mock")

    llm_client = create_llm_client(settings)

    assert isinstance(llm_client, MockLLMClient)
    assert llm_client.provider == "mock"
    result = asyncio.run(
        llm_client.generate(
            [LLMMessage("user", "Summarize this dataset.")],
            system_instruction=(
                "You interpret natural-language questions about a CSV dataset."
            ),
        )
    )
    assert json.loads(result.text)["intent"] == "summarize"
    assert result.provider == "mock"


def test_mock_mode_is_never_selected_silently_for_gemini() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="gemini",
        gemini_api_key=None,
    )

    with pytest.raises(LLMConfigurationError) as error:
        create_llm_client(settings)

    assert "GEMINI_API_KEY" in str(error.value)


def test_factory_wires_retry_base_delay_and_jitter_settings() -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key="test-secret-key",
        llm_retry_base_delay_seconds=1.0,
        llm_retry_jitter_ratio=0.0,
        llm_max_retries=1,
    )
    models = FakeModels([asyncio.TimeoutError(), make_success()])
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    llm_client = create_llm_client(
        settings,
        client=FakeGeminiSdkClient(models),
        sleep=record_sleep,
    )
    result = asyncio.run(
        llm_client.generate([LLMMessage("user", "Hello")])
    )

    assert result.text == "Generated answer"
    assert sleeps == [1.0]


# End-to-end mock scenarios


def test_mock_success_returns_validated_code_and_usage(
    stored_dataset: str,
) -> None:
    question = ask(stored_dataset)
    assert question.status_code == 200, question.text
    question_result = question.json()
    assert question_result["status"] == "interpreted"
    assert question_result["usage"]["provider"] == "mock"
    assert question_result["usage"]["model"] == "mock-deterministic"
    assert question_result["usage"]["request_count"] == 1
    assert question_result["usage"]["input_tokens"] > 0

    generated = generate(stored_dataset)
    assert generated.status_code == 200, generated.text
    result = generated.json()
    assert result["status"] == "generated"
    assert result["generated_code"] == "result = df.shape"
    assert result["operation"] == "summarize"
    assert result["usage"]["provider"] == "mock"
    assert result["usage"]["request_count"] == 2
    assert len(result["usage"]["requests"]) == 2
    assert result["interpretation_usage"]["request_count"] == 1
    assert result["code_generation_usage"]["request_count"] == 1


def test_mock_clarification_scenario_skips_code_generation(
    stored_dataset: str,
    mock_settings: Settings,
) -> None:
    mock_settings.llm_mock_scenario = "clarification"

    response = generate(stored_dataset)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "clarification_needed"
    assert result["generated_code"] is None
    assert result["clarification_question"]
    assert result["usage"]["request_count"] == 1
    assert result["code_generation_usage"] is None


@pytest.mark.parametrize(
    ("scenario", "status_code", "error_code", "temporary"),
    [
        ("rate_limit", 429, LLMErrorCode.RATE_LIMIT, "true"),
        ("quota_exceeded", 429, LLMErrorCode.QUOTA_EXCEEDED, "false"),
        ("timeout", 504, LLMErrorCode.TIMEOUT, "true"),
        ("network_error", 503, LLMErrorCode.CONNECTION, "true"),
        ("server_error", 503, LLMErrorCode.TRANSIENT, "true"),
        ("authentication_error", 502, LLMErrorCode.AUTHENTICATION, "false"),
        (
            "unsupported_model",
            502,
            LLMErrorCode.MODEL_UNAVAILABLE,
            "false",
        ),
    ],
)
def test_mock_provider_errors_return_controlled_responses(
    stored_dataset: str,
    mock_settings: Settings,
    scenario: str,
    status_code: int,
    error_code: str,
    temporary: str,
) -> None:
    mock_settings.llm_mock_scenario = scenario

    response = ask(stored_dataset)

    assert response.status_code == status_code
    assert response.headers["x-llm-error-code"] == error_code
    assert response.headers["x-llm-error-temporary"] == temporary
    assert "mock provider simulated" not in response.text.lower()
    if scenario == "rate_limit":
        assert response.headers["retry-after"] == "1"


@pytest.mark.parametrize(
    ("scenario", "error_code", "detail"),
    [
        (
            "malformed",
            LLMErrorCode.VALIDATION,
            "The model returned an invalid question interpretation.",
        ),
        (
            "empty",
            LLMErrorCode.EMPTY_RESPONSE,
            "The model returned an empty question interpretation.",
        ),
    ],
)
def test_mock_malformed_and_empty_output_use_normal_validation(
    stored_dataset: str,
    mock_settings: Settings,
    scenario: str,
    error_code: str,
    detail: str,
) -> None:
    mock_settings.llm_mock_scenario = scenario

    response = ask(stored_dataset)

    assert response.status_code == 502
    assert response.headers["x-llm-error-code"] == error_code
    assert response.json()["detail"] == detail
    assert "not-json" not in response.text


@pytest.mark.parametrize("scenario", ["malformed_code", "empty_code"])
def test_mock_code_validation_failures_are_controlled(
    stored_dataset: str,
    mock_settings: Settings,
    scenario: str,
) -> None:
    mock_settings.llm_mock_scenario = scenario

    response = generate(stored_dataset)

    assert response.status_code == 502
    assert response.headers["x-llm-error-code"] == (
        LLMErrorCode.EMPTY_RESPONSE
        if scenario == "empty_code"
        else LLMErrorCode.VALIDATION
    )
    assert response.json()["detail"] == (
        "The model returned an invalid code generation response."
    )


def test_mock_mode_does_not_initialize_the_gemini_sdk(
    stored_dataset: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_initialized(*args: object, **kwargs: object) -> None:
        raise AssertionError("Gemini must not be initialized in mock mode.")

    monkeypatch.setattr(gemini_module.genai, "Client", fail_if_initialized)

    assert ask(stored_dataset).status_code == 200
    assert generate(stored_dataset).status_code == 200


# Usage metadata


def test_complete_usage_is_normalized() -> None:
    response = LLMResponse(
        text="answer",
        provider="gemini",
        model=MODEL,
        prompt_tokens=11,
        completion_tokens=7,
        total_tokens=18,
        response_id="response-123",
        retry_count=1,
        request_duration_seconds=1.25,
    )

    usage = usage_from_response(response)

    assert usage.provider == "gemini"
    assert usage.model == MODEL
    assert usage.input_tokens == 11
    assert usage.output_tokens == 7
    assert usage.total_tokens == 18
    assert usage.request_id == "response-123"
    assert usage.request_duration_seconds == 1.25
    assert usage.retry_count == 1


@pytest.mark.parametrize(
    ("usage_metadata", "expected"),
    [
        (None, (None, None, None)),
        (
            SimpleNamespace(
                prompt_token_count=11,
                candidates_token_count=None,
                total_token_count=None,
            ),
            (11, None, None),
        ),
        (
            SimpleNamespace(
                prompt_token_count=None,
                candidates_token_count=7,
                total_token_count=7,
            ),
            (None, 7, 7),
        ),
    ],
)
def test_missing_or_partial_gemini_usage_is_not_fabricated(
    usage_metadata: Any,
    expected: tuple[Optional[int], Optional[int], Optional[int]],
) -> None:
    gemini, models, sleeps = make_client(
        [make_success(usage_metadata=usage_metadata)]
    )

    result = asyncio.run(
        gemini.generate([LLMMessage("user", "Hello")])
    )

    assert (
        result.input_tokens,
        result.output_tokens,
        result.total_tokens,
    ) == expected
    assert models.calls == 1
    assert sleeps == []


def test_combined_usage_does_not_invent_absent_values() -> None:
    first = LLMUsage(
        provider="gemini",
        model=MODEL,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        request_id="first",
        request_duration_seconds=1.0,
        retry_count=1,
    )
    second = LLMUsage(
        provider="gemini",
        model=MODEL,
        input_tokens=20,
        output_tokens=7,
        total_tokens=None,
        request_id="second",
        request_duration_seconds=None,
    )

    combined = combine_usage([first, second])

    assert combined is not None
    assert combined.input_tokens == 30
    assert combined.output_tokens == 12
    assert combined.total_tokens is None
    assert combined.request_duration_seconds is None
    assert combined.retry_count == 1
    assert combined.request_count == 2
    assert combined.request_id is None
    assert len(combined.requests) == 2


def test_usage_metadata_is_included_in_question_response(
    mock_settings: Settings,
    stored_dataset: str,
) -> None:
    response = ask(stored_dataset)

    assert response.status_code == 200
    usage = response.json()["usage"]
    assert usage["provider"] == "mock"
    assert usage["input_tokens"] is not None
    assert usage["output_tokens"] is not None
    assert usage["total_tokens"] == (
        usage["input_tokens"] + usage["output_tokens"]
    )


# Retries, timeouts, cancellation, and error mapping


def test_transient_failure_then_success_uses_configured_backoff() -> None:
    gemini, models, sleeps = make_client(
        [asyncio.TimeoutError(), make_success()],
        max_retries=1,
        retry_backoff_seconds=1.0,
    )

    result = asyncio.run(
        gemini.generate([LLMMessage("user", "Hello")])
    )

    assert result.text == "Generated answer"
    assert result.retry_count == 1
    assert models.calls == 2
    assert sleeps == [1.0]


def test_exponential_backoff_is_jittered_and_capped() -> None:
    gemini, models, sleeps = make_client(
        [
            asyncio.TimeoutError(),
            asyncio.TimeoutError(),
            make_success(),
        ],
        max_retries=2,
        retry_backoff_seconds=1.0,
        retry_jitter_ratio=0.5,
        random_uniform=lambda low, high: high,
    )

    asyncio.run(gemini.generate([LLMMessage("user", "Hello")]))

    assert models.calls == 3
    assert sleeps == [1.5, 3.0]


def test_retries_stop_at_the_configured_maximum() -> None:
    gemini, models, sleeps = make_client(
        [
            asyncio.TimeoutError(),
            asyncio.TimeoutError(),
            asyncio.TimeoutError(),
        ],
        max_retries=2,
        retry_backoff_seconds=0.25,
    )

    with pytest.raises(LLMTimeoutError):
        asyncio.run(gemini.generate([LLMMessage("user", "Hello")]))

    assert models.calls == 3
    assert sleeps == [0.25, 0.5]


@pytest.mark.parametrize(
    "error",
    [
        LLMAuthenticationError("Authentication failed."),
        LLMModelUnavailableError("Model unavailable."),
        LLMQuotaExceededError("Quota exhausted."),
        LLMInvalidRequestError("Invalid request."),
    ],
)
def test_permanent_errors_are_not_retried(error: Exception) -> None:
    gemini, models, sleeps = make_client([error], max_retries=3)

    with pytest.raises(type(error)):
        asyncio.run(
            gemini.generate([LLMMessage("user", "Hello")])
        )

    assert models.calls == 1
    assert sleeps == []


def test_cancellation_is_propagated_without_retry() -> None:
    gemini = GeminiClient(
        api_key=API_KEY,
        model=MODEL,
        timeout_seconds=5.0,
        max_retries=3,
        client=CancelledGeminiSdkClient(),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(gemini.generate([LLMMessage("user", "Hello")]))


@pytest.mark.parametrize(
    ("error", "status_code", "code", "temporary"),
    [
        (LLMTimeoutError("timeout"), 504, LLMErrorCode.TIMEOUT, True),
        (
            LLMRateLimitError("rate", retry_after=2.2),
            429,
            LLMErrorCode.RATE_LIMIT,
            True,
        ),
        (
            LLMQuotaExceededError("quota"),
            429,
            LLMErrorCode.QUOTA_EXCEEDED,
            False,
        ),
        (
            LLMAuthenticationError("auth"),
            502,
            LLMErrorCode.AUTHENTICATION,
            False,
        ),
        (
            LLMConnectionError("network"),
            503,
            LLMErrorCode.CONNECTION,
            True,
        ),
        (
            LLMTransientError("temporary"),
            503,
            LLMErrorCode.TRANSIENT,
            True,
        ),
        (
            LLMInvalidRequestError("invalid"),
            502,
            LLMErrorCode.INVALID_REQUEST,
            False,
        ),
    ],
)
def test_internal_errors_have_consistent_http_mappings(
    error: Exception,
    status_code: int,
    code: str,
    temporary: bool,
) -> None:
    mapped = map_llm_error(error)

    assert mapped is not None
    assert mapped.status_code == status_code
    assert mapped.code == code
    assert mapped.temporary is temporary
    assert mapped.headers is not None
    assert mapped.headers["X-LLM-Error-Code"] == code
    if isinstance(error, LLMRateLimitError):
        assert mapped.headers["Retry-After"] == "3"


def test_logs_include_usage_but_not_prompts_or_api_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.llm")
    usage = SimpleNamespace(
        prompt_token_count=11,
        candidates_token_count=7,
        total_token_count=18,
    )
    gemini, _, _ = make_client([make_success(usage_metadata=usage)])

    asyncio.run(
        gemini.generate(
            [LLMMessage("user", "CUSTOMER_SECRET_QUESTION")],
            system_instruction="SENSITIVE_SYSTEM_INSTRUCTION",
        )
    )

    assert "input_tokens=11" in caplog.text
    assert "output_tokens=7" in caplog.text
    assert "retry_count=0" in caplog.text
    assert API_KEY not in caplog.text
    assert "CUSTOMER_SECRET_QUESTION" not in caplog.text
    assert "SENSITIVE_SYSTEM_INSTRUCTION" not in caplog.text
