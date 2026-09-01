"""Tests for AEGIS Risk Engine."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from aegis.ai_engine.decision_engine import DecisionContract
from aegis.domain.enums import TradingAction
from aegis.risk_engine.risk_engine import RiskEngine, RiskDecision
from aegis.risk_engine.risk_limits import RiskLimits


def make_long_decision(**overrides) -> DecisionContract:
    defaults = dict(
        action=TradingAction.LONG,
        confidence=Decimal("0.85"),
        thesis="Strong bullish",
        entry_price=Decimal("50.00"),
        stop_loss=Decimal("48.00"),
        take_profit=Decimal("55.00"),
    )
    defaults.update(overrides)
    return DecisionContract(**defaults)


def make_hold_decision() -> DecisionContract:
    return DecisionContract(
        action=TradingAction.HOLD,
        confidence=Decimal("0.5"),
        thesis="Wait",
    )


def make_close_decision() -> DecisionContract:
    return DecisionContract(
        action=TradingAction.CLOSE,
        confidence=Decimal("0.9"),
        thesis="Exit",
    )


def test_risk_engine_approves_valid_long() -> None:
    """AC-06.01: Risk Engine accepts only valid Decision Contracts."""
    engine = RiskEngine()
    decision = make_long_decision()
    result = engine.evaluate(decision)
    assert result.is_approved


def test_risk_engine_approves_hold() -> None:
    """AC-06.01: Risk Engine accepts only valid Decision Contracts."""
    engine = RiskEngine()
    decision = make_hold_decision()
    result = engine.evaluate(decision)
    assert result.is_approved


def test_risk_engine_approves_close() -> None:
    """AC-06.01: Risk Engine accepts only valid Decision Contracts."""
    engine = RiskEngine()
    decision = make_close_decision()
    result = engine.evaluate(decision)
    assert result.is_approved


def test_position_sizing_deterministic() -> None:
    """AC-06.02: Position sizing is deterministic."""
    engine = RiskEngine()
    decision = make_long_decision()
    result1 = engine.evaluate(decision)
    result2 = engine.evaluate(decision)
    assert result1.approved_quantity == result2.approved_quantity


def test_position_sizing_uses_stop_loss() -> None:
    """AC-06.02: Position sizing is deterministic."""
    engine = RiskEngine()
    decision = make_long_decision(
        entry_price=Decimal("50.00"),
        stop_loss=Decimal("48.00"),
    )
    result = engine.evaluate(decision)
    assert result.approved_quantity > 0


def test_low_confidence_rejected() -> None:
    """AC-06.08: Risk rejection produces a machine-readable reason code."""
    engine = RiskEngine()
    decision = make_long_decision(confidence=Decimal("0.3"))
    result = engine.evaluate(decision)
    assert not result.is_approved
    assert any(v.code == "LOW_CONFIDENCE" for v in result.violations)


def test_kill_switch_blocks() -> None:
    """AC-06.07: Kill switch blocks new orders."""
    engine = RiskEngine()
    engine.activate_kill_switch()
    decision = make_long_decision()
    result = engine.evaluate(decision)
    assert not result.is_approved
    assert any(v.code == "KILL_SWITCH_ACTIVE" for v in result.violations)


def test_kill_switch_does_not_block_hold() -> None:
    """AC-06.07: Kill switch blocks new orders."""
    engine = RiskEngine()
    engine.activate_kill_switch()
    decision = make_hold_decision()
    result = engine.evaluate(decision)
    assert result.is_approved


def test_max_positions_enforced() -> None:
    """AC-06.04: Maximum position size is enforced."""
    limits = RiskLimits(max_simultaneous_positions=1)
    engine = RiskEngine(limits=limits)
    engine.record_position_open()
    decision = make_long_decision()
    result = engine.evaluate(decision)
    assert not result.is_approved
    assert any(v.code == "MAX_POSITIONS" for v in result.violations)


def test_daily_loss_limit_enforced() -> None:
    """AC-06.06: Daily loss limit is enforced."""
    limits = RiskLimits(max_daily_loss_pct=Decimal("0.05"))
    engine = RiskEngine(limits=limits)
    engine.record_daily_pnl(Decimal("-6.00"))
    decision = make_long_decision()
    result = engine.evaluate(decision)
    assert not result.is_approved
    assert any(v.code == "DAILY_LOSS_LIMIT" for v in result.violations)


def test_risk_limits_properties() -> None:
    """AC-06.11: Critical financial calculations use Decimal."""
    limits = RiskLimits(reference_capital=Decimal("100.00"))
    assert limits.max_risk_per_trade == Decimal("1.00")
    assert limits.max_daily_loss == Decimal("5.00")
    assert limits.max_position_size == Decimal("20.00")
    assert limits.max_exposure == Decimal("100.00")


def test_risk_decision_has_reason_codes() -> None:
    """AC-06.08: Risk rejection produces a machine-readable reason code."""
    engine = RiskEngine()
    decision = make_long_decision(confidence=Decimal("0.1"))
    result = engine.evaluate(decision)
    assert len(result.reasons) > 0
    assert len(result.violations) > 0


def test_unapproved_order_cannot_reach_execution() -> None:
    """AC-06.10: An unapproved order can never reach Execution Engine."""
    engine = RiskEngine()
    engine.activate_kill_switch()
    decision = make_long_decision()
    result = engine.evaluate(decision)
    assert not result.is_approved


class TestCircuitBreakerCooldown:

    def test_circuit_breaker_auto_clears_after_cooldown(self) -> None:
        """Circuit breaker auto-clears after cooldown with recovery."""
        import time as _time
        limits = RiskLimits(
            reference_capital=Decimal("100"),
            circuit_breaker_drawdown_pct=Decimal("0.10"),
            circuit_breaker_cooldown_minutes=0,  # immediate
            circuit_breaker_reentry_equity_recovery_pct=Decimal("0.01"),
        )
        engine = RiskEngine(limits=limits)
        engine.update_equity(Decimal("80"))  # drawdown 20% → trip CB
        assert engine._circuit_breaker_active
        assert engine.is_kill_switch_active()

        engine.update_equity(Decimal("82"))  # recovered 2.5% from 80 → >1%
        assert not engine._circuit_breaker_active
        assert not engine.is_kill_switch_active()

    def test_circuit_breaker_does_not_clear_without_recovery(self) -> None:
        """CB stays active when equity hasn't recovered from trough."""
        limits = RiskLimits(
            reference_capital=Decimal("100"),
            circuit_breaker_drawdown_pct=Decimal("0.05"),
            circuit_breaker_cooldown_minutes=0,
            circuit_breaker_reentry_equity_recovery_pct=Decimal("0.05"),
        )
        engine = RiskEngine(limits=limits)
        engine.update_equity(Decimal("90"))  # 10% drawdown → trip
        assert engine._circuit_breaker_active

        engine.update_equity(Decimal("91"))  # small recovery, below threshold
        assert engine._circuit_breaker_active, "CB should stay active (recovery < 5%)"


class TestPositionSizingByQuality:

    def test_high_confidence_and_setup_sizes_full(self) -> None:
        """High confidence + high setup score → full size."""
        engine = RiskEngine()
        decision = make_long_decision(confidence=Decimal("0.90"))
        result = engine.evaluate(decision, setup_score=85)
        assert result.approved_quantity > 0

    def test_low_confidence_reduces_size(self) -> None:
        """Low confidence → smaller position."""
        engine = RiskEngine()
        decision = make_long_decision(confidence=Decimal("0.55"))
        result = engine.evaluate(decision, setup_score=50)
        high = make_long_decision(confidence=Decimal("0.90"))
        result_high = engine.evaluate(high, setup_score=85)
        assert result.approved_quantity <= result_high.approved_quantity

    def test_daily_loss_reduces_size(self) -> None:
        """Daily loss at warn threshold → size reduction."""
        limits = RiskLimits(
            reference_capital=Decimal("100"),
            max_risk_per_trade_pct=Decimal("0.01"),
            max_position_size_pct=Decimal("1.0"),
            daily_loss_warn_pct=Decimal("0.03"),
            daily_loss_strong_pct=Decimal("0.04"),
            daily_loss_block_pct=Decimal("0.05"),
        )
        engine = RiskEngine(limits=limits)
        engine.record_daily_pnl(Decimal("-3.50"))
        decision = make_long_decision(confidence=Decimal("0.90"))
        result = engine.evaluate(decision, setup_score=80)
        assert result.approved_quantity > 0

        engine2 = RiskEngine(limits=limits)
        decision2 = make_long_decision(confidence=Decimal("0.90"))
        result2 = engine2.evaluate(decision2, setup_score=80)
        assert result.approved_quantity <= result2.approved_quantity
