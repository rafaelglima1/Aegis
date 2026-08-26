"""AEGIS configuration — single source of truth for all operational config.

Every operational parameter must flow through Settings.
No direct os.getenv() for operational configuration.
"""

from __future__ import annotations

import hmac
from decimal import Decimal, InvalidOperation
from enum import Enum
from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


class TradingEnvironment(str, Enum):
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"


# Fields that must never appear in repr/str/logs
_SECRET_FIELDS = frozenset({
    "llm_api_key",
    "sandbox_api_key",
    "sandbox_api_secret",
    "live_api_key",
    "live_api_secret",
    "aegis_api_key",
})


class Settings(BaseSettings):
    """AC1: Single source of truth for all AEGIS configuration.

    Every operational parameter flows through here.
    No parallel env access in other modules.
    """

    model_config = {"env_file": ".env", "extra": "ignore"}

    # --- Application ---
    app_name: str = Field(default="AEGIS", description="Application name")
    app_version: str = Field(default="1.3.0", description="Application version")
    log_level: str = Field(default="INFO", description="Log level")

    # --- Environment ---
    trading_environment: TradingEnvironment = Field(
        default=TradingEnvironment.SANDBOX,
        validation_alias=AliasChoices("trading_environment", "TRADING_ENVIRONMENT"),
        description="Trading environment: SANDBOX or LIVE",
    )
    live_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("live_enabled", "LIVE_ENABLED"),
        description="Live trading enabled",
    )
    live_confirmation_required: bool = Field(
        default=True,
        validation_alias=AliasChoices("live_confirmation_required", "LIVE_CONFIRMATION_REQUIRED"),
        description="Live confirmation required",
    )

    # --- Capital & Positions ---
    initial_capital: Decimal = Field(
        default=Decimal("100.00"),
        validation_alias=AliasChoices("initial_capital", "TRADING_CAPITAL"),
        description="Initial capital in BRL",
    )
    max_positions: int = Field(
        default=1,
        validation_alias=AliasChoices("max_positions", "MAX_POSITIONS"),
        description="Max simultaneous positions",
    )

    # --- Trading ---
    trading_symbols: str = Field(
        default="BTC-BRL,ETH-BRL",
        validation_alias=AliasChoices("trading_symbols", "TRADING_SYMBOLS"),
        description="Comma-separated trading symbols",
    )
    trading_timeframe: str = Field(
        default="1h",
        validation_alias=AliasChoices("trading_timeframe", "TRADING_TIMEFRAME"),
        description="Trading timeframe",
    )
    long_only: bool = Field(
        default=True,
        validation_alias=AliasChoices("long_only", "LONG_ONLY"),
        description="Long only mode",
    )

    # --- Risk Parameters ---
    risk_per_trade_pct: Decimal = Field(
        default=Decimal("1.0"),
        validation_alias=AliasChoices("risk_per_trade_pct", "RISK_PER_TRADE_PCT"),
        description="Risk per trade percentage",
    )
    max_daily_loss_pct: Decimal = Field(
        default=Decimal("5.0"),
        validation_alias=AliasChoices("max_daily_loss_pct", "MAX_DAILY_LOSS_PCT"),
        description="Maximum daily loss percentage",
    )
    max_position_size_pct: Decimal = Field(
        default=Decimal("20.0"),
        validation_alias=AliasChoices("max_position_size_pct", "MAX_POSITION_SIZE_PCT"),
        description="Maximum position size percentage",
    )
    max_exposure_pct: Decimal = Field(
        default=Decimal("100.0"),
        validation_alias=AliasChoices("max_exposure_pct", "MAX_EXPOSURE_PCT"),
        description="Maximum exposure percentage",
    )
    circuit_breaker_pct: Decimal = Field(
        default=Decimal("10.0"),
        validation_alias=AliasChoices("circuit_breaker_pct", "CIRCUIT_BREAKER_PCT"),
        description="Circuit breaker drawdown percentage",
    )
    min_confidence: Decimal = Field(
        default=Decimal("0.50"),
        validation_alias=AliasChoices("min_confidence", "MIN_CONFIDENCE"),
        description="Minimum confidence for LONG",
    )
    mandatory_stop: bool = Field(
        default=True,
        validation_alias=AliasChoices("mandatory_stop", "MANDATORY_STOP"),
        description="Stop loss mandatory",
    )
    mandatory_take_profit: bool = Field(
        default=True,
        validation_alias=AliasChoices("mandatory_take_profit", "MANDATORY_TAKE_PROFIT"),
        description="Take profit mandatory",
    )

    # --- LLM ---
    llm_provider: str = Field(default="kilo", description="LLM provider")
    llm_api_key: str = Field(default="", description="LLM API key")
    llm_base_url: str = Field(
        default="https://api.kilo.ai/api/gateway",
        validation_alias=AliasChoices("llm_base_url", "LLM_BASE_URL"),
        description="LLM base URL",
    )
    llm_model: str = Field(
        default="kilo-auto/free",
        validation_alias=AliasChoices("llm_model", "LLM_MODEL"),
        description="LLM model",
    )

    # --- Exchange Credentials ---
    sandbox_api_key: str = Field(default="", description="Sandbox API key")
    sandbox_api_secret: str = Field(default="", description="Sandbox API secret")
    live_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("live_api_key", "MB_API_KEY", "LIVE_API_KEY"),
        description="Live exchange API key",
    )
    live_api_secret: str = Field(
        default="",
        validation_alias=AliasChoices("live_api_secret", "MB_API_SECRET", "LIVE_API_SECRET"),
        description="Live exchange API secret",
    )

    # --- API Authentication ---
    aegis_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("aegis_api_key", "AEGIS_API_KEY"),
        description="API authentication key for sensitive endpoints",
    )

    # --- Infrastructure ---
    database_url: str = Field(
        default="postgresql+asyncpg://aegis:aegis@localhost:5432/aegis",
        description="Database URL",
    )
    database_echo: bool = Field(default=False, description="Database echo SQL")
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis URL")

    # --- Reconciliation ---
    reconciliation_interval_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "reconciliation_interval_seconds",
            "RECONCILIATION_INTERVAL_SECONDS",
        ),
        description="Seconds between periodic reconciliation checks (0=disabled)",
    )

    # --- Validators ---
    @field_validator("trading_environment", mode="after")
    @classmethod
    def validate_trading_environment(cls, v: TradingEnvironment) -> TradingEnvironment:
        if v not in (TradingEnvironment.SANDBOX, TradingEnvironment.LIVE):
            raise ValueError(f"TRADING_ENVIRONMENT must be SANDBOX or LIVE, got: {v}")
        return v

    @field_validator("initial_capital", mode="before")
    @classmethod
    def validate_initial_capital(cls, v: Any) -> Decimal:
        if isinstance(v, str):
            v = v.strip()
        try:
            d = Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise ValueError(f"TRADING_CAPITAL must be a valid decimal, got: {v}")
        if d <= 0:
            raise ValueError(f"TRADING_CAPITAL must be positive, got: {d}")
        return d

    @field_validator("max_positions", mode="before")
    @classmethod
    def validate_max_positions(cls, v: Any) -> int:
        try:
            i = int(v)
        except (ValueError, TypeError):
            raise ValueError(f"MAX_POSITIONS must be a valid integer, got: {v}")
        if i != 1:
            raise ValueError(
                f"MAX_POSITIONS must be exactly 1 in V1.3. "
                f"Received {i}. Single position only."
            )
        return i

    @field_validator("risk_per_trade_pct", mode="before")
    @classmethod
    def validate_risk_per_trade(cls, v: Any) -> Decimal:
        try:
            d = Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise ValueError(f"RISK_PER_TRADE_PCT must be a valid decimal, got: {v}")
        if d <= 0 or d > 100:
            raise ValueError(f"RISK_PER_TRADE_PCT must be between 0 and 100, got: {d}")
        return d

    @field_validator("max_daily_loss_pct", mode="before")
    @classmethod
    def validate_max_daily_loss(cls, v: Any) -> Decimal:
        try:
            d = Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise ValueError(f"MAX_DAILY_LOSS_PCT must be a valid decimal, got: {v}")
        if d <= 0 or d > 100:
            raise ValueError(f"MAX_DAILY_LOSS_PCT must be between 0 and 100, got: {d}")
        return d

    @field_validator("max_position_size_pct", mode="before")
    @classmethod
    def validate_max_position_size(cls, v: Any) -> Decimal:
        try:
            d = Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise ValueError(f"MAX_POSITION_SIZE_PCT must be a valid decimal, got: {v}")
        if d <= 0 or d > 100:
            raise ValueError(f"MAX_POSITION_SIZE_PCT must be between 0 and 100, got: {d}")
        return d

    @field_validator("max_exposure_pct", mode="before")
    @classmethod
    def validate_max_exposure(cls, v: Any) -> Decimal:
        try:
            d = Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise ValueError(f"MAX_EXPOSURE_PCT must be a valid decimal, got: {v}")
        if d <= 0 or d > 100:
            raise ValueError(f"MAX_EXPOSURE_PCT must be between 0 and 100, got: {d}")
        return d

    @field_validator("circuit_breaker_pct", mode="before")
    @classmethod
    def validate_circuit_breaker(cls, v: Any) -> Decimal:
        try:
            d = Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise ValueError(f"CIRCUIT_BREAKER_PCT must be a valid decimal, got: {v}")
        if d <= 0 or d > 100:
            raise ValueError(f"CIRCUIT_BREAKER_PCT must be between 0 and 100, got: {d}")
        return d

    @field_validator("min_confidence", mode="before")
    @classmethod
    def validate_min_confidence(cls, v: Any) -> Decimal:
        try:
            d = Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise ValueError(f"MIN_CONFIDENCE must be a valid decimal, got: {v}")
        if d < 0 or d > 1:
            raise ValueError(f"MIN_CONFIDENCE must be between 0 and 1, got: {d}")
        return d

    @field_validator("risk_per_trade_pct", mode="after")
    @classmethod
    def normalize_risk_pct(cls, v: Decimal) -> Decimal:
        """Normalize: if user sends 1.0 meaning 1%, keep as 1.0 (percentage)."""
        return v

    @property
    def trading_symbols_list(self) -> list[str]:
        """Parse comma-separated symbols into list."""
        return [s.strip() for s in self.trading_symbols.split(",") if s.strip()]

    @property
    def risk_per_trade_decimal(self) -> Decimal:
        """Risk per trade as decimal fraction (e.g., 1.0% -> 0.01)."""
        return self.risk_per_trade_pct / Decimal("100")

    @property
    def max_daily_loss_decimal(self) -> Decimal:
        """Max daily loss as decimal fraction."""
        return self.max_daily_loss_pct / Decimal("100")

    @property
    def max_position_size_decimal(self) -> Decimal:
        """Max position size as decimal fraction."""
        return self.max_position_size_pct / Decimal("100")

    @property
    def max_exposure_decimal(self) -> Decimal:
        """Max exposure as decimal fraction."""
        return self.max_exposure_pct / Decimal("100")

    @property
    def circuit_breaker_decimal(self) -> Decimal:
        """Circuit breaker as decimal fraction."""
        return self.circuit_breaker_pct / Decimal("100")

    def _masked_dict(self) -> dict[str, Any]:
        """Return field values with secrets masked."""
        d = self.model_dump()
        for k in d:
            if k in _SECRET_FIELDS and d[k]:
                d[k] = "***REDACTED***"
        return d

    def __repr__(self) -> str:
        """Secret-safe repr — masks API keys and secrets."""
        parts = ", ".join(f"{k}={v!r}" for k, v in self._masked_dict().items())
        return f"Settings({parts})"

    def __str__(self) -> str:
        """Secret-safe str — masks API keys and secrets."""
        return " ".join(f"{k}={v!r}" for k, v in self._masked_dict().items())


# AC2: Cached singleton — all access goes through here
@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
