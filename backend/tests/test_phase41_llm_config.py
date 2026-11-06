"""Tests for Gemini configuration, provider selection, and DI."""

from pathlib import Path
from typing import Any, Optional, Sequence

import pytest
from pydantic import ValidationError

import app.core.config as config_module
import app.services.llm.gemini_client as gemini_module
from app.api import dependencies
from app.core.config import Settings
from app.services.llm import (
    LLMClient,
    LLMConfigurationError,
    LLMMessage,
    LLMResponse,
    create_llm_client,
)

LLM_ENVIRONMENT_VARIABLES = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "GEMINI_API_KEY",
    "LLM_TIMEOUT_SECONDS",
    "LLM_MAX_RETRIES",
)


@pytest.fixture(autouse=True)
def clear_llm_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep tests independent of developer shell configuration."""
    for name in LLM_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


class EmptyModels:
    async def generate_content(self, **kwargs: Any) -> Any:
        raise AssertionError("No provider request should be made.")


class EmptyAio:
    def __init__(self) -> None:
        self.models = EmptyModels()


class EmptyGeminiClient:
    def __init__(self) -> None:
        self.aio = EmptyAio()


class MockLLMClient:
    provider = "gemini"
    model = "mock-model"

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        system_instruction: Optional[str] = None,
    ) -> LLMResponse:
        return LLMResponse(
            text="mock",
            provider=self.provider,
            model=self.model,
        )


def test_default_llm_configuration() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_provider == "gemini"
    assert settings.llm_model == "gemini-3.5-flash-lite"
    assert settings.gemini_api_key is None
    assert settings.llm_timeout_seconds == 30.0
    assert settings.llm_max_retries == 2


def test_llm_environment_variables_override_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "  Gemini  ")
    monkeypatch.setenv("LLM_MODEL", "  gemini-test-model  ")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "gemini"
    assert settings.llm_model == "gemini-test-model"
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "test-secret-key"
    assert settings.llm_timeout_seconds == 12.5
    assert settings.llm_max_retries == 3


def test_get_settings_loads_backend_dotenv_with_process_env_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODEL=gemini-from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "BACKEND_ENV_FILE", env_file)
    config_module.get_settings.cache_clear()

    try:
        assert config_module.get_settings().llm_model == "gemini-from-dotenv"

        monkeypatch.setenv("LLM_MODEL", "gemini-from-process-env")
        config_module.get_settings.cache_clear()

        assert (
            config_module.get_settings().llm_model
            == "gemini-from-process-env"
        )
    finally:
        config_module.get_settings.cache_clear()


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_timeout_seconds": 0},
        {"llm_timeout_seconds": -1},
        {"llm_timeout_seconds": 601},
        {"llm_max_retries": -1},
        {"llm_max_retries": 6},
        {"llm_provider": ""},
        {"llm_model": ""},
    ],
)
def test_invalid_llm_settings_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_settings_do_not_reveal_api_key_in_representations() -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key="super-secret-key",
    )

    assert "super-secret-key" not in repr(settings)
    assert "super-secret-key" not in str(settings)


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_missing_gemini_api_key_is_a_clear_configuration_error(
    api_key: Optional[str],
) -> None:
    settings = Settings(_env_file=None, gemini_api_key=api_key)

    with pytest.raises(LLMConfigurationError) as error:
        create_llm_client(settings)

    assert "GEMINI_API_KEY" in str(error.value)


def test_unsupported_provider_is_rejected_clearly() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="ollama",
        gemini_api_key="test-secret-key",
    )

    with pytest.raises(LLMConfigurationError) as error:
        create_llm_client(settings)

    message = str(error.value)
    assert "Unsupported LLM provider 'ollama'" in message
    assert "gemini" in message


def test_factory_injects_mocked_client_and_selects_configured_model() -> None:
    fake_client = EmptyGeminiClient()
    settings = Settings(
        _env_file=None,
        llm_model="gemini-custom-model",
        gemini_api_key="test-secret-key",
    )

    client = create_llm_client(settings, client=fake_client)

    assert isinstance(client, LLMClient)
    assert client.provider == "gemini"
    assert client.model == "gemini-custom-model"


def test_factory_initializes_sdk_client_without_network_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class RecordingSdkClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(gemini_module.genai, "Client", RecordingSdkClient)
    settings = Settings(
        _env_file=None,
        gemini_api_key="test-secret-key",
        llm_timeout_seconds=12.5,
    )

    client = create_llm_client(settings)

    assert client.provider == "gemini"
    assert captured["api_key"] == "test-secret-key"
    assert captured["http_options"].timeout == 12500


def test_get_llm_client_delegates_to_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MockLLMClient()
    captured_settings: dict[str, Settings] = {}

    def fake_factory(settings: Settings) -> LLMClient:
        captured_settings["value"] = settings
        return fake_client

    monkeypatch.setattr(dependencies, "create_llm_client", fake_factory)
    settings = Settings(
        _env_file=None,
        gemini_api_key="test-secret-key",
    )

    assert dependencies.get_llm_client(settings) is fake_client
    assert captured_settings["value"] is settings
