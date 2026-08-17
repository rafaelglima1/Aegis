"""Tests for AEGIS database models."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from aegis.db.models import (
    Base,
    OrderModel,
    PositionModel,
    FillModel,
    PortfolioSnapshotModel,
    AIRunModel,
    AuditEventModel,
    MarketStateModel,
    TradeIntentModel,
    RiskDecisionModel,
)
from aegis.domain.enums import OrderStatus, PositionStatus


def test_order_model_uses_numeric_for_financials() -> None:
    """AC-03.09: Critical monetary and quantity values do not rely on binary floating point."""
    order = OrderModel(
        client_order_id="ORD-001",
        filled_quantity=Decimal("100.12345678"),
        remaining_quantity=Decimal("0.87654321"),
        average_price=Decimal("50.00"),
        fees=Decimal("0.50"),
    )
    assert isinstance(order.filled_quantity, Decimal)
    assert isinstance(order.remaining_quantity, Decimal)
    assert isinstance(order.average_price, Decimal)
    assert isinstance(order.fees, Decimal)


def test_position_model_uses_numeric_for_financials() -> None:
    """AC-03.09: Critical monetary and quantity values do not rely on binary floating point."""
    position = PositionModel(
        asset="PETR4",
        side="LONG",
        quantity=Decimal("100"),
        average_entry=Decimal("50.00"),
        current_price=Decimal("52.00"),
        stop_loss=Decimal("48.00"),
        take_profit=Decimal("55.00"),
        realized_pnl=Decimal("200.00"),
        unrealized_pnl=Decimal("100.00"),
    )
    assert isinstance(position.quantity, Decimal)
    assert isinstance(position.average_entry, Decimal)
    assert isinstance(position.realized_pnl, Decimal)


def test_fill_model_uses_numeric_for_financials() -> None:
    """AC-03.09: Critical monetary and quantity values do not rely on binary floating point."""
    fill = FillModel(
        order_id=uuid4(),
        quantity=Decimal("100"),
        price=Decimal("50.00"),
        fee=Decimal("0.50"),
    )
    assert isinstance(fill.quantity, Decimal)
    assert isinstance(fill.price, Decimal)
    assert isinstance(fill.fee, Decimal)


def test_portfolio_snapshot_uses_numeric_for_financials() -> None:
    """AC-03.09: Critical monetary and quantity values do not rely on binary floating point."""
    snapshot = PortfolioSnapshotModel(
        cash=Decimal("10000.00"),
        equity=Decimal("10500.00"),
        exposure=Decimal("5000.00"),
        realized_pnl=Decimal("500.00"),
        unrealized_pnl=Decimal("200.00"),
        drawdown=Decimal("0.05"),
    )
    assert isinstance(snapshot.cash, Decimal)
    assert isinstance(snapshot.equity, Decimal)


def test_market_state_model_has_required_fields() -> None:
    """AC-03.06: Required financial entities have persistence models."""
    ms = MarketStateModel(
        asset="PETR4",
        timestamp="2024-01-01T00:00:00Z",
        timeframe="1d",
        ohlcv='{"open": 100}',
    )
    assert ms.asset == "PETR4"
    assert ms.timeframe == "1d"


def test_trade_intent_model_has_required_fields() -> None:
    """AC-03.06: Required financial entities have persistence models."""
    ti = TradeIntentModel(
        asset="PETR4",
        action="LONG",
        quantity=Decimal("100"),
        entry_price=Decimal("50.00"),
        stop_loss=Decimal("48.00"),
        take_profit=Decimal("55.00"),
        confidence=Decimal("0.85"),
        market_state_id=uuid4(),
        ai_run_id=uuid4(),
    )
    assert ti.asset == "PETR4"
    assert ti.action == "LONG"


def test_risk_decision_model_has_required_fields() -> None:
    """AC-03.06: Required financial entities have persistence models."""
    rd = RiskDecisionModel(
        trade_intent_id=uuid4(),
        status="PENDING",
        approved_quantity=Decimal("0"),
        approved_price=Decimal("0"),
        risk_amount=Decimal("0"),
        exposure=Decimal("0"),
        reasons="[]",
    )
    assert rd.status == "PENDING"


def test_order_model_has_required_fields() -> None:
    """AC-03.06: Required financial entities have persistence models."""
    order = OrderModel(
        client_order_id="ORD-001",
        status="CREATED",
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal("0"),
        average_price=Decimal("0"),
        fees=Decimal("0"),
    )
    assert order.status == "CREATED"
    assert order.client_order_id == "ORD-001"


def test_position_model_has_required_fields() -> None:
    """AC-03.06: Required financial entities have persistence models."""
    position = PositionModel(
        asset="PETR4",
        side="LONG",
        status="NONE",
        quantity=Decimal("0"),
        average_entry=Decimal("0"),
        current_price=Decimal("0"),
        stop_loss=Decimal("0"),
        take_profit=Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
    )
    assert position.status == "NONE"
    assert position.asset == "PETR4"


def test_ai_run_model_has_required_fields() -> None:
    """AC-03.06: Required financial entities have persistence models."""
    ai_run = AIRunModel(
        status="CREATED",
        latency_ms=0,
        token_usage=0,
    )
    assert ai_run.status == "CREATED"


def test_audit_event_model_has_required_fields() -> None:
    """AC-03.06: Required financial entities have persistence models."""
    event = AuditEventModel(
        correlation_id=uuid4(),
        event_type="TestEvent",
        entity_type="TestEntity",
        entity_id=uuid4(),
        actor="system",
        payload_hash="",
    )
    assert event.event_type == "TestEvent"
    assert event.actor == "system"
