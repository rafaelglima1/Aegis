"""Tests for AEGIS configuration."""

from __future__ import annotations

import pytest

from aegis.config import Settings, TradingEnvironment


def test_default_trading_environment_is_sandbox() -> None:
    """AC-01.07: SANDBOX is the default trading environment."""
    settings = Settings()
    assert settings.trading_environment == TradingEnvironment.SANDBOX


def test_live_enabled_defaults_to_false() -> None:
    """AC-01.08: LIVE_ENABLED defaults to false."""
    settings = Settings()
    assert settings.live_enabled is False


def test_trading_environment_accepts_sandbox() -> None:
    """AC-01.06: TRADING_ENVIRONMENT accepts SANDBOX."""
    settings = Settings(TRADING_ENVIRONMENT="SANDBOX")
    assert settings.trading_environment == TradingEnvironment.SANDBOX


def test_trading_environment_accepts_live() -> None:
    """AC-01.06: TRADING_ENVIRONMENT accepts LIVE."""
    settings = Settings(trading_environment="LIVE")
    assert settings.trading_environment == TradingEnvironment.LIVE


def test_trading_environment_rejects_invalid() -> None:
    """AC-01.06: TRADING_ENVIRONMENT rejects invalid values."""
    with pytest.raises(Exception):
        Settings(trading_environment="PAPER")


def test_config_loads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-01.05: Configuration is loaded through environment mechanisms."""
    monkeypatch.setenv("TRADING_ENVIRONMENT", "LIVE")
    monkeypatch.setenv("LIVE_ENABLED", "true")
    settings = Settings()
    assert settings.trading_environment == TradingEnvironment.LIVE
    assert settings.live_enabled is True


def test_no_secrets_in_settings() -> None:
    """AC-01.09: No secrets are hardcoded."""
    settings = Settings()
    assert settings.llm_api_key == ""
    assert settings.sandbox_api_key == ""
    assert settings.live_api_key == ""
    # Default values are empty, not hardcoded secrets
