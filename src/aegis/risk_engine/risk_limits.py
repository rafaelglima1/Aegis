"""AEGIS risk limits — deterministic hard limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

# Hard limits that CANNOT be overridden
MAX_POSITIONS_HARD_LIMIT = 1
MAX_LEVERAGE_HARD_LIMIT = Decimal("1.0")


@dataclass(frozen=True)
class RiskLimits:
    """Hard risk limits that cannot be overridden by LLM or frontend.

    All parameters are configurable via .env.prod or Settings.
    """

    # Capital and position sizing
    reference_capital: Decimal = Decimal("100.00")
    max_risk_per_trade_pct: Decimal = Decimal("0.01")  # 1%
    max_simultaneous_positions: int = MAX_POSITIONS_HARD_LIMIT
    max_position_size_pct: Decimal = Decimal("0.20")  # 20%
    max_exposure_pct: Decimal = Decimal("1.00")  # 100%

    # Risk management
    mandatory_stop: bool = True
    circuit_breaker_drawdown_pct: Decimal = Decimal("0.10")  # 10%
    max_daily_loss_pct: Decimal = Decimal("0.05")  # 5%

    # New: Quality filters
    min_confidence: Decimal = Decimal("0.50")  # Minimum confidence for LONG
    min_risk_reward: Decimal = Decimal("1.50")  # Minimum R/R ratio
    trend_filter_enabled: bool = True  # Require trend alignment for LONG
    max_entry_deviation_pct: Decimal = Decimal("0.05")  # Max 5% deviation from current price

    # Anti flip-flop
    cooldown_minutes: int = 60  # Minimum minutes between trades on same symbol
    min_thesis_change_pct: Decimal = Decimal("0.02")  # 2% price change for CLOSE justification

    # Daily limits
    max_daily_trades: int = 5  # Maximum trades per day
    max_daily_trades_per_symbol: int = 2  # Maximum trades per symbol per day

    # Position monitoring
    max_position_duration_hours: int = 48  # Max hold time before forced review

    # Costs
    fee_rate: Decimal = Decimal("0.005")  # 0.5% fee rate
    slippage_bps: Decimal = Decimal("10")  # 10 basis points slippage

    def __post_init__(self) -> None:
        """Enforce hard limits at construction time."""
        if self.max_simultaneous_positions > MAX_POSITIONS_HARD_LIMIT:
            object.__setattr__(self, "max_simultaneous_positions", MAX_POSITIONS_HARD_LIMIT)

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
