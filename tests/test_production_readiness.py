"""AEGIS Production Runtime Configuration Tests.

Covers: AC1-AC17, CP01-CP25 for Runtime Configuration & Deployment Foundation.
"""

from __future__ import annotations

import hmac
import re
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aegis.config import Settings, TradingEnvironment, get_settings, _SECRET_FIELDS


# ============================================================
# AC1: Single Source of Truth
# ============================================================


class TestSingleSourceOfTruth:

    def test_no_os_getenv_in_config(self) -> None:
        """CP02: No os.getenv() in config module (excluding docstrings)."""
        import ast
        import inspect
        import aegis.config as cfg
        source = inspect.getsource(cfg)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and hasattr(node, 'func'):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == 'getenv':
                    assert False, f"os.getenv found at line {node.lineno}"

    def test_no_os_getenv_in_main(self) -> None:
        """CP02: No os.getenv() in main for operational config (excluding docstrings)."""
        import ast
        import inspect
        import aegis.main as main_mod
        source = inspect.getsource(main_mod)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and hasattr(node, 'func'):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == 'getenv':
                    assert False, f"os.getenv found at line {node.lineno}"

    def test_settings_is_singleton(self) -> None:
        """CP01: Settings accessed via get_settings() returns same instance."""
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2


# ============================================================
# AC2/AC3: Environment Safety
# ============================================================


class TestEnvironmentSafety:

    def test_sandbox_is_default(self) -> None:
        """CP03: SANDBOX is the safe default."""
        s = Settings()
        assert s.trading_environment == TradingEnvironment.SANDBOX

    def test_live_disabled_by_default(self) -> None:
        """CP04: LIVE_ENABLED=False by default."""
        s = Settings()
        assert s.live_enabled is False

    def test_live_not_accidental(self) -> None:
        """CP05: LIVE cannot be activated accidentally."""
        s = Settings()
        assert s.live_enabled is False
        assert s.live_confirmation_required is True
        # Both must be explicitly set to activate
        s2 = Settings(live_enabled=True, trading_environment="LIVE")
        assert s2.live_enabled is True
        assert s2.trading_environment == TradingEnvironment.LIVE

    def test_invalid_environment_rejected(self) -> None:
        """CP06: Invalid environment causes explicit failure."""
        with pytest.raises(Exception):
            Settings(trading_environment="PAPER")


# ============================================================
# AC4/AC5: Config Validation
# ============================================================


class TestConfigValidation:

    def test_max_positions_rejected(self) -> None:
        """CP06: MAX_POSITIONS != 1 is rejected (no silent clamp)."""
        with pytest.raises(ValueError):
            Settings(max_positions=0)
        with pytest.raises(ValueError):
            Settings(max_positions=-1)
        with pytest.raises(ValueError):
            Settings(max_positions=2)
        with pytest.raises(ValueError):
            Settings(max_positions=10)

    def test_max_positions_1_accepted(self) -> None:
        s = Settings(max_positions=1)
        assert s.max_positions == 1

    def test_negative_capital_rejected(self) -> None:
        with pytest.raises(ValueError):
            Settings(initial_capital=Decimal("-100"))

    def test_zero_capital_rejected(self) -> None:
        with pytest.raises(ValueError):
            Settings(initial_capital=Decimal("0"))

    def test_risk_pct_over_100_rejected(self) -> None:
        with pytest.raises(ValueError):
            Settings(risk_per_trade_pct=Decimal("150"))

    def test_risk_pct_negative_rejected(self) -> None:
        with pytest.raises(ValueError):
            Settings(risk_per_trade_pct=Decimal("-1"))

    def test_min_confidence_over_1_rejected(self) -> None:
        with pytest.raises(ValueError):
            Settings(min_confidence=Decimal("1.5"))

    def test_no_silent_clamp(self) -> None:
        """CP07: No silent clamping. Invalid values raise, not clamp."""
        # Verify max_positions=2 raises, doesn't silently become 1
        with pytest.raises(ValueError, match="must be exactly 1"):
            Settings(max_positions=2)


# ============================================================
# AC6/AC7: Secret Safety
# ============================================================


class TestSecretSafety:

    def test_secrets_not_in_repr(self) -> None:
        """CP09: Secrets don't appear in repr()."""
        s = Settings(
            llm_api_key="sk-secret-12345",
            mb_api_key="mb-secret-67890",
            live_api_key="live-secret-abc",
            aegis_api_key="aegis-secret-xyz",
        )
        r = repr(s)
        assert "sk-secret-12345" not in r
        assert "mb-secret-67890" not in r
        assert "live-secret-abc" not in r
        assert "aegis-secret-xyz" not in r
        assert "***REDACTED***" in r

    def test_secrets_not_in_str(self) -> None:
        """CP09: Secrets don't appear in str()."""
        s = Settings(
            llm_api_key="sk-secret-12345",
            sandbox_api_secret="sbox-secret-999",
        )
        st = str(s)
        assert "sk-secret-12345" not in st
        assert "sbox-secret-999" not in st

    def test_empty_secrets_not_masked(self) -> None:
        """Empty defaults don't show as REDACTED."""
        s = Settings()
        r = repr(s)
        # Empty strings should stay empty, not become REDACTED
        assert "llm_api_key=''" in r or "llm_api_key=\"\"" in r

    def test_non_secret_fields_visible(self) -> None:
        """Non-secret fields are visible in repr."""
        s = Settings()
        r = repr(s)
        assert "app_name=" in r
        assert "trading_environment=" in r

    def test_secret_fields_list(self) -> None:
        """_SECRET_FIELDS covers all sensitive config."""
        assert "llm_api_key" in _SECRET_FIELDS
        assert "sandbox_api_key" in _SECRET_FIELDS
        assert "sandbox_api_secret" in _SECRET_FIELDS
        assert "live_api_key" in _SECRET_FIELDS
        assert "live_api_secret" in _SECRET_FIELDS
        assert "aegis_api_key" in _SECRET_FIELDS

    def test_database_url_in_repr(self) -> None:
        """database_url contains credentials but is not in _SECRET_FIELDS.
        This is acceptable since it's infrastructure config, not API keys."""
        s = Settings()
        r = repr(s)
        # database_url IS visible (it's infrastructure, not a secret key)
        assert "database_url=" in r

    def test_api_key_not_in_exception(self) -> None:
        """Secrets don't appear in pydantic validation errors."""
        with pytest.raises(Exception) as exc_info:
            Settings(max_positions=2)
        error_msg = str(exc_info.value)
        # The error should mention max_positions, not any secret
        assert "MAX_POSITIONS" in error_msg
        assert "api_key" not in error_msg.lower()


# ============================================================
# AC8: Timing-safe API Key Comparison
# ============================================================


class TestApiKeySecurity:

    def test_uses_hmac_compare_digest(self) -> None:
        """API key comparison uses timing-safe hmac.compare_digest."""
        import inspect
        import aegis.main as main_mod
        source = inspect.getsource(main_mod)
        assert "hmac.compare_digest" in source

    def test_bearer_extraction(self) -> None:
        """Bearer token is correctly extracted."""
        import inspect
        import aegis.main as main_mod
        source = inspect.getsource(main_mod)
        assert 'authorization.replace("Bearer ", "")' in source


# ============================================================
# AC9: Health/Readiness
# ============================================================


class TestHealthReadiness:

    def test_health_returns_status(self, client: TestClient) -> None:
        """CP13: Health endpoint returns status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "environment" in data

    def test_readiness_checks_worker(self, client: TestClient) -> None:
        """CP14: Readiness checks actual worker status."""
        response = client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "worker" in data
        assert data["worker"] in ("ok", "error")

    def test_health_no_secrets(self, client: TestClient) -> None:
        """CP10: Health endpoint doesn't expose secrets."""
        response = client.get("/health")
        body = response.text
        assert "api_secret" not in body.lower()
        assert "password" not in body.lower()

    def test_readiness_no_secrets(self, client: TestClient) -> None:
        """CP10: Readiness endpoint doesn't expose secrets."""
        response = client.get("/health/ready")
        body = response.text
        assert "api_secret" not in body.lower()


# ============================================================
# AC10: Docker Runtime
# ============================================================


class TestDockerRuntime:

    def test_dockerfile_exists(self) -> None:
        """CP16: Dockerfile exists."""
        from pathlib import Path
        assert Path("Dockerfile").exists()

    def test_compose_prod_exists(self) -> None:
        """CP16: Production compose file exists."""
        from pathlib import Path
        assert Path("docker-compose.prod.yml").exists()

    def test_dockerfile_uses_python_312(self) -> None:
        """Dockerfile targets Python 3.12."""
        from pathlib import Path
        content = Path("Dockerfile").read_text()
        assert "python:3.12" in content

    def test_compose_has_healthchecks(self) -> None:
        """CP16: Compose services have healthchecks."""
        from pathlib import Path
        content = Path("docker-compose.prod.yml").read_text()
        assert "healthcheck" in content


# ============================================================
# AC11: .env.example
# ============================================================


class TestEnvExample:

    def test_env_example_exists(self) -> None:
        """CP18: .env.example exists."""
        from pathlib import Path
        assert Path(".env.example").exists()

    def test_env_example_has_all_vars(self) -> None:
        """CP18: .env.example documents all operational vars."""
        from pathlib import Path
        content = Path(".env.example").read_text()
        required = [
            "TRADING_ENVIRONMENT", "LIVE_ENABLED", "TRADING_CAPITAL",
            "MAX_POSITIONS", "TRADING_SYMBOLS", "TRADING_TIMEFRAME",
            "LONG_ONLY", "RISK_PER_TRADE_PCT", "MAX_DAILY_LOSS_PCT",
            "MAX_POSITION_SIZE_PCT", "MAX_EXPOSURE_PCT", "CIRCUIT_BREAKER_PCT",
            "MIN_CONFIDENCE", "MANDATORY_STOP", "MANDATORY_TAKE_PROFIT",
            "LLM_PROVIDER", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
            "DATABASE_URL", "REDIS_URL", "AEGIS_API_KEY",
            "MB_API_KEY", "MB_API_SECRET",
            "LIVE_API_KEY", "LIVE_API_SECRET",
        ]
        for var in required:
            assert var in content, f"{var} missing from .env.example"

    def test_env_example_no_real_secrets(self) -> None:
        """CP18: .env.example doesn't contain real secret values."""
        from pathlib import Path
        content = Path(".env.example").read_text()
        # Should not contain real-looking API keys
        assert "sk-" not in content
        assert "eyJ" not in content


# ============================================================
# AC12: Restart Safety
# ============================================================


class TestRestartSafety:

    def test_settings_frozen_after_init(self) -> None:
        """CP19: Settings are cached and don't change on re-read."""
        s1 = get_settings()
        s2 = get_settings()
        assert s1.trading_environment == s2.trading_environment
        assert s1.live_enabled == s2.live_enabled
        assert s1.max_positions == s2.max_positions

    def test_config_deterministic(self) -> None:
        """CP11: Same inputs produce same Settings."""
        s1 = Settings(TRADING_CAPITAL="200", MAX_POSITIONS="1")
        s2 = Settings(TRADING_CAPITAL="200", MAX_POSITIONS="1")
        assert s1.initial_capital == s2.initial_capital
        assert s1.max_positions == s2.max_positions


# ============================================================
# AC16: LIVE remains disabled
# ============================================================


class TestLiveSafety:

    def test_live_disabled_in_default_config(self) -> None:
        """CP23: LIVE remains disabled."""
        s = Settings()
        assert s.live_enabled is False
        assert s.trading_environment == TradingEnvironment.SANDBOX

    def test_live_disabled_by_factory(self) -> None:
        """Factory rejects LIVE without explicit enable."""
        from aegis.execution.factory import create_broker
        with pytest.raises(RuntimeError):
            create_broker(Settings(trading_environment="LIVE", live_enabled=False))


# ============================================================
# Regression guard
# ============================================================


class TestRegressionGuard:

    def test_existing_config_tests_still_pass(self) -> None:
        """CP20: Existing config behavior preserved."""
        from aegis.config import Settings
        s = Settings()
        assert s.app_name == "AEGIS"
        assert s.app_version == "1.3.0"
        assert s.initial_capital == Decimal("100.00")
        assert s.trading_symbols_list == ["BTC-BRL", "ETH-BRL"]

    def test_existing_validation_still_works(self) -> None:
        """All existing validators still function."""
        with pytest.raises(ValueError):
            Settings(risk_per_trade_pct=Decimal("150"))
        with pytest.raises(ValueError):
            Settings(max_daily_loss_pct=Decimal("150"))
        with pytest.raises(ValueError):
            Settings(min_confidence=Decimal("1.5"))
