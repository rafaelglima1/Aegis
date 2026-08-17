"""AEGIS risk limits — deterministic hard limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class RiskLimits:
    """Hard risk limits that cannot be overridden by LLM or frontend."""

    reference_capital: Decimal = Decimal("100.00")
    max_risk_per_trade_pct: Decimal = Decimal("0.01")  # 1%
    max_simultaneous_positions: int = 1
    mandatory_stop: bool = True
    circuit_breaker_drawdown_pct: Decimal = Decimal("0.10")  # 10%
    max_daily_loss_pct: Decimal = Decimal("0.05")  # 5%
    max_position_size_pct: Decimal = Decimal("0.20")  # 20%
    max_exposure_pct: Decimal = Decimal("1.00")  # 100%

    @property
    def max_risk_per_trade(self) -> Decimal:
        """Maximum risk amount per trade."""
        return self.reference_capital * self.max_risk_per_trade_pct

    @property
    def max_daily_loss(self) -> Decimal:
        """Maximum daily loss amount."""
        return self.reference_capital * self.max_daily_loss_pct

    @property
    def max_position_size(self) -> Decimal:
        """Maximum position size."""
        return self.reference_capital * self.max_position_size_pct

    @property
    def max_exposure(self) -> Decimal:
        """Maximum total exposure."""
        return self.reference_capital * self.max_exposure_pct
