"""AEGIS Execution Engine — orchestration, reconciliation and recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from aegis.domain.enums import OrderSide, OrderStatus
from aegis.domain.contracts import utc_now
from aegis.execution.broker import BrokerAdapter, OrderResult
from aegis.risk_engine.risk_engine import RiskDecision


class OrderState(Enum):
    """Internal order tracking state."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass
class TrackedOrder:
    """Order with internal tracking state."""

    order_id: UUID
    idempotency_key: UUID
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    correlation_id: UUID
    state: OrderState = OrderState.PENDING
    fill_quantity: Decimal = Decimal("0")
    fill_price: Decimal | None = None
    fee: Decimal = Decimal("0")
    submitted_at: Any = None
    filled_at: Any = None
    error: str | None = None
    version: int = 1


@dataclass
class ReconciliationResult:
    """Result of reconciliation check."""

    order_id: UUID
    reconciled: bool
    final_state: OrderState
    details: str = ""


class ExecutionOrchestrator:
    """AC-10.01: Only Approved Order Intent can be executed.
    AC-10.02: Execution Engine uses BrokerAdapter."""

    def __init__(self, broker: BrokerAdapter) -> None:
        self._broker = broker
        self._orders: dict[UUID, TrackedOrder] = {}
        self._idempotency_keys: set[UUID] = set()
        self._audit_log: list[dict[str, Any]] = []

    @property
    def orders(self) -> dict[UUID, TrackedOrder]:
        return self._orders.copy()

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        return self._audit_log.copy()

    def _audit(self, event: str, data: dict[str, Any]) -> None:
        self._audit_log.append({"event": event, "timestamp": str(utc_now()), **data})

    async def submit_order(
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
        """AC-10.01: Only Approved Order Intent can be executed.
        AC-10.10: Idempotency prevents duplicate order submission.
        AC-C3-03: RiskDecision is mandatory — no boolean fallback."""

        if risk_decision is None or not risk_decision.is_approved:
            self._audit("order_rejected", {"reason": "RISK_NOT_APPROVED", "order_id": str(order_id)})
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.REJECTED,
                error="Risk approval required",
            )

        if idempotency_key in self._idempotency_keys:
            existing = self._orders.get(order_id)
            if existing:
                self._audit("order_duplicate", {"order_id": str(order_id)})
                return OrderResult(
                    order_id=order_id,
                    status=OrderStatus.REJECTED,
                    error="Duplicate order",
                )

        self._idempotency_keys.add(idempotency_key)

        tracked = TrackedOrder(
            order_id=order_id,
            idempotency_key=idempotency_key,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            correlation_id=correlation_id,
            state=OrderState.SUBMITTED,
            submitted_at=utc_now(),
        )
        self._orders[order_id] = tracked

        self._audit("order_submitted", {"order_id": str(order_id), "symbol": symbol})

        from aegis.execution.broker import OrderSubmission

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

        if result.status == OrderStatus.SUBMITTED:
            tracked.state = OrderState.SUBMITTED
        elif result.status == OrderStatus.ACKNOWLEDGED:
            tracked.state = OrderState.ACKNOWLEDGED
        elif result.status == OrderStatus.PARTIALLY_FILLED:
            tracked.state = OrderState.PARTIALLY_FILLED
            tracked.fill_quantity = result.fill_quantity or Decimal("0")
            tracked.fill_price = result.fill_price
        elif result.status == OrderStatus.FILLED:
            tracked.state = OrderState.FILLED
            tracked.fill_quantity = result.fill_quantity or quantity
            tracked.fill_price = result.fill_price
            tracked.fee = result.fee
            tracked.filled_at = utc_now()
        elif result.status == OrderStatus.CANCELLED:
            tracked.state = OrderState.CANCELLED
        elif result.status == OrderStatus.REJECTED:
            tracked.state = OrderState.REJECTED
            tracked.error = result.error

        self._audit("order_result", {
            "order_id": str(order_id),
            "status": result.status.value,
        })

        return result

    async def cancel_order(self, order_id: UUID, idempotency_key: UUID) -> Any:
        """AC-10.06: Cancellation is processed."""
        tracked = self._orders.get(order_id)
        if not tracked:
            return None

        result = await self._broker.cancel_order(order_id, idempotency_key)

        if result.success:
            tracked.state = OrderState.CANCELLED
            self._audit("order_cancelled", {"order_id": str(order_id)})

        return result

    async def reconcile_order(self, order_id: UUID) -> ReconciliationResult:
        """AC-10.07: Unknown order state triggers reconciliation.
        AC-10.09: System never assumes order success without confirmation."""
        tracked = self._orders.get(order_id)
        if not tracked:
            return ReconciliationResult(
                order_id=order_id,
                reconciled=False,
                final_state=OrderState.UNKNOWN,
                details="Order not found",
            )

        if tracked.state in (OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED):
            return ReconciliationResult(
                order_id=order_id,
                reconciled=True,
                final_state=tracked.state,
                details="Order already in terminal state",
            )

        result = await self._broker.get_order_status(order_id)

        if result.status == OrderStatus.FILLED:
            tracked.state = OrderState.FILLED
            tracked.fill_quantity = result.fill_quantity or tracked.quantity
            tracked.fill_price = result.fill_price
            tracked.filled_at = utc_now()
        elif result.status == OrderStatus.CANCELLED:
            tracked.state = OrderState.CANCELLED
        elif result.status == OrderStatus.REJECTED:
            tracked.state = OrderState.REJECTED
        elif result.status == OrderStatus.SUBMITTED:
            tracked.state = OrderState.SUBMITTED
        elif result.status == OrderStatus.ACKNOWLEDGED:
            tracked.state = OrderState.ACKNOWLEDGED
        else:
            tracked.state = OrderState.UNKNOWN

        self._audit("order_reconciled", {
            "order_id": str(order_id),
            "new_state": tracked.state.value,
        })

        return ReconciliationResult(
            order_id=order_id,
            reconciled=True,
            final_state=tracked.state,
        )

    async def recover_on_restart(self) -> list[ReconciliationResult]:
        """AC-10.08: Application restart triggers required reconciliation."""
        results = []
        for order_id, tracked in self._orders.items():
            if tracked.state not in (OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED):
                result = await self.reconcile_order(order_id)
                results.append(result)
        return results

    def get_order_state(self, order_id: UUID) -> OrderState:
        tracked = self._orders.get(order_id)
        if not tracked:
            return OrderState.UNKNOWN
        return tracked.state
