"""AEGIS V1.3 — Correction #8 Tests: Configuration Single Source of Truth.

AC-C8-01 through AC-C8-20: Settings is the central source for initial_capital and max_positions.
"""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from aegis.config import Settings, get_settings
from aegis.execution.sandbox import SandboxBroker
from aegis.portfolio.portfolio import Portfolio
from aegis.risk_engine.risk_engine import RiskEngine
from aegis.risk_engine.risk_limits import RiskLimits


# ============================================================
# AC-C8-01: Settings.initial_capital == Decimal("100.00")
# ============================================================


class TestSettingsCapital:
    """AC-C8-01: Settings is the central source of initial_capital."""

    def test_settings_default_initial_capital(self) -> None:
        """Settings.initial_capital == Decimal("100.00")."""
        settings = Settings()
        assert settings.initial_capital == Decimal("100.00")

    def test_settings_custom_initial_capital(self) -> None:
        """Settings accepts custom initial_capital."""
        settings = Settings(initial_capital=Decimal("250.00"))
        assert settings.initial_capital == Decimal("250.00")


# ============================================================
# AC-C8-02: Settings.max_positions == 1
# ============================================================


class TestSettingsMaxPositions:
    """AC-C8-02: Settings is the central source of max_positions."""

    def test_settings_default_max_positions(self) -> None:
        """Settings.max_positions == 1."""
        settings = Settings()
        assert settings.max_positions == 1

    def test_settings_custom_max_positions(self) -> None:
        """Settings accepts max_positions (clamped by hard limit)."""
        settings = Settings(max_positions=3)
        assert settings.max_positions == 1  # Clamped to hard limit


# ============================================================
# AC-C8-03/AC-C8-04: Worker capital and max_positions come from Settings
# ============================================================


class TestWorkerCapitalFromSettings:
    """AC-C8-03: Worker.capital == Settings.initial_capital."""

    def test_worker_capital_from_settings(self) -> None:
        """Worker.capital derives from Settings, not os.getenv."""
        from aegis.worker import AutonomousWorker

        settings = Settings(initial_capital=Decimal("250.00"))
        worker = AutonomousWorker(settings=settings)
        assert worker.capital == settings.initial_capital
        assert worker.capital == Decimal("250.00")

    def test_worker_no_independent_trading_capital_env(self) -> None:
        """AC-C8-16: Worker does not have independent TRADING_CAPITAL env read."""
        from aegis.worker import AutonomousWorker

        source = inspect.getsource(AutonomousWorker.__init__)
        # The old pattern: os.getenv("TRADING_CAPITAL", "100.0") should NOT exist
        assert 'os.getenv("TRADING_CAPITAL"' not in source
        assert "os.getenv('TRADING_CAPITAL'" not in source


class TestWorkerMaxPositionsFromSettings:
    """AC-C8-04: Worker.max_positions == Settings.max_positions (clamped by hard limit)."""

    def test_worker_max_positions_from_settings(self) -> None:
        """Worker.max_positions derives from Settings, clamped to hard limit."""
        from aegis.worker import AutonomousWorker

        settings = Settings(max_positions=3)
        worker = AutonomousWorker(settings=settings)
        assert worker.max_positions == settings.max_positions
        assert worker.max_positions == 1  # Clamped by hard limit

    def test_worker_no_independent_max_positions_env(self) -> None:
        """AC-C8-17: Worker does not have independent MAX_POSITIONS env read."""
        from aegis.worker import AutonomousWorker

        source = inspect.getsource(AutonomousWorker.__init__)
        # The old pattern: os.getenv("MAX_POSITIONS", "1") should NOT exist
        assert 'os.getenv("MAX_POSITIONS"' not in source
        assert "os.getenv('MAX_POSITIONS'" not in source


# ============================================================
# AC-C8-05: Portfolio utilizes Settings.initial_capital
# ============================================================


class TestPortfolioCapitalFromSettings:
    """AC-C8-05: Portfolio.initial_cash == Settings.initial_capital."""

    def test_portfolio_capital_from_settings(self) -> None:
        """Portfolio receives capital derived from Settings."""
        from aegis.worker import AutonomousWorker

        settings = Settings(initial_capital=Decimal("250.00"))
        worker = AutonomousWorker(settings=settings)
        assert worker.portfolio.cash == Decimal("250.00")

    def test_portfolio_matches_settings_directly(self) -> None:
        """Portfolio.initial_cash == Settings.initial_capital via Worker."""
        from aegis.worker import AutonomousWorker

        settings = Settings(initial_capital=Decimal("500.00"))
        worker = AutonomousWorker(settings=settings)
        assert worker.portfolio.cash == settings.initial_capital


# ============================================================
# AC-C8-06: Risk Engine utilizes Settings.initial_capital
# ============================================================


class TestRiskCapitalFromSettings:
    """AC-C8-06: RiskLimits.reference_capital == Settings.initial_capital."""

    def test_risk_capital_from_settings(self) -> None:
        """RiskEngine receives reference_capital derived from Settings."""
        from aegis.worker import AutonomousWorker

        settings = Settings(initial_capital=Decimal("250.00"))
        worker = AutonomousWorker(settings=settings)
        assert worker.risk_engine.limits.reference_capital == Decimal("250.00")

    def test_risk_matches_settings(self) -> None:
        """RiskLimits.reference_capital == Settings.initial_capital."""
        from aegis.worker import AutonomousWorker

        settings = Settings(initial_capital=Decimal("750.00"))
        worker = AutonomousWorker(settings=settings)
        assert worker.risk_engine.limits.reference_capital == settings.initial_capital


# ============================================================
# AC-C8-07: Risk Engine utilizes Settings.max_positions
# ============================================================


class TestRiskMaxPositionsFromSettings:
    """AC-C8-07: RiskLimits.max_simultaneous_positions == Settings.max_positions (clamped by hard limit)."""

    def test_risk_max_positions_from_settings(self) -> None:
        """RiskEngine receives max_simultaneous_positions from Settings, clamped to hard limit."""
        from aegis.worker import AutonomousWorker
        from aegis.risk_engine.risk_limits import MAX_POSITIONS_HARD_LIMIT

        settings = Settings(max_positions=3)
        worker = AutonomousWorker(settings=settings)
        assert worker.risk_engine.limits.max_simultaneous_positions == MAX_POSITIONS_HARD_LIMIT

    def test_risk_max_positions_matches_settings(self) -> None:
        """RiskLimits.max_simultaneous_positions is clamped to hard limit."""
        from aegis.worker import AutonomousWorker
        from aegis.risk_engine.risk_limits import MAX_POSITIONS_HARD_LIMIT

        settings = Settings(max_positions=5)
        worker = AutonomousWorker(settings=settings)
        assert worker.risk_engine.limits.max_simultaneous_positions == MAX_POSITIONS_HARD_LIMIT


# ============================================================
# AC-C8-08: SandboxBroker balance == Settings.initial_capital
# ============================================================


class TestBrokerCapitalFromSettings:
    """AC-C8-08: SandboxBroker.balance derives from Settings."""

    def test_broker_balance_from_settings(self) -> None:
        """Broker receives same capital derived from Settings."""
        from aegis.worker import AutonomousWorker

        settings = Settings(initial_capital=Decimal("250.00"))
        worker = AutonomousWorker(settings=settings)
        assert worker.broker.balance == Decimal("250.00")

    def test_broker_matches_portfolio(self) -> None:
        """Broker.balance == Portfolio.cash (no divergence)."""
        from aegis.worker import AutonomousWorker

        settings = Settings(initial_capital=Decimal("300.00"))
        worker = AutonomousWorker(settings=settings)
        assert worker.broker.balance == worker.portfolio.cash
        assert worker.broker.balance == Decimal("300.00")


# ============================================================
# AC-C8-09: initial_capital = 100 produces 100 in all components
# ============================================================


class TestDefaultCapitalPropagation:
    """AC-C8-09: default initial_capital = 100 propagates everywhere."""

    def test_default_100_propagates_to_all_components(self) -> None:
        """Settings(100) -> Worker -> Portfolio, Risk, Broker all = 100."""
        from aegis.worker import AutonomousWorker

        settings = Settings()
        assert settings.initial_capital == Decimal("100.00")

        worker = AutonomousWorker(settings=settings)
        assert worker.capital == Decimal("100.00")
        assert worker.portfolio.cash == Decimal("100.00")
        assert worker.risk_engine.limits.reference_capital == Decimal("100.00")
        assert worker.broker.balance == Decimal("100.00")


# ============================================================
# AC-C8-10: initial_capital = 250 produces 250 in all components
# ============================================================


class TestNonDefaultCapitalPropagation:
    """AC-C8-10: initial_capital = 250 propagates everywhere."""

    def test_250_propagates_to_all_components(self) -> None:
        """Settings(250) -> Worker -> Portfolio, Risk, Broker all = 250."""
        from aegis.worker import AutonomousWorker

        settings = Settings(initial_capital=Decimal("250.00"))
        worker = AutonomousWorker(settings=settings)
        assert worker.capital == Decimal("250.00")
        assert worker.portfolio.cash == Decimal("250.00")
        assert worker.risk_engine.limits.reference_capital == Decimal("250.00")
        assert worker.broker.balance == Decimal("250.00")

    def test_500_propagates_to_all_components(self) -> None:
        """Settings(500) -> all components = 500."""
        from aegis.worker import AutonomousWorker

        settings = Settings(initial_capital=Decimal("500.00"))
        worker = AutonomousWorker(settings=settings)
        assert worker.capital == Decimal("500.00")
        assert worker.portfolio.cash == Decimal("500.00")
        assert worker.risk_engine.limits.reference_capital == Decimal("500.00")
        assert worker.broker.balance == Decimal("500.00")


# ============================================================
# AC-C8-11: max_positions = 1 propagates correctly
# ============================================================


class TestDefaultMaxPositionsPropagation:
    """AC-C8-11: default max_positions = 1 propagates."""

    def test_default_max_positions_1(self) -> None:
        """Settings(1) -> Worker -> Risk all = 1."""
        from aegis.worker import AutonomousWorker

        settings = Settings()
        assert settings.max_positions == 1

        worker = AutonomousWorker(settings=settings)
        assert worker.max_positions == 1
        assert worker.risk_engine.limits.max_simultaneous_positions == 1


# ============================================================
# AC-C8-12: max_positions = 3 propagates correctly
# ============================================================


class TestNonDefaultMaxPositionsPropagation:
    """AC-C8-12: max_positions propagates, but is clamped by hard limit."""

    def test_max_positions_3(self) -> None:
        """Settings(3) -> Worker = 1 (clamped by Settings validator), Risk = 1."""
        from aegis.worker import AutonomousWorker

        settings = Settings(max_positions=3)
        worker = AutonomousWorker(settings=settings)
        assert worker.max_positions == 1  # Clamped by Settings validator
        assert worker.risk_engine.limits.max_simultaneous_positions == 1

    def test_max_positions_5(self) -> None:
        """Settings(5) -> Worker = 1 (clamped by Settings validator), Risk = 1."""
        from aegis.worker import AutonomousWorker

        settings = Settings(max_positions=5)
        worker = AutonomousWorker(settings=settings)
        assert worker.max_positions == 1  # Clamped by Settings validator
        assert worker.risk_engine.limits.max_simultaneous_positions == 1


# ============================================================
# AC-C8-13: Restart preserves persisted financial state
# AC-C8-14: Restart does not overwrite persisted state with initial_capital
# ============================================================


class TestRestartPreservesPersistedState:
    """AC-C8-13/AC-C8-14: Restart loads persisted state, not Settings default."""

    def test_restart_preserves_portfolio_cash(self, tmp_path: Path) -> None:
        """Persisted capital overrides Settings.initial_capital after restart."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            # Step 1: Start with Settings.initial_capital = 100
            settings = Settings(initial_capital=Decimal("100.00"))
            worker1 = AutonomousWorker(settings=settings)
            assert worker1.portfolio.cash == Decimal("100.00")

            # Step 2: Simulate activity: capital changes to 80
            worker1.portfolio._cash = Decimal("80.00")
            worker1._state["capital"] = "80.00"
            worker1._state["positions"] = []
            worker1._state["orders"] = []
            worker1._state["history"] = []
            worker1._state["decisions"] = []
            worker1._save_state()

            # Step 3: Restart with same Settings
            worker2 = AutonomousWorker(settings=settings)
            worker2._load_state()

            # Step 4: Persisted state (80) overrides Settings default (100)
            assert worker2.portfolio.cash == Decimal("80.00")
            assert worker2._state["capital"] == "80.00"
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_restart_with_different_capital_setting(self, tmp_path: Path) -> None:
        """Persisted state still overrides even with different Settings capital."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            # Step 1: Start with Settings.initial_capital = 250
            settings1 = Settings(initial_capital=Decimal("250.00"))
            worker1 = AutonomousWorker(settings=settings1)
            assert worker1.portfolio.cash == Decimal("250.00")

            # Step 2: Simulate activity: capital changes to 180
            worker1.portfolio._cash = Decimal("180.00")
            worker1._state["capital"] = "180.00"
            worker1._state["positions"] = []
            worker1._state["orders"] = []
            worker1._state["history"] = []
            worker1._state["decisions"] = []
            worker1._save_state()

            # Step 3: Restart with DIFFERENT Settings.initial_capital = 500
            settings2 = Settings(initial_capital=Decimal("500.00"))
            worker2 = AutonomousWorker(settings=settings2)
            # Portfolio starts with Settings value
            assert worker2.portfolio.cash == Decimal("500.00")

            # Step 4: Load persisted state — 180 overrides 500
            worker2._load_state()
            assert worker2.portfolio.cash == Decimal("180.00")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_restart_preserves_peak_equity(self, tmp_path: Path) -> None:
        """Persisted peak_equity survives restart."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            settings = Settings(initial_capital=Decimal("100.00"))
            worker1 = AutonomousWorker(settings=settings)
            worker1.portfolio._peak_equity = Decimal("120.00")
            worker1._state["positions"] = []
            worker1._state["orders"] = []
            worker1._state["history"] = []
            worker1._state["decisions"] = []
            worker1._save_state()

            worker2 = AutonomousWorker(settings=settings)
            worker2._load_state()
            assert worker2.portfolio._peak_equity == Decimal("120.00")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")


# ============================================================
# AC-C8-15: No hardcoded "Máximo 1" in prompt
# ============================================================


class TestNoHardcodedPrompt:
    """AC-C8-15: Prompt uses dynamic max_positions, not hardcoded."""

    def test_prompt_not_hardcoded_max_1(self) -> None:
        """The default prompt template does not hardcode 'Máximo 1'."""
        from aegis.worker import AutonomousWorker

        settings = Settings(max_positions=5)
        worker = AutonomousWorker(settings=settings)
        # The default prompt should not contain hardcoded "Máximo 1"
        # Check the registered prompt version
        pv = worker.prompt_manager.get("trading_v1")
        assert pv is not None
        assert "Máximo 1" not in pv.template


# ============================================================
# AC-C8-16: No hardcoded capital in Worker
# ============================================================


class TestNoHardcodedWorkerCapital:
    """AC-C8-16: Worker does not hardcode capital."""

    def test_worker_no_os_getenv_trading_capital(self) -> None:
        """Worker.__init__ does not read TRADING_CAPITAL via os.getenv."""
        from aegis.worker import AutonomousWorker

        source = inspect.getsource(AutonomousWorker.__init__)
        assert 'os.getenv("TRADING_CAPITAL"' not in source
        assert "os.getenv('TRADING_CAPITAL'" not in source
        assert 'env.get("TRADING_CAPITAL"' not in source


# ============================================================
# AC-C8-17: No independent max_positions source in Worker
# ============================================================


class TestNoIndependentMaxPositions:
    """AC-C8-17: Worker does not have independent max_positions source."""

    def test_worker_no_os_getenv_max_positions(self) -> None:
        """Worker.__init__ does not read MAX_POSITIONS via os.getenv."""
        from aegis.worker import AutonomousWorker

        source = inspect.getsource(AutonomousWorker.__init__)
        assert 'os.getenv("MAX_POSITIONS"' not in source
        assert "os.getenv('MAX_POSITIONS'" not in source
        assert 'env.get("MAX_POSITIONS"' not in source


# ============================================================
# AC-C8-18: No Portfolio/Broker divergence
# ============================================================


class TestNoPortfolioBrokerDivergence:
    """AC-C8-18: Portfolio.cash == Broker.balance."""

    def test_portfolio_broker_no_divergence(self) -> None:
        """Portfolio.cash == Broker.balance for various capital values."""
        from aegis.worker import AutonomousWorker

        for cap in ["100.00", "250.00", "500.00", "1000.00"]:
            settings = Settings(initial_capital=Decimal(cap))
            worker = AutonomousWorker(settings=settings)
            assert worker.portfolio.cash == worker.broker.balance, (
                f"Divergence at capital={cap}: "
                f"Portfolio={worker.portfolio.cash}, Broker={worker.broker.balance}"
            )


# ============================================================
# AC-C8-19: No Portfolio/Risk divergence
# ============================================================


class TestNoPortfolioRiskDivergence:
    """AC-C8-19: Portfolio and Risk use the same capital from Settings."""

    def test_portfolio_risk_no_divergence(self) -> None:
        """Portfolio.cash and RiskLimits.reference_capital both from Settings."""
        from aegis.worker import AutonomousWorker

        for cap in ["100.00", "250.00", "500.00"]:
            settings = Settings(initial_capital=Decimal(cap))
            worker = AutonomousWorker(settings=settings)
            assert worker.portfolio.cash == worker.risk_engine.limits.reference_capital, (
                f"Divergence at capital={cap}: "
                f"Portfolio={worker.portfolio.cash}, Risk={worker.risk_engine.limits.reference_capital}"
            )


# ============================================================
# AC-C8-20: All existing tests continue passing
# (Verified by full test suite run — not duplicated here)
# ============================================================


# ============================================================
# Regression: Settings still reads from environment
# ============================================================


class TestSettingsReadsFromEnvironment:
    """Settings reads TRADING_CAPITAL and MAX_POSITIONS from env vars."""

    def test_settings_reads_trading_capital_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings.initial_capital reads TRADING_CAPITAL env var."""
        monkeypatch.setenv("TRADING_CAPITAL", "750.00")
        # Clear lru_cache to force re-read
        get_settings.cache_clear()
        settings = Settings()
        assert settings.initial_capital == Decimal("750.00")
        get_settings.cache_clear()

    def test_settings_reads_max_positions_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings.max_positions reads MAX_POSITIONS env var (clamped by hard limit)."""
        monkeypatch.setenv("MAX_POSITIONS", "4")
        settings = Settings()
        assert settings.max_positions == 1  # Clamped by hard limit

    def test_worker_inherits_env_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Worker picks up TRADING_CAPITAL from Settings which reads env."""
        monkeypatch.setenv("TRADING_CAPITAL", "333.00")
        get_settings.cache_clear()
        try:
            from aegis.worker import AutonomousWorker

            settings = Settings()
            worker = AutonomousWorker(settings=settings)
            assert worker.capital == Decimal("333.00")
            assert worker.portfolio.cash == Decimal("333.00")
            assert worker.risk_engine.limits.reference_capital == Decimal("333.00")
            assert worker.broker.balance == Decimal("333.00")
        finally:
            get_settings.cache_clear()
