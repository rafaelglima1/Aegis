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
