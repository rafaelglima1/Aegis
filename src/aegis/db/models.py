"""AEGIS SQLAlchemy models — persistence layer for domain entities."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    Integer,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> Any:
    return uuid4()


class MarketStateModel(Base):
    """Market state persistence model."""

    __tablename__ = "market_states"

    market_state_id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    asset = Column(String(20), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    timeframe = Column(String(10), nullable=False)
    ohlcv = Column(Text, nullable=False)
    indicators = Column(Text, default="{}")
    market_context = Column(Text, default="{}")
    data_quality = Column(String(20), default="GOOD")
    source = Column(String(50), default="UNKNOWN")
    hash = Column(String(64), default="")
    created_at = Column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        Index("ix_market_states_asset_timestamp", "asset", "timestamp"),
    )


class AIRunModel(Base):
    """AI run persistence model."""

    __tablename__ = "ai_runs"

    ai_run_id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    agent = Column(String(50), default="")
    provider = Column(String(50), default="")
    model = Column(String(100), default="")
    prompt_version = Column(String(50), default="")
    input_hash = Column(String(64), default="")
    output_hash = Column(String(64), default="")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), default="CREATED", server_default=text("'CREATED'"), nullable=False)
    latency_ms = Column(Integer, default=0)
    token_usage = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class TradeIntentModel(Base):
    """Trade intent persistence model."""

    __tablename__ = "trade_intents"

    trade_intent_id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    asset = Column(String(20), nullable=False, index=True)
    action = Column(String(10), nullable=False)
    quantity = Column(Numeric(20, 8), nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=False)
    stop_loss = Column(Numeric(20, 8), nullable=False)
    take_profit = Column(Numeric(20, 8), nullable=False)
    confidence = Column(Numeric(5, 4), nullable=False)
    thesis = Column(Text, default="")
    invalidation = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=utc_now)
    market_state_id = Column(UUID(as_uuid=True), ForeignKey("market_states.market_state_id"), nullable=False)
    ai_run_id = Column(UUID(as_uuid=True), ForeignKey("ai_runs.ai_run_id"), nullable=False)


class RiskDecisionModel(Base):
    """Risk decision persistence model."""

    __tablename__ = "risk_decisions"

    risk_decision_id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    trade_intent_id = Column(UUID(as_uuid=True), ForeignKey("trade_intents.trade_intent_id"), nullable=False)
    status = Column(String(20), default="PENDING", server_default=text("'PENDING'"), nullable=False)
    approved_quantity = Column(Numeric(20, 8), server_default=text("0"), nullable=False)
    approved_price = Column(Numeric(20, 8), server_default=text("0"), nullable=False)
    risk_amount = Column(Numeric(20, 8), server_default=text("0"), nullable=False)
    exposure = Column(Numeric(20, 8), server_default=text("0"), nullable=False)
    reasons = Column(Text, server_default=text("'[]'"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class OrderModel(Base):
    """Order persistence model."""

    __tablename__ = "orders"

    order_id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    client_order_id = Column(String(50), nullable=False, unique=True, index=True)
    status = Column(String(20), server_default=text("'CREATED'"), nullable=False, index=True)
    filled_quantity = Column(Numeric(20, 8), default=0)
    remaining_quantity = Column(Numeric(20, 8), default=0)
    average_price = Column(Numeric(20, 8), default=0)
    fees = Column(Numeric(20, 8), default=0)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    fills = relationship("FillModel", back_populates="order", lazy="selectin")


class FillModel(Base):
    """Fill persistence model."""

    __tablename__ = "fills"

    fill_id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.order_id"), nullable=False, index=True)
    quantity = Column(Numeric(20, 8), nullable=False)
    price = Column(Numeric(20, 8), nullable=False)
    fee = Column(Numeric(20, 8), default=0)
    timestamp = Column(DateTime(timezone=True), default=utc_now)

    order = relationship("OrderModel", back_populates="fills")


class PositionModel(Base):
    """Position persistence model."""

    __tablename__ = "positions"

    position_id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    asset = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)
    status = Column(String(20), default="NONE", server_default=text("'NONE'"), nullable=False, index=True)
    quantity = Column(Numeric(20, 8), default=0)
    average_entry = Column(Numeric(20, 8), default=0)
    current_price = Column(Numeric(20, 8), default=0)
    stop_loss = Column(Numeric(20, 8), default=0)
    take_profit = Column(Numeric(20, 8), default=0)
    realized_pnl = Column(Numeric(20, 8), default=0)
    unrealized_pnl = Column(Numeric(20, 8), default=0)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class PortfolioSnapshotModel(Base):
    """Portfolio snapshot persistence model."""

    __tablename__ = "portfolio_snapshots"

    snapshot_id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    timestamp = Column(DateTime(timezone=True), default=utc_now)
    cash = Column(Numeric(20, 8), default=0)
    equity = Column(Numeric(20, 8), default=0)
    exposure = Column(Numeric(20, 8), default=0)
    realized_pnl = Column(Numeric(20, 8), default=0)
    unrealized_pnl = Column(Numeric(20, 8), default=0)
    drawdown = Column(Numeric(20, 8), default=0)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class AuditEventModel(Base):
    """Audit event persistence model."""

    __tablename__ = "audit_events"

    audit_event_id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    correlation_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now)
    actor = Column(String(50), default="system", server_default=text("'system'"), nullable=False)
    payload_hash = Column(String(64), default="")

    __table_args__ = (
        Index("ix_audit_events_correlation_event", "correlation_id", "event_type"),
    )
