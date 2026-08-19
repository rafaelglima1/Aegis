"""AEGIS V1.3 - Correction #9 Tests.

LIVE lifecycle, hot-reload integrity, execution accounting, restart consistency.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from aegis.domain.enums import (
    OrderSide,
    OrderStatus,
    PositionSide,
    PositionStatus,
    TradingAction,
)
from aegis.execution.broker import OrderResult, OrderSubmission
from aegis.execution.engine import ExecutionEngine
from aegis.execution.sandbox import SandboxBroker
from aegis.portfolio.portfolio import Portfolio
from aegis.risk_engine.risk_engine import RiskDecision
from aegis.risk_engine.risk_limits import RiskLimits


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============================================================
# C9-TEST-01: LIVE BUY opens LONG
# ============================================================


class TestLiveBuyOpensLong:

    def test_buy_opens_long_position(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        submission = OrderSubmission(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("0.001"),
            price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )
        result = run(broker.submit_order(submission))
        assert result.status == OrderStatus.FILLED
        assert result.fill_price is not None
        assert result.fill_price > submission.price
        position = run(broker.get_position("BTC-BRL"))
        assert position["quantity"] == Decimal("0.001")


# ============================================================
# C9-TEST-02: LIVE SELL closes LONG
# ============================================================


class TestLiveSellClosesLong:

    def test_sell_closes_long_position(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("10000.00"))
        buy = OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )
        buy_result = run(broker.submit_order(buy))
        assert buy_result.status == OrderStatus.FILLED
        assert run(broker.get_position("BTC-BRL"))["quantity"] == Decimal("0.001")

        sell = OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("51000.00"),
            correlation_id=uuid4(),
        )
        sell_result = run(broker.submit_order(sell))
        assert sell_result.status == OrderStatus.FILLED
        assert sell_result.fill_price is not None
        assert sell_result.fill_price < sell.price
        assert run(broker.get_position("BTC-BRL"))["quantity"] == Decimal("0")


# ============================================================
# C9-TEST-03: SELL without LONG is rejected
# ============================================================


class TestLiveSellWithoutLongRejected:

    def test_sell_without_position_rejected(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        submission = OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )
        result = run(broker.submit_order(submission))
        assert result.status == OrderStatus.REJECTED
        assert "no position" in result.error.lower()


# ============================================================
# C9-TEST-04: Cannot create SHORT
# ============================================================


class TestLiveCannotCreateShort:

    def test_sell_does_not_create_short(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        submission = OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )
        result = run(broker.submit_order(submission))
        assert result.status == OrderStatus.REJECTED
        pos = run(broker.get_position("BTC-BRL"))
        assert pos["quantity"] >= Decimal("0")


# ============================================================
# C9-TEST-05: SELL behind Risk Gate
# ============================================================


class TestSellBehindRiskGate:

    def test_sell_without_risk_approval_rejected(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("10000.00"))
        engine = ExecutionEngine(broker)
        result = run(engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(), risk_decision=None,
        ))
        assert result.status == OrderStatus.REJECTED

    def test_sell_with_risk_rejection_rejected(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("10000.00"))
        engine = ExecutionEngine(broker)
        risk_decision = RiskDecision(
            status="REJECTED", violations=[],
            approved_quantity=Decimal("0"), approved_price=Decimal("0"),
        )
        result = run(engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(), risk_decision=risk_decision,
        ))
        assert result.status == OrderStatus.REJECTED


# ============================================================
# C9-TEST-06: Hot reload max_positions propagates
# ============================================================


class TestHotReloadMaxPositions:

    def test_max_positions_propagates_to_risk_engine(self, tmp_path: Path) -> None:
        """C9-06: Real _reload_config propagates max_positions through Settings.

        AC-C10-07: RiskEngine clamps to MAX_POSITIONS_HARD_LIMIT=1.
        """
        from aegis.config import Settings
        from aegis.worker import AutonomousWorker
        from aegis.risk_engine.risk_limits import MAX_POSITIONS_HARD_LIMIT
        import aegis.worker as worker_mod

        # Write temp env file
        env_file = tmp_path / ".env.prod"
        env_file.write_text("MAX_POSITIONS=3\nTRADING_CAPITAL=100.00\n", encoding="utf-8")
        worker_mod._SETTINGS_FILE = env_file

        settings = Settings(max_positions=1)
        worker = AutonomousWorker(settings=settings)
        assert worker.max_positions == 1
        assert worker.risk_engine.limits.max_simultaneous_positions == 1

        # Execute REAL reload mechanism
        worker._reload_config()

        assert worker.max_positions == 1  # Clamped by Settings validator
        assert worker.risk_engine.limits.max_simultaneous_positions == MAX_POSITIONS_HARD_LIMIT
        assert worker._settings.max_positions == 1  # Clamped by Settings validator


# ============================================================
# C9-TEST-07: Hot reload does not corrupt Portfolio cash
# ============================================================


class TestHotReloadNoCashCorruption:

    def test_hot_reload_preserves_portfolio_cash(self, tmp_path: Path) -> None:
        """C9-07: Real _reload_config preserves Portfolio cash."""
        from aegis.config import Settings
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        env_file = tmp_path / ".env.prod"
        env_file.write_text("MAX_POSITIONS=1\nTRADING_CAPITAL=100.00\n", encoding="utf-8")
        worker_mod._SETTINGS_FILE = env_file

        settings = Settings(initial_capital=Decimal("100.00"))
        worker = AutonomousWorker(settings=settings)
        original_cash = worker.portfolio.cash

        # Write new config and reload
        env_file.write_text("MAX_POSITIONS=5\nTRADING_CAPITAL=999.00\n", encoding="utf-8")
        worker._reload_config()

        assert worker.portfolio.cash == original_cash


# ============================================================
# C9-TEST-08: Hot reload does not corrupt Broker balance
# ============================================================


class TestHotReloadNoBrokerCorruption:

    def test_hot_reload_preserves_broker_balance(self, tmp_path: Path) -> None:
        """C9-08: Real _reload_config preserves Broker balance."""
        from aegis.config import Settings
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        env_file = tmp_path / ".env.prod"
        env_file.write_text("MAX_POSITIONS=1\nTRADING_CAPITAL=100.00\n", encoding="utf-8")
        worker_mod._SETTINGS_FILE = env_file

        settings = Settings(initial_capital=Decimal("100.00"))
        worker = AutonomousWorker(settings=settings)
        original_balance = worker.broker.balance

        env_file.write_text("MAX_POSITIONS=5\nTRADING_CAPITAL=999.00\n", encoding="utf-8")
        worker._reload_config()

        assert worker.broker.balance == original_balance


# ============================================================
# C9-TEST-09: BUY accounting uses effective fill_price
# ============================================================


class TestBuyAccountingUsesFillPrice:

    def test_buy_balance_uses_fill_price(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("10000.00"))
        initial = broker.balance
        submission = OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )
        result = run(broker.submit_order(submission))
        assert result.status == OrderStatus.FILLED
        assert result.fill_price is not None
        expected_cost = result.fill_price * Decimal("0.001") + result.fee
        actual_deduction = initial - broker.balance
        assert actual_deduction == expected_cost
        assert result.fill_price != submission.price


# ============================================================
# C9-TEST-10: SELL accounting uses effective fill_price
# ============================================================


class TestSellAccountingUsesFillPrice:

    def test_sell_uses_fill_price_for_proceeds(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("10000.00"))
        buy = OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )
        buy_result = run(broker.submit_order(buy))
        assert buy_result.status == OrderStatus.FILLED
        balance_after_buy = broker.balance

        sell = OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("51000.00"),
            correlation_id=uuid4(),
        )
        sell_result = run(broker.submit_order(sell))
        assert sell_result.status == OrderStatus.FILLED
        assert sell_result.fill_price is not None
        expected_proceeds = sell_result.fill_price * Decimal("0.001") - sell_result.fee
        actual_credit = broker.balance - balance_after_buy
        assert actual_credit == expected_proceeds
        assert sell_result.fill_price < sell.price


# ============================================================
# C9-TEST-11: Portfolio cash correct after BUY
# ============================================================


class TestPortfolioCashAfterBuy:

    def test_portfolio_cash_after_buy(self) -> None:
        from aegis.config import Settings
        from aegis.worker import AutonomousWorker

        settings = Settings(initial_capital=Decimal("10000.00"))
        worker = AutonomousWorker(settings=settings)

        fill_price = Decimal("50050.00")
        fee = Decimal("0.50")
        worker.portfolio.record_fill(
            asset="BTC-BRL", side=PositionSide.LONG,
            quantity=Decimal("0.001"), price=fill_price, fee=fee,
        )
        expected_cash = Decimal("10000.00") - fee - (fill_price * Decimal("0.001"))
        assert worker.portfolio.cash == expected_cash


# ============================================================
# C9-TEST-12: Portfolio/Broker consistent after SELL
# ============================================================


class TestPortfolioBrokerConsistentAfterSell:

    def test_consistent_after_sell(self) -> None:
        portfolio = Portfolio(initial_cash=Decimal("10000.00"))
        portfolio.record_fill(
            asset="BTC-BRL", side=PositionSide.LONG,
            quantity=Decimal("1"), price=Decimal("50000.00"), fee=Decimal("0.50"),
        )
        assert portfolio.cash == Decimal("10000.00") - Decimal("0.50") - Decimal("50000.00")

        realized = portfolio.close_position(
            asset="BTC-BRL", price=Decimal("51000.00"), fee=Decimal("0.50"),
        )
        assert realized == Decimal("999.00")
        assert portfolio.cash == Decimal("10000.00") - Decimal("1.00") + Decimal("1000.00")


# ============================================================
# C9-TEST-13: Fees are accounted correctly
# ============================================================


class TestFeesAccountedCorrectly:

    def test_fees_in_buy_and_sell(self) -> None:
        portfolio = Portfolio(initial_cash=Decimal("10000.00"))
        portfolio.record_fill(
            asset="BTC-BRL", side=PositionSide.LONG,
            quantity=Decimal("1"), price=Decimal("50000.00"), fee=Decimal("10.00"),
        )
        assert portfolio.total_fees == Decimal("10.00")
        assert portfolio.cash == Decimal("10000.00") - Decimal("10.00") - Decimal("50000.00")

        portfolio.close_position(
            asset="BTC-BRL", price=Decimal("51000.00"), fee=Decimal("10.00"),
        )
        assert portfolio.total_fees == Decimal("20.00")


# ============================================================
# C9-TEST-14: Realized P&L correct after close
# ============================================================


class TestRealizedPnLCorrect:

    def test_realized_pnl_formula(self) -> None:
        portfolio = Portfolio(initial_cash=Decimal("100000.00"))
        portfolio.record_fill(
            asset="BTC-BRL", side=PositionSide.LONG,
            quantity=Decimal("2"), price=Decimal("50000.00"), fee=Decimal("10.00"),
        )
        realized = portfolio.close_position(
            asset="BTC-BRL", price=Decimal("52000.00"), fee=Decimal("10.00"),
        )
        assert realized == Decimal("3980.00")
        assert portfolio.total_realized_pnl == Decimal("3980.00")


# ============================================================
# C9-TEST-15: Restart preserves open LONG
# ============================================================


class TestRestartPreservesOpenLong:

    def test_restart_preserves_position(self, tmp_path: Path) -> None:
        import aegis.worker as worker_mod
        orig_file = worker_mod._STATE_FILE
        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker
            from aegis.config import Settings

            settings = Settings(initial_capital=Decimal("10000.00"))
            w1 = AutonomousWorker(settings=settings)
            fill_price = Decimal("50050.00")
            fee = Decimal("0.50")
            w1.portfolio.record_fill(
                asset="BTC-BRL", side=PositionSide.LONG,
                quantity=Decimal("0.001"), price=fill_price, fee=fee,
            )
            w1._state["positions"] = [{
                "id": "pos-1", "symbol": "BTC-BRL", "side": "LONG",
                "quantity": "0.001", "entry_price": str(fill_price),
                "current_price": "50100.00", "entry_fee": str(fee),
                "pnl": "0", "pnl_pct": "0", "status": "OPEN",
                "opened_at": "2025-01-01T00:00:00Z",
            }]
            w1._state["orders"] = []
            w1._state["history"] = []
            w1._state["decisions"] = []
            w1._save_state()

            w2 = AutonomousWorker(settings=settings)
            w2._load_state()

            assert w2.portfolio.cash == w1.portfolio.cash
            assert "BTC-BRL" in w2.portfolio._positions
            pos = w2.portfolio._positions["BTC-BRL"]
            assert pos.quantity == Decimal("0.001")
            assert pos.average_entry == fill_price
            assert pos.entry_fee == fee
        finally:
            worker_mod._STATE_FILE = orig_file


# ============================================================
# C9-TEST-16: Restart preserves cash
# ============================================================


class TestRestartPreservesCash:

    def test_restart_preserves_cash(self, tmp_path: Path) -> None:
        import aegis.worker as worker_mod
        orig_file = worker_mod._STATE_FILE
        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker
            from aegis.config import Settings

            settings = Settings(initial_capital=Decimal("10000.00"))
            w1 = AutonomousWorker(settings=settings)
            w1.portfolio._cash = Decimal("8000.00")
            w1._state["capital"] = "8000.00"
            w1._state["positions"] = []
            w1._state["orders"] = []
            w1._state["history"] = []
            w1._state["decisions"] = []
            w1._save_state()

            w2 = AutonomousWorker(settings=settings)
            w2._load_state()
            assert w2.portfolio.cash == Decimal("8000.00")
        finally:
            worker_mod._STATE_FILE = orig_file


# ============================================================
# C9-TEST-17: Restart preserves fees/P&L
# ============================================================


class TestRestartPreservesFeesPnL:

    def test_restart_preserves_fees_and_pnl(self, tmp_path: Path) -> None:
        import aegis.worker as worker_mod
        orig_file = worker_mod._STATE_FILE
        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker
            from aegis.config import Settings

            settings = Settings(initial_capital=Decimal("10000.00"))
            w1 = AutonomousWorker(settings=settings)
            w1.portfolio._total_fees = Decimal("5.00")
            w1.portfolio._total_realized_pnl = Decimal("200.00")
            w1._state["positions"] = []
            w1._state["orders"] = []
            w1._state["history"] = []
            w1._state["decisions"] = []
            w1._save_state()

            w2 = AutonomousWorker(settings=settings)
            w2._load_state()
            assert w2.portfolio.total_fees == Decimal("5.00")
            assert w2.portfolio.total_realized_pnl == Decimal("200.00")
        finally:
            worker_mod._STATE_FILE = orig_file


# ============================================================
# C9-TEST-18: Restart reconstructs RiskEngine
# ============================================================


class TestRestartReconstructsRiskEngine:

    def test_restart_rebuilds_risk_engine(self, tmp_path: Path) -> None:
        import aegis.worker as worker_mod
        orig_file = worker_mod._STATE_FILE
        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker
            from aegis.config import Settings

            settings = Settings(initial_capital=Decimal("10000.00"))
            w1 = AutonomousWorker(settings=settings)
            w1.portfolio.record_fill(
                asset="BTC-BRL", side=PositionSide.LONG,
                quantity=Decimal("0.001"), price=Decimal("50000.00"), fee=Decimal("0.50"),
            )
            w1._state["positions"] = [{
                "id": "pos-1", "symbol": "BTC-BRL", "side": "LONG",
                "quantity": "0.001", "entry_price": "50000.00",
                "current_price": "50000.00", "entry_fee": "0.50",
                "pnl": "0", "pnl_pct": "0", "status": "OPEN",
                "opened_at": "2025-01-01T00:00:00Z",
            }]
            w1._state["orders"] = []
            w1._state["history"] = []
            w1._state["decisions"] = []
            w1._save_state()

            w2 = AutonomousWorker(settings=settings)
            w2._load_state()

            assert w2.risk_engine._positions_count == 1
        finally:
            worker_mod._STATE_FILE = orig_file


# ============================================================
# C9-TEST-19: Restart does not create financial divergence
# ============================================================


class TestRestartNoDivergence:

    def test_broker_syncs_with_portfolio(self, tmp_path: Path) -> None:
        import aegis.worker as worker_mod
        orig_file = worker_mod._STATE_FILE
        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker
            from aegis.config import Settings

            settings = Settings(initial_capital=Decimal("10000.00"))
            w1 = AutonomousWorker(settings=settings)
            w1.portfolio._cash = Decimal("7500.00")
            w1._state["capital"] = "7500.00"
            w1._state["positions"] = []
            w1._state["orders"] = []
            w1._state["history"] = []
            w1._state["decisions"] = []
            w1._save_state()

            w2 = AutonomousWorker(settings=settings)
            w2._load_state()
            assert w2.portfolio.cash == Decimal("7500.00")
            assert w2.broker.balance == Decimal("7500.00")
        finally:
            worker_mod._STATE_FILE = orig_file


# ============================================================
# C9-TEST-20: Python runtime is 3.12+
# ============================================================


class TestPythonVersion:

    def test_pyproject_requires_312(self) -> None:
        import tomllib
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        assert data["project"]["requires-python"] == ">=3.12"

    def test_dockerfile_uses_312(self) -> None:
        dockerfile = Path("Dockerfile").read_text()
        assert "python:3.12-slim" in dockerfile
        assert "python3.12/site-packages" in dockerfile

    def test_mypy_targets_312(self) -> None:
        import tomllib
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        assert data["tool"]["mypy"]["python_version"] == "3.12"

    def test_ruff_targets_312(self) -> None:
        import tomllib
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        assert data["tool"]["ruff"]["target-version"] == "py312"


# ============================================================
# Regression: Safety invariants
# ============================================================


class TestCorrection9Safety:

    def test_no_risk_bypass_in_src(self) -> None:
        import aegis.execution.engine as engine_mod
        import inspect
        source = inspect.getsource(engine_mod)
        assert "risk_decision is None or not risk_decision.is_approved" in source

    def test_sandbox_live_fail_safe(self) -> None:
        from aegis.execution.factory import create_broker
        from aegis.config import Settings, TradingEnvironment
        settings = Settings(trading_environment=TradingEnvironment.LIVE, live_enabled=False)
        with pytest.raises(RuntimeError):
            create_broker(settings)

    def test_mercadobitcoin_does_not_reject_all_sell(self) -> None:
        import aegis.execution.mercadobitcoin as mb_mod
        import inspect
        source = inspect.getsource(mb_mod)
        assert "SELL not allowed" not in source

    def test_hot_reload_does_not_update_capital(self) -> None:
        import aegis.worker as worker_mod
        import inspect
        source = inspect.getsource(worker_mod.AutonomousWorker._reload_config)
        assert "self.capital = Decimal(env.get(" not in source
