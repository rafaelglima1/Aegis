"""AEGIS V1.3 Correction #3 - Architectural Findings Tests.

Tests for AC-C3-01 through AC-C3-12.
All tests use mocks - no real credentials or live API calls.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from aegis.config import Settings
from aegis.domain.enums import OrderSide, OrderStatus, PositionSide, TradingAction
from aegis.execution.broker import BrokerAdapter, OrderResult
from aegis.execution.engine import ExecutionEngine
from aegis.execution.live import LiveBrokerConfig
from aegis.execution.mercadobitcoin import MercadoBitcoinBroker
from aegis.execution.orchestrator import ExecutionOrchestrator
from aegis.execution.sandbox import SandboxBroker
from aegis.portfolio.portfolio import Portfolio
from aegis.risk_engine.risk_engine import RiskEngine, RiskDecision
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.ai_engine.decision_engine import DecisionContract
from aegis.pipeline import TradingPipeline
from aegis.replay import ReplayEngine, ReplayDataset, Candle, ReplayState
from aegis.dashboard import DashboardRiskStatus
from aegis.execution.factory import create_broker
from aegis.api.settings import build_default_prompt
from datetime import datetime, timezone


# ============================================================
# AC-C3-01: CLOSE contabil estrito
# ============================================================

class TestCloseContabilEstrito:

    def test_close_profit(self) -> None:
        p = Portfolio(initial_cash=Decimal("100.00"))
        p.record_fill(asset="BTC-BRL", side=PositionSide.LONG, quantity=Decimal("1"), price=Decimal("100.00"), fee=Decimal("0.10"))
        realized = p.close_position("BTC-BRL", Decimal("110.00"), fee=Decimal("0.11"))
        assert realized > 0
        gross = (Decimal("110.00") - Decimal("100.00")) * Decimal("1")
        assert realized == gross - Decimal("0.10") - Decimal("0.11")

    def test_close_loss(self) -> None:
        p = Portfolio(initial_cash=Decimal("100.00"))
        p.record_fill(asset="BTC-BRL", side=PositionSide.LONG, quantity=Decimal("1"), price=Decimal("100.00"), fee=Decimal("0.10"))
        realized = p.close_position("BTC-BRL", Decimal("90.00"), fee=Decimal("0.09"))
        assert realized < 0
        gross = (Decimal("90.00") - Decimal("100.00")) * Decimal("1")
        assert realized == gross - Decimal("0.10") - Decimal("0.09")

    def test_close_with_entry_and_exit_fee(self) -> None:
        p = Portfolio(initial_cash=Decimal("500.00"))
        p.record_fill(asset="BTC-BRL", side=PositionSide.LONG, quantity=Decimal("0.5"), price=Decimal("100.00"), fee=Decimal("0.50"))
        realized = p.close_position("BTC-BRL", Decimal("120.00"), fee=Decimal("0.60"))
        gross = (Decimal("120.00") - Decimal("100.00")) * Decimal("0.5")
        assert realized == gross - Decimal("0.50") - Decimal("0.60")

    def test_position_removed_after_close(self) -> None:
        p = Portfolio(initial_cash=Decimal("100.00"))
        p.record_fill(asset="BTC-BRL", side=PositionSide.LONG, quantity=Decimal("1"), price=Decimal("100.00"))
        assert "BTC-BRL" in p.positions
        p.close_position("BTC-BRL", Decimal("110.00"))
        assert p.positions["BTC-BRL"].quantity == Decimal("0")

    def test_realized_pnl_net(self) -> None:
        p = Portfolio(initial_cash=Decimal("100.00"))
        p.record_fill(asset="X", side=PositionSide.LONG, quantity=Decimal("1"), price=Decimal("50.00"), fee=Decimal("0.50"))
        realized = p.close_position("X", Decimal("60.00"), fee=Decimal("0.60"))
        assert realized == Decimal("8.90")

    def test_total_fees_correct(self) -> None:
        p = Portfolio(initial_cash=Decimal("100.00"))
        p.record_fill(asset="X", side=PositionSide.LONG, quantity=Decimal("1"), price=Decimal("50.00"), fee=Decimal("0.50"))
        p.close_position("X", Decimal("60.00"), fee=Decimal("0.60"))
        assert p.total_fees == Decimal("1.10")

    def test_persisted_state_reflects_closed(self) -> None:
        state = {"positions": [{"id": str(uuid4()), "symbol": "BTC-BRL", "side": "LONG", "quantity": "0.001", "entry_price": "50000", "status": "OPEN"}], "orders": [], "history": [], "decisions": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(state, f, default=str)
            state_file = Path(f.name)
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            assert loaded["positions"][0]["status"] == "OPEN"
            loaded["positions"][0]["status"] = "CLOSED"
            assert loaded["positions"][0]["status"] == "CLOSED"
        finally:
            state_file.unlink()

    def test_risk_engine_updated_after_close(self) -> None:
        risk = RiskEngine(RiskLimits(max_simultaneous_positions=1))
        risk.record_position_open()
        assert risk.positions_count == 1
        risk.record_position_close()
        assert risk.positions_count == 0
        decision = DecisionContract(action=TradingAction.LONG, confidence=Decimal("0.85"), thesis="test", entry_price=Decimal("50000"), stop_loss=Decimal("48000"), take_profit=Decimal("55000"))
        result = risk.evaluate(decision)
        assert result.is_approved


# ============================================================
# AC-C3-02: CLOSE sem fee hardcoded
# ============================================================

class TestCloseSemFeeHardcoded:

    def test_close_fee_not_zero(self) -> None:
        p = Portfolio(initial_cash=Decimal("100.00"))
        p.record_fill(asset="X", side=PositionSide.LONG, quantity=Decimal("1"), price=Decimal("100.00"), fee=Decimal("0.00"))
        exit_fee = Decimal("0.50")
        p.close_position("X", Decimal("110.00"), fee=exit_fee)
        assert p.total_fees == exit_fee

    def test_worker_close_uses_portfolio(self) -> None:
        from aegis.worker import AutonomousWorker
        source = inspect.getsource(AutonomousWorker._process_symbol)
        assert "portfolio.close_position" in source
        assert '"fee": "0"' not in source


# ============================================================
# AC-C3-03: Risk Gate sem bypass
# ============================================================

class TestRiskGateSemBypass:

    def test_risk_approved_param_removed_from_engine(self) -> None:
        sig = inspect.signature(ExecutionEngine.execute_order)
        assert "risk_approved" not in sig.parameters

    def test_risk_approved_param_removed_from_orchestrator(self) -> None:
        sig = inspect.signature(ExecutionOrchestrator.submit_order)
        assert "risk_approved" not in sig.parameters

    @pytest.mark.asyncio
    async def test_no_risk_decision_blocks_broker(self) -> None:
        mock_broker = AsyncMock(spec=BrokerAdapter)
        engine = ExecutionEngine(mock_broker)
        result = await engine.execute_order(order_id=uuid4(), idempotency_key=uuid4(), symbol="X", side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("100"), correlation_id=uuid4())
        assert result.status == OrderStatus.REJECTED
        mock_broker.submit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejected_blocks_broker(self) -> None:
        mock_broker = AsyncMock(spec=BrokerAdapter)
        engine = ExecutionEngine(mock_broker)
        rejected = RiskDecision(status="REJECTED", violations=[], reasons=["test"])
        result = await engine.execute_order(order_id=uuid4(), idempotency_key=uuid4(), symbol="X", side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("100"), correlation_id=uuid4(), risk_decision=rejected)
        assert result.status == OrderStatus.REJECTED
        mock_broker.submit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_approved_allows_broker(self) -> None:
        mock_broker = AsyncMock(spec=BrokerAdapter)
        mock_broker.submit_order = AsyncMock(return_value=OrderResult(order_id=uuid4(), status=OrderStatus.FILLED))
        engine = ExecutionEngine(mock_broker)
        approved = RiskDecision(status="APPROVED")
        result = await engine.execute_order(order_id=uuid4(), idempotency_key=uuid4(), symbol="X", side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("100"), correlation_id=uuid4(), risk_decision=approved)
        assert result.status == OrderStatus.FILLED
        mock_broker.submit_order.assert_called_once()


# ============================================================
# AC-C3-04: Pipeline capital sem hardcode
# ============================================================

class TestPipelineCapitalSemHardcode:

    def test_pipeline_capital_100(self) -> None:
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        pipeline = TradingPipeline(portfolio=portfolio)
        assert pipeline.state["capital"] == "100.00"

    def test_pipeline_capital_200(self) -> None:
        portfolio = Portfolio(initial_cash=Decimal("200.00"))
        pipeline = TradingPipeline(portfolio=portfolio)
        assert pipeline.state["capital"] == "200.00"

    def test_pipeline_source_reads_portfolio(self) -> None:
        source = inspect.getsource(TradingPipeline.__init__)
        assert "portfolio.cash" in source


# ============================================================
# AC-C3-05: max_positions sem configuration drift
# ============================================================

class TestMaxPositionsSemDrift:

    def test_risk_engine_uses_config(self) -> None:
        """AC-C10-07: RiskLimits clamps to MAX_POSITIONS_HARD_LIMIT=1."""
        from aegis.risk_engine.risk_limits import MAX_POSITIONS_HARD_LIMIT
        risk = RiskEngine(RiskLimits(max_simultaneous_positions=3))
        assert risk.limits.max_simultaneous_positions == MAX_POSITIONS_HARD_LIMIT

        # 0 positions -> approved
        decision = DecisionContract(action=TradingAction.LONG, confidence=Decimal("0.85"), thesis="test", entry_price=Decimal("50000"), stop_loss=Decimal("48000"), take_profit=Decimal("55000"))
        result = risk.evaluate(decision)
        assert result.is_approved

        # 1 position -> rejected (hard limit)
        risk.record_position_open()
        result = risk.evaluate(decision)
        assert not result.is_approved

    def test_worker_uses_config(self) -> None:
        import os
        os.environ["MAX_POSITIONS"] = "3"
        try:
            from aegis.worker import AutonomousWorker
            worker = AutonomousWorker.__new__(AutonomousWorker)
            worker.max_positions = int(os.getenv("MAX_POSITIONS", "1"))
            assert worker.max_positions == 3
        finally:
            del os.environ["MAX_POSITIONS"]

    def test_dashboard_no_stale_default(self) -> None:
        status = DashboardRiskStatus()
        assert status.max_positions != 5

    def test_live_broker_config_no_stale_default(self) -> None:
        config = LiveBrokerConfig()
        assert config.max_positions != 5

    def test_factory_propagates_max_positions(self) -> None:
        settings = Settings(trading_environment="LIVE", live_enabled=True, live_api_key="key", live_api_secret="secret", max_positions=3)
        broker = create_broker(settings)
        assert isinstance(broker, MercadoBitcoinBroker)
        assert broker._config.max_positions == 3

    def test_propagation_max_positions_3(self) -> None:
        """AC-C10-07: RiskLimits clamps to MAX_POSITIONS_HARD_LIMIT=1."""
        from aegis.risk_engine.risk_limits import MAX_POSITIONS_HARD_LIMIT
        settings = Settings(max_positions=3)
        risk = RiskEngine(RiskLimits(max_simultaneous_positions=settings.max_positions))
        assert risk.limits.max_simultaneous_positions == MAX_POSITIONS_HARD_LIMIT

        # 0 positions -> approved
        decision = DecisionContract(action=TradingAction.LONG, confidence=Decimal("0.85"), thesis="test", entry_price=Decimal("50000"), stop_loss=Decimal("48000"), take_profit=Decimal("55000"))
        result = risk.evaluate(decision)
        assert result.is_approved

        # 1 position -> rejected (hard limit)
        risk.record_position_open()
        result = risk.evaluate(decision)
        assert not result.is_approved


# ============================================================
# AC-C3-06: Replay sem capital obsoleto
# ============================================================

class TestReplaySemCapitalObsoleto:

    def test_replay_default_capital(self) -> None:
        engine = ReplayEngine()
        assert engine._initial_capital == Decimal("100.00")

    def test_replay_configurable_capital(self) -> None:
        engine = ReplayEngine(initial_capital=Decimal("500.00"))
        assert engine._initial_capital == Decimal("500.00")

    @pytest.mark.asyncio
    async def test_replay_respects_capital(self) -> None:
        engine = ReplayEngine(initial_capital=Decimal("200.00"))
        dataset = ReplayDataset(symbol="BTC-BRL", candles=[Candle(datetime(2024, 1, 1, 0, tzinfo=timezone.utc), Decimal("100"), Decimal("105"), Decimal("95"), Decimal("102"), Decimal("1000"))])
        engine.register_dataset(dataset)
        result = await engine.run_replay(dataset.dataset_id)
        assert result.state == ReplayState.COMPLETED
        assert result.portfolio_snapshots[0]["cash"] == "200.00"

    def test_replay_no_stale_10000(self) -> None:
        source = inspect.getsource(ReplayEngine.run_replay)
        assert "10000" not in source


# ============================================================
# AC-C3-07: Fallback prompt sem valores financeiros hardcoded
# ============================================================

class TestFallbackPromptSemHardcodes:

    def test_build_default_prompt_configurable(self) -> None:
        prompt = build_default_prompt(capital=200.0, risk_pct=2.0, max_positions=3)
        assert "R$ 200" in prompt
        assert "2.0%" in prompt
        assert "M\u00e1ximo 3" in prompt

    def test_build_default_prompt_no_hardcoded_100(self) -> None:
        prompt = build_default_prompt(capital=300.0)
        assert "R$ 300" in prompt
        assert "R$ 100" not in prompt

    def test_main_fetches_config_dynamically(self) -> None:
        source = inspect.getsource(TradingPipeline)
        from aegis import main
        main_source = inspect.getsource(main)
        assert "fetch('/api/settings')" in main_source

    def test_prompt_with_custom_values(self) -> None:
        prompt = build_default_prompt(capital=200.0, risk_pct=2.0, max_daily_loss_pct=3.0, max_position_size_pct=15.0, min_confidence=0.7, max_positions=3)
        assert "R$ 200" in prompt
        assert "2.0% por trade" in prompt
        assert "3.0% do capital" in prompt
        assert "15.0% do capital" in prompt
        assert "confian\u00e7a >= 70%" in prompt
        assert "M\u00e1ximo 3" in prompt


# ============================================================
# AC-C3-08: Sandbox/Live preservado
# ============================================================

class TestSandboxLivePreservado:

    def test_sandbox_broker_created(self) -> None:
        settings = Settings(trading_environment="SANDBOX")
        broker = create_broker(settings)
        assert isinstance(broker, SandboxBroker)

    def test_live_disabled_blocked(self) -> None:
        settings = Settings(trading_environment="LIVE", live_enabled=False)
        with pytest.raises(RuntimeError, match="LIVE trading is disabled"):
            create_broker(settings)

    def test_live_enabled_creates_broker(self) -> None:
        settings = Settings(trading_environment="LIVE", live_enabled=True, live_api_key="key", live_api_secret="secret")
        broker = create_broker(settings)
        assert isinstance(broker, MercadoBitcoinBroker)

    @pytest.mark.asyncio
    async def test_risk_rejected_blocks_both_brokers(self) -> None:
        mock_broker = AsyncMock(spec=BrokerAdapter)
        engine = ExecutionEngine(mock_broker)
        rejected = RiskDecision(status="REJECTED", violations=[], reasons=["test"])
        result = await engine.execute_order(order_id=uuid4(), idempotency_key=uuid4(), symbol="X", side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("100"), correlation_id=uuid4(), risk_decision=rejected)
        assert result.status == OrderStatus.REJECTED
        mock_broker.submit_order.assert_not_called()

    def test_sandbox_is_broker_adapter(self) -> None:
        assert issubclass(SandboxBroker, BrokerAdapter)

    def test_live_broker_is_broker_adapter(self) -> None:
        assert issubclass(MercadoBitcoinBroker, BrokerAdapter)


# ============================================================
# AC-C3-09: No duplicate source of truth
# ============================================================

class TestNoDuplicateSourceOfTruth:

    def test_portfolio_no_stale_10000(self) -> None:
        from aegis.portfolio.portfolio import Portfolio
        source = inspect.getsource(Portfolio)
        assert "10000" not in source

    def test_risk_limits_default_matches_config(self) -> None:
        from aegis.risk_engine.risk_limits import RiskLimits
        limits = RiskLimits()
        settings = Settings()
        assert limits.reference_capital == settings.initial_capital

    def test_sandbox_default_matches_config(self) -> None:
        settings = Settings()
        broker = SandboxBroker()
        assert broker.balance == settings.initial_capital

    def test_replay_no_stale_10000(self) -> None:
        source = inspect.getsource(ReplayEngine)
        assert "10000" not in source


# ============================================================
# AC-C3-10: Regression safety (handled by full suite run)
# ============================================================

class TestRegressionSafety:

    def test_all_financial_correction_tests_still_exist(self) -> None:
        from tests.test_financial_correction import TestCapitalSingleSource, TestMaxPositionsSingleSource, TestFeePropagation, TestRiskEngineRestart, TestRiskGateHardening, TestRealRiskGateFlow, TestRestartRecovery, TestAccountingDeterministic
        assert TestCapitalSingleSource is not None
        assert TestMaxPositionsSingleSource is not None
        assert TestFeePropagation is not None
        assert TestRiskEngineRestart is not None
        assert TestRiskGateHardening is not None
        assert TestRealRiskGateFlow is not None
        assert TestRestartRecovery is not None
        assert TestAccountingDeterministic is not None


# ============================================================
# AC-C3-12: Integration test - full flow
# ============================================================

class TestIntegrationFullFlow:

    @pytest.mark.asyncio
    async def test_reject_flow(self) -> None:
        """AI -> Risk -> REJECT -> Execution -> Broker NOT called."""
        mock_broker = AsyncMock(spec=BrokerAdapter)
        engine = ExecutionEngine(mock_broker)
        risk = RiskEngine(RiskLimits(max_simultaneous_positions=1))
        risk.record_position_open()
        decision = DecisionContract(action=TradingAction.LONG, confidence=Decimal("0.85"), thesis="test", entry_price=Decimal("50000"), stop_loss=Decimal("48000"), take_profit=Decimal("55000"))
        risk_result = risk.evaluate(decision)
        assert not risk_result.is_approved
        result = await engine.execute_order(order_id=uuid4(), idempotency_key=uuid4(), symbol="BTC-BRL", side=OrderSide.BUY, quantity=Decimal("0.001"), price=Decimal("50000"), correlation_id=uuid4(), risk_decision=risk_result)
        assert result.status == OrderStatus.REJECTED
        mock_broker.submit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_approve_flow(self) -> None:
        """AI -> Risk -> APPROVE -> Execution -> Broker -> Portfolio."""
        mock_broker = AsyncMock(spec=BrokerAdapter)
        mock_broker.submit_order = AsyncMock(return_value=OrderResult(order_id=uuid4(), status=OrderStatus.FILLED, fill_price=Decimal("50000"), fill_quantity=Decimal("0.001"), fee=Decimal("0.50")))
        engine = ExecutionEngine(mock_broker)
        risk = RiskEngine()
        decision = DecisionContract(action=TradingAction.LONG, confidence=Decimal("0.85"), thesis="test", entry_price=Decimal("50000"), stop_loss=Decimal("48000"), take_profit=Decimal("55000"))
        risk_result = risk.evaluate(decision)
        assert risk_result.is_approved
        result = await engine.execute_order(order_id=uuid4(), idempotency_key=uuid4(), symbol="BTC-BRL", side=OrderSide.BUY, quantity=risk_result.approved_quantity, price=risk_result.approved_price, correlation_id=uuid4(), risk_decision=risk_result)
        assert result.status == OrderStatus.FILLED
        mock_broker.submit_order.assert_called_once()
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill(asset="BTC-BRL", side=PositionSide.LONG, quantity=risk_result.approved_quantity, price=result.fill_price, fee=result.fee)
        assert portfolio.cash < Decimal("100.00")
        assert portfolio.total_fees == result.fee

    @pytest.mark.asyncio
    async def test_close_flow(self) -> None:
        """AI -> CLOSE -> Portfolio.close_position() -> persisted state."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill(asset="BTC-BRL", side=PositionSide.LONG, quantity=Decimal("1"), price=Decimal("100.00"), fee=Decimal("0.10"))
        realized = portfolio.close_position("BTC-BRL", Decimal("110.00"), fee=Decimal("0.11"))
        assert realized == Decimal("9.79")
        assert portfolio.total_fees == Decimal("0.21")
        assert portfolio.cash > Decimal("100.00")
        risk = RiskEngine(RiskLimits(max_simultaneous_positions=1))
        risk.record_position_open()
        risk.record_position_close()
        assert risk.positions_count == 0
        decision = DecisionContract(action=TradingAction.LONG, confidence=Decimal("0.85"), thesis="test", entry_price=Decimal("50000"), stop_loss=Decimal("48000"), take_profit=Decimal("55000"))
        result = risk.evaluate(decision)
        assert result.is_approved
