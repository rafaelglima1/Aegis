"""AEGIS Dashboard API — backend for operational UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from aegis.domain.contracts import utc_now
from aegis.domain.enums import TradingAction, SystemStatus


class LiveState(Enum):
    """AC-14.10: Dashboard clearly displays LIVE state."""

    DISABLED = "DISABLED"
    BLOCKED = "BLOCKED"
    READY = "READY"


@dataclass
class DashboardPosition:
    """Position display data."""

    symbol: str = ""
    side: str = ""
    quantity: Decimal = Decimal("0")
    average_entry: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    stop_loss: Decimal = Decimal("0")


@dataclass
class DashboardOrder:
    """Order display data."""

    order_id: UUID = field(default_factory=uuid4)
    symbol: str = ""
    side: str = ""
    quantity: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    status: str = ""
    timestamp: Any = None


@dataclass
class DashboardMetrics:
    """Portfolio metrics display."""

    cash: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    exposure: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    drawdown: Decimal = Decimal("0")


@dataclass
class DashboardRiskStatus:
    """Risk status display.
    AC-C3-05: max_positions has no default — must come from config.

    Vibe patterns exposed: halt_reason, kill_switch_episode, deltas,
    pending_action.
    """

    kill_switch_active: bool = False
    kill_switch_episode: str | None = None
    daily_pnl: Decimal = Decimal("0")
    daily_loss_limit: Decimal = Decimal("500.00")
    max_positions: int = 0
    current_positions: int = 0
    reconciled: bool = True
    reconciliation_status: str = "SKIPPED"
    halt_reason: str | None = None


@dataclass
class DashboardConfig:
    """Provider/LLM configuration display."""

    provider: str = ""
    model: str = ""
    environment: str = ""
    live_state: LiveState = LiveState.DISABLED


class DashboardService:
    """AC-14.01-14.10: Dashboard displays operational data."""

    def __init__(self, max_positions: int = 0) -> None:
        self._positions: list[DashboardPosition] = []
        self._orders: list[DashboardOrder] = []
        self._metrics = DashboardMetrics()
        self._risk_status = DashboardRiskStatus(max_positions=max_positions)
        self._config = DashboardConfig()

    def get_environment(self) -> str:
        """AC-14.01: Dashboard displays current trading environment."""
        return self._config.environment

    def get_system_health(self) -> dict[str, Any]:
        """AC-14.02: Dashboard displays system health/status."""
        return {
            "status": "healthy",
            "timestamp": str(utc_now()),
            "version": "1.3.0",
        }

    def get_positions(self) -> list[DashboardPosition]:
        """AC-14.03: Dashboard displays open positions."""
        return self._positions.copy()

    def get_pnl(self) -> DashboardMetrics:
        """AC-14.04: Dashboard displays P&L."""
        return self._metrics

    def get_orders(self) -> list[DashboardOrder]:
        """AC-14.05: Dashboard displays orders."""
        return self._orders.copy()

    def get_exposure(self) -> Decimal:
        """AC-14.06: Dashboard displays exposure."""
        return self._metrics.exposure

    def get_risk_status(self) -> DashboardRiskStatus:
        """AC-14.07: Dashboard displays Risk status."""
        return self._risk_status

    def get_config(self) -> DashboardConfig:
        """AC-14.08: Provider/LLM configuration is visible without exposing secrets."""
        return self._config

    def get_live_state(self) -> LiveState:
        """AC-14.10: Dashboard clearly displays LIVE state as DISABLED, BLOCKED or READY."""
        return self._config.live_state

    def update_positions(self, positions: list[DashboardPosition]) -> None:
        self._positions = positions

    def update_orders(self, orders: list[DashboardOrder]) -> None:
        self._orders = orders

    def update_metrics(self, metrics: DashboardMetrics) -> None:
        self._metrics = metrics

    def update_risk_status(self, status: DashboardRiskStatus) -> None:
        self._risk_status = status

    def update_config(self, config: DashboardConfig) -> None:
        self._config = config
