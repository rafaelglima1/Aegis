"""Tests for AEGIS domain enums."""

from __future__ import annotations

from aegis.domain.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionStatus,
    TradingAction,
    AIRunStatus,
    SystemStatus,
)


def test_order_side_values() -> None:
    """AC-02.02: Domain enums are centralized."""
    assert OrderSide.BUY == "BUY"
    assert OrderSide.SELL == "SELL"


def test_order_status_values() -> None:
    """AC-02.02: Domain enums are centralized."""
    assert OrderStatus.CREATED == "CREATED"
    assert OrderStatus.SUBMITTED == "SUBMITTED"
    assert OrderStatus.ACKNOWLEDGED == "ACKNOWLEDGED"
    assert OrderStatus.PARTIALLY_FILLED == "PARTIALLY_FILLED"
    assert OrderStatus.FILLED == "FILLED"
    assert OrderStatus.CANCELLED == "CANCELLED"
    assert OrderStatus.REJECTED == "REJECTED"
    assert OrderStatus.EXPIRED == "EXPIRED"
    assert OrderStatus.ERROR == "ERROR"


def test_position_status_values() -> None:
    """AC-02.02: Domain enums are centralized."""
    assert PositionStatus.NONE == "NONE"
    assert PositionStatus.OPENING == "OPENING"
    assert PositionStatus.OPEN == "OPEN"
    assert PositionStatus.CLOSING == "CLOSING"
    assert PositionStatus.CLOSED == "CLOSED"


def test_trading_action_values() -> None:
    """AC-02.02: Domain enums are centralized."""
    assert TradingAction.LONG == "LONG"
    assert TradingAction.HOLD == "HOLD"
    assert TradingAction.CLOSE == "CLOSE"


def test_ai_run_status_values() -> None:
    """AC-02.02: Domain enums are centralized."""
    assert AIRunStatus.CREATED == "CREATED"
    assert AIRunStatus.RUNNING == "RUNNING"
    assert AIRunStatus.COMPLETED == "COMPLETED"
    assert AIRunStatus.FAILED == "FAILED"
    assert AIRunStatus.TIMEOUT == "TIMEOUT"
    assert AIRunStatus.REJECTED == "REJECTED"


def test_system_status_values() -> None:
    """AC-02.02: Domain enums are centralized."""
    assert SystemStatus.RUNNING == "RUNNING"
    assert SystemStatus.PAUSED == "PAUSED"
    assert SystemStatus.EMERGENCY_STOP == "EMERGENCY_STOP"
