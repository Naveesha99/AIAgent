"""Environment-backed application settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.execution_limits import (
    ABSOLUTE_EXECUTION_MAX_CODE_SIZE_BYTES,
    ABSOLUTE_EXECUTION_MAX_STDERR_BYTES,
    ABSOLUTE_EXECUTION_MAX_STDOUT_BYTES,
    ABSOLUTE_EXECUTION_MAX_TIMEOUT_SECONDS,
    DEFAULT_EXECUTION_MAX_CODE_SIZE_BYTES,
    DEFAULT_EXECUTION_MAX_STDERR_BYTES,
    DEFAULT_EXECUTION_MAX_STDOUT_BYTES,
    DEFAULT_EXECUTION_MAX_TIMEOUT_SECONDS,
)
from app.core.llm_scenarios import DEFAULT_MOCK_SCENARIO, MOCK_SCENARIOS


BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Settings populated from process environment variables."""

    model_config = SettingsConfigDict(case_sensitive=False)

    app_env: Literal["development", "production"] = "development"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    max_upload_size_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_csv_columns: int = Field(default=200, gt=0)
    max_csv_rows: int = Field(default=100_000, gt=0)
    max_csv_field_size: int = Field(default=1024 * 1024, gt=0)
    max_csv_validation_lines: int = Field(default=200_000, gt=0)
    data_quality_sample_limit: int = Field(default=5, ge=0, le=20)
    analytics_max_result_rows: int = Field(default=1000, gt=0)
    analytics_max_groups: int = Field(default=100, gt=0)
    analytics_max_value_frequencies: int = Field(default=10, ge=1)
    dataset_storage_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2]
        / "data"
        / "datasets"
    )
    dataset_ttl_hours: int = Field(default=24, gt=0)
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000"
    )
    llm_provider: str = Field(default="gemini", min_length=1)
    llm_model: str = Field(default="gemini-3.5-flash-lite", min_length=1)
    gemini_api_key: Optional[SecretStr] = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_retry_base_delay_seconds: float = Field(default=1.0, ge=0, le=60)
    llm_retry_max_delay_seconds: float = Field(default=30.0, ge=0, le=300)
    llm_retry_jitter_ratio: float = Field(default=0.2, ge=0, le=1)
    llm_mock_scenario: str = Field(default=DEFAULT_MOCK_SCENARIO, min_length=1)
    llm_mock_model: str = Field(default="mock-deterministic", min_length=1)
    execution_enabled: bool = False
    execution_max_timeout_seconds: float = Field(
        default=DEFAULT_EXECUTION_MAX_TIMEOUT_SECONDS,
        gt=0,
        le=ABSOLUTE_EXECUTION_MAX_TIMEOUT_SECONDS,
    )
    execution_max_code_size_bytes: int = Field(
        default=DEFAULT_EXECUTION_MAX_CODE_SIZE_BYTES,
        gt=0,
        le=ABSOLUTE_EXECUTION_MAX_CODE_SIZE_BYTES,
    )
    execution_max_stdout_bytes: int = Field(
        default=DEFAULT_EXECUTION_MAX_STDOUT_BYTES,
        gt=0,
        le=ABSOLUTE_EXECUTION_MAX_STDOUT_BYTES,
    )
    execution_max_stderr_bytes: int = Field(
        default=DEFAULT_EXECUTION_MAX_STDERR_BYTES,
        gt=0,
        le=ABSOLUTE_EXECUTION_MAX_STDERR_BYTES,
    )

    @field_validator("llm_provider", mode="before")
    @classmethod
    def normalize_llm_provider(cls, value: object) -> object:
        """Normalize the provider name so environment values are case-insensitive."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("llm_model", mode="before")
    @classmethod
    def normalize_llm_model(cls, value: object) -> object:
        """Reject surrounding whitespace in configured model identifiers."""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("llm_mock_model", mode="before")
    @classmethod
    def normalize_llm_mock_model(cls, value: object) -> object:
        """Strip surrounding whitespace from the mock model identifier."""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("llm_mock_scenario", mode="before")
    @classmethod
    def normalize_mock_scenario(cls, value: object) -> object:
        """Normalize and validate the deterministic mock scenario name."""
        if isinstance(value, str):
            scenario = value.strip().lower()
            if scenario not in MOCK_SCENARIOS:
                raise ValueError(
                    "LLM_MOCK_SCENARIO must be one of: "
                    + ", ".join(sorted(MOCK_SCENARIOS))
                )
            return scenario
        return value

    @model_validator(mode="after")
    def validate_retry_delays(self) -> "Settings":
        """Keep the exponential backoff delay range coherent."""
        if (
            self.llm_retry_base_delay_seconds
            > self.llm_retry_max_delay_seconds
        ):
            raise ValueError(
                "LLM_RETRY_BASE_DELAY_SECONDS must not exceed "
                "LLM_RETRY_MAX_DELAY_SECONDS."
            )
        return self

    @property
    def allowed_origins(self) -> list[str]:
        """Parse the comma-separated list of explicitly allowed browser origins."""
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Return cached settings, loading backend/.env when present."""
    return Settings(_env_file=BACKEND_ENV_FILE)
