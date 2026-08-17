"""AEGIS Execution Engine — orchestrates broker interactions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from aegis.domain.enums import OrderSide, OrderStatus
from aegis.execution.broker import BrokerAdapter, OrderSubmission, OrderResult


class ExecutionEngine:
    """AC-08.08: Order lifecycle follows the Order State Machine."""

    def __init__(self, broker: BrokerAdapter) -> None:
        self._broker = broker

    async def execute_order(
        self,
        order_id: UUID,
        idempotency_key: UUID,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        correlation_id: UUID,
        risk_approved: bool = False,
    ) -> OrderResult:
        """AC-08.09: Broker cannot receive an order without Risk approval."""
        if not risk_approved:
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.REJECTED,
                error="Risk approval required",
            )

        submission = OrderSubmission(
            order_id=order_id,
            idempotency_key=idempotency_key,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            correlation_id=correlation_id,
        )

        return await self._broker.submit_order(submission)

    async def cancel_order(self, order_id: UUID, idempotency_key: UUID) -> Any:
        """AC-08.04: Order cancellation works in Sandbox."""
        return await self._broker.cancel_order(order_id, idempotency_key)

    async def get_order_status(self, order_id: UUID) -> OrderResult:
        """AC-08.05: Order status retrieval works."""
        return await self._broker.get_order_status(order_id)
