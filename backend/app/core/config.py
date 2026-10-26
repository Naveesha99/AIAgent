"""Environment-backed application settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    """Return the cached application settings."""
    return Settings()
