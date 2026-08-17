"""AEGIS Accounting — deterministic financial calculations."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from aegis.portfolio.portfolio import Portfolio


class Accounting:
    """AC-07.09: Accounting does not depend on LLM output."""

    def __init__(self, portfolio: Portfolio) -> None:
        self._portfolio = portfolio

    def calculate_pnl(self, entry_price: Decimal, exit_price: Decimal, quantity: Decimal) -> Decimal:
        """AC-07.05: Realized P&L is calculated."""
        return (exit_price - entry_price) * quantity

    def calculate_average_cost(
        self,
        existing_quantity: Decimal,
        existing_cost: Decimal,
        new_quantity: Decimal,
        new_price: Decimal,
    ) -> Decimal:
        """AC-07.04: Average cost is calculated deterministically."""
        total_quantity = existing_quantity + new_quantity
        if total_quantity <= 0:
            return Decimal("0")
        total_cost = existing_cost * existing_quantity + new_price * new_quantity
        return total_cost / total_quantity

    def calculate_fees(self, quantity: Decimal, price: Decimal, fee_rate: Decimal) -> Decimal:
        """AC-07.07: Fees are accounted for."""
        return quantity * price * fee_rate

    def calculate_exposure(self, quantity: Decimal, price: Decimal) -> Decimal:
        """AC-07.08: Exposure is calculated."""
        return quantity * price

    def get_portfolio_summary(self) -> dict[str, Any]:
        """AC-07.01: Cash balance is maintained."""
        snapshot = self._portfolio.snapshot()
        return {
            "cash": str(snapshot.cash),
            "equity": str(snapshot.equity),
            "exposure": str(snapshot.exposure),
            "realized_pnl": str(snapshot.realized_pnl),
            "unrealized_pnl": str(snapshot.unrealized_pnl),
            "drawdown": str(snapshot.drawdown),
            "total_fees": str(self._portfolio.total_fees),
        }
