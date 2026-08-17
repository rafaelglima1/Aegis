"""AEGIS Portfolio — cash, positions, fills, P&L tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from aegis.domain.contracts import utc_now
from aegis.domain.enums import PositionSide, PositionStatus


@dataclass
class PositionEntry:
    """Position tracking entry."""

    position_id: UUID = field(default_factory=uuid4)
    asset: str = ""
    side: PositionSide = PositionSide.LONG
    status: PositionStatus = PositionStatus.NONE
    quantity: Decimal = Decimal("0")
    average_entry: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")
    stop_loss: Decimal = Decimal("0")
    take_profit: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")

    @property
    def exposure(self) -> Decimal:
        return self.quantity * self.current_price

    def update_price(self, price: Decimal) -> None:
        """AC-07.06: Unrealized P&L is calculated."""
        self.current_price = price
        if self.quantity > 0:
            self.unrealized_pnl = (price - self.average_entry) * self.quantity
        else:
            self.unrealized_pnl = Decimal("0")


@dataclass
class PortfolioSnapshot:
    """AC-07.10: Financial state survives restart."""

    snapshot_id: UUID = field(default_factory=uuid4)
    timestamp: Any = field(default_factory=utc_now)
    cash: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    exposure: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    drawdown: Decimal = Decimal("0")


class Portfolio:
    """AC-07.01: Cash balance is maintained."""

    def __init__(self, initial_cash: Decimal = Decimal("10000.00")) -> None:
        self._cash = initial_cash
        self._positions: dict[str, PositionEntry] = {}
        self._total_realized_pnl = Decimal("0")
        self._total_fees = Decimal("0")
        self._peak_equity = initial_cash

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def positions(self) -> dict[str, PositionEntry]:
        return self._positions.copy()

    @property
    def total_realized_pnl(self) -> Decimal:
        return self._total_realized_pnl

    @property
    def total_fees(self) -> Decimal:
        return self._total_fees

    @property
    def exposure(self) -> Decimal:
        """AC-07.08: Exposure is calculated."""
        return sum(p.exposure for p in self._positions.values())

    @property
    def unrealized_pnl(self) -> Decimal:
        """AC-07.06: Unrealized P&L is calculated."""
        return sum(p.unrealized_pnl for p in self._positions.values())

    @property
    def equity(self) -> Decimal:
        return self._cash + self.unrealized_pnl

    @property
    def drawdown(self) -> Decimal:
        if self._peak_equity <= 0:
            return Decimal("0")
        return (self._peak_equity - self.equity) / self._peak_equity

    def record_fill(
        self,
        asset: str,
        side: PositionSide,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal = Decimal("0"),
    ) -> None:
        """AC-07.03: Fills update positions correctly."""
        self._total_fees += fee
        self._cash -= fee

        if asset not in self._positions:
            self._positions[asset] = PositionEntry(
                asset=asset,
                side=side,
                status=PositionStatus.OPEN,
                current_price=price,
            )

        pos = self._positions[asset]

        if pos.quantity == 0:
            pos.average_entry = price
            pos.quantity = quantity
        else:
            total_cost = pos.average_entry * pos.quantity + price * quantity
            pos.quantity += quantity
            if pos.quantity > 0:
                pos.average_entry = total_cost / pos.quantity

        pos.current_price = price
        self._cash -= price * quantity

    def close_position(self, asset: str, price: Decimal, fee: Decimal = Decimal("0")) -> Decimal:
        """AC-07.05: Realized P&L is calculated."""
        if asset not in self._positions:
            return Decimal("0")

        pos = self._positions[asset]
        self._total_fees += fee
        self._cash -= fee

        realized = Decimal("0")
        if pos.quantity > 0:
            realized = (price - pos.average_entry) * pos.quantity
            self._total_realized_pnl += realized
            self._cash += price * pos.quantity

        pos.quantity = Decimal("0")
        pos.status = PositionStatus.CLOSED
        pos.realized_pnl = realized
        pos.unrealized_pnl = Decimal("0")

        return realized

    def update_prices(self, prices: dict[str, Decimal]) -> None:
        """AC-07.06: Unrealized P&L is calculated."""
        for asset, price in prices.items():
            if asset in self._positions:
                self._positions[asset].update_price(price)

        if self.equity > self._peak_equity:
            self._peak_equity = self.equity

    def snapshot(self) -> PortfolioSnapshot:
        """AC-07.10: Financial state survives restart."""
        return PortfolioSnapshot(
            cash=self._cash,
            equity=self.equity,
            exposure=self.exposure,
            realized_pnl=self._total_realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            drawdown=self.drawdown,
        )
