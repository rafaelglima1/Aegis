"""AEGIS Deterministic Risk Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from aegis.ai_engine.decision_engine import DecisionContract
from aegis.domain.contracts import utc_now
from aegis.domain.enums import TradingAction
from aegis.risk_engine.risk_limits import RiskLimits


@dataclass
class RiskLimitViolation:
    """AC-06.08: Risk rejection produces a machine-readable reason code."""

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


class RiskEngine:
    """AC-06.01: Risk Engine accepts only valid Decision Contracts.

    V1.0 Business Rules:
    - Long only (no SHORT)
    - No leverage (spot only)
    - 1% risk per trade
    - Max 1 position simultaneous
    - Take profit mandatory
    - Circuit breaker: 10% drawdown
    """

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self._limits = limits or RiskLimits()
        self._kill_switch_active = False
        self._daily_pnl = Decimal("0")
        self._positions_count = 0
        self._peak_equity = self._limits.reference_capital
        self._circuit_breaker_active = False
        self._current_exposure = Decimal("0")

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    def activate_kill_switch(self) -> None:
        """AC-06.07: Kill switch blocks new orders."""
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

    def record_exposure_change(self, amount: Decimal) -> None:
        """Record a change in total exposure."""
        self._current_exposure += amount

    def update_equity(self, current_equity: Decimal) -> None:
        """Update equity and check circuit breaker (10% drawdown)."""
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        drawdown = (self._peak_equity - current_equity) / self._peak_equity
        if drawdown >= self._limits.circuit_breaker_drawdown_pct:
            self._circuit_breaker_active = True
            self.activate_kill_switch()

    @property
    def circuit_breaker_active(self) -> bool:
        return self._circuit_breaker_active

    def evaluate(self, decision: DecisionContract) -> RiskDecision:
        """Evaluate a Decision Contract through deterministic risk checks.

        V1.0 Rules enforced:
        - Long only (no SHORT)
        - Take profit mandatory for LONG
        - Stop loss mandatory for LONG
        - 1% risk per trade
        - Max 1 position
        - Circuit breaker 10% drawdown
        """
        violations: list[RiskLimitViolation] = []

        # Circuit breaker check
        if self._circuit_breaker_active:
            violations.append(
                RiskLimitViolation(
                    code="CIRCUIT_BREAKER_ACTIVE",
                    message="Circuit breaker active (10% drawdown reached)",
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

        # V1.0: Long only — reject SHORT
        # TradingAction only has LONG/HOLD/CLOSE, so SHORT is already blocked
        # But we validate explicitly for safety
        if decision.action == TradingAction.LONG:
            # V1.0: Take profit mandatory
            if not decision.take_profit or decision.take_profit <= 0:
                violations.append(
                    RiskLimitViolation(
                        code="TAKE_PROFIT_MISSING",
                        message="Take profit is mandatory for LONG positions",
                    )
                )

            # V1.0: Stop loss mandatory
            if not decision.stop_loss or decision.stop_loss <= 0:
                violations.append(
                    RiskLimitViolation(
                        code="STOP_LOSS_MISSING",
                        message="Stop loss is mandatory for LONG positions",
                    )
                )

            # V1.0: Validate stop_loss < entry_price (for LONG)
            if (decision.entry_price and decision.stop_loss
                    and decision.stop_loss >= decision.entry_price):
                violations.append(
                    RiskLimitViolation(
                        code="STOP_LOSS_INVALID",
                        message="Stop loss must be below entry price for LONG",
                    )
                )

            # V1.0: Validate take_profit > entry_price (for LONG)
            if (decision.entry_price and decision.take_profit
                    and decision.take_profit <= decision.entry_price):
                violations.append(
                    RiskLimitViolation(
                        code="TAKE_PROFIT_INVALID",
                        message="Take profit must be above entry price for LONG",
                    )
                )

        if decision.confidence < Decimal("0.5"):
            violations.append(
                RiskLimitViolation(
                    code="LOW_CONFIDENCE",
                    message=f"Confidence {decision.confidence} below minimum 0.5",
                )
            )

        if self._positions_count >= self._limits.max_simultaneous_positions:
            violations.append(
                RiskLimitViolation(
                    code="MAX_POSITIONS",
                    message=f"Maximum simultaneous positions ({self._limits.max_simultaneous_positions}) reached",
                )
            )

        # Exposure limit check
        if self._current_exposure >= self._limits.max_exposure:
            violations.append(
                RiskLimitViolation(
                    code="MAX_EXPOSURE",
                    message=f"Maximum exposure ({self._limits.max_exposure}) reached",
                )
            )

        if self._daily_pnl < -self._limits.max_daily_loss:
            violations.append(
                RiskLimitViolation(
                    code="DAILY_LOSS_LIMIT",
                    message=f"Daily loss limit exceeded: {self._daily_pnl}",
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
        """AC-06.02: Position sizing is deterministic."""
        if not decision.entry_price or decision.entry_price <= 0:
            return Decimal("0")

        max_risk = self._limits.max_risk_per_trade
        stop_distance = abs(decision.entry_price - (decision.stop_loss or decision.entry_price))

        if stop_distance <= 0:
            return Decimal("0")

        quantity = max_risk / stop_distance
        max_quantity = self._limits.max_position_size / decision.entry_price

        return min(quantity, max_quantity)
