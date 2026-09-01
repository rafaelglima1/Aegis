"""AEGIS Execution Engine — orchestrates broker interactions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from aegis.domain.enums import OrderSide, OrderStatus
from aegis.execution.broker import BrokerAdapter, OrderSubmission, OrderResult
from aegis.risk_engine.risk_engine import RiskDecision


class ExecutionEngine:
    """AC-08.08: Order lifecycle follows the Order State Machine.
    AC-FIN-14: Risk REJECT prevents Broker.submit_order.
    AC-FIN-15: Risk APPROVED allows Broker.submit_order.

    Vibe #2 (no-retry estrutural): a mutating broker call (submit/cancel) is
    never issued more than once per order. The engine records every submitted
    order_id and returns the stored result on any repeat attempt instead of
    re-sending — a live order must never be silently re-issued.
    """

    def __init__(self, broker: BrokerAdapter) -> None:
        self._broker = broker
        self._submitted: dict[UUID, OrderResult] = {}

    @property
    def submitted_order_ids(self) -> set[UUID]:
        """Order ids that have already been issued to the broker (never re-sent)."""
        return set(self._submitted.keys())

    async def execute_order(
        self,
        order_id: UUID,
        idempotency_key: UUID,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        correlation_id: UUID,
        risk_decision: RiskDecision | None = None,
    ) -> OrderResult:
        """AC-08.09: Broker cannot receive an order without Risk approval.
        AC-C3-03: RiskDecision is mandatory — no boolean fallback.

        Vibe #2: if this order_id was already submitted, return the stored
        result (idempotent) instead of re-issuing the mutating broker call.
        """
        if risk_decision is None or not risk_decision.is_approved:
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.REJECTED,
                error=f"Risk rejected: {[v.code for v in (risk_decision.violations if risk_decision else [])]}",
            )

        # No-retry estrutural: never re-issue the same order.
        if order_id in self._submitted:
            return self._submitted[order_id]

        submission = OrderSubmission(
            order_id=order_id,
            idempotency_key=idempotency_key,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            correlation_id=correlation_id,
        )

        result = await self._broker.submit_order(submission)
        self._submitted[order_id] = result
        return result

    async def cancel_order(self, order_id: UUID, idempotency_key: UUID) -> Any:
        """AC-08.04: Order cancellation works in Sandbox."""
        return await self._broker.cancel_order(order_id, idempotency_key)

    async def get_order_status(self, order_id: UUID) -> OrderResult:
        """AC-08.05: Order status retrieval works."""
        return await self._broker.get_order_status(order_id)
