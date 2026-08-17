"""Tests for AEGIS domain events."""

from __future__ import annotations

from uuid import uuid4

from aegis.domain.events import (
    DomainEvent,
    MarketDataReceived,
    CandleClosed,
    MarketStateCreated,
    AIRunStarted,
    AIRunCompleted,
    TradeIntentCreated,
    RiskDecisionCreated,
    OrderCreated,
    OrderSubmitted,
    OrderAcknowledged,
    FillReceived,
    PositionUpdated,
    PortfolioSnapshotCreated,
    AuditEventRecorded,
)


def test_domain_event_has_correlation_id() -> None:
    """AC-02.07: Domain events have validated schemas."""
    cid = uuid4()
    event = DomainEvent(correlation_id=cid)
    assert event.correlation_id == cid
    assert event.event_id is not None
    assert event.event_time is not None


def test_market_data_received_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = MarketDataReceived(correlation_id=uuid4())
    assert event.event_type == "MarketDataReceived"


def test_candle_closed_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = CandleClosed(correlation_id=uuid4())
    assert event.event_type == "CandleClosed"


def test_market_state_created_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = MarketStateCreated(correlation_id=uuid4())
    assert event.event_type == "MarketStateCreated"


def test_ai_run_started_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = AIRunStarted(correlation_id=uuid4())
    assert event.event_type == "AIRunStarted"


def test_ai_run_completed_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = AIRunCompleted(correlation_id=uuid4())
    assert event.event_type == "AIRunCompleted"


def test_trade_intent_created_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = TradeIntentCreated(correlation_id=uuid4())
    assert event.event_type == "TradeIntentCreated"


def test_risk_decision_created_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = RiskDecisionCreated(correlation_id=uuid4())
    assert event.event_type == "RiskDecisionCreated"


def test_order_created_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = OrderCreated(correlation_id=uuid4())
    assert event.event_type == "OrderCreated"


def test_order_submitted_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = OrderSubmitted(correlation_id=uuid4())
    assert event.event_type == "OrderSubmitted"


def test_order_acknowledged_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = OrderAcknowledged(correlation_id=uuid4())
    assert event.event_type == "OrderAcknowledged"


def test_fill_received_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = FillReceived(correlation_id=uuid4())
    assert event.event_type == "FillReceived"


def test_position_updated_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = PositionUpdated(correlation_id=uuid4())
    assert event.event_type == "PositionUpdated"


def test_portfolio_snapshot_created_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = PortfolioSnapshotCreated(correlation_id=uuid4())
    assert event.event_type == "PortfolioSnapshotCreated"


def test_audit_event_recorded_event() -> None:
    """AC-02.07: Domain events have validated schemas."""
    event = AuditEventRecorded(correlation_id=uuid4())
    assert event.event_type == "AuditEventRecorded"
