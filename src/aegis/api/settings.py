"""AEGIS Settings API — manage API keys and configuration."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_FILE = Path("/home/ubuntu/aegis/.env.prod")


class LLMSettings(BaseModel):
    base_url: str = Field(default="https://api.openai.com/v1", description="LLM API base URL")
    api_key: str = Field(default="", description="LLM API key")
    model: str = Field(default="gpt-4", description="Model name")


class BrokerSettings(BaseModel):
    api_key: str = Field(default="", description="Mercado Bitcoin API key")
    api_secret: str = Field(default="", description="Mercado Bitcoin API secret")


class TradingSettings(BaseModel):
    trading_environment: str = Field(default="SANDBOX", description="SANDBOX or LIVE")
    live_enabled: bool = Field(default=False, description="Enable live trading")
    symbols: str = Field(default="BTC-BRL,ETH-BRL,SOL-BRL", description="Trading pairs, comma-separated")
    timeframe: str = Field(default="1h", description="Primary candle timeframe (1h)")
    timeframes: str = Field(default="1d,4h,1h", description="All timeframes: 1d,4h,1h")
    capital: float = Field(default=100.0, description="Virtual capital in BRL")
    risk_per_trade_pct: float = Field(default=1.0, description="Risk per trade %")
    max_positions: int = Field(default=1, description="Max simultaneous positions")
    circuit_breaker_pct: float = Field(default=10.0, description="Circuit breaker drawdown %")
    long_only: bool = Field(default=True, description="Long only (no SHORT)")
    leverage: int = Field(default=0, description="Leverage (0 = spot only)")
    instrument: str = Field(default="SPOT", description="Instrument type: SPOT")
    # Risk engine rules
    mandatory_stop: bool = Field(default=True, description="Stop loss mandatory")
    mandatory_take_profit: bool = Field(default=True, description="Take profit mandatory")
    max_daily_loss_pct: float = Field(default=5.0, description="Max daily loss %")
    max_position_size_pct: float = Field(default=20.0, description="Max position size % of capital")
    max_exposure_pct: float = Field(default=100.0, description="Max total exposure % of capital")
    min_confidence: float = Field(default=0.5, description="Min AI confidence to trade (0-1)")


class AllSettings(BaseModel):
    llm: LLMSettings
    broker: BrokerSettings
    trading: TradingSettings


def _read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if SETTINGS_FILE.exists():
        for line in SETTINGS_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def _write_env(data: dict[str, str]) -> None:
    lines = []
    for key, value in data.items():
        lines.append(f"{key}={value}")
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text("\n".join(lines) + "\n")


@router.get("", response_model=AllSettings)
async def get_settings() -> AllSettings:
    env = _read_env()
    return AllSettings(
        llm=LLMSettings(
            base_url=env.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            api_key=_mask(env.get("LLM_API_KEY", "")),
            model=env.get("LLM_MODEL", "gpt-4"),
        ),
        broker=BrokerSettings(
            api_key=_mask(env.get("MB_API_KEY", "")),
            api_secret=_mask(env.get("MB_API_SECRET", "")),
        ),
        trading=TradingSettings(
            trading_environment=env.get("TRADING_ENVIRONMENT", "SANDBOX"),
            live_enabled=env.get("LIVE_ENABLED", "false").lower() == "true",
            symbols=env.get("TRADING_SYMBOLS", "BTC-BRL,ETH-BRL,SOL-BRL"),
            timeframe=env.get("TRADING_TIMEFRAME", "1h"),
            timeframes=env.get("TRADING_TIMEFRAMES", "1d,4h,1h"),
            capital=float(env.get("TRADING_CAPITAL", "100.0")),
            risk_per_trade_pct=float(env.get("RISK_PER_TRADE_PCT", "1.0")),
            max_positions=int(env.get("MAX_POSITIONS", "1")),
            circuit_breaker_pct=float(env.get("CIRCUIT_BREAKER_PCT", "10.0")),
            long_only=env.get("LONG_ONLY", "true").lower() == "true",
            leverage=int(env.get("LEVERAGE", "0")),
            instrument=env.get("INSTRUMENT", "SPOT"),
            mandatory_stop=env.get("MANDATORY_STOP", "true").lower() == "true",
            mandatory_take_profit=env.get("MANDATORY_TAKE_PROFIT", "true").lower() == "true",
            max_daily_loss_pct=float(env.get("MAX_DAILY_LOSS_PCT", "5.0")),
            max_position_size_pct=float(env.get("MAX_POSITION_SIZE_PCT", "20.0")),
            max_exposure_pct=float(env.get("MAX_EXPOSURE_PCT", "100.0")),
            min_confidence=float(env.get("MIN_CONFIDENCE", "0.5")),
        ),
    )


@router.put("/llm")
async def update_llm(settings: LLMSettings) -> dict[str, str]:
    env = _read_env()
    if settings.api_key and not settings.api_key.startswith("***"):
        env["LLM_API_KEY"] = settings.api_key
    env["LLM_BASE_URL"] = settings.base_url
    env["LLM_MODEL"] = settings.model
    _write_env(env)
    return {"status": "ok", "message": "LLM settings updated"}


@router.put("/broker")
async def update_broker(settings: BrokerSettings) -> dict[str, str]:
    env = _read_env()
    if settings.api_key and not settings.api_key.startswith("***"):
        env["MB_API_KEY"] = settings.api_key
    if settings.api_secret and not settings.api_secret.startswith("***"):
        env["MB_API_SECRET"] = settings.api_secret
    _write_env(env)
    return {"status": "ok", "message": "Broker settings updated"}


@router.put("/trading")
async def update_trading(settings: TradingSettings) -> dict[str, str]:
    env = _read_env()
    env["TRADING_ENVIRONMENT"] = settings.trading_environment
    env["LIVE_ENABLED"] = str(settings.live_enabled).lower()
    env["TRADING_SYMBOLS"] = settings.symbols
    env["TRADING_TIMEFRAME"] = settings.timeframe
    env["TRADING_TIMEFRAMES"] = settings.timeframes
    env["TRADING_CAPITAL"] = str(settings.capital)
    env["RISK_PER_TRADE_PCT"] = str(settings.risk_per_trade_pct)
    env["MAX_POSITIONS"] = str(settings.max_positions)
    env["CIRCUIT_BREAKER_PCT"] = str(settings.circuit_breaker_pct)
    env["LONG_ONLY"] = str(settings.long_only).lower()
    env["LEVERAGE"] = str(settings.leverage)
    env["INSTRUMENT"] = settings.instrument
    env["MANDATORY_STOP"] = str(settings.mandatory_stop).lower()
    env["MANDATORY_TAKE_PROFIT"] = str(settings.mandatory_take_profit).lower()
    env["MAX_DAILY_LOSS_PCT"] = str(settings.max_daily_loss_pct)
    env["MAX_POSITION_SIZE_PCT"] = str(settings.max_position_size_pct)
    env["MAX_EXPOSURE_PCT"] = str(settings.max_exposure_pct)
    env["MIN_CONFIDENCE"] = str(settings.min_confidence)
    _write_env(env)
    return {"status": "ok", "message": "Trading settings updated"}


def _mask(value: str) -> str:
    if not value or len(value) < 8:
        return "***"
    return value[:4] + "***" + value[-4:]
