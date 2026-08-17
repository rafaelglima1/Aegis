"""AEGIS domain contracts — Pydantic models for all domain entities."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from aegis.domain.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionStatus,
    TradingAction,
    AIRunStatus,
)


def utc_now() -> datetime:
    """AC-02.10: Internal timestamps use UTC."""
    return datetime.now(timezone.utc)


def new_uuid() -> UUID:
    """Generate a new UUID."""
    return uuid4()


class MarketState(BaseModel):
    """Market state domain contract."""

    market_state_id: UUID = Field(default_factory=new_uuid)
    asset: str
    timestamp: datetime
    timeframe: str
    ohlcv: dict[str, Any]
    indicators: dict[str, Any] = Field(default_factory=dict)
    market_context: dict[str, Any] = Field(default_factory=dict)
    data_quality: str = "GOOD"
    source: str = "UNKNOWN"
    hash: str = ""


class TradeIntent(BaseModel):
    """Trade intent domain contract."""

    trade_intent_id: UUID = Field(default_factory=new_uuid)
    asset: str
    action: TradingAction
    quantity: Decimal
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    confidence: Decimal
    thesis: str = ""
    invalidation: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    market_state_id: UUID
    ai_run_id: UUID


class RiskDecision(BaseModel):
    """Risk decision domain contract."""

    risk_decision_id: UUID = Field(default_factory=new_uuid)
    trade_intent_id: UUID
    status: str = "PENDING"
    approved_quantity: Decimal = Decimal("0")
    approved_price: Decimal = Decimal("0")
    risk_amount: Decimal = Decimal("0")
    exposure: Decimal = Decimal("0")
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class OrderRequest(BaseModel):
    """Order request domain contract."""

    execution_id: UUID = Field(default_factory=new_uuid)
    trade_intent_id: UUID
    risk_decision_id: UUID
    client_order_id: str
    asset: str
    side: OrderSide
    type: OrderType
    quantity: Decimal
    price: Decimal
    stop_loss: Decimal = Decimal("0")
    take_profit: Decimal = Decimal("0")
    created_at: datetime = Field(default_factory=utc_now)
    idempotency_key: str = ""


class Order(BaseModel):
    """Order domain contract."""

    order_id: UUID = Field(default_factory=new_uuid)
    client_order_id: str
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal = Decimal("0")
    average_price: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Fill(BaseModel):
    """Fill domain contract."""

    fill_id: UUID = Field(default_factory=new_uuid)
    order_id: UUID
    quantity: Decimal
    price: Decimal
    fee: Decimal = Decimal("0")
    timestamp: datetime = Field(default_factory=utc_now)


class Position(BaseModel):
    """Position domain contract."""

    position_id: UUID = Field(default_factory=new_uuid)
    asset: str
    side: PositionSide
    status: PositionStatus = PositionStatus.NONE
    quantity: Decimal = Decimal("0")
    average_entry: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")
    stop_loss: Decimal = Decimal("0")
    take_profit: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    opened_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class PortfolioSnapshot(BaseModel):
    """Portfolio snapshot domain contract."""

    snapshot_id: UUID = Field(default_factory=new_uuid)
    timestamp: datetime = Field(default_factory=utc_now)
    cash: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    exposure: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    drawdown: Decimal = Decimal("0")


class AIRun(BaseModel):
    """AI run domain contract."""

    ai_run_id: UUID = Field(default_factory=new_uuid)
    agent: str = ""
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    input_hash: str = ""
    output_hash: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: AIRunStatus = AIRunStatus.CREATED
    latency_ms: int = 0
    token_usage: int = 0


class AuditEvent(BaseModel):
    """Audit event domain contract."""

    audit_event_id: UUID = Field(default_factory=new_uuid)
    correlation_id: UUID
    event_type: str
    entity_type: str
    entity_id: UUID
    timestamp: datetime = Field(default_factory=utc_now)
    actor: str = "system"
    payload_hash: str = ""
