"""AEGIS repository layer — persistence operations for domain entities."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.db.models import (
    OrderModel,
    PositionModel,
    FillModel,
    PortfolioSnapshotModel,
    AIRunModel,
    AuditEventModel,
    MarketStateModel,
    TradeIntentModel,
    RiskDecisionModel,
)
from aegis.domain.enums import OrderStatus, PositionStatus


class OrderRepository:
    """Repository for order persistence operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, order: OrderModel) -> OrderModel:
        self._session.add(order)
        await self._session.flush()
        return order

    async def get_by_id(self, order_id: UUID) -> OrderModel | None:
        result = await self._session.execute(
            select(OrderModel).where(OrderModel.order_id == order_id)
        )
        return result.scalar_one_or_none()

    async def get_by_client_order_id(self, client_order_id: str) -> OrderModel | None:
        result = await self._session.execute(
            select(OrderModel).where(OrderModel.client_order_id == client_order_id)
        )
        return result.scalar_one_or_none()

    async def update_status(self, order_id: UUID, status: OrderStatus) -> OrderModel | None:
        await self._session.execute(
            update(OrderModel)
            .where(OrderModel.order_id == order_id)
            .values(status=status.value)
        )
        await self._session.flush()
        return await self.get_by_id(order_id)

    async def list_by_status(self, status: OrderStatus) -> list[OrderModel]:
        result = await self._session.execute(
            select(OrderModel).where(OrderModel.status == status.value)
        )
        return list(result.scalars().all())


class PositionRepository:
    """Repository for position persistence operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, position: PositionModel) -> PositionModel:
        self._session.add(position)
        await self._session.flush()
        return position

    async def get_by_id(self, position_id: UUID) -> PositionModel | None:
        result = await self._session.execute(
            select(PositionModel).where(PositionModel.position_id == position_id)
        )
        return result.scalar_one_or_none()

    async def get_open_by_asset(self, asset: str) -> PositionModel | None:
        result = await self._session.execute(
            select(PositionModel).where(
                PositionModel.asset == asset,
                PositionModel.status == PositionStatus.OPEN.value,
            )
        )
        return result.scalar_one_or_none()

    async def update_status(self, position_id: UUID, status: PositionStatus) -> PositionModel | None:
        await self._session.execute(
            update(PositionModel)
            .where(PositionModel.position_id == position_id)
            .values(status=status.value)
        )
        await self._session.flush()
        return await self.get_by_id(position_id)

    async def update_price(self, position_id: UUID, current_price: Decimal) -> PositionModel | None:
        """Update current price for unrealized P&L calculation."""
        await self._session.execute(
            update(PositionModel)
            .where(PositionModel.position_id == position_id)
            .values(current_price=current_price)
        )
        await self._session.flush()
        return await self.get_by_id(position_id)

    async def list_open(self) -> list[PositionModel]:
        """List all open positions."""
        result = await self._session.execute(
            select(PositionModel).where(PositionModel.status == PositionStatus.OPEN.value)
        )
        return list(result.scalars().all())


class FillRepository:
    """Repository for fill persistence operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, fill: FillModel) -> FillModel:
        self._session.add(fill)
        await self._session.flush()
        return fill

    async def get_by_order_id(self, order_id: UUID) -> list[FillModel]:
        result = await self._session.execute(
            select(FillModel).where(FillModel.order_id == order_id)
        )
        return list(result.scalars().all())


class PortfolioRepository:
    """Repository for portfolio snapshot persistence operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, snapshot: PortfolioSnapshotModel) -> PortfolioSnapshotModel:
        self._session.add(snapshot)
        await self._session.flush()
        return snapshot

    async def get_latest(self) -> PortfolioSnapshotModel | None:
        result = await self._session.execute(
            select(PortfolioSnapshotModel)
            .order_by(PortfolioSnapshotModel.timestamp.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class AIRunRepository:
    """Repository for AI run persistence operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, ai_run: AIRunModel) -> AIRunModel:
        self._session.add(ai_run)
        await self._session.flush()
        return ai_run

    async def get_by_id(self, ai_run_id: UUID) -> AIRunModel | None:
        result = await self._session.execute(
            select(AIRunModel).where(AIRunModel.ai_run_id == ai_run_id)
        )
        return result.scalar_one_or_none()


class AuditRepository:
    """Repository for audit event persistence operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, event: AuditEventModel) -> AuditEventModel:
        self._session.add(event)
        await self._session.flush()
        return event

    async def get_by_correlation_id(self, correlation_id: UUID) -> list[AuditEventModel]:
        result = await self._session.execute(
            select(AuditEventModel).where(
                AuditEventModel.correlation_id == correlation_id
            )
        )
        return list(result.scalars().all())


class MarketStateRepository:
    """Repository for market state persistence operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, market_state: MarketStateModel) -> MarketStateModel:
        self._session.add(market_state)
        await self._session.flush()
        return market_state

    async def get_by_id(self, market_state_id: UUID) -> MarketStateModel | None:
        result = await self._session.execute(
            select(MarketStateModel).where(
                MarketStateModel.market_state_id == market_state_id
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_by_asset(self, asset: str) -> MarketStateModel | None:
        result = await self._session.execute(
            select(MarketStateModel)
            .where(MarketStateModel.asset == asset)
            .order_by(MarketStateModel.timestamp.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class TradeIntentRepository:
    """Repository for trade intent persistence operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, trade_intent: TradeIntentModel) -> TradeIntentModel:
        self._session.add(trade_intent)
        await self._session.flush()
        return trade_intent

    async def get_by_id(self, trade_intent_id: UUID) -> TradeIntentModel | None:
        result = await self._session.execute(
            select(TradeIntentModel).where(
                TradeIntentModel.trade_intent_id == trade_intent_id
            )
        )
        return result.scalar_one_or_none()


class RiskDecisionRepository:
    """Repository for risk decision persistence operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, risk_decision: RiskDecisionModel) -> RiskDecisionModel:
        self._session.add(risk_decision)
        await self._session.flush()
        return risk_decision

    async def get_by_id(self, risk_decision_id: UUID) -> RiskDecisionModel | None:
        result = await self._session.execute(
            select(RiskDecisionModel).where(
                RiskDecisionModel.risk_decision_id == risk_decision_id
            )
        )
        return result.scalar_one_or_none()
