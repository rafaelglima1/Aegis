"""Tests for AEGIS domain contracts."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from aegis.domain.contracts import (
    MarketState,
    TradeIntent,
    RiskDecision,
    OrderRequest,
    Order,
    Fill,
    Position,
    PortfolioSnapshot,
    AIRun,
    AuditEvent,
    utc_now,
)
from aegis.domain.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionStatus,
    TradingAction,
    AIRunStatus,
)


def test_utc_now_returns_utc() -> None:
    """AC-02.10: Internal timestamps use UTC."""
    now = utc_now()
    assert now.tzinfo is not None
    assert now.tzinfo.utcoffset(now).total_seconds() == 0


def test_market_state_has_required_fields() -> None:
    """AC-02.01: Domain Contracts are explicitly defined."""
    ms = MarketState(
        asset="PETR4",
        timestamp=utc_now(),
        timeframe="1d",
        ohlcv={"open": 100, "high": 105, "low": 98, "close": 102, "volume": 1000},
    )
    assert ms.asset == "PETR4"
    assert ms.timeframe == "1d"
    assert ms.market_state_id is not None


def test_trade_intent_has_required_fields() -> None:
    """AC-02.01: Domain Contracts are explicitly defined."""
    ti = TradeIntent(
        asset="PETR4",
        action=TradingAction.LONG,
        quantity=Decimal("100"),
        entry_price=Decimal("50.00"),
        stop_loss=Decimal("48.00"),
        take_profit=Decimal("55.00"),
        confidence=Decimal("0.85"),
        market_state_id=uuid4(),
        ai_run_id=uuid4(),
    )
    assert ti.asset == "PETR4"
    assert ti.action == TradingAction.LONG
    assert ti.quantity == Decimal("100")


def test_risk_decision_has_required_fields() -> None:
    """AC-02.01: Domain Contracts are explicitly defined."""
    rd = RiskDecision(trade_intent_id=uuid4())
    assert rd.risk_decision_id is not None
    assert rd.status == "PENDING"


def test_order_request_has_required_fields() -> None:
    """AC-02.01: Domain Contracts are explicitly defined."""
    orq = OrderRequest(
        trade_intent_id=uuid4(),
        risk_decision_id=uuid4(),
        client_order_id="ORD-001",
        asset="PETR4",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=Decimal("100"),
        price=Decimal("50.00"),
    )
    assert orq.client_order_id == "ORD-001"
    assert orq.side == OrderSide.BUY


def test_order_has_required_fields() -> None:
    """AC-02.01: Domain Contracts are explicitly defined."""
    o = Order(client_order_id="ORD-001")
    assert o.status == OrderStatus.CREATED
    assert o.filled_quantity == Decimal("0")


def test_fill_has_required_fields() -> None:
    """AC-02.01: Domain Contracts are explicitly defined."""
    f = Fill(order_id=uuid4(), quantity=Decimal("100"), price=Decimal("50.00"))
    assert f.quantity == Decimal("100")
    assert f.price == Decimal("50.00")


def test_position_has_required_fields() -> None:
    """AC-02.01: Domain Contracts are explicitly defined."""
    p = Position(asset="PETR4", side=PositionSide.LONG)
    assert p.asset == "PETR4"
    assert p.status == PositionStatus.NONE


def test_portfolio_snapshot_has_required_fields() -> None:
    """AC-02.01: Domain Contracts are explicitly defined."""
    ps = PortfolioSnapshot()
    assert ps.cash == Decimal("0")
    assert ps.equity == Decimal("0")


def test_ai_run_has_required_fields() -> None:
    """AC-02.01: Domain Contracts are explicitly defined."""
    ar = AIRun()
    assert ar.status == AIRunStatus.CREATED


def test_audit_event_has_required_fields() -> None:
    """AC-02.01: Domain Contracts are explicitly defined."""
    ae = AuditEvent(
        correlation_id=uuid4(),
        event_type="TestEvent",
        entity_type="TestEntity",
        entity_id=uuid4(),
    )
    assert ae.event_type == "TestEvent"
    assert ae.correlation_id is not None
