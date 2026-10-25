"""Environment-backed application settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings populated from process environment variables."""

    model_config = SettingsConfigDict(case_sensitive=False)

    app_env: Literal["development", "production"] = "development"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
