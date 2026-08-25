"""AEGIS Broker Adapter contract — abstract interface for all brokers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from aegis.domain.enums import OrderSide, OrderStatus


@dataclass
class OrderSubmission:
    """Order submission request."""

    order_id: UUID
    idempotency_key: UUID
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    correlation_id: UUID


@dataclass
class OrderResult:
    """Order submission result."""

    order_id: UUID
    status: OrderStatus
    fill_price: Decimal | None = None
    fill_quantity: Decimal | None = None
    fee: Decimal = Decimal("0")
    error: str | None = None


@dataclass
class CancelResult:
    """Order cancellation result."""

    order_id: UUID
    success: bool
    error: str | None = None


class BrokerAdapter(ABC):
    """AC-08.01: BrokerAdapter contract is explicitly defined."""

    @abstractmethod
    async def submit_order(self, submission: OrderSubmission) -> OrderResult:
        """Submit an order to the broker."""

    @abstractmethod
    async def cancel_order(self, order_id: UUID, idempotency_key: UUID) -> CancelResult:
        """Cancel an order."""

    @abstractmethod
    async def get_order_status(self, order_id: UUID) -> OrderResult:
        """Get current order status."""

    @abstractmethod
    async def get_position(self, symbol: str) -> dict[str, Any]:
        """Get current position for a symbol."""

    async def get_exchange_snapshot(self) -> Any:
        """Get exchange state snapshot for reconciliation.

        Returns ExchangeSnapshot or None if not supported.
        Default implementation returns None (not supported).
        Subclasses override for real implementations.
        """
        return None
