"""AEGIS domain events — validated schemas for event-driven operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from aegis.domain.contracts import utc_now


class DomainEvent(BaseModel):
    """Base domain event with correlation_id and timestamps."""

    event_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID
    event_time: datetime = Field(default_factory=utc_now)
    ingestion_time: datetime = Field(default_factory=utc_now)
    processing_time: datetime | None = None
    execution_time: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class MarketDataReceived(DomainEvent):
    """Market data received event."""

    event_type: str = "MarketDataReceived"


class CandleClosed(DomainEvent):
    """Candle closed event."""

    event_type: str = "CandleClosed"


class MarketStateCreated(DomainEvent):
    """Market state created event."""

    event_type: str = "MarketStateCreated"


class AIRunStarted(DomainEvent):
    """AI run started event."""

    event_type: str = "AIRunStarted"


class AIRunCompleted(DomainEvent):
    """AI run completed event."""

    event_type: str = "AIRunCompleted"


class TradeIntentCreated(DomainEvent):
    """Trade intent created event."""

    event_type: str = "TradeIntentCreated"


class RiskDecisionCreated(DomainEvent):
    """Risk decision created event."""

    event_type: str = "RiskDecisionCreated"


class OrderCreated(DomainEvent):
    """Order created event."""

    event_type: str = "OrderCreated"


class OrderSubmitted(DomainEvent):
    """Order submitted event."""

    event_type: str = "OrderSubmitted"


class OrderAcknowledged(DomainEvent):
    """Order acknowledged event."""

    event_type: str = "OrderAcknowledged"


class FillReceived(DomainEvent):
    """Fill received event."""

    event_type: str = "FillReceived"


class PositionUpdated(DomainEvent):
    """Position updated event."""

    event_type: str = "PositionUpdated"


class PortfolioSnapshotCreated(DomainEvent):
    """Portfolio snapshot created event."""

    event_type: str = "PortfolioSnapshotCreated"


class AuditEventRecorded(DomainEvent):
    """Audit event recorded."""

    event_type: str = "AuditEventRecorded"
