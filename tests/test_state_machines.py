"""Tests for AEGIS state machines."""

from __future__ import annotations

import pytest

from aegis.domain.enums import OrderStatus, PositionStatus, AIRunStatus, SystemStatus
from aegis.domain.state_machines import (
    OrderStateMachine,
    PositionStateMachine,
    AIRunStateMachine,
    SystemStateMachine,
    InvalidStateTransition,
)


# Order State Machine Tests


class TestOrderStateMachine:
    """AC-02.03: Order State Machine has explicit valid transitions."""

    def test_initial_status_is_created(self) -> None:
        sm = OrderStateMachine()
        assert sm.status == OrderStatus.CREATED

    def test_created_to_submitted(self) -> None:
        sm = OrderStateMachine()
        sm.transition(OrderStatus.SUBMITTED)
        assert sm.status == OrderStatus.SUBMITTED

    def test_submitted_to_acknowledged(self) -> None:
        sm = OrderStateMachine(OrderStatus.SUBMITTED)
        sm.transition(OrderStatus.ACKNOWLEDGED)
        assert sm.status == OrderStatus.ACKNOWLEDGED

    def test_acknowledged_to_partially_filled(self) -> None:
        sm = OrderStateMachine(OrderStatus.ACKNOWLEDGED)
        sm.transition(OrderStatus.PARTIALLY_FILLED)
        assert sm.status == OrderStatus.PARTIALLY_FILLED

    def test_partially_filled_to_filled(self) -> None:
        sm = OrderStateMachine(OrderStatus.PARTIALLY_FILLED)
        sm.transition(OrderStatus.FILLED)
        assert sm.status == OrderStatus.FILLED

    def test_acknowledged_to_filled(self) -> None:
        sm = OrderStateMachine(OrderStatus.ACKNOWLEDGED)
        sm.transition(OrderStatus.FILLED)
        assert sm.status == OrderStatus.FILLED

    def test_created_to_cancelled(self) -> None:
        sm = OrderStateMachine()
        sm.transition(OrderStatus.CANCELLED)
        assert sm.status == OrderStatus.CANCELLED

    def test_created_to_rejected(self) -> None:
        sm = OrderStateMachine()
        sm.transition(OrderStatus.REJECTED)
        assert sm.status == OrderStatus.REJECTED

    def test_terminal_filled_cannot_transition(self) -> None:
        sm = OrderStateMachine(OrderStatus.FILLED)
        assert not sm.can_transition(OrderStatus.SUBMITTED)

    def test_terminal_cancelled_cannot_transition(self) -> None:
        sm = OrderStateMachine(OrderStatus.CANCELLED)
        assert not sm.can_transition(OrderStatus.SUBMITTED)


class TestOrderStateMachineInvalidTransitions:
    """AC-02.06: Invalid state transitions are rejected."""

    def test_created_to_filled_rejected(self) -> None:
        sm = OrderStateMachine()
        with pytest.raises(InvalidStateTransition):
            sm.transition(OrderStatus.FILLED)

    def test_submitted_to_filled_rejected(self) -> None:
        sm = OrderStateMachine(OrderStatus.SUBMITTED)
        with pytest.raises(InvalidStateTransition):
            sm.transition(OrderStatus.FILLED)

    def test_filled_to_submitted_rejected(self) -> None:
        sm = OrderStateMachine(OrderStatus.FILLED)
        with pytest.raises(InvalidStateTransition):
            sm.transition(OrderStatus.SUBMITTED)

    def test_cancelled_to_any_rejected(self) -> None:
        sm = OrderStateMachine(OrderStatus.CANCELLED)
        with pytest.raises(InvalidStateTransition):
            sm.transition(OrderStatus.SUBMITTED)


# Position State Machine Tests


class TestPositionStateMachine:
    """AC-02.04: Position State Machine has explicit valid transitions."""

    def test_initial_status_is_none(self) -> None:
        sm = PositionStateMachine()
        assert sm.status == PositionStatus.NONE

    def test_none_to_opening(self) -> None:
        sm = PositionStateMachine()
        sm.transition(PositionStatus.OPENING)
        assert sm.status == PositionStatus.OPENING

    def test_opening_to_open(self) -> None:
        sm = PositionStateMachine(PositionStatus.OPENING)
        sm.transition(PositionStatus.OPEN)
        assert sm.status == PositionStatus.OPEN

    def test_open_to_closing(self) -> None:
        sm = PositionStateMachine(PositionStatus.OPEN)
        sm.transition(PositionStatus.CLOSING)
        assert sm.status == PositionStatus.CLOSING

    def test_closing_to_closed(self) -> None:
        sm = PositionStateMachine(PositionStatus.CLOSING)
        sm.transition(PositionStatus.CLOSED)
        assert sm.status == PositionStatus.CLOSED

    def test_open_to_closed(self) -> None:
        sm = PositionStateMachine(PositionStatus.OPEN)
        sm.transition(PositionStatus.CLOSED)
        assert sm.status == PositionStatus.CLOSED

    def test_terminal_closed_cannot_transition(self) -> None:
        sm = PositionStateMachine(PositionStatus.CLOSED)
        assert not sm.can_transition(PositionStatus.OPENING)


class TestPositionStateMachineInvalidTransitions:
    """AC-02.06: Invalid state transitions are rejected."""

    def test_none_to_open_rejected(self) -> None:
        sm = PositionStateMachine()
        with pytest.raises(InvalidStateTransition):
            sm.transition(PositionStatus.OPEN)

    def test_opening_to_closing_rejected(self) -> None:
        sm = PositionStateMachine(PositionStatus.OPENING)
        with pytest.raises(InvalidStateTransition):
            sm.transition(PositionStatus.CLOSING)

    def test_closed_to_opening_rejected(self) -> None:
        sm = PositionStateMachine(PositionStatus.CLOSED)
        with pytest.raises(InvalidStateTransition):
            sm.transition(PositionStatus.OPENING)


# AI Run State Machine Tests


class TestAIRunStateMachine:
    """AC-02.05: Decision State Machine has explicit valid transitions."""

    def test_initial_status_is_created(self) -> None:
        sm = AIRunStateMachine()
        assert sm.status == AIRunStatus.CREATED

    def test_created_to_running(self) -> None:
        sm = AIRunStateMachine()
        sm.transition(AIRunStatus.RUNNING)
        assert sm.status == AIRunStatus.RUNNING

    def test_running_to_completed(self) -> None:
        sm = AIRunStateMachine(AIRunStatus.RUNNING)
        sm.transition(AIRunStatus.COMPLETED)
        assert sm.status == AIRunStatus.COMPLETED

    def test_running_to_failed(self) -> None:
        sm = AIRunStateMachine(AIRunStatus.RUNNING)
        sm.transition(AIRunStatus.FAILED)
        assert sm.status == AIRunStatus.FAILED

    def test_running_to_timeout(self) -> None:
        sm = AIRunStateMachine(AIRunStatus.RUNNING)
        sm.transition(AIRunStatus.TIMEOUT)
        assert sm.status == AIRunStatus.TIMEOUT

    def test_created_to_rejected(self) -> None:
        sm = AIRunStateMachine()
        sm.transition(AIRunStatus.REJECTED)
        assert sm.status == AIRunStatus.REJECTED

    def test_terminal_completed_cannot_transition(self) -> None:
        sm = AIRunStateMachine(AIRunStatus.COMPLETED)
        assert not sm.can_transition(AIRunStatus.RUNNING)


class TestAIRunStateMachineInvalidTransitions:
    """AC-02.06: Invalid state transitions are rejected."""

    def test_created_to_completed_rejected(self) -> None:
        sm = AIRunStateMachine()
        with pytest.raises(InvalidStateTransition):
            sm.transition(AIRunStatus.COMPLETED)

    def test_completed_to_running_rejected(self) -> None:
        sm = AIRunStateMachine(AIRunStatus.COMPLETED)
        with pytest.raises(InvalidStateTransition):
            sm.transition(AIRunStatus.RUNNING)


# System State Machine Tests


class TestSystemStateMachine:
    """System state machine tests."""

    def test_initial_status_is_running(self) -> None:
        sm = SystemStateMachine()
        assert sm.status == SystemStatus.RUNNING

    def test_running_to_paused(self) -> None:
        sm = SystemStateMachine()
        sm.transition(SystemStatus.PAUSED)
        assert sm.status == SystemStatus.PAUSED

    def test_running_to_emergency_stop(self) -> None:
        sm = SystemStateMachine()
        sm.transition(SystemStatus.EMERGENCY_STOP)
        assert sm.status == SystemStatus.EMERGENCY_STOP

    def test_paused_to_running(self) -> None:
        sm = SystemStateMachine(SystemStatus.PAUSED)
        sm.transition(SystemStatus.RUNNING)
        assert sm.status == SystemStatus.RUNNING

    def test_paused_to_emergency_stop(self) -> None:
        sm = SystemStateMachine(SystemStatus.PAUSED)
        sm.transition(SystemStatus.EMERGENCY_STOP)
        assert sm.status == SystemStatus.EMERGENCY_STOP

    def test_terminal_emergency_stop_cannot_transition(self) -> None:
        sm = SystemStateMachine(SystemStatus.EMERGENCY_STOP)
        assert not sm.can_transition(SystemStatus.RUNNING)
