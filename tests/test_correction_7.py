"""AEGIS V1.3 — Correction #7 Tests.

C7-01: Portfolio peak_equity persists across restart.
C7-02: SandboxBroker starts with configured capital.
C7-03: CLOSE routes through ExecutionEngine/Broker.
C7-04: RiskEngine peak_equity persists across restart.
C7-05: Dashboard drawdown uses equity.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from aegis.domain.enums import OrderSide, OrderStatus, PositionSide, PositionStatus, TradingAction
from aegis.execution.broker import OrderResult, OrderSubmission
from aegis.execution.engine import ExecutionEngine
from aegis.execution.sandbox import SandboxBroker
from aegis.portfolio.portfolio import Portfolio, PositionEntry
from aegis.risk_engine.risk_engine import RiskEngine, RiskDecision
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.ai_engine.decision_engine import DecisionContract


# ============================================================
# C7-01: Portfolio Peak Equity Persistence
# ============================================================


class TestPortfolioPeakEquityPersistence:
    """C7-01: Portfolio._peak_equity survives restart."""

    def test_peak_equity_persisted_in_save_state(self, tmp_path) -> None:
        """_save_state includes peak_equity in JSON output."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            worker = AutonomousWorker()
            worker.portfolio = Portfolio(initial_cash=Decimal("100.00"))
            # Simulate equity going above initial capital
            worker.portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
            # cash = 49.50, need equity > 100 → price > 100.50
            worker.portfolio.update_prices({"BTC-BRL": Decimal("110.00")})
            # equity = 49.50 + (110-50)*1 = 109.50
            assert worker.portfolio._peak_equity > Decimal("100.00")

            worker._state["positions"] = [{
                "id": str(uuid4()),
                "symbol": "BTC-BRL",
                "side": "LONG",
                "quantity": "1",
                "entry_price": "50.00",
                "current_price": "70.00",
                "entry_fee": "0.50",
                "status": "OPEN",
                "opened_at": "2025-01-01T00:00:00Z",
            }]
            worker._save_state()

            saved = json.loads((tmp_path / "state.json").read_text())
            assert "peak_equity" in saved
            assert Decimal(saved["peak_equity"]) == worker.portfolio._peak_equity
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_peak_equity_restored_on_load(self, tmp_path) -> None:
        """_load_state restores Portfolio._peak_equity from saved state."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            # Save state with peak_equity = 120.00
            state = {
                "capital": "49.50",
                "pnl": "0.00",
                "total_fees": "0.50",
                "peak_equity": "120.00",
                "risk_peak_equity": "120.00",
                "positions": [],
                "orders": [],
                "history": [],
                "decisions": [],
            }
            (tmp_path / "state.json").write_text(json.dumps(state))

            worker = AutonomousWorker()
            worker._load_state()
            assert worker.portfolio._peak_equity == Decimal("120.00")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_peak_equity_backward_compat_no_field(self, tmp_path) -> None:
        """Old state files without peak_equity default to capital."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            # Old format without peak_equity
            state = {
                "capital": "80.00",
                "pnl": "0.00",
                "total_fees": "0.00",
                "positions": [],
                "orders": [],
                "history": [],
                "decisions": [],
            }
            (tmp_path / "state.json").write_text(json.dumps(state))

            worker = AutonomousWorker()
            worker._load_state()
            assert worker.portfolio._peak_equity == Decimal("80.00")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")


# ============================================================
# C7-04: RiskEngine Peak Equity Persistence
# ============================================================


class TestRiskEnginePeakEquityPersistence:
    """C7-04: RiskEngine._peak_equity survives restart."""

    def test_risk_peak_equity_persisted(self, tmp_path) -> None:
        """_save_state includes risk_peak_equity."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            worker = AutonomousWorker()
            worker.portfolio = Portfolio(initial_cash=Decimal("100.00"))
            worker.risk_engine.update_equity(Decimal("115.00"))
            assert worker.risk_engine._peak_equity == Decimal("115.00")

            worker._save_state()

            saved = json.loads((tmp_path / "state.json").read_text())
            assert "risk_peak_equity" in saved
            assert Decimal(saved["risk_peak_equity"]) == Decimal("115.00")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_risk_peak_equity_restored(self, tmp_path) -> None:
        """_load_state restores RiskEngine._peak_equity."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            state = {
                "capital": "100.00",
                "pnl": "0.00",
                "total_fees": "0.00",
                "peak_equity": "110.00",
                "risk_peak_equity": "112.50",
                "positions": [],
                "orders": [],
                "history": [],
                "decisions": [],
            }
            (tmp_path / "state.json").write_text(json.dumps(state))

            worker = AutonomousWorker()
            worker._load_state()
            assert worker.risk_engine._peak_equity == Decimal("112.50")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_drawdown_uses_historical_peak_after_restart(self, tmp_path) -> None:
        """After restart, drawdown uses peak from before restart, not initial capital."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            state = {
                "capital": "105.00",
                "pnl": "5.00",
                "total_fees": "1.00",
                "peak_equity": "120.00",
                "risk_peak_equity": "120.00",
                "positions": [],
                "orders": [],
                "history": [],
                "decisions": [],
            }
            (tmp_path / "state.json").write_text(json.dumps(state))

            worker = AutonomousWorker()
            worker._load_state()

            # Drawdown should be (120 - 105) / 120 = 12.5%, not (100 - 105) / 100
            drawdown = worker.portfolio.drawdown
            expected = (Decimal("120.00") - Decimal("105.00")) / Decimal("120.00")
            assert drawdown == expected
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_circuit_breaker_after_restart(self, tmp_path) -> None:
        """Circuit breaker fires at correct drawdown after restart."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            state = {
                "capital": "90.00",
                "pnl": "-10.00",
                "total_fees": "1.00",
                "peak_equity": "100.00",
                "risk_peak_equity": "100.00",
                "positions": [],
                "orders": [],
                "history": [],
                "decisions": [],
            }
            (tmp_path / "state.json").write_text(json.dumps(state))

            worker = AutonomousWorker()
            worker._load_state()

            # 10% drawdown from 100 → circuit breaker should activate
            worker.risk_engine.update_equity(Decimal("90.00"))
            assert worker.risk_engine.circuit_breaker_active is True
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")


# ============================================================
# C7-02: Sandbox Initial Balance
# ============================================================


class TestSandboxInitialBalance:
    """C7-02: SandboxBroker starts with configured TRADING_CAPITAL."""

    def test_factory_passes_capital_to_sandbox(self) -> None:
        """create_broker(initial_balance=X) creates SandboxBroker with balance X."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings, TradingEnvironment

        settings = Settings(
            trading_environment=TradingEnvironment.SANDBOX,
            initial_capital=Decimal("250.00"),
        )
        broker = create_broker(settings, initial_balance=Decimal("250.00"))
        assert isinstance(broker, SandboxBroker)
        assert broker.balance == Decimal("250.00")

    def test_factory_default_uses_settings_capital(self) -> None:
        """create_broker without initial_balance uses Settings.initial_capital."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings, TradingEnvironment

        settings = Settings(
            trading_environment=TradingEnvironment.SANDBOX,
            initial_capital=Decimal("500.00"),
        )
        broker = create_broker(settings)
        assert isinstance(broker, SandboxBroker)
        assert broker.balance == Decimal("500.00")

    def test_sandbox_balance_matches_portfolio(self) -> None:
        """SandboxBroker.balance equals Portfolio.cash at initialization."""
        capital = Decimal("150.00")
        portfolio = Portfolio(initial_cash=capital)
        broker = SandboxBroker(initial_balance=capital)
        assert broker.balance == portfolio.cash


# ============================================================
# C7-03: CLOSE through ExecutionEngine/Broker
# ============================================================


class TestCloseThroughBroker:
    """C7-03: CLOSE routes through ExecutionEngine → Broker → Portfolio."""

    @pytest.mark.asyncio
    async def test_close_sends_sell_order_to_broker(self) -> None:
        """Manual CLOSE submits SELL order through ExecutionEngine."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        risk = RiskEngine(RiskLimits(
            reference_capital=Decimal("100.00"),
            max_position_size_pct=Decimal("0.50"),
            max_risk_per_trade_pct=Decimal("0.10"),
        ))
        execution = ExecutionEngine(broker)

        # BUY first to create a position
        buy_risk = RiskDecision(
            status="APPROVED",
            approved_quantity=Decimal("1"),
            approved_price=Decimal("50.00"),
        )
        buy_result = await execution.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            correlation_id=uuid4(),
            risk_decision=buy_risk,
        )
        assert buy_result.status == OrderStatus.FILLED
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), buy_result.fill_price, buy_result.fee)

        # Verify position exists in broker
        pos = await broker.get_position("BTC-BRL")
        assert pos["quantity"] > Decimal("0")

        # CLOSE via ExecutionEngine (SELL)
        close_risk = RiskDecision(
            status="APPROVED",
            approved_quantity=Decimal("1"),
            approved_price=Decimal("60.00"),
        )
        close_result = await execution.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.SELL,
            quantity=Decimal("1"),
            price=Decimal("60.00"),
            correlation_id=uuid4(),
            risk_decision=close_risk,
        )
        assert close_result.status == OrderStatus.FILLED
        assert close_result.fill_price is not None

    @pytest.mark.asyncio
    async def test_close_uses_broker_fill_price(self) -> None:
        """Portfolio.close_position() uses fill_price from broker result."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        execution = ExecutionEngine(broker)

        # BUY
        buy_risk = RiskDecision(status="APPROVED", approved_quantity=Decimal("1"), approved_price=Decimal("50.00"))
        await execution.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("1"), price=Decimal("50.00"),
            correlation_id=uuid4(), risk_decision=buy_risk,
        )
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.50"), Decimal("0.50"))

        # SELL
        close_risk = RiskDecision(status="APPROVED", approved_quantity=Decimal("1"), approved_price=Decimal("60.00"))
        close_result = await execution.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("1"), price=Decimal("60.00"),
            correlation_id=uuid4(), risk_decision=close_risk,
        )

        # Portfolio close uses broker's fill_price (with slippage), not raw price
        realized = portfolio.close_position("BTC-BRL", close_result.fill_price, close_result.fee)
        assert realized != Decimal("0")  # P&L calculated from broker fill

    @pytest.mark.asyncio
    async def test_close_updates_broker_balance(self) -> None:
        """After CLOSE, SandboxBroker.balance includes sell proceeds."""
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        execution = ExecutionEngine(broker)

        # BUY
        buy_risk = RiskDecision(status="APPROVED", approved_quantity=Decimal("1"), approved_price=Decimal("50.00"))
        await execution.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("1"), price=Decimal("50.00"),
            correlation_id=uuid4(), risk_decision=buy_risk,
        )
        balance_after_buy = broker.balance
        assert balance_after_buy < Decimal("100.00")

        # SELL
        close_risk = RiskDecision(status="APPROVED", approved_quantity=Decimal("1"), approved_price=Decimal("60.00"))
        await execution.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("1"), price=Decimal("60.00"),
            correlation_id=uuid4(), risk_decision=close_risk,
        )
        assert broker.balance > balance_after_buy

    @pytest.mark.asyncio
    async def test_duplicate_close_rejected(self) -> None:
        """Closing an already-closed position is rejected."""
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        execution = ExecutionEngine(broker)

        # BUY
        buy_risk = RiskDecision(status="APPROVED", approved_quantity=Decimal("1"), approved_price=Decimal("50.00"))
        await execution.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("1"), price=Decimal("50.00"),
            correlation_id=uuid4(), risk_decision=buy_risk,
        )

        # First SELL
        close_risk = RiskDecision(status="APPROVED", approved_quantity=Decimal("1"), approved_price=Decimal("60.00"))
        result1 = await execution.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("1"), price=Decimal("60.00"),
            correlation_id=uuid4(), risk_decision=close_risk,
        )
        assert result1.status == OrderStatus.FILLED

        # Second SELL — should be rejected (no position)
        result2 = await execution.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("1"), price=Decimal("60.00"),
            correlation_id=uuid4(), risk_decision=close_risk,
        )
        assert result2.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_close_without_position_rejected(self) -> None:
        """SELL for a symbol with no position is rejected by SandboxBroker."""
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        execution = ExecutionEngine(broker)

        close_risk = RiskDecision(status="APPROVED", approved_quantity=Decimal("1"), approved_price=Decimal("60.00"))
        result = await execution.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("1"), price=Decimal("60.00"),
            correlation_id=uuid4(), risk_decision=close_risk,
        )
        assert result.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_buy_close_full_lifecycle(self) -> None:
        """Full BUY → CLOSE lifecycle through broker updates all state correctly."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        execution = ExecutionEngine(broker)

        # BUY
        buy_risk = RiskDecision(status="APPROVED", approved_quantity=Decimal("1"), approved_price=Decimal("50.00"))
        buy_result = await execution.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("1"), price=Decimal("50.00"),
            correlation_id=uuid4(), risk_decision=buy_risk,
        )
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), buy_result.fill_price, buy_result.fee)

        # CLOSE
        close_risk = RiskDecision(status="APPROVED", approved_quantity=Decimal("1"), approved_price=Decimal("60.00"))
        close_result = await execution.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("1"), price=Decimal("60.00"),
            correlation_id=uuid4(), risk_decision=close_risk,
        )
        realized = portfolio.close_position("BTC-BRL", close_result.fill_price, close_result.fee)

        # Verify all state is consistent
        assert portfolio.cash > Decimal("90.00")  # capital recovered + profit
        assert portfolio._positions["BTC-BRL"].status == PositionStatus.CLOSED
        assert portfolio._positions["BTC-BRL"].quantity == Decimal("0")
        pos = await broker.get_position("BTC-BRL")
        assert pos["quantity"] == Decimal("0")


# ============================================================
# C7-05: Dashboard Drawdown Uses Equity
# ============================================================


class TestDashboardDrawdownEquity:
    """C7-05: State exposes equity for dashboard drawdown calculation."""

    def test_state_includes_equity(self) -> None:
        """Worker state dict includes equity field."""
        from aegis.worker import AutonomousWorker

        worker = AutonomousWorker()
        assert "equity" in worker._state
        assert worker._state["equity"] == str(worker.capital)

    def test_equity_differs_from_capital_with_positions(self) -> None:
        """equity = cash + unrealized_pnl, not just cash."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        portfolio.update_prices({"BTC-BRL": Decimal("60.00")})

        # cash = 49.50, unrealized = 10.00, equity = 59.50
        assert portfolio.cash == Decimal("49.50")
        assert portfolio.equity == Decimal("59.50")
        assert portfolio.cash != portfolio.equity

    def test_drawdown_uses_equity_not_cash(self) -> None:
        """Portfolio.drawdown is based on equity (peak_equity - equity)."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        # cash = 49.50, price 110 → equity = 49.50 + 60 = 109.50, peak = 109.50
        portfolio.update_prices({"BTC-BRL": Decimal("110.00")})
        assert portfolio._peak_equity == Decimal("109.50")

        # Price drops → equity = 49.50 + (80-50)*1 = 79.50
        portfolio.update_prices({"BTC-BRL": Decimal("80.00")})
        drawdown = portfolio.drawdown
        expected = (Decimal("109.50") - Decimal("79.50")) / Decimal("109.50")
        assert drawdown == expected


# ============================================================
# C7-SAFETY: Regression / Safety
# ============================================================


class TestCorrection7Safety:
    """Safety and regression checks for Correction #7."""

    def test_risk_approved_count_zero(self) -> None:
        """risk_approved attribute does not exist (0 occurrences in source)."""
        import ast
        from pathlib import Path

        src_dir = Path(__file__).parent.parent / "src"
        for py_file in src_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert "risk_approved" not in content, f"risk_approved found in {py_file}"

    def test_sandbox_live_fail_safe_preserved(self) -> None:
        """LIVE + LIVE_ENABLED=false still raises RuntimeError."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings, TradingEnvironment

        settings = Settings(
            trading_environment=TradingEnvironment.LIVE,
            live_enabled=False,
        )
        with pytest.raises(RuntimeError, match="LIVE trading is disabled"):
            create_broker(settings)

    def test_portfolio_remains_canonical(self) -> None:
        """Portfolio is still the single source of truth for financial values."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        assert portfolio.cash == Decimal("49.50")
        assert portfolio.total_fees == Decimal("0.50")
        # After price update, equity differs from cash
        portfolio.update_prices({"BTC-BRL": Decimal("60.00")})
        assert portfolio.equity == Decimal("59.50")  # 49.50 + 10.00 unrealized

    def test_close_position_manual_is_async(self) -> None:
        """close_position_manual is now async (needed for ExecutionEngine)."""
        import inspect
        from aegis.worker import AutonomousWorker
        worker = AutonomousWorker()
        assert inspect.iscoroutinefunction(worker.close_position_manual)

    def test_mercadobitcoin_blocks_sell(self) -> None:
        """MercadoBitcoinBroker rejects SELL — fail-closed in LIVE."""
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig

        config = MercadoBitcoinConfig(enabled=True, api_key="test", api_secret="test")
        broker = MercadoBitcoinBroker(config)
        # Pretend authenticated so SELL check is reached
        broker._access_token = "fake_token"
        broker._token_expiry = 9999999999.0

        submission = OrderSubmission(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.SELL,
            quantity=Decimal("1"),
            price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(broker.submit_order(submission))
            assert result.status == OrderStatus.REJECTED
            assert "SELL not allowed" in result.error
        finally:
            loop.close()
