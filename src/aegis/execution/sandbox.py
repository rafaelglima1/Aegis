"""AEGIS Sandbox Broker — simulated execution for paper trading."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from aegis.domain.enums import OrderSide, OrderStatus
from aegis.execution.broker import (
    BrokerAdapter,
    CancelResult,
    OrderResult,
    OrderSubmission,
)


@dataclass
class SandboxOrder:
    """Internal sandbox order tracking."""

    order_id: UUID
    idempotency_key: UUID
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    status: OrderStatus = OrderStatus.CREATED
    fill_price: Decimal | None = None
    fill_quantity: Decimal | None = None
    fee: Decimal = Decimal("0")


class SandboxBroker(BrokerAdapter):
    """AC-08.02: SandboxBroker implements BrokerAdapter."""

    def __init__(self, initial_balance: Decimal = Decimal("10000.00")) -> None:
        self._balance = initial_balance
        self._orders: dict[UUID, SandboxOrder] = {}
        self._idempotency_keys: set[UUID] = set()
        self._slippage_bps = Decimal("0.001")

    @property
    def balance(self) -> Decimal:
        return self._balance

    async def submit_order(self, submission: OrderSubmission) -> OrderResult:
        """AC-08.03: Order submission works in Sandbox."""
        """AC-08.07: Idempotency prevents duplicate orders."""
        if submission.idempotency_key in self._idempotency_keys:
            existing = self._orders.get(submission.order_id)
            if existing:
                return OrderResult(
                    order_id=submission.order_id,
                    status=existing.status,
                    fill_price=existing.fill_price,
                    fill_quantity=existing.fill_quantity,
                    fee=existing.fee,
                )
            return OrderResult(
                order_id=submission.order_id,
                status=OrderStatus.REJECTED,
                error="Duplicate order",
            )

        self._idempotency_keys.add(submission.idempotency_key)

        required_cost = submission.price * submission.quantity
        if self._balance < required_cost:
            order = SandboxOrder(
                order_id=submission.order_id,
                idempotency_key=submission.idempotency_key,
                symbol=submission.symbol,
                side=submission.side,
                quantity=submission.quantity,
                price=submission.price,
                status=OrderStatus.REJECTED,
            )
            self._orders[submission.order_id] = order
            return OrderResult(
                order_id=submission.order_id,
                status=OrderStatus.REJECTED,
                error="Insufficient balance",
            )

        slippage = submission.price * self._slippage_bps
        fill_price = submission.price + slippage

        order = SandboxOrder(
            order_id=submission.order_id,
            idempotency_key=submission.idempotency_key,
            symbol=submission.symbol,
            side=submission.side,
            quantity=submission.quantity,
            price=submission.price,
            status=OrderStatus.FILLED,
            fill_price=fill_price,
            fill_quantity=submission.quantity,
            fee=Decimal("0.50"),
        )
        self._orders[submission.order_id] = order
        self._balance -= required_cost + order.fee

        return OrderResult(
            order_id=submission.order_id,
            status=OrderStatus.FILLED,
            fill_price=fill_price,
            fill_quantity=submission.quantity,
            fee=order.fee,
        )

    async def cancel_order(self, order_id: UUID, idempotency_key: UUID) -> CancelResult:
        """AC-08.04: Order cancellation works in Sandbox."""
        if idempotency_key in self._idempotency_keys:
            return CancelResult(order_id=order_id, success=False, error="Duplicate request")
        self._idempotency_keys.add(idempotency_key)

        order = self._orders.get(order_id)
        if not order:
            return CancelResult(order_id=order_id, success=False, error="Order not found")

        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            return CancelResult(
                order_id=order_id,
                success=False,
                error=f"Cannot cancel order in {order.status.value} status",
            )

        order.status = OrderStatus.CANCELLED
        return CancelResult(order_id=order_id, success=True)

    async def get_order_status(self, order_id: UUID) -> OrderResult:
        """AC-08.05: Order status retrieval works."""
        order = self._orders.get(order_id)
        if not order:
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.REJECTED,
                error="Order not found",
            )
        return OrderResult(
            order_id=order.order_id,
            status=order.status,
            fill_price=order.fill_price,
            fill_quantity=order.fill_quantity,
            fee=order.fee,
        )

    async def get_position(self, symbol: str) -> dict[str, Any]:
        """Get current position for a symbol."""
        filled = [
            o for o in self._orders.values()
            if o.symbol == symbol and o.status == OrderStatus.FILLED
        ]
        total_qty = sum((o.fill_quantity or Decimal("0") for o in filled), Decimal("0"))
        return {"symbol": symbol, "quantity": total_qty, "orders": len(filled)}
