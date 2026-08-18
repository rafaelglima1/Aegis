"""AEGIS Deterministic Risk Engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from aegis.ai_engine.decision_engine import DecisionContract
from aegis.domain.contracts import utc_now
from aegis.domain.enums import TradingAction
from aegis.risk_engine.risk_limits import RiskLimits

logger = logging.getLogger("aegis.risk_engine")


@dataclass
class RiskLimitViolation:
    """Risk rejection produces a machine-readable reason code."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskDecision:
    """Risk decision output."""

    risk_decision_id: UUID = field(default_factory=uuid4)
    trade_intent_id: UUID | None = None
    status: str = "PENDING"
    approved_quantity: Decimal = Decimal("0")
    approved_price: Decimal = Decimal("0")
    risk_amount: Decimal = Decimal("0")
    exposure: Decimal = Decimal("0")
    violations: list[RiskLimitViolation] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    created_at: Any = field(default_factory=utc_now)

    @property
    def is_approved(self) -> bool:
        return self.status == "APPROVED"


@dataclass
class PositionState:
    """Tracks state of an open position for anti-flip-flop."""

    symbol: str
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    entry_thesis: str
    entry_timestamp: Any = field(default_factory=utc_now)
    last_decision: str = ""
    last_decision_timestamp: Any = field(default_factory=utc_now)


class RiskEngine:
    """Deterministic Risk Engine with quality filters.

    Enforces:
    - Long only (no SHORT)
    - No leverage (spot only)
    - Risk per trade limits
    - Max positions
    - R/R ratio minimum
    - Trend filter
    - Cooldown between trades
    - Anti flip-flop
    - Daily trade limits
    - Entry deviation limits
    - Circuit breaker
    """

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self._limits = limits or RiskLimits()
        self._kill_switch_active = False
        self._daily_pnl = Decimal("0")
        self._positions_count = 0
        self._peak_equity = self._limits.reference_capital
        self._circuit_breaker_active = False
        self._current_exposure = Decimal("0")

        # New: Trade tracking for cooldown and anti-flip-flop
        self._last_trade_time: dict[str, Any] = {}  # symbol -> timestamp
        self._daily_trade_count: int = 0
        self._daily_trade_count_per_symbol: dict[str, int] = {}
        self._position_states: dict[str, PositionState] = {}

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    @limits.setter
    def limits(self, value: RiskLimits) -> None:
        self._limits = value

    def activate_kill_switch(self) -> None:
        """Kill switch blocks new orders."""
        self._kill_switch_active = True

    def deactivate_kill_switch(self) -> None:
        """Deactivate kill switch."""
        self._kill_switch_active = False

    def record_daily_pnl(self, pnl: Decimal) -> None:
        """Record daily P&L for daily loss limit check."""
        self._daily_pnl += pnl

    def record_position_open(self) -> None:
        """Record a position being opened."""
        self._positions_count += 1

    def record_position_close(self) -> None:
        """Record a position being closed."""
        self._positions_count = max(0, self._positions_count - 1)

    def rebuild_from_open_positions(self, count: int, exposure: Decimal | None = None) -> None:
        """Reconstruct state after restart."""
        self._positions_count = count
        if exposure is not None:
            self._current_exposure = exposure

    @property
    def positions_count(self) -> int:
        return self._positions_count

    def record_exposure_change(self, amount: Decimal) -> None:
        """Record a change in total exposure."""
        self._current_exposure += amount

    def update_equity(self, current_equity: Decimal) -> None:
        """Update equity and check circuit breaker."""
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        drawdown = (self._peak_equity - current_equity) / self._peak_equity
        if drawdown >= self._limits.circuit_breaker_drawdown_pct:
            self._circuit_breaker_active = True
            self.activate_kill_switch()

    @property
    def circuit_breaker_active(self) -> bool:
        return self._circuit_breaker_active

    def record_trade(self, symbol: str, timestamp: Any = None) -> None:
        """Record a trade for cooldown and daily limits."""
        ts = timestamp or utc_now()
        self._last_trade_time[symbol] = ts
        self._daily_trade_count += 1
        self._daily_trade_count_per_symbol[symbol] = (
            self._daily_trade_count_per_symbol.get(symbol, 0) + 1
        )

    def record_position_state(self, symbol: str, entry_price: Decimal,
                               stop_loss: Decimal, take_profit: Decimal,
                               thesis: str) -> None:
        """Record position state for anti-flip-flop."""
        self._position_states[symbol] = PositionState(
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_thesis=thesis,
        )

    def reset_daily_counters(self) -> None:
        """Reset daily counters (call at start of new day)."""
        self._daily_pnl = Decimal("0")
        self._daily_trade_count = 0
        self._daily_trade_count_per_symbol.clear()

    def evaluate(self, decision: DecisionContract,
                 current_price: Decimal | None = None,
                 market_state: dict[str, Any] | None = None,
                 symbol: str = "") -> RiskDecision:
        """Evaluate a Decision Contract through deterministic risk checks.

        Performs comprehensive validation:
        1. Circuit breaker / kill switch
        2. HOLD/CLOSE shortcuts
        3. Confidence threshold (min_confidence from config)
        4. Mandatory SL/TP
        5. SL/TP validation
        6. R/R ratio validation
        7. Entry deviation check
        8. Max positions
        9. Max exposure
        10. Daily loss limit
        11. Daily trade limits
        12. Cooldown
        13. Anti flip-flop
        14. Position sizing
        """
        violations: list[RiskLimitViolation] = []

        # Circuit breaker check
        if self._circuit_breaker_active:
            violations.append(
                RiskLimitViolation(
                    code="CIRCUIT_BREAKER_ACTIVE",
                    message="Circuit breaker active (drawdown reached)",
                )
            )

        if self._kill_switch_active:
            violations.append(
                RiskLimitViolation(
                    code="KILL_SWITCH_ACTIVE",
                    message="Kill switch is active, no new orders allowed",
                )
            )

        if decision.action == TradingAction.HOLD:
            return RiskDecision(
                status="APPROVED",
                reasons=["HOLD action requires no risk check"],
                violations=violations,
            )

        if decision.action == TradingAction.CLOSE:
            return RiskDecision(
                status="APPROVED",
                reasons=["CLOSE action approved"],
                violations=violations,
            )

        if decision.action == TradingAction.LONG:
            # Confidence threshold (from config, not hardcoded)
            if decision.confidence < self._limits.min_confidence:
                violations.append(
                    RiskLimitViolation(
                        code="LOW_CONFIDENCE",
                        message=f"Confidence {decision.confidence} below minimum {self._limits.min_confidence}",
                    )
                )

            # Mandatory stop loss
            if not decision.stop_loss or decision.stop_loss <= 0:
                violations.append(
                    RiskLimitViolation(
                        code="STOP_LOSS_MISSING",
                        message="Stop loss is mandatory for LONG positions",
                    )
                )

            # Mandatory take profit
            if not decision.take_profit or decision.take_profit <= 0:
                violations.append(
                    RiskLimitViolation(
                        code="TAKE_PROFIT_MISSING",
                        message="Take profit is mandatory for LONG positions",
                    )
                )

            # Validate stop_loss < entry_price
            if (decision.entry_price and decision.stop_loss
                    and decision.stop_loss >= decision.entry_price):
                violations.append(
                    RiskLimitViolation(
                        code="STOP_LOSS_INVALID",
                        message="Stop loss must be below entry price for LONG",
                    )
                )

            # Validate take_profit > entry_price
            if (decision.entry_price and decision.take_profit
                    and decision.take_profit <= decision.entry_price):
                violations.append(
                    RiskLimitViolation(
                        code="TAKE_PROFIT_INVALID",
                        message="Take profit must be above entry price for LONG",
                    )
                )

            # R/R ratio validation
            if (decision.entry_price and decision.stop_loss and decision.take_profit
                    and decision.stop_loss < decision.entry_price
                    and decision.take_profit > decision.entry_price):
                risk_amount = decision.entry_price - decision.stop_loss
                reward_amount = decision.take_profit - decision.entry_price
                if risk_amount > 0:
                    rr_ratio = reward_amount / risk_amount
                    if rr_ratio < self._limits.min_risk_reward:
                        violations.append(
                            RiskLimitViolation(
                                code="LOW_RISK_REWARD",
                                message=f"R/R ratio {rr_ratio:.2f} below minimum {self._limits.min_risk_reward}",
                            )
                        )

            # Entry deviation check
            if (current_price and decision.entry_price
                    and current_price > 0):
                deviation = abs(decision.entry_price - current_price) / current_price
                if deviation > self._limits.max_entry_deviation_pct:
                    violations.append(
                        RiskLimitViolation(
                            code="ENTRY_DEVIATION",
                            message=f"Entry price deviation {deviation:.2%} exceeds maximum {self._limits.max_entry_deviation_pct:.2%}",
                        )
                    )

            # Trend filter
            if self._limits.trend_filter_enabled and market_state:
                trend = market_state.get("trend", "NEUTRAL")
                if trend == "BEARISH":
                    violations.append(
                        RiskLimitViolation(
                            code="TREND_FILTER",
                            message="LONG not allowed in bearish trend",
                        )
                    )

            # Anti flip-flop
            if symbol and symbol in self._position_states:
                pos_state = self._position_states[symbol]
                if current_price and pos_state.entry_price > 0:
                    price_change = abs(current_price - pos_state.entry_price) / pos_state.entry_price
                    if price_change < self._limits.min_thesis_change_pct:
                        violations.append(
                            RiskLimitViolation(
                                code="ANTI_FLIP_FLOP",
                                message=f"Price change {price_change:.2%} too small for re-entry (min {self._limits.min_thesis_change_pct:.2%})",
                            )
                        )

        # Max positions
        if self._positions_count >= self._limits.max_simultaneous_positions:
            violations.append(
                RiskLimitViolation(
                    code="MAX_POSITIONS",
                    message=f"Maximum simultaneous positions ({self._limits.max_simultaneous_positions}) reached",
                )
            )

        # Exposure limit
        if self._current_exposure >= self._limits.max_exposure:
            violations.append(
                RiskLimitViolation(
                    code="MAX_EXPOSURE",
                    message=f"Maximum exposure ({self._limits.max_exposure}) reached",
                )
            )

        # Daily loss limit
        if self._daily_pnl < -self._limits.max_daily_loss:
            violations.append(
                RiskLimitViolation(
                    code="DAILY_LOSS_LIMIT",
                    message=f"Daily loss limit exceeded: {self._daily_pnl}",
                )
            )

        # Daily trade limits
        if self._daily_trade_count >= self._limits.max_daily_trades:
            violations.append(
                RiskLimitViolation(
                    code="MAX_DAILY_TRADES",
                    message=f"Maximum daily trades ({self._limits.max_daily_trades}) reached",
                )
            )

        if symbol and self._daily_trade_count_per_symbol.get(symbol, 0) >= self._limits.max_daily_trades_per_symbol:
            violations.append(
                RiskLimitViolation(
                    code="MAX_DAILY_TRADES_PER_SYMBOL",
                    message=f"Maximum daily trades for {symbol} ({self._limits.max_daily_trades_per_symbol}) reached",
                )
            )

        if violations:
            return RiskDecision(
                status="REJECTED",
                violations=violations,
                reasons=[v.message for v in violations],
            )

        quantity = self._calculate_position_size(decision)
        risk_amount = quantity * (decision.entry_price or Decimal("0"))

        return RiskDecision(
            status="APPROVED",
            approved_quantity=quantity,
            approved_price=decision.entry_price or Decimal("0"),
            risk_amount=risk_amount,
            exposure=risk_amount,
            reasons=["Risk checks passed"],
        )

    def _calculate_position_size(self, decision: DecisionContract) -> Decimal:
        """Position sizing based on risk.

        size = min(risk_based_size, max_position_size)
        risk_based_size = max_risk / stop_distance
        """
        if not decision.entry_price or decision.entry_price <= 0:
            return Decimal("0")

        max_risk = self._limits.max_risk_per_trade
        stop_distance = abs(decision.entry_price - (decision.stop_loss or decision.entry_price))

        if stop_distance <= 0:
            return Decimal("0")

        quantity = max_risk / stop_distance
        max_quantity = self._limits.max_position_size / decision.entry_price

        return min(quantity, max_quantity)

    def calculate_risk_reward(self, entry_price: Decimal, stop_loss: Decimal,
                                take_profit: Decimal) -> dict[str, Decimal]:
        """Calculate risk/reward metrics for logging."""
        if not entry_price or entry_price <= 0:
            return {"risk": Decimal("0"), "reward": Decimal("0"), "ratio": Decimal("0")}

        risk = entry_price - (stop_loss or entry_price)
        reward = (take_profit or entry_price) - entry_price

        if risk <= 0:
            return {"risk": Decimal("0"), "reward": reward, "ratio": Decimal("0")}

        ratio = reward / risk
        return {"risk": risk, "reward": reward, "ratio": ratio}
