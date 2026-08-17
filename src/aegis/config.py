"""AEGIS configuration — loaded from environment, never hardcoded."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class TradingEnvironment(str, Enum):
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = {"env_file": ".env", "extra": "ignore"}

    app_name: str = Field(default="AEGIS", description="Application name")
    app_version: str = Field(default="1.3.0", description="Application version")
    log_level: str = Field(default="INFO", description="Log level")

    trading_environment: TradingEnvironment = Field(
        default=TradingEnvironment.SANDBOX,
        description="Trading environment: SANDBOX or LIVE",
    )

    live_enabled: bool = Field(default=False, description="Live trading enabled")
    live_confirmation_required: bool = Field(
        default=True, description="Live confirmation required"
    )

    database_url: str = Field(
        default="postgresql+asyncpg://aegis:aegis@localhost:5432/aegis",
        description="Database URL",
    )
    database_echo: bool = Field(default=False, description="Database echo SQL")

    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis URL")

    llm_provider: str = Field(default="kilo", description="LLM provider")
    llm_api_key: str = Field(default="", description="LLM API key")
    llm_base_url: str = Field(default="", description="LLM base URL (OpenAI-compatible)")
    llm_model: str = Field(default="kilo-auto/free", description="LLM model")

    sandbox_api_key: str = Field(default="", description="Sandbox API key")
    sandbox_api_secret: str = Field(
        default="", description="Sandbox API secret"
    )

    live_api_key: str = Field(default="", description="Live API key")
    live_api_secret: str = Field(default="", description="Live API secret")

    @field_validator("trading_environment", mode="after")
    @classmethod
    def validate_trading_environment(cls, v: TradingEnvironment) -> TradingEnvironment:
        if v not in (TradingEnvironment.SANDBOX, TradingEnvironment.LIVE):
            raise ValueError(f"TRADING_ENVIRONMENT must be SANDBOX or LIVE, got: {v}")
        return v


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
