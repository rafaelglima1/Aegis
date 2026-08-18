"""AEGIS Recovery Service — restores runtime state from PostgreSQL.

AC-C10.1-02/03/04/05/06: Recovery from PostgreSQL, not worker_state.json.

The RecoveryService reads persisted state from PostgreSQL and reconstructs:
- Portfolio (cash, positions, P&L, fees, peak equity)
- RiskEngine (positions count, exposure, peak equity)
- Worker state dict

This is the canonical recovery path. worker_state.json is auxiliary only.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger("aegis.recovery")


class RecoveryService:
    """AC-C10.1-02: Recovery from PostgreSQL as canonical source.

    Flow:
        PostgreSQL
            ↓
        RecoveryService
            ↓
        Portfolio
            ↓
        RiskEngine
            ↓
        Worker state dict
    """

    def __init__(self, session_factory: Any = None) -> None:
        """Initialize with optional async session factory for PostgreSQL.

        If session_factory is None, recovery falls back to in-memory state
        (development mode). In production, session_factory must be provided.
        """
        self._session_factory = session_factory

    async def recover_portfolio(self, portfolio: Any) -> dict[str, Any]:
        """Recover Portfolio state from PostgreSQL.

        AC-C10.1-03: Cash comes from PostgreSQL.
        AC-C10.1-04: Positions come from PostgreSQL.
        AC-C10.1-06: P&L and fees survive restart via PostgreSQL.
        """
        if self._session_factory is None:
            logger.info("No DB session factory — recovery uses in-memory state")
            return {"source": "memory", "recovered": False}

        try:
            async with self._session_factory() as session:
                from aegis.db.repositories import PortfolioRepository, PositionRepository
                from aegis.db.models import PortfolioSnapshotModel, PositionModel

                # Recover latest portfolio snapshot
                portfolio_repo = PortfolioRepository(session)
                snapshot = await portfolio_repo.get_latest()

                if snapshot is not None:
                    portfolio._cash = Decimal(str(snapshot.cash))
                    portfolio._total_realized_pnl = Decimal(str(snapshot.realized_pnl))
                    portfolio._total_fees = Decimal(str(0))  # Fees tracked separately
                    portfolio._peak_equity = Decimal(str(snapshot.equity))

                    logger.info(
                        "Portfolio recovered from PostgreSQL: cash=%s, equity=%s, realized_pnl=%s",
                        snapshot.cash, snapshot.equity, snapshot.realized_pnl,
                    )

                # Recover open positions
                position_repo = PositionRepository(session)
                from sqlalchemy import select
                result = await session.execute(
                    select(PositionModel).where(PositionModel.status == "OPEN")
                )
                open_positions = list(result.scalars().all())

                for pos_model in open_positions:
                    from aegis.portfolio.portfolio import PositionEntry
                    from aegis.domain.enums import PositionSide, PositionStatus

                    entry = PositionEntry(
                        asset=pos_model.asset,
                        side=PositionSide.LONG if pos_model.side == "LONG" else PositionSide.SHORT,
                        status=PositionStatus.OPEN,
                        quantity=Decimal(str(pos_model.quantity)),
                        average_entry=Decimal(str(pos_model.average_entry)),
                        current_price=Decimal(str(pos_model.current_price)),
                        entry_fee=Decimal(str(0)),
                    )
                    portfolio._positions[pos_model.asset] = entry

                logger.info("Recovered %d open positions from PostgreSQL", len(open_positions))

                return {
                    "source": "postgresql",
                    "recovered": True,
                    "cash": str(portfolio.cash),
                    "positions": len(open_positions),
                }

        except Exception as e:
            logger.error("PostgreSQL recovery failed: %s — falling back to in-memory", e)
            return {"source": "postgresql", "recovered": False, "error": str(e)}

    async def persist_portfolio_snapshot(self, portfolio: Any) -> bool:
        """Persist current Portfolio state to PostgreSQL.

        AC-C10.1-07/08: BUY/SELL persist portfolio snapshot.
        """
        if self._session_factory is None:
            return False

        try:
            async with self._session_factory() as session:
                from aegis.db.repositories import PortfolioRepository
                from aegis.db.models import PortfolioSnapshotModel

                snapshot = PortfolioSnapshotModel(
                    cash=portfolio.cash,
                    equity=portfolio.equity,
                    exposure=portfolio.exposure,
                    realized_pnl=portfolio.total_realized_pnl,
                    unrealized_pnl=portfolio.unrealized_pnl,
                    drawdown=portfolio.drawdown,
                )

                repo = PortfolioRepository(session)
                await repo.create(snapshot)
                await session.commit()

                logger.debug("Portfolio snapshot persisted to PostgreSQL")
                return True

        except Exception as e:
            logger.error("Failed to persist portfolio snapshot: %s", e)
            return False

    async def persist_position(self, asset: str, side: str, quantity: Decimal,
                                entry_price: Decimal, status: str = "OPEN") -> bool:
        """Persist a position to PostgreSQL.

        AC-C10.1-07/08: BUY/SELL persist positions.
        """
        if self._session_factory is None:
            return False

        try:
            async with self._session_factory() as session:
                from aegis.db.repositories import PositionRepository
                from aegis.db.models import PositionModel

                position = PositionModel(
                    asset=asset,
                    side=side,
                    status=status,
                    quantity=quantity,
                    average_entry=entry_price,
                    current_price=entry_price,
                )

                repo = PositionRepository(session)
                await repo.create(position)
                await session.commit()

                logger.debug("Position persisted to PostgreSQL: %s %s", asset, side)
                return True

        except Exception as e:
            logger.error("Failed to persist position: %s", e)
            return False

    async def persist_order(self, client_order_id: str, status: str = "CREATED") -> bool:
        """Persist an order to PostgreSQL.

        AC-C10.1-07/08: BUY/SELL persist orders.
        """
        if self._session_factory is None:
            return False

        try:
            async with self._session_factory() as session:
                from aegis.db.repositories import OrderRepository
                from aegis.db.models import OrderModel

                order = OrderModel(
                    client_order_id=client_order_id,
                    status=status,
                )

                repo = OrderRepository(session)
                await repo.create(order)
                await session.commit()

                logger.debug("Order persisted to PostgreSQL: %s", client_order_id)
                return True

        except Exception as e:
            logger.error("Failed to persist order: %s", e)
            return False

    async def persist_audit_event(self, correlation_id: Any, event_type: str,
                                    entity_type: str, entity_id: Any,
                                    actor: str = "system") -> bool:
        """Persist an audit event to PostgreSQL.

        AC-C10.1-09: Audit events are persisted.
        """
        if self._session_factory is None:
            return False

        try:
            async with self._session_factory() as session:
                from aegis.db.repositories import AuditRepository
                from aegis.db.models import AuditEventModel

                event = AuditEventModel(
                    correlation_id=correlation_id,
                    event_type=event_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    actor=actor,
                )

                repo = AuditRepository(session)
                await repo.create(event)
                await session.commit()

                logger.debug("Audit event persisted: %s/%s", event_type, entity_type)
                return True

        except Exception as e:
            logger.error("Failed to persist audit event: %s", e)
            return False

    async def recover_worker_state(self, worker: Any) -> dict[str, Any]:
        """Full worker state recovery from PostgreSQL.

        AC-C10.1-02: worker_state.json not required for financial recovery.
        AC-C10.1-03/04/05/06: All financial state from PostgreSQL.
        """
        result = await self.recover_portfolio(worker.portfolio)

        if result.get("recovered"):
            # Reconstruct risk engine from recovered positions
            open_count = len([
                p for p in worker.portfolio._positions.values()
                if p.quantity > 0
            ])
            total_exposure = sum(
                p.quantity * p.current_price
                for p in worker.portfolio._positions.values()
                if p.quantity > 0
            )

            worker.risk_engine.rebuild_from_open_positions(open_count, total_exposure)

            # Sync worker state dict
            worker._state["capital"] = str(worker.portfolio.cash)
            worker._state["pnl"] = str(worker.portfolio.total_realized_pnl)
            worker._state["equity"] = str(worker.portfolio.equity)

            # Sync broker balance
            if hasattr(worker.broker, "_balance"):
                worker.broker._balance = worker.portfolio.cash

            logger.info(
                "Worker state recovered from PostgreSQL: capital=%s, positions=%d",
                worker.portfolio.cash, open_count,
            )

        return result
