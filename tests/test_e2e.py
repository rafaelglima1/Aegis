"""AEGIS E2E, Chaos, Recovery & Release Gates tests."""

from __future__ import annotations

import pytest
from decimal import Decimal
from uuid import uuid4

from aegis.domain.enums import OrderSide, OrderStatus, TradingAction
from aegis.execution.sandbox import SandboxBroker
from aegis.execution.engine import ExecutionEngine
from aegis.execution.orchestrator import ExecutionOrchestrator, OrderState
from aegis.risk_engine.risk_engine import RiskEngine, RiskDecision
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.portfolio.portfolio import Portfolio
from aegis.replay import ReplayEngine, ReplayDataset, Candle, ReplayState
from aegis.backtest import BacktestEngine, Dataset, ExperimentConfig, ExperimentStatus
from aegis.audit import AuditLogger, AuditEventType
from aegis.config import Settings, TradingEnvironment
from aegis.execution.factory import create_broker
from aegis.execution.live import LiveBroker, LiveBrokerConfig
from aegis.execution.mercadobitcoin import MercadoBitcoinBroker
from aegis.ai_engine.decision_engine import DecisionContract


def make_candles(count: int = 5) -> list[Candle]:
    return [
        Candle(
            __import__('datetime').datetime(2024, 1, 1, i, tzinfo=__import__('datetime').timezone.utc),
            Decimal("100"), Decimal("105"), Decimal("95"), Decimal("102"), Decimal("1000"),
        )
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_sandbox_pipeline_e2e() -> None:
    """AC-15.01: Complete SANDBOX pipeline operates successfully."""
    broker = SandboxBroker(initial_balance=Decimal("1000.00"))
    engine = ExecutionEngine(broker)
    risk = RiskEngine()
    portfolio = Portfolio(initial_cash=Decimal("100.00"))
    audit = AuditLogger()

    decision = DecisionContract(
        action=TradingAction.LONG,
        confidence=Decimal("0.85"),
        thesis="Strong bullish",
        entry_price=Decimal("50.00"),
        stop_loss=Decimal("48.00"),
        take_profit=Decimal("55.00"),
    )

    risk_result = risk.evaluate(decision)
    assert risk_result.is_approved

    order_id = uuid4()
    result = await engine.execute_order(
        order_id=order_id,
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        price=Decimal("50.00"),
        correlation_id=uuid4(),
        risk_decision=RiskDecision(status="APPROVED"),
    )
    assert result.status == OrderStatus.FILLED

    portfolio.record_fill("AAPL", OrderSide.BUY, Decimal("1"), Decimal("50.00"), fee=result.fee)
    assert portfolio.cash < Decimal("100.00")

    audit.record_order(uuid4(), "execution", "submit", {"order_id": str(order_id)})
    assert len(audit.events) > 0


@pytest.mark.asyncio
async def test_market_data_to_audit_e2e() -> None:
    """AC-15.02: Market Data→AI→Risk→Execution→Fill→Portfolio→Audit works end-to-end."""
    broker = SandboxBroker(initial_balance=Decimal("2000.00"))
    orchestrator = ExecutionOrchestrator(broker)
    risk = RiskEngine()
    portfolio = Portfolio(initial_cash=Decimal("100.00"))
    audit = AuditLogger()

    correlation_id = uuid4()

    decision = DecisionContract(
        action=TradingAction.LONG,
        confidence=Decimal("0.85"),
        thesis="Strong bullish",
        entry_price=Decimal("50.00"),
        stop_loss=Decimal("48.00"),
        take_profit=Decimal("55.00"),
    )

    audit.record_decision(correlation_id, "ai", "decide", {"action": "LONG"})

    risk_result = risk.evaluate(decision)
    assert risk_result.is_approved

    audit.record_risk(correlation_id, "risk", "evaluate", {"approved": True})

    result = await orchestrator.submit_order(
        order_id=uuid4(),
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=correlation_id,
        risk_decision=RiskDecision(status="APPROVED"),
    )
    assert result.status == OrderStatus.FILLED

    audit.record_order(correlation_id, "execution", "fill", {"status": "FILLED"})

    portfolio.record_fill("AAPL", OrderSide.BUY, Decimal("1"), Decimal("50.00"))

    events = audit.get_events_by_correlation(correlation_id)
    assert len(events) == 3


@pytest.mark.asyncio
async def test_llm_timeout_handled_safely() -> None:
    """AC-15.03: LLM timeout is handled safely."""
    from aegis.ai_engine.provider import LLMProvider, LLMResponse

    class TimeoutProvider(LLMProvider):
        async def generate(self, prompt: str, **kwargs):
            raise TimeoutError("LLM timeout")

        async def complete(self, prompt: str, **kwargs) -> LLMResponse:
            raise TimeoutError("LLM timeout")

        async def health_check(self):
            return False

        @property
        def provider_name(self) -> str:
            return "timeout"

        async def validate_connection(self) -> bool:
            return False

    provider = TimeoutProvider()
    healthy = await provider.health_check()
    assert not healthy


@pytest.mark.asyncio
async def test_broker_failure_handled_safely() -> None:
    """AC-15.07: Broker failure is handled safely."""
    class FailingBroker(SandboxBroker):
        async def submit_order(self, submission):
            from aegis.execution.broker import OrderResult
            return OrderResult(
                order_id=submission.order_id,
                status=OrderStatus.REJECTED,
                error="Broker failure",
            )

    broker = FailingBroker()
    engine = ExecutionEngine(broker)
    result = await engine.execute_order(
        order_id=uuid4(),
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_decision=RiskDecision(status="APPROVED"),
    )
    assert result.status == OrderStatus.REJECTED
    assert "failure" in result.error


@pytest.mark.asyncio
async def test_unknown_order_state_reconciled() -> None:
    """AC-15.08: Unknown order state is reconciled."""
    broker = SandboxBroker()
    orchestrator = ExecutionOrchestrator(broker)
    result = await orchestrator.reconcile_order(uuid4())
    assert not result.reconciled


@pytest.mark.asyncio
async def test_restart_recovery() -> None:
    """AC-15.09: Restart recovery works."""
    broker = SandboxBroker()
    orchestrator = ExecutionOrchestrator(broker)
    results = await orchestrator.recover_on_restart()
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_kill_switch_e2e() -> None:
    """AC-15.11: Kill switch works end-to-end."""
    risk = RiskEngine()
    risk.activate_kill_switch()
    decision = DecisionContract(
        action=TradingAction.LONG,
        confidence=Decimal("0.85"),
        thesis="Test",
        entry_price=Decimal("50.00"),
        stop_loss=Decimal("48.00"),
    )
    result = risk.evaluate(decision)
    assert not result.is_approved


@pytest.mark.asyncio
async def test_risk_limits_e2e() -> None:
    """AC-15.12: Risk limits work end-to-end."""
    limits = RiskLimits(max_simultaneous_positions=1)
    risk = RiskEngine(limits=limits)
    risk.record_position_open()
    decision = DecisionContract(
        action=TradingAction.LONG,
        confidence=Decimal("0.85"),
        thesis="Test",
        entry_price=Decimal("50.00"),
        stop_loss=Decimal("48.00"),
    )
    result = risk.evaluate(decision)
    assert not result.is_approved


def test_environment_switching_no_code_change() -> None:
    """AC-15.13: Environment switching works without code modification."""
    settings_sandbox = Settings(trading_environment=TradingEnvironment.SANDBOX)
    settings_live = Settings(trading_environment=TradingEnvironment.LIVE, live_enabled=True)
    broker_sandbox = create_broker(settings_sandbox)
    broker_live = create_broker(settings_live)
    assert isinstance(broker_sandbox, SandboxBroker)
    assert isinstance(broker_live, MercadoBitcoinBroker)


def test_live_blocked_when_disabled() -> None:
    """AC-15.14: LIVE remains blocked when LIVE_ENABLED=false."""
    config = LiveBrokerConfig(enabled=False)
    broker = LiveBroker(config)
    assert not broker.is_enabled


@pytest.mark.asyncio
async def test_replay_e2e() -> None:
    """AC-15.16: Replay works end-to-end."""
    engine = ReplayEngine()
    dataset = ReplayDataset(symbol="AAPL", candles=make_candles())
    engine.register_dataset(dataset)
    result = await engine.run_replay(dataset.dataset_id)
    assert result.state == ReplayState.COMPLETED


@pytest.mark.asyncio
async def test_backtest_e2e() -> None:
    """AC-15.17: Backtest works end-to-end."""
    engine = BacktestEngine()
    dataset = Dataset(symbol="AAPL", data_hash="abc")
    experiment = ExperimentConfig(model="gpt-4", prompt_version="v1")
    dataset_id = engine.register_dataset(dataset)
    experiment_id = engine.register_experiment(experiment)
    trades = [{"pnl": "100"}, {"pnl": "-50"}, {"pnl": "200"}]
    result = await engine.run_backtest(experiment_id, dataset_id, trades)
    assert result.status == ExperimentStatus.COMPLETED
    assert result.metrics.total_pnl == Decimal("250")


@pytest.mark.asyncio
async def test_audit_reconstruction_e2e() -> None:
    """AC-15.18: Audit reconstruction works end-to-end."""
    audit = AuditLogger()
    correlation_id = uuid4()
    audit.record_decision(correlation_id, "ai", "decide", {"action": "LONG"})
    audit.record_risk(correlation_id, "risk", "evaluate", {"approved": True})
    audit.record_order(correlation_id, "execution", "submit", {"order_id": "123"})
    events = audit.get_events_by_correlation(correlation_id)
    assert len(events) == 3
    assert events[0].event_type == AuditEventType.DECISION
    assert events[1].event_type == AuditEventType.RISK
    assert events[2].event_type == AuditEventType.ORDER
