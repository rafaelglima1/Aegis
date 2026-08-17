"""AEGIS domain state machines — explicit valid transitions."""

from __future__ import annotations

from aegis.domain.enums import OrderStatus, PositionStatus, AIRunStatus, SystemStatus, DecisionStatus


class InvalidStateTransition(Exception):
    """Raised when a state machine transition is invalid."""

    def __init__(self, entity: str, current: str, target: str) -> None:
        self.entity = entity
        self.current = current
        self.target = target
        super().__init__(f"Invalid {entity} transition: {current} -> {target}")


# Order State Machine
# Valid transitions per the blueprint:
#   CREATED -> SUBMITTED
#   SUBMITTED -> ACKNOWLEDGED
#   ACKNOWLEDGED -> PARTIALLY_FILLED
#   PARTIALLY_FILLED -> FILLED
# Terminal states: CANCELLED, REJECTED, EXPIRED, ERROR
ORDER_VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.SUBMITTED, OrderStatus.CANCELLED, OrderStatus.REJECTED},
    OrderStatus.SUBMITTED: {
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.ERROR,
    },
    OrderStatus.ACKNOWLEDGED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.ERROR,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.ERROR,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
    OrderStatus.ERROR: set(),
}


class OrderStateMachine:
    """Order state machine enforcing valid transitions."""

    def __init__(self, status: OrderStatus = OrderStatus.CREATED) -> None:
        self._status = status

    @property
    def status(self) -> OrderStatus:
        return self._status

    def transition(self, target: OrderStatus) -> OrderStatus:
        valid = ORDER_VALID_TRANSITIONS.get(self._status, set())
        if target not in valid:
            raise InvalidStateTransition("Order", self._status.value, target.value)
        self._status = target
        return self._status

    def can_transition(self, target: OrderStatus) -> bool:
        valid = ORDER_VALID_TRANSITIONS.get(self._status, set())
        return target in valid


# Position State Machine
# Valid transitions per the blueprint:
#   NONE -> OPENING
#   OPENING -> OPEN
#   OPEN -> CLOSING
#   CLOSING -> CLOSED
POSITION_VALID_TRANSITIONS: dict[PositionStatus, set[PositionStatus]] = {
    PositionStatus.NONE: {PositionStatus.OPENING},
    PositionStatus.OPENING: {PositionStatus.OPEN, PositionStatus.NONE},
    PositionStatus.OPEN: {PositionStatus.CLOSING, PositionStatus.CLOSED},
    PositionStatus.CLOSING: {PositionStatus.CLOSED},
    PositionStatus.CLOSED: set(),
}


class PositionStateMachine:
    """Position state machine enforcing valid transitions."""

    def __init__(self, status: PositionStatus = PositionStatus.NONE) -> None:
        self._status = status

    @property
    def status(self) -> PositionStatus:
        return self._status

    def transition(self, target: PositionStatus) -> PositionStatus:
        valid = POSITION_VALID_TRANSITIONS.get(self._status, set())
        if target not in valid:
            raise InvalidStateTransition("Position", self._status.value, target.value)
        self._status = target
        return self._status

    def can_transition(self, target: PositionStatus) -> bool:
        valid = POSITION_VALID_TRANSITIONS.get(self._status, set())
        return target in valid


# AI Run State Machine
# Valid transitions per the blueprint:
#   CREATED -> RUNNING
#   RUNNING -> COMPLETED
# Terminal states: FAILED, TIMEOUT, REJECTED
AIRUN_VALID_TRANSITIONS: dict[AIRunStatus, set[AIRunStatus]] = {
    AIRunStatus.CREATED: {AIRunStatus.RUNNING, AIRunStatus.REJECTED},
    AIRunStatus.RUNNING: {AIRunStatus.COMPLETED, AIRunStatus.FAILED, AIRunStatus.TIMEOUT},
    AIRunStatus.COMPLETED: set(),
    AIRunStatus.FAILED: set(),
    AIRunStatus.TIMEOUT: set(),
    AIRunStatus.REJECTED: set(),
}


class AIRunStateMachine:
    """AI Run state machine enforcing valid transitions."""

    def __init__(self, status: AIRunStatus = AIRunStatus.CREATED) -> None:
        self._status = status

    @property
    def status(self) -> AIRunStatus:
        return self._status

    def transition(self, target: AIRunStatus) -> AIRunStatus:
        valid = AIRUN_VALID_TRANSITIONS.get(self._status, set())
        if target not in valid:
            raise InvalidStateTransition("AIRun", self._status.value, target.value)
        self._status = target
        return self._status

    def can_transition(self, target: AIRunStatus) -> bool:
        valid = AIRUN_VALID_TRANSITIONS.get(self._status, set())
        return target in valid


# Decision State Machine (AC-02.05)
# Valid transitions:
#   DRAFT -> LLM_PENDING
#   LLM_PENDING -> LLM_COMPLETED | ERROR
#   LLM_COMPLETED -> RISK_PENDING
#   RISK_PENDING -> RISK_APPROVED | RISK_REJECTED
#   RISK_APPROVED -> EXECUTING | CANCELLED | EXPIRED
#   EXECUTING -> FILLED | ERROR | CANCELLED
#   Terminal: FILLED, CANCELLED, EXPIRED, ERROR, RISK_REJECTED
DECISION_VALID_TRANSITIONS: dict[DecisionStatus, set[DecisionStatus]] = {
    DecisionStatus.DRAFT: {DecisionStatus.LLM_PENDING, DecisionStatus.CANCELLED},
    DecisionStatus.LLM_PENDING: {DecisionStatus.LLM_COMPLETED, DecisionStatus.ERROR},
    DecisionStatus.LLM_COMPLETED: {DecisionStatus.RISK_PENDING, DecisionStatus.CANCELLED},
    DecisionStatus.RISK_PENDING: {DecisionStatus.RISK_APPROVED, DecisionStatus.RISK_REJECTED},
    DecisionStatus.RISK_APPROVED: {DecisionStatus.EXECUTING, DecisionStatus.CANCELLED, DecisionStatus.EXPIRED},
    DecisionStatus.EXECUTING: {DecisionStatus.FILLED, DecisionStatus.ERROR, DecisionStatus.CANCELLED},
    DecisionStatus.FILLED: set(),
    DecisionStatus.CANCELLED: set(),
    DecisionStatus.EXPIRED: set(),
    DecisionStatus.ERROR: set(),
    DecisionStatus.RISK_REJECTED: set(),
}


class DecisionStateMachine:
    """AC-02.05: Decision state machine enforcing valid transitions."""

    def __init__(self, status: DecisionStatus = DecisionStatus.DRAFT) -> None:
        self._status = status

    @property
    def status(self) -> DecisionStatus:
        return self._status

    def transition(self, target: DecisionStatus) -> DecisionStatus:
        valid = DECISION_VALID_TRANSITIONS.get(self._status, set())
        if target not in valid:
            raise InvalidStateTransition("Decision", self._status.value, target.value)
        self._status = target
        return self._status

    def can_transition(self, target: DecisionStatus) -> bool:
        valid = DECISION_VALID_TRANSITIONS.get(self._status, set())
        return target in valid


# System State Machine
# Valid transitions per the blueprint:
#   RUNNING -> PAUSED
#   RUNNING -> EMERGENCY_STOP
#   PAUSED -> RUNNING
#   PAUSED -> EMERGENCY_STOP
SYSTEM_VALID_TRANSITIONS: dict[SystemStatus, set[SystemStatus]] = {
    SystemStatus.RUNNING: {SystemStatus.PAUSED, SystemStatus.EMERGENCY_STOP},
    SystemStatus.PAUSED: {SystemStatus.RUNNING, SystemStatus.EMERGENCY_STOP},
    SystemStatus.EMERGENCY_STOP: set(),
}


class SystemStateMachine:
    """System state machine enforcing valid transitions."""

    def __init__(self, status: SystemStatus = SystemStatus.RUNNING) -> None:
        self._status = status

    @property
    def status(self) -> SystemStatus:
        return self._status

    def transition(self, target: SystemStatus) -> SystemStatus:
        valid = SYSTEM_VALID_TRANSITIONS.get(self._status, set())
        if target not in valid:
            raise InvalidStateTransition("System", self._status.value, target.value)
        self._status = target
        return self._status

    def can_transition(self, target: SystemStatus) -> bool:
        valid = SYSTEM_VALID_TRANSITIONS.get(self._status, set())
        return target in valid
