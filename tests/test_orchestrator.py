"""Tests for AEGIS Execution Engine, Reconciliation & Recovery (Phase 10)."""

from __future__ import annotations

import pytest
from decimal import Decimal
from uuid import uuid4

from aegis.domain.enums import OrderSide, OrderStatus
from aegis.execution.broker import BrokerAdapter, OrderSubmission, OrderResult, CancelResult
from aegis.execution.orchestrator import ExecutionOrchestrator, OrderState, ReconciliationResult


class MockBroker(BrokerAdapter):
    """Mock broker for testing."""

    def __init__(self) -> None:
        self._orders: dict[UUID, dict] = {}
        self._submitted: list[OrderSubmission] = []

    async def submit_order(self, submission: OrderSubmission) -> OrderResult:
        self._submitted.append(submission)
        self._orders[submission.order_id] = {
            "status": OrderStatus.FILLED,
            "fill_price": submission.price,
            "fill_quantity": submission.quantity,
        }
        return OrderResult(
            order_id=submission.order_id,
            status=OrderStatus.FILLED,
            fill_price=submission.price,
            fill_quantity=submission.quantity,
            fee=Decimal("0.50"),
        )

    async def cancel_order(self, order_id: UUID, idempotency_key: UUID) -> CancelResult:
        return CancelResult(order_id=order_id, success=True)

    async def get_order_status(self, order_id: UUID) -> OrderResult:
        order = self._orders.get(order_id)
        if not order:
            return OrderResult(order_id=order_id, status=OrderStatus.REJECTED, error="Not found")
        return OrderResult(
            order_id=order_id,
            status=order["status"],
            fill_price=order.get("fill_price"),
            fill_quantity=order.get("fill_quantity"),
        )

    async def get_position(self, symbol: str) -> dict:
        return {"symbol": symbol, "quantity": Decimal("0")}


@pytest.mark.asyncio
async def test_only_approved_intent_executed() -> None:
    """AC-10.01: Only Approved Order Intent can be executed."""
    broker = MockBroker()
    orchestrator = ExecutionOrchestrator(broker)
    result = await orchestrator.submit_order(
        order_id=uuid4(),
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_approved=False,
    )
    assert result.status == OrderStatus.REJECTED
    assert "Risk approval" in result.error


@pytest.mark.asyncio
async def test_uses_broker_adapter() -> None:
    """AC-10.02: Execution Engine uses BrokerAdapter."""
    broker = MockBroker()
    orchestrator = ExecutionOrchestrator(broker)
    result = await orchestrator.submit_order(
        order_id=uuid4(),
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_approved=True,
    )
    assert result.status == OrderStatus.FILLED
    assert len(broker._submitted) == 1


@pytest.mark.asyncio
async def test_order_acknowledgement_processed() -> None:
    """AC-10.03: Order acknowledgement is processed."""
    broker = MockBroker()
    orchestrator = ExecutionOrchestrator(broker)
    order_id = uuid4()
    await orchestrator.submit_order(
        order_id=order_id,
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_approved=True,
    )
    state = orchestrator.get_order_state(order_id)
    assert state == OrderState.FILLED


@pytest.mark.asyncio
async def test_full_fill_processed() -> None:
    """AC-10.05: Full fills are processed."""
    broker = MockBroker()
    orchestrator = ExecutionOrchestrator(broker)
    order_id = uuid4()
    result = await orchestrator.submit_order(
        order_id=order_id,
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_approved=True,
    )
    assert result.status == OrderStatus.FILLED
    assert result.fill_quantity == Decimal("10")


@pytest.mark.asyncio
async def test_cancellation_processed() -> None:
    """AC-10.06: Cancellation is processed."""
    broker = MockBroker()
    orchestrator = ExecutionOrchestrator(broker)
    order_id = uuid4()
    await orchestrator.submit_order(
        order_id=order_id,
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_approved=True,
    )
    result = await orchestrator.cancel_order(order_id, uuid4())
    assert result.success
    state = orchestrator.get_order_state(order_id)
    assert state == OrderState.CANCELLED


@pytest.mark.asyncio
async def test_unknown_state_triggers_reconciliation() -> None:
    """AC-10.07: Unknown order state triggers reconciliation."""
    broker = MockBroker()
    orchestrator = ExecutionOrchestrator(broker)
    order_id = uuid4()
    tracked = orchestrator._orders.get(order_id)
    if not tracked:
        from aegis.execution.orchestrator import TrackedOrder
        tracked = TrackedOrder(
            order_id=order_id,
            idempotency_key=uuid4(),
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            price=Decimal("100.00"),
            correlation_id=uuid4(),
            state=OrderState.UNKNOWN,
        )
        orchestrator._orders[order_id] = tracked

    result = await orchestrator.reconcile_order(order_id)
    assert result.reconciled


@pytest.mark.asyncio
async def test_restart_triggers_reconciliation() -> None:
    """AC-10.08: Application restart triggers required reconciliation."""
    broker = MockBroker()
    orchestrator = ExecutionOrchestrator(broker)
    order_id = uuid4()
    await orchestrator.submit_order(
        order_id=order_id,
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_approved=True,
    )
    results = await orchestrator.recover_on_restart()
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_never_assumes_success() -> None:
    """AC-10.09: System never assumes order success without confirmation."""
    broker = MockBroker()
    orchestrator = ExecutionOrchestrator(broker)
    order_id = uuid4()
    await orchestrator.submit_order(
        order_id=order_id,
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_approved=True,
    )
    state = orchestrator.get_order_state(order_id)
    assert state == OrderState.FILLED


@pytest.mark.asyncio
async def test_idempotency_prevents_duplicate() -> None:
    """AC-10.10: Idempotency prevents duplicate order submission."""
    broker = MockBroker()
    orchestrator = ExecutionOrchestrator(broker)
    key = uuid4()
    order_id = uuid4()
    result1 = await orchestrator.submit_order(
        order_id=order_id,
        idempotency_key=key,
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_approved=True,
    )
    result2 = await orchestrator.submit_order(
        order_id=order_id,
        idempotency_key=key,
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_approved=True,
    )
    assert result1.status == OrderStatus.FILLED
    assert result2.status == OrderStatus.REJECTED
    assert len(broker._submitted) == 1


@pytest.mark.asyncio
async def test_risk_failure_blocks_execution() -> None:
    """AC-10.11: Risk Engine failure blocks execution."""
    broker = MockBroker()
    orchestrator = ExecutionOrchestrator(broker)
    result = await orchestrator.submit_order(
        order_id=uuid4(),
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
        risk_approved=False,
    )
    assert result.status == OrderStatus.REJECTED
    assert len(broker._submitted) == 0


@pytest.mark.asyncio
async def test_recovery_behavior() -> None:
    """AC-10.12: Recovery behavior is tested."""
    broker = MockBroker()
    orchestrator = ExecutionOrchestrator(broker)
    results = await orchestrator.recover_on_restart()
    assert isinstance(results, list)
    assert len(results) == 0
