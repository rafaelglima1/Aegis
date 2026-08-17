"""Tests for AEGIS Broker Contract & Sandbox Execution (Phase 08)."""

from __future__ import annotations

import pytest
from decimal import Decimal
from uuid import uuid4

from aegis.domain.enums import OrderSide, OrderStatus
from aegis.execution.broker import BrokerAdapter, OrderSubmission, OrderResult
from aegis.execution.sandbox import SandboxBroker
from aegis.execution.engine import ExecutionEngine
from aegis.risk_engine.risk_engine import RiskDecision


def make_submission(**overrides) -> OrderSubmission:
    defaults = dict(
        order_id=uuid4(),
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("0.5"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
    )
    defaults.update(overrides)
    return OrderSubmission(**defaults)


def test_broker_adapter_is_abstract() -> None:
    """AC-08.01: BrokerAdapter contract is explicitly defined."""
    with pytest.raises(TypeError):
        BrokerAdapter()


def test_sandbox_implements_broker_adapter() -> None:
    """AC-08.02: SandboxBroker implements BrokerAdapter."""
    broker = SandboxBroker()
    assert isinstance(broker, BrokerAdapter)


@pytest.mark.asyncio
async def test_order_submission_sandbox() -> None:
    """AC-08.03: Order submission works in Sandbox."""
    broker = SandboxBroker()
    submission = make_submission()
    result = await broker.submit_order(submission)
    assert result.status == OrderStatus.FILLED
    assert result.fill_price is not None
    assert result.fill_quantity is not None


@pytest.mark.asyncio
async def test_order_cancellation_sandbox() -> None:
    """AC-08.04: Order cancellation works in Sandbox."""
    broker = SandboxBroker()
    order_id = uuid4()
    submission = make_submission(order_id=order_id)
    result = await broker.submit_order(submission)
    assert result.status == OrderStatus.FILLED
    cancel_result = await broker.cancel_order(order_id, uuid4())
    assert not cancel_result.success
    assert "Cannot cancel" in cancel_result.error


@pytest.mark.asyncio
async def test_order_status_retrieval() -> None:
    """AC-08.05: Order status retrieval works."""
    broker = SandboxBroker()
    submission = make_submission()
    await broker.submit_order(submission)
    status = await broker.get_order_status(submission.order_id)
    assert status.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_sandbox_fill_simulation() -> None:
    """AC-08.06: Sandbox fill simulation works."""
    broker = SandboxBroker()
    submission = make_submission()
    result = await broker.submit_order(submission)
    assert result.fill_price is not None
    assert result.fill_price > submission.price


@pytest.mark.asyncio
async def test_idempotency_prevents_duplicate() -> None:
    """AC-08.07: Idempotency prevents duplicate orders."""
    broker = SandboxBroker()
    key = uuid4()
    submission = make_submission(idempotency_key=key)
    result1 = await broker.submit_order(submission)
    result2 = await broker.submit_order(submission)
    assert result1.status == OrderStatus.FILLED
    assert result2.status == OrderStatus.FILLED
    assert result1.fill_price == result2.fill_price


@pytest.mark.asyncio
async def test_order_lifecycle_follows_state_machine() -> None:
    """AC-08.08: Order lifecycle follows the Order State Machine."""
    broker = SandboxBroker()
    order_id = uuid4()
    submission = make_submission(order_id=order_id)
    result = await broker.submit_order(submission)
    assert result.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_broker_requires_risk_approval() -> None:
    """AC-08.09: Broker cannot receive an order without Risk approval."""
    broker = SandboxBroker()
    engine = ExecutionEngine(broker)
    result = await engine.execute_order(
        order_id=uuid4(),
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("0.5"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
    )
    assert result.status == OrderStatus.REJECTED
    assert "Risk rejected" in result.error


@pytest.mark.asyncio
async def test_broker_with_risk_approval() -> None:
    """AC-08.09: Broker cannot receive an order without Risk approval."""
    broker = SandboxBroker()
    engine = ExecutionEngine(broker)
    approved = RiskDecision(status="APPROVED")
    result = await engine.execute_order(
        order_id=uuid4(),
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("0.5"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_decision=approved,
    )
    assert result.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_sandbox_no_live_credentials() -> None:
    """AC-08.10: Sandbox never uses LIVE credentials."""
    broker = SandboxBroker()
    assert not hasattr(broker, "_live_credentials")
    assert not hasattr(broker, "api_key")
    assert not hasattr(broker, "api_secret")


@pytest.mark.asyncio
async def test_sandbox_balance_deducted() -> None:
    """AC-08.11: Sandbox execution tests pass."""
    broker = SandboxBroker(initial_balance=Decimal("2000.00"))
    submission = make_submission(quantity=Decimal("10"), price=Decimal("100.00"))
    result = await broker.submit_order(submission)
    assert result.status == OrderStatus.FILLED
    assert broker.balance < Decimal("1000.00")
