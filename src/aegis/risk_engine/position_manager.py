"""AEGIS Position Manager — break-even, trailing stop, profit protection.

Manages adaptive SL adjustments for open positions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

logger = logging.getLogger("aegis.position_manager")


@dataclass
class PositionManagerConfig:
    """Configuration for position management."""

    # Break-even
    break_even_enabled: bool = True
    break_even_trigger_r: Decimal = Decimal("0.8")  # Activate at +0.8R
    break_even_offset_pct: Decimal = Decimal("0.001")  # 0.1% above entry

    # Trailing stop
    trailing_enabled: bool = True
    trailing_trigger_r: Decimal = Decimal("1.2")  # Activate at +1.2R
    trailing_distance_pct: Decimal = Decimal("0.02")  # 2% trailing distance

    # Profit protection
    profit_protection_enabled: bool = True
    profit_levels: list[dict[str, Decimal]] = field(default_factory=lambda: [
        {"trigger_r": Decimal("1.5"), "lock_r": Decimal("0.5")},  # At +1.5R, lock +0.5R
        {"trigger_r": Decimal("2.0"), "lock_r": Decimal("1.0")},  # At +2.0R, lock +1.0R
    ])

    # Daily loss escalation
    daily_loss_warn_pct: Decimal = Decimal("0.03")  # 3% - reduce exposure
    daily_loss_strong_pct: Decimal = Decimal("0.04")  # 4% - only strong setups
    daily_loss_block_pct: Decimal = Decimal("0.05")  # 5% - block new entries


@dataclass
class ManagedPosition:
    """Tracks management state for an open position."""

    symbol: str
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    quantity: Decimal
    side: str = "LONG"

    # State tracking
    break_even_activated: bool = False
    trailing_activated: bool = False
    current_stop: Decimal = Decimal("0")
    highest_price: Decimal = Decimal("0")
    locked_profit_r: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.current_stop == Decimal("0"):
            self.current_stop = self.stop_loss
        if self.highest_price == Decimal("0"):
            self.highest_price = self.entry_price

    @property
    def risk_per_unit(self) -> Decimal:
        """Risk per unit (entry - stop)."""
        return self.entry_price - self.current_stop

    def unrealized_r(self, current_price: Decimal) -> Decimal:
        """Calculate unrealized R-multiple."""
        risk = self.entry_price - self.stop_loss
        if risk <= 0:
            return Decimal("0")
        return (current_price - self.entry_price) / risk


class PositionManager:
    """Manages adaptive SL for open positions.

    Supports:
    - Break-even protection
    - Trailing stop
    - Profit protection
    - Daily loss escalation
    """

    def __init__(self, config: PositionManagerConfig | None = None) -> None:
        self._config = config or PositionManagerConfig()
        self._positions: dict[str, ManagedPosition] = {}

    def register_position(self, symbol: str, entry_price: Decimal,
                          stop_loss: Decimal, take_profit: Decimal,
                          quantity: Decimal) -> ManagedPosition:
        """Register a new position for management."""
        pos = ManagedPosition(
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=quantity,
            current_stop=stop_loss,
            highest_price=entry_price,
        )
        self._positions[symbol] = pos
        return pos

    def unregister_position(self, symbol: str) -> None:
        """Remove position from management."""
        self._positions.pop(symbol, None)

    def get_position(self, symbol: str) -> ManagedPosition | None:
        """Get managed position state."""
        return self._positions.get(symbol)

    def evaluate(self, symbol: str, current_price: Decimal) -> dict[str, Any]:
        """Evaluate position management actions.

        Returns dict with:
        - action: "NONE", "MOVE_STOP", "CLOSE"
        - new_stop: new stop loss price (if MOVE_STOP)
        - reason: reason code
        - break_even_activated: bool
        - trailing_activated: bool
        """
        pos = self._positions.get(symbol)
        if pos is None:
            return {"action": "NONE", "reason": "no_position"}

        result = {"action": "NONE", "reason": "", "new_stop": pos.current_stop}

        # Update highest price for trailing
        if current_price > pos.highest_price:
            pos.highest_price = current_price

        # Calculate current R
        unrealized_r = pos.unrealized_r(current_price)

        # Priority: profit protection > trailing > break-even
        if self._config.profit_protection_enabled:
            pp_result = self._check_profit_protection(pos, unrealized_r)
            if pp_result["action"] != "NONE":
                return pp_result

        if self._config.trailing_enabled:
            trail_result = self._check_trailing_stop(pos, unrealized_r, current_price)
            if trail_result["action"] != "NONE":
                return trail_result

        if self._config.break_even_enabled:
            be_result = self._check_break_even(pos, unrealized_r, current_price)
            if be_result["action"] != "NONE":
                return be_result

        return result

    def _check_break_even(self, pos: ManagedPosition, unrealized_r: Decimal,
                          current_price: Decimal) -> dict[str, Any]:
        """Check if break-even should be activated."""
        if pos.break_even_activated:
            return {"action": "NONE", "reason": "already_activated"}

        if unrealized_r >= self._config.break_even_trigger_r:
            new_stop = pos.entry_price * (1 + self._config.break_even_offset_pct)
            if new_stop > pos.current_stop:
                pos.break_even_activated = True
                pos.current_stop = new_stop
                logger.info(
                    "Break-even activated for %s: SL moved to %s (+%.2fR)",
                    pos.symbol, new_stop, unrealized_r,
                )
                return {"action": "MOVE_STOP", "new_stop": new_stop,
                        "reason": "BREAK_EVEN", "break_even_activated": True}

        return {"action": "NONE", "reason": ""}

    def _check_trailing_stop(self, pos: ManagedPosition, unrealized_r: Decimal,
                              current_price: Decimal) -> dict[str, Any]:
        """Check if trailing stop should be activated or updated."""
        if not self._config.trailing_enabled:
            return {"action": "NONE", "reason": "trailing_disabled"}

        # Activate trailing
        if not pos.trailing_activated:
            if unrealized_r >= self._config.trailing_trigger_r:
                pos.trailing_activated = True
                new_stop = current_price * (1 - self._config.trailing_distance_pct)
                if new_stop > pos.current_stop:
                    pos.current_stop = new_stop
                    logger.info(
                        "Trailing stop activated for %s: SL moved to %s (+%.2fR)",
                        pos.symbol, new_stop, unrealized_r,
                    )
                    return {"action": "MOVE_STOP", "new_stop": new_stop,
                            "reason": "TRAILING_STOP", "trailing_activated": True}

        # Update existing trailing
        if pos.trailing_activated:
            new_stop = current_price * (1 - self._config.trailing_distance_pct)
            if new_stop > pos.current_stop:
                pos.current_stop = new_stop
                return {"action": "MOVE_STOP", "new_stop": new_stop,
                        "reason": "TRAILING_UPDATE"}

        return {"action": "NONE", "reason": ""}

    def _check_profit_protection(self, pos: ManagedPosition, unrealized_r: Decimal) -> dict[str, Any]:
        """Check if profit protection should lock in gains."""
        for level in sorted(self._config.profit_levels,
                           key=lambda x: x["trigger_r"], reverse=True):
            trigger_r = level["trigger_r"]
            lock_r = level["lock_r"]

            if unrealized_r >= trigger_r and pos.locked_profit_r < lock_r:
                new_stop = pos.entry_price + (pos.entry_price - pos.stop_loss) * lock_r
                if new_stop > pos.current_stop:
                    pos.locked_profit_r = lock_r
                    pos.current_stop = new_stop
                    logger.info(
                        "Profit protection for %s: locked +%.1fR at SL=%s",
                        pos.symbol, lock_r, new_stop,
                    )
                    return {"action": "MOVE_STOP", "new_stop": new_stop,
                            "reason": "PROFIT_PROTECTION"}

        return {"action": "NONE", "reason": ""}

    def check_daily_loss_escalation(self, daily_pnl_pct: Decimal) -> dict[str, Any]:
        """Check daily loss escalation levels.

        Returns:
            level: "NORMAL", "REDUCE", "STRONG_ONLY", "BLOCKED"
            message: human-readable reason
        """
        if daily_pnl_pct <= -self._config.daily_loss_block_pct:
            return {"level": "BLOCKED", "message": "Daily loss limit reached (5%)"}
        elif daily_pnl_pct <= -self._config.daily_loss_strong_pct:
            return {"level": "STRONG_ONLY", "message": "Daily loss high (4%) - only strong setups"}
        elif daily_pnl_pct <= -self._config.daily_loss_warn_pct:
            return {"level": "REDUCE", "message": "Daily loss elevated (3%) - reduce exposure"}
        return {"level": "NORMAL", "message": "Normal trading"}
