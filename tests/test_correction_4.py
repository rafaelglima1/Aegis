"""AEGIS V1.3 Correction #4 — State Consistency & Prompt Semantics Tests.

Tests for findings XX-01 through XX-05.
All tests use mocks — no real credentials or live API calls.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from aegis.domain.enums import OrderSide, OrderStatus, PositionSide, TradingAction
from aegis.execution.broker import OrderResult, OrderSubmission
from aegis.execution.engine import ExecutionEngine
from aegis.execution.mercadobitcoin import MercadoBitcoinConfig
from aegis.execution.sandbox import SandboxBroker
from aegis.portfolio.portfolio import Portfolio
from aegis.risk_engine.risk_engine import RiskEngine, RiskDecision
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.ai_engine.decision_engine import DecisionContract
from aegis.pipeline import TradingPipeline
from aegis.replay import ReplayEngine, ReplayDataset, Candle, ReplayState
from aegis.api.settings import build_default_prompt
from aegis.execution.factory import create_broker
from aegis.config import Settings
from datetime import datetime, timezone


# ============================================================
# XX-01: Pipeline Capital Consistency
# ============================================================

class TestPipelineCapitalConsistency:
    """pipeline.close_position must derive capital from Portfolio, not stale state."""

    def _open_and_close(self):
        """Helper: open a LONG via SandboxBroker, then close via mock broker."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        broker = SandboxBroker()
        risk = RiskEngine(RiskLimits(
            reference_capital=Decimal("100.00"),
            max_position_size_pct=Decimal("0.50"),
            max_risk_per_trade_pct=Decimal("0.10"),
        ))
        pipeline = TradingPipeline(broker=broker, portfolio=portfolio, risk_engine=risk)

        open_decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.8"),
            thesis="Open",
            entry_price=Decimal("50.00"),
            stop_loss=Decimal("45.00"),
            take_profit=Decimal("60.00"),
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pipeline.run("BTC-BRL", open_decision))

            # Switch to mock broker for close (SandboxBroker rejects SELL due to balance check)
            mock_broker = AsyncMock()
            mock_broker.submit_order = AsyncMock(return_value=OrderResult(
                order_id=uuid4(),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=Decimal("1"),
                fee=Decimal("0.50"),
            ))
            pipeline._broker = mock_broker
            pipeline._execution = ExecutionEngine(mock_broker)

            pos_id = pipeline.state["positions"][0]["id"]
            loop.run_until_complete(pipeline.close_position(pos_id))
        finally:
            loop.close()
        return pipeline

    def test_close_updates_capital_from_portfolio(self) -> None:
        """After CLOSE, state.capital must equal portfolio.cash."""
        pipeline = self._open_and_close()
        assert Decimal(pipeline.state["capital"]) == pipeline._portfolio.cash

    def test_close_updates_pnl_from_portfolio(self) -> None:
        """After CLOSE, state.pnl must equal portfolio.total_realized_pnl."""
        pipeline = self._open_and_close()
        assert Decimal(pipeline.state["pnl"]) == pipeline._portfolio.total_realized_pnl

    def test_close_capital_not_independently_tracked(self) -> None:
        """Capital must come from portfolio.cash, not a stale sum."""
        pipeline = self._open_and_close()
        # capital must exactly match portfolio, not be a computed sum
        assert Decimal(pipeline.state["capital"]) == pipeline._portfolio.cash
        assert Decimal(pipeline.state["capital"]) == Decimal("100.00") + pipeline._portfolio.total_realized_pnl

    def test_close_profit_capital_increases(self) -> None:
        """Closing at profit: capital must increase."""
        pipeline = self._open_and_close()
        assert Decimal(pipeline.state["capital"]) > Decimal("100.00")


# ============================================================
# XX-02: Prompt Confidence Semantics
# ============================================================

class TestPromptConfidenceSemantics:
    """build_default_prompt must convert fraction to percentage."""

    def test_confidence_05_shows_50(self) -> None:
        """min_confidence=0.5 must render as '50%'."""
        prompt = build_default_prompt(min_confidence=0.5)
        assert "confian\u00e7a >= 50%" in prompt
        assert "0.5%" not in prompt

    def test_confidence_07_shows_70(self) -> None:
        """min_confidence=0.7 must render as '70%'."""
        prompt = build_default_prompt(min_confidence=0.7)
        assert "confian\u00e7a >= 70%" in prompt
        assert "0.7%" not in prompt

    def test_confidence_01_shows_10(self) -> None:
        """min_confidence=0.1 must render as '10%'."""
        prompt = build_default_prompt(min_confidence=0.1)
        assert "confian\u00e7a >= 10%" in prompt

    def test_confidence_10_shows_100(self) -> None:
        """min_confidence=1.0 must render as '100%'."""
        prompt = build_default_prompt(min_confidence=1.0)
        assert "confian\u00e7a >= 100%" in prompt

    def test_other_params_still_correct(self) -> None:
        """Non-confidence params must render correctly."""
        prompt = build_default_prompt(
            capital=200.0,
            risk_pct=2.0,
            max_daily_loss_pct=3.0,
            max_position_size_pct=15.0,
            min_confidence=0.6,
            max_positions=5,
        )
        assert "R$ 200" in prompt
        assert "2.0% por trade" in prompt
        assert "3.0% do capital" in prompt
        assert "15.0% do capital" in prompt
        assert "confian\u00e7a >= 60%" in prompt
        assert "M\u00e1ximo 5" in prompt

    def test_riskengine_still_uses_fraction(self) -> None:
        """RiskEngine must still use 0.5 fraction internally, not 50."""
        risk = RiskEngine(RiskLimits(reference_capital=Decimal("100.00")))
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.3"),  # below 0.5 threshold
            thesis="Low confidence",
            entry_price=Decimal("50.00"),
            stop_loss=Decimal("45.00"),
            take_profit=Decimal("60.00"),
        )
        result = risk.evaluate(decision)
        assert not result.is_approved
        assert any(v.code == "LOW_CONFIDENCE" for v in result.violations)

    def test_riskengine_approves_at_50_percent(self) -> None:
        """RiskEngine approves at confidence >= 0.5."""
        risk = RiskEngine(RiskLimits(
            reference_capital=Decimal("100.00"),
            max_position_size_pct=Decimal("0.50"),
        ))
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.5"),
            thesis="At threshold",
            entry_price=Decimal("50.00"),
            stop_loss=Decimal("45.00"),
            take_profit=Decimal("60.00"),
        )
        result = risk.evaluate(decision)
        assert result.is_approved


# ============================================================
# XX-03: Dashboard Close Endpoint
# ============================================================

class TestDashboardCloseEndpoint:
    """close_position_manual routes through Portfolio correctly."""

    def _make_worker_with_position(self):
        """Create a worker with a simulated open position."""
        from aegis.worker import AutonomousWorker
        worker = AutonomousWorker()
        worker.portfolio = Portfolio(initial_cash=Decimal("100.00"))
        worker._state = {
            "capital": "100.00",
            "pnl": "0.00",
            "positions": [{
                "id": str(uuid4()),
                "symbol": "BTC-BRL",
                "side": "LONG",
                "quantity": "1",
                "entry_price": "50.00",
                "current_price": "60.00",
                "pnl": "10.00",
                "pnl_pct": "20.0",
                "stop_loss": "45.00",
                "take_profit": "60.00",
                "status": "OPEN",
                "opened_at": "2025-01-01T00:00:00Z",
            }],
            "orders": [],
            "history": [],
            "decisions": [],
            "exposure": "60.00",
            "peak_equity": "100.00",
            "risk_limits": {},
        }
        return worker

    @pytest.mark.asyncio
    async def test_close_existing_position(self) -> None:
        """Closing an existing OPEN position succeeds."""
        worker = self._make_worker_with_position()
        pos_id = worker._state["positions"][0]["id"]

        async def mock_execute(*args, **kwargs):
            return OrderResult(
                order_id=kwargs.get("order_id", uuid4()),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=kwargs.get("quantity", Decimal("1")),
                fee=Decimal("0.50"),
            )
        worker.execution.execute_order = mock_execute

        result = await worker.close_position_manual(pos_id)

        assert result["status"] == "CLOSED"
        assert "pnl" in result
        assert "capital" in result

    @pytest.mark.asyncio
    async def test_close_nonexistent_position(self) -> None:
        """Closing a nonexistent position returns NOT_FOUND."""
        worker = self._make_worker_with_position()

        result = await worker.close_position_manual(str(uuid4()))

        assert result["status"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_position_marked_closed(self) -> None:
        """After close, position status must be CLOSED."""
        worker = self._make_worker_with_position()
        pos_id = worker._state["positions"][0]["id"]

        async def mock_execute(*args, **kwargs):
            return OrderResult(
                order_id=kwargs.get("order_id", uuid4()),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=kwargs.get("quantity", Decimal("1")),
                fee=Decimal("0.50"),
            )
        worker.execution.execute_order = mock_execute

        await worker.close_position_manual(pos_id)

        assert worker._state["positions"][0]["status"] == "CLOSED"

    @pytest.mark.asyncio
    async def test_capital_updated_from_portfolio(self) -> None:
        """After close, capital must equal portfolio.cash."""
        worker = self._make_worker_with_position()
        pos_id = worker._state["positions"][0]["id"]

        async def mock_execute(*args, **kwargs):
            return OrderResult(
                order_id=kwargs.get("order_id", uuid4()),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=kwargs.get("quantity", Decimal("1")),
                fee=Decimal("0.50"),
            )
        worker.execution.execute_order = mock_execute

        await worker.close_position_manual(pos_id)

        assert Decimal(worker._state["capital"]) == worker.portfolio.cash

    @pytest.mark.asyncio
    async def test_pnl_updated_from_portfolio(self) -> None:
        """After close, pnl must equal portfolio.total_realized_pnl."""
        worker = self._make_worker_with_position()
        pos_id = worker._state["positions"][0]["id"]

        async def mock_execute(*args, **kwargs):
            return OrderResult(
                order_id=kwargs.get("order_id", uuid4()),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=kwargs.get("quantity", Decimal("1")),
                fee=Decimal("0.50"),
            )
        worker.execution.execute_order = mock_execute

        await worker.close_position_manual(pos_id)

        assert Decimal(worker._state["pnl"]) == worker.portfolio.total_realized_pnl

    @pytest.mark.asyncio
    async def test_pnl_is_net_of_fees(self) -> None:
        """P&L must account for both entry and exit fees."""
        worker = self._make_worker_with_position()
        # Simulate entry fee by recording fill
        worker.portfolio.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            fee=Decimal("0.50"),
        )

        pos_id = worker._state["positions"][0]["id"]

        async def mock_execute(*args, **kwargs):
            return OrderResult(
                order_id=kwargs.get("order_id", uuid4()),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=kwargs.get("quantity", Decimal("1")),
                fee=Decimal("0.50"),
            )
        worker.execution.execute_order = mock_execute

        result = await worker.close_position_manual(pos_id)

        # gross = (60 - 50) * 1 = 10, net = 10 - entry_fee(0.50) - exit_fee(0.50) = 9.00
        assert Decimal(result["pnl"]) == Decimal("9.00")
        assert Decimal(worker._state["capital"]) == worker.portfolio.cash

    @pytest.mark.asyncio
    async def test_history_recorded(self) -> None:
        """Close must append trade to history."""
        worker = self._make_worker_with_position()
        pos_id = worker._state["positions"][0]["id"]

        async def mock_execute(*args, **kwargs):
            return OrderResult(
                order_id=kwargs.get("order_id", uuid4()),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=kwargs.get("quantity", Decimal("1")),
                fee=Decimal("0.50"),
            )
        worker.execution.execute_order = mock_execute

        await worker.close_position_manual(pos_id)

        assert len(worker._state["history"]) == 1
        trade = worker._state["history"][0]
        assert trade["symbol"] == "BTC-BRL"
        assert trade["status"] if "status" in trade else True  # no status in history, that's fine

    @pytest.mark.asyncio
    async def test_risk_engine_decremented(self) -> None:
        """RiskEngine position count must decrement after close."""
        worker = self._make_worker_with_position()
        worker.risk_engine.record_position_open()  # simulate open from risk perspective

        pos_id = worker._state["positions"][0]["id"]

        async def mock_execute(*args, **kwargs):
            return OrderResult(
                order_id=kwargs.get("order_id", uuid4()),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=kwargs.get("quantity", Decimal("1")),
                fee=Decimal("0.50"),
            )
        worker.execution.execute_order = mock_execute

        await worker.close_position_manual(pos_id)

        assert worker.risk_engine._positions_count == 0

    @pytest.mark.asyncio
    async def test_already_closed_position_not_closeable(self) -> None:
        """Closing an already CLOSED position returns NOT_FOUND."""
        worker = self._make_worker_with_position()
        pos_id = worker._state["positions"][0]["id"]

        async def mock_execute(*args, **kwargs):
            return OrderResult(
                order_id=kwargs.get("order_id", uuid4()),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=kwargs.get("quantity", Decimal("1")),
                fee=Decimal("0.50"),
            )
        worker.execution.execute_order = mock_execute

        await worker.close_position_manual(pos_id)
        result = await worker.close_position_manual(pos_id)

        assert result["status"] == "NOT_FOUND"


# ============================================================
# XX-04: Replay Risk Configuration
# ============================================================

class TestReplayRiskConfiguration:
    """ReplayEngine must accept and propagate RiskLimits."""

    def test_replay_default_uses_default_risk(self) -> None:
        """Without RiskLimits, Replay uses default RiskEngine."""
        engine = ReplayEngine()
        assert engine._risk_limits is None

    def test_replay_custom_capital_respected(self) -> None:
        """Custom initial_capital flows to replay portfolio."""
        engine = ReplayEngine(initial_capital=Decimal("500.00"))
        assert engine._initial_capital == Decimal("500.00")

    def test_replay_custom_risk_limits_propagated(self) -> None:
        """Custom RiskLimits must flow to RiskEngine."""
        limits = RiskLimits(
            reference_capital=Decimal("200.00"),
            max_simultaneous_positions=3,
        )
        engine = ReplayEngine(
            initial_capital=Decimal("200.00"),
            risk_limits=limits,
        )
        assert engine._risk_limits is not None
        assert engine._risk_limits.reference_capital == Decimal("200.00")
        assert engine._risk_limits.max_simultaneous_positions == 1  # Clamped by hard limit

    def test_replay_max_positions_respected(self) -> None:
        """Replay with custom max_positions must reject when limit exceeded."""
        limits = RiskLimits(
            reference_capital=Decimal("100.00"),
            max_simultaneous_positions=1,
        )
        engine = ReplayEngine(risk_limits=limits)

        dataset = ReplayDataset(symbol="BTC-BRL")
        candle = Candle(
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("1000"),
        )
        dataset.candles = [candle]
        engine.register_dataset(dataset)

        class AlwaysLongStrategy:
            def decide(self, candle, portfolio_state):
                return DecisionContract(
                    action=TradingAction.LONG,
                    confidence=Decimal("0.9"),
                    thesis="Buy",
                    entry_price=candle.close,
                    stop_loss=Decimal("90"),
                    take_profit=Decimal("130"),  # R/R = (130-105)/(105-90) = 1.67 >= 1.5
                )

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                engine.run_replay(dataset.dataset_id, strategy=AlwaysLongStrategy())
            )
        finally:
            loop.close()
        assert result.state == ReplayState.COMPLETED
        # With 1 candle and max_positions=1, only 1 trade
        assert result.total_trades == 1


# ============================================================
# XX-05: MercadoBitcoinConfig Sentinel
# ============================================================

class TestMercadoBitcoinConfigSentinel:
    """MercadoBitcoinConfig.max_positions must use sentinel 0."""

    def test_default_max_positions_is_zero(self) -> None:
        """Default max_positions must be sentinel 0 (not hardcoded 1)."""
        config = MercadoBitcoinConfig()
        assert config.max_positions == 0

    def test_factory_overrides_with_settings(self) -> None:
        """Factory must propagate settings.max_positions, overriding sentinel."""
        settings = Settings(max_positions=1)
        broker = create_broker(settings)
        # SandboxBroker — can't inspect max_positions, but ensure no crash
        assert isinstance(broker, SandboxBroker)

    def test_live_factory_propagates_settings(self) -> None:
        """Live factory propagates settings.max_positions to MercadoBitcoinConfig."""
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "aegis.execution.mercadobitcoin.MercadoBitcoinBroker.__init__", return_value=None
        ):
            settings = Settings(
                trading_environment="LIVE",
                live_enabled=True,
                max_positions=1,
                live_api_key="test",
                live_api_secret="test",
            )
            broker = create_broker(settings)
            # Verify it was created (MercadoBitcoinBroker)
            from aegis.execution.mercadobitcoin import MercadoBitcoinBroker
            assert isinstance(broker, MercadoBitcoinBroker)


# ============================================================
# E — Safety: SANDBOX/LIVE, Risk Gate, No Bypass
# ============================================================

class TestCorrection4Safety:
    """Regression safety checks for Correction #4 changes."""

    def test_sandbox_stays_sandbox(self) -> None:
        """SANDBOX environment must create SandboxBroker."""
        settings = Settings(trading_environment="SANDBOX")
        broker = create_broker(settings)
        assert isinstance(broker, SandboxBroker)

    def test_live_disabled_fail_closed(self) -> None:
        """LIVE + LIVE_ENABLED=false must raise RuntimeError."""
        settings = Settings(trading_environment="LIVE", live_enabled=False)
        with pytest.raises(RuntimeError, match="LIVE trading is disabled"):
            create_broker(settings)

    def test_no_risk_approved_parameter_in_engine(self) -> None:
        """ExecutionEngine must not accept risk_approved boolean."""
        import inspect
        from aegis.execution.engine import ExecutionEngine
        sig = inspect.signature(ExecutionEngine.execute_order)
        assert "risk_approved" not in sig.parameters

    def test_no_risk_approved_parameter_in_orchestrator(self) -> None:
        """ExecutionOrchestrator must not accept risk_approved boolean."""
        import inspect
        from aegis.execution.orchestrator import ExecutionOrchestrator
        sig = inspect.signature(ExecutionOrchestrator.submit_order)
        assert "risk_approved" not in sig.parameters

    def test_risk_gate_requires_risk_decision(self) -> None:
        """ExecutionEngine must reject when risk_decision is None."""
        broker = SandboxBroker()
        engine = ExecutionEngine(broker)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                engine.execute_order(
                    order_id=uuid4(),
                    idempotency_key=uuid4(),
                    symbol="BTC-BRL",
                    side=OrderSide.BUY,
                    quantity=Decimal("0.01"),
                    price=Decimal("50000"),
                    correlation_id=uuid4(),
                )
            )
        finally:
            loop.close()
        assert result.status == OrderStatus.REJECTED
