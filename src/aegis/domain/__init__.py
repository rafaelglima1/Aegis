"""AEGIS domain contracts — centralized enums, state machines, and Pydantic models."""

from aegis.domain.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionStatus,
    TradingAction,
    AIRunStatus,
    SystemStatus,
)
from aegis.domain.contracts import (
    MarketState,
    TradeIntent,
    RiskDecision,
    OrderRequest,
    Order,
    Fill,
    Position,
    PortfolioSnapshot,
    AIRun,
    AuditEvent,
)
from aegis.domain.state_machines import (
    OrderStateMachine,
    PositionStateMachine,
    AIRunStateMachine,
    SystemStateMachine,
    InvalidStateTransition,
)

__all__ = [
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "PositionSide",
    "PositionStatus",
    "TradingAction",
    "AIRunStatus",
    "SystemStatus",
    "MarketState",
    "TradeIntent",
    "RiskDecision",
    "OrderRequest",
    "Order",
    "Fill",
    "Position",
    "PortfolioSnapshot",
    "AIRun",
    "AuditEvent",
    "OrderStateMachine",
    "PositionStateMachine",
    "AIRunStateMachine",
    "SystemStateMachine",
    "InvalidStateTransition",
]
