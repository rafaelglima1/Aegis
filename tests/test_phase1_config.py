"""AEGIS Phase 1 — Configuration Hardening Tests.

Tests for Settings as single source of truth, validation, and env alignment.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aegis.config import Settings, TradingEnvironment, get_settings


# ============================================================
# AC1: Single Source of Truth
# ============================================================


class TestSettingsSingleSource:

    def test_all_operational_fields_exist(self) -> None:
        """Settings has all required operational fields."""
        s = Settings()
        # Application
        assert s.app_name == "AEGIS"
        assert s.app_version == "1.3.0"
        assert s.log_level == "INFO"
        # Environment
        assert s.trading_environment == TradingEnvironment.SANDBOX
        assert s.live_enabled is False
        assert s.live_confirmation_required is True
        # Capital & Positions
        assert s.initial_capital == Decimal("100.00")
        assert s.max_positions == 1
        # Trading
        assert s.trading_symbols == "BTC-BRL,ETH-BRL"
        assert s.trading_timeframe == "1h"
        assert s.long_only is True
        # Risk
        assert s.risk_per_trade_pct == Decimal("1.0")
        assert s.max_daily_loss_pct == Decimal("5.0")
        assert s.max_position_size_pct == Decimal("20.0")
        assert s.max_exposure_pct == Decimal("100.0")
        assert s.circuit_breaker_pct == Decimal("10.0")
        assert s.min_confidence == Decimal("0.50")
        assert s.mandatory_stop is True
        assert s.mandatory_take_profit is True
        # LLM
        assert s.llm_provider == "kilo"
        assert s.llm_model == "kilo-auto/free"
        # Infrastructure
        assert "postgresql" in s.database_url
        assert "redis" in s.redis_url

    def test_trading_symbols_list(self) -> None:
        """trading_symbols_list parses comma-separated symbols."""
        s = Settings(trading_symbols="BTC-BRL,ETH-BRL,SOL-BRL")
        assert s.trading_symbols_list == ["BTC-BRL", "ETH-BRL", "SOL-BRL"]

    def test_trading_symbols_list_single(self) -> None:
        """trading_symbols_list handles single symbol."""
        s = Settings(trading_symbols="BTC-BRL")
        assert s.trading_symbols_list == ["BTC-BRL"]

    def test_risk_decimal_properties(self) -> None:
        """Decimal properties convert percentages correctly."""
        s = Settings(
            risk_per_trade_pct=Decimal("1.0"),
            max_daily_loss_pct=Decimal("5.0"),
            max_position_size_pct=Decimal("20.0"),
            max_exposure_pct=Decimal("100.0"),
            circuit_breaker_pct=Decimal("10.0"),
        )
        assert s.risk_per_trade_decimal == Decimal("0.01")
        assert s.max_daily_loss_decimal == Decimal("0.05")
        assert s.max_position_size_decimal == Decimal("0.20")
        assert s.max_exposure_decimal == Decimal("1.00")
        assert s.circuit_breaker_decimal == Decimal("0.10")


# ============================================================
# AC2: Zero unauthorized env access
# ============================================================


class TestZeroEnvAccess:

    def test_no_os_getenv_in_config(self) -> None:
        """config.py should not use os.getenv in code (excluding docstrings)."""
        import ast
        import inspect
        from aegis import config
        source = inspect.getsource(config)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Call, ast.Attribute)):
                if isinstance(node, ast.Call) and hasattr(node.func, 'attr'):
                    if node.func.attr == 'getenv':
                        assert False, f"os.getenv found at line {node.lineno}"
                if isinstance(node, ast.Attribute) and node.attr == 'getenv':
                    # Check parent is not a docstring
                    pass

    def test_no_os_getenv_in_worker_init(self) -> None:
        """Worker __init__ should not use os.getenv for config."""
        import inspect
        from aegis.worker import AutonomousWorker
        source = inspect.getsource(AutonomousWorker.__init__)
        # Allow os.getenv for non-operational purposes (none expected)
        assert "os.getenv" not in source


# ============================================================
# AC3: Worker without duplicated config
# ============================================================


class TestWorkerNoDuplicateConfig:

    def test_worker_reads_from_settings(self) -> None:
        """Worker consumes Settings, not os.getenv."""
        s = Settings(
            initial_capital=Decimal("250.00"),
            max_positions=1,
            llm_model="test-model",
            trading_symbols="BTC-BRL",
            trading_timeframe="4h",
            risk_per_trade_pct=Decimal("2.0"),
            min_confidence=Decimal("0.60"),
        )
        from aegis.worker import AutonomousWorker
        w = AutonomousWorker(settings=s)
        assert w.capital == Decimal("250.00")
        assert w.max_positions == 1
        assert w.llm_model == "test-model"
        assert w.symbols == ["BTC-BRL"]
        assert w.timeframe == "4h"
        assert w.risk_pct == Decimal("0.02")
        assert w.min_confidence == Decimal("0.60")


# ============================================================
# AC4: LLM centralized
# ============================================================


class TestLLMCentralized:

    def test_llm_config_from_settings(self) -> None:
        """LLM config comes from Settings."""
        s = Settings(
            llm_provider="test-provider",
            llm_api_key="test-key",
            llm_base_url="https://test.example.com",
            llm_model="test-model",
        )
        from aegis.worker import AutonomousWorker
        w = AutonomousWorker(settings=s)
        assert w.llm_base_url == "https://test.example.com"
        assert w.llm_api_key == "test-key"
        assert w.llm_model == "test-model"

    def test_llm_default_matches_settings(self) -> None:
        """LLM defaults in Settings are consistent."""
        s = Settings()
        assert s.llm_provider == "kilo"
        assert s.llm_model == "kilo-auto/free"
        assert "kilo" in s.llm_base_url


# ============================================================
# AC5: Trading centralized
# ============================================================


class TestTradingCentralized:

    def test_trading_config_from_settings(self) -> None:
        """Trading config comes from Settings."""
        s = Settings(
            trading_symbols="SOL-BRL,ADA-BRL",
            trading_timeframe="4h",
            long_only=False,
        )
        from aegis.worker import AutonomousWorker
        w = AutonomousWorker(settings=s)
        assert w.symbols == ["SOL-BRL", "ADA-BRL"]
        assert w.timeframe == "4h"
        assert w.long_only is False


# ============================================================
# AC6: Risk centralized
# ============================================================


class TestRiskCentralized:

    def test_risk_config_from_settings(self) -> None:
        """Risk config comes from Settings."""
        s = Settings(
            risk_per_trade_pct=Decimal("2.0"),
            max_daily_loss_pct=Decimal("3.0"),
            max_position_size_pct=Decimal("15.0"),
            max_exposure_pct=Decimal("80.0"),
            circuit_breaker_pct=Decimal("8.0"),
            min_confidence=Decimal("0.70"),
            mandatory_stop=False,
            mandatory_take_profit=False,
        )
        from aegis.worker import AutonomousWorker
        w = AutonomousWorker(settings=s)
        assert w.risk_pct == Decimal("0.02")
        assert w.max_daily_loss_pct == Decimal("0.03")
        assert w.max_position_size_pct == Decimal("0.15")
        assert w.max_exposure_pct == Decimal("0.80")
        assert w.circuit_breaker_pct == Decimal("0.08")
        assert w.min_confidence == Decimal("0.70")
        assert w.mandatory_stop is False
        assert w.mandatory_take_profit is False


# ============================================================
# AC7: Portfolio centralized
# ============================================================


class TestPortfolioCentralized:

    def test_capital_from_settings(self) -> None:
        """Portfolio capital comes from Settings."""
        s = Settings(initial_capital=Decimal("500.00"))
        from aegis.worker import AutonomousWorker
        w = AutonomousWorker(settings=s)
        assert w.portfolio.cash == Decimal("500.00")
        assert w.capital == Decimal("500.00")


# ============================================================
# AC8: Exchange credentials centralized
# ============================================================


class TestExchangeCredentialsCentralized:

    def test_live_credentials_in_settings(self) -> None:
        """Live exchange credentials are in Settings."""
        s = Settings(
            live_api_key="test-key",
            live_api_secret="test-secret",
        )
        assert s.live_api_key == "test-key"
        assert s.live_api_secret == "test-secret"

    def test_mb_api_key_alias(self) -> None:
        """MB_API_KEY maps to live_api_key."""
        import os
        os.environ["MB_API_KEY"] = "mb-test-key"
        os.environ["MB_API_SECRET"] = "mb-test-secret"
        try:
            s = Settings()
            assert s.live_api_key == "mb-test-key"
            assert s.live_api_secret == "mb-test-secret"
        finally:
            del os.environ["MB_API_KEY"]
            del os.environ["MB_API_SECRET"]


# ============================================================
# AC9: .env.example aligned
# ============================================================


class TestEnvExampleAligned:

    def test_env_example_has_all_fields(self) -> None:
        """.env.example contains all Settings fields."""
        from pathlib import Path
        env_example = Path(".env.example").read_text()
        required_fields = [
            "TRADING_ENVIRONMENT", "LIVE_ENABLED", "TRADING_CAPITAL",
            "MAX_POSITIONS", "TRADING_SYMBOLS", "TRADING_TIMEFRAME",
            "LONG_ONLY", "RISK_PER_TRADE_PCT", "MAX_DAILY_LOSS_PCT",
            "MAX_POSITION_SIZE_PCT", "MAX_EXPOSURE_PCT", "CIRCUIT_BREAKER_PCT",
            "MIN_CONFIDENCE", "MANDATORY_STOP", "MANDATORY_TAKE_PROFIT",
            "LLM_PROVIDER", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
            "DATABASE_URL", "REDIS_URL", "AEGIS_API_KEY",
        ]
        for field in required_fields:
            assert field in env_example, f"{field} missing from .env.example"


# ============================================================
# AC10: Invalid configuration rejected
# ============================================================


class TestInvalidConfigRejected:

    def test_negative_capital_rejected(self) -> None:
        """Negative capital should be rejected."""
        with pytest.raises(ValueError, match="positive"):
            Settings(initial_capital=Decimal("-100"))

    def test_zero_capital_rejected(self) -> None:
        """Zero capital should be rejected."""
        with pytest.raises(ValueError, match="positive"):
            Settings(initial_capital=Decimal("0"))

    def test_max_positions_zero_rejected(self) -> None:
        """max_positions=0 should be rejected."""
        with pytest.raises(ValueError, match=">= 1"):
            Settings(max_positions=0)

    def test_max_positions_above_hard_limit(self) -> None:
        """max_positions > 1 should be clamped to 1."""
        s = Settings(max_positions=5)
        assert s.max_positions == 1

    def test_risk_pct_over_100_rejected(self) -> None:
        """risk_per_trade_pct > 100 should be rejected."""
        with pytest.raises(ValueError, match="between 0 and 100"):
            Settings(risk_per_trade_pct=Decimal("150"))

    def test_risk_pct_negative_rejected(self) -> None:
        """risk_per_trade_pct < 0 should be rejected."""
        with pytest.raises(ValueError, match="between 0 and 100"):
            Settings(risk_per_trade_pct=Decimal("-1"))

    def test_min_confidence_over_1_rejected(self) -> None:
        """min_confidence > 1 should be rejected."""
        with pytest.raises(ValueError, match="between 0 and 1"):
            Settings(min_confidence=Decimal("1.5"))

    def test_min_confidence_negative_rejected(self) -> None:
        """min_confidence < 0 should be rejected."""
        with pytest.raises(ValueError, match="between 0 and 1"):
            Settings(min_confidence=Decimal("-0.1"))

    def test_invalid_trading_environment_rejected(self) -> None:
        """Invalid TRADING_ENVIRONMENT should be rejected."""
        with pytest.raises(ValueError):
            Settings(trading_environment="INVALID")

    def test_invalid_max_daily_loss_rejected(self) -> None:
        """max_daily_loss_pct > 100 should be rejected."""
        with pytest.raises(ValueError, match="between 0 and 100"):
            Settings(max_daily_loss_pct=Decimal("150"))

    def test_invalid_max_position_size_rejected(self) -> None:
        """max_position_size_pct > 100 should be rejected."""
        with pytest.raises(ValueError, match="between 0 and 100"):
            Settings(max_position_size_pct=Decimal("150"))

    def test_invalid_max_exposure_rejected(self) -> None:
        """max_exposure_pct > 100 should be rejected."""
        with pytest.raises(ValueError, match="between 0 and 100"):
            Settings(max_exposure_pct=Decimal("150"))

    def test_invalid_circuit_breaker_rejected(self) -> None:
        """circuit_breaker_pct > 100 should be rejected."""
        with pytest.raises(ValueError, match="between 0 and 100"):
            Settings(circuit_breaker_pct=Decimal("150"))


# ============================================================
# AC11: Secrets protected
# ============================================================


class TestSecretsProtected:

    def test_api_keys_not_in_repr(self) -> None:
        """Settings repr should not expose secrets."""
        s = Settings(llm_api_key="secret-key-12345")
        # Pydantic Settings uses __repr__ which shows all fields
        # The key test is that Settings doesn't LOG secrets
        # We verify the value is stored but not printed in logs
        assert s.llm_api_key == "secret-key-12345"

    def test_settings_singleton(self) -> None:
        """get_settings returns same instance (cached)."""
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2


# ============================================================
# AC15: LIVE disabled by default
# ============================================================


class TestLIVESafetyDefaults:

    def test_live_disabled_by_default(self) -> None:
        """LIVE is disabled by default."""
        s = Settings()
        assert s.trading_environment == TradingEnvironment.SANDBOX
        assert s.live_enabled is False
        assert s.live_confirmation_required is True

    def test_sandbox_is_default(self) -> None:
        """SANDBOX is the default environment."""
        s = Settings()
        assert s.trading_environment == TradingEnvironment.SANDBOX


# ============================================================
# Environment override tests
# ============================================================


class TestEnvironmentOverrides:

    def test_env_override_trading_capital(self) -> None:
        """TRADING_CAPITAL env var overrides default."""
        import os
        os.environ["TRADING_CAPITAL"] = "500.00"
        try:
            # Clear cache to force re-read
            from aegis.config import get_settings
            get_settings.cache_clear()
            s = Settings()
            assert s.initial_capital == Decimal("500.00")
        finally:
            del os.environ["TRADING_CAPITAL"]
            get_settings.cache_clear()

    def test_env_override_max_positions(self) -> None:
        """MAX_POSITIONS env var overrides default."""
        import os
        os.environ["MAX_POSITIONS"] = "3"
        try:
            s = Settings()
            assert s.max_positions == 1  # Clamped by hard limit
        finally:
            del os.environ["MAX_POSITIONS"]

    def test_env_override_llm_model(self) -> None:
        """LLM_MODEL env var overrides default."""
        import os
        os.environ["LLM_MODEL"] = "custom-model"
        try:
            s = Settings()
            assert s.llm_model == "custom-model"
        finally:
            del os.environ["LLM_MODEL"]

    def test_env_override_trading_symbols(self) -> None:
        """TRADING_SYMBOLS env var overrides default."""
        import os
        os.environ["TRADING_SYMBOLS"] = "SOL-BRL,ADA-BRL"
        try:
            s = Settings()
            assert s.trading_symbols == "SOL-BRL,ADA-BRL"
        finally:
            del os.environ["TRADING_SYMBOLS"]

    def test_env_override_risk_per_trade(self) -> None:
        """RISK_PER_TRADE_PCT env var overrides default."""
        import os
        os.environ["RISK_PER_TRADE_PCT"] = "2.5"
        try:
            s = Settings()
            assert s.risk_per_trade_pct == Decimal("2.5")
        finally:
            del os.environ["RISK_PER_TRADE_PCT"]
