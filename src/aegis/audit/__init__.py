"""AEGIS Audit, Observability & Security package."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from aegis.domain.contracts import utc_now


class AuditEventType(Enum):
    """Audit event types."""

    DECISION = "DECISION"
    RISK = "RISK"
    ORDER = "ORDER"
    FILL = "FILL"
    PORTFOLIO = "PORTFOLIO"
    SYSTEM = "SYSTEM"
    ERROR = "ERROR"


@dataclass
class AuditEvent:
    """AC-13.01: Every critical operation produces an audit event.
    AC-13.02: correlation_id enables end-to-end operation tracing.
    AC-13.03: Audit records decision, risk, order and outcome."""

    event_id: UUID = field(default_factory=uuid4)
    correlation_id: UUID = field(default_factory=uuid4)
    event_type: AuditEventType = AuditEventType.SYSTEM
    timestamp: Any = field(default_factory=utc_now)
    component: str = ""
    action: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None


class AuditLogger:
    """AC-13.01: Every critical operation produces an audit event.
    AC-13.02: correlation_id enables end-to-end operation tracing."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._logger = logging.getLogger("aegis.audit")

    def record(self, event: AuditEvent) -> None:
        """Record an audit event — auto-sanitize secrets via SecurityGuard."""
        sanitized_data = SecurityGuard.sanitize(event.data)
        event.data = sanitized_data
        self._events.append(event)
        self._logger.info(
            "audit_event",
            extra={
                "event_id": str(event.event_id),
                "correlation_id": str(event.correlation_id),
                "event_type": event.event_type.value,
                "component": event.component,
                "action": event.action,
                "success": event.success,
            },
        )

    def record_event(
        self,
        event_type: AuditEventType,
        correlation_id: UUID,
        data: dict[str, Any],
        component: str = "",
        action: str = "",
        success: bool = True,
    ) -> AuditEvent:
        """Generic event recording — used by pipeline."""
        event = AuditEvent(
            correlation_id=correlation_id,
            event_type=event_type,
            component=component,
            action=action,
            data=data,
            success=success,
        )
        self.record(event)
        return event

    def record_decision(
        self,
        correlation_id: UUID,
        component: str,
        action: str,
        data: dict[str, Any],
        success: bool = True,
    ) -> AuditEvent:
        """AC-13.03: Audit records decision, risk, order and outcome."""
        event = AuditEvent(
            correlation_id=correlation_id,
            event_type=AuditEventType.DECISION,
            component=component,
            action=action,
            data=data,
            success=success,
        )
        self.record(event)
        return event

    def record_risk(
        self,
        correlation_id: UUID,
        component: str,
        action: str,
        data: dict[str, Any],
        success: bool = True,
    ) -> AuditEvent:
        event = AuditEvent(
            correlation_id=correlation_id,
            event_type=AuditEventType.RISK,
            component=component,
            action=action,
            data=data,
            success=success,
        )
        self.record(event)
        return event

    def record_order(
        self,
        correlation_id: UUID,
        component: str,
        action: str,
        data: dict[str, Any],
        success: bool = True,
    ) -> AuditEvent:
        event = AuditEvent(
            correlation_id=correlation_id,
            event_type=AuditEventType.ORDER,
            component=component,
            action=action,
            data=data,
            success=success,
        )
        self.record(event)
        return event

    def get_events_by_correlation(self, correlation_id: UUID) -> list[AuditEvent]:
        """AC-13.13: A trading operation can be reconstructed from the audit trail."""
        return [e for e in self._events if e.correlation_id == correlation_id]

    @property
    def events(self) -> list[AuditEvent]:
        return self._events.copy()


class MetricsCollector:
    """AC-13.05: Required operational metrics are available."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}

    def increment(self, name: str, value: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + value

    def set_gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value

    def record_histogram(self, name: str, value: float) -> None:
        if name not in self._histograms:
            self._histograms[name] = []
        self._histograms[name].append(value)

    def get_counter(self, name: str) -> int:
        return self._counters.get(name, 0)

    def get_gauge(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    def get_histogram(self, name: str) -> dict[str, float]:
        values = self._histograms.get(name, [])
        if not values:
            return {"count": 0, "min": 0, "max": 0, "avg": 0}
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
        }


class SecurityGuard:
    """AC-13.09: Secrets never appear in logs.
    AC-13.10: Secrets never appear in audit records.
    AC-13.11: Secrets never reach the frontend."""

    SECRET_PATTERNS = [
        re.compile(r"api[_-]?key", re.IGNORECASE),
        re.compile(r"api[_-]?secret", re.IGNORECASE),
        re.compile(r"password", re.IGNORECASE),
        re.compile(r"token", re.IGNORECASE),
        re.compile(r"secret", re.IGNORECASE),
    ]

    @classmethod
    def sanitize(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Remove secrets from data."""
        sanitized = {}
        for key, value in data.items():
            if cls._is_secret_key(key):
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, dict):
                sanitized[key] = cls.sanitize(value)
            else:
                sanitized[key] = value
        return sanitized

    @classmethod
    def _is_secret_key(cls, key: str) -> bool:
        return any(pattern.search(key) for pattern in cls.SECRET_PATTERNS)

    @classmethod
    def check_for_secrets(cls, text: str) -> list[str]:
        """AC-13.12: Security scan finds no hardcoded secrets."""
        found = []
        for pattern in cls.SECRET_PATTERNS:
            if pattern.search(text):
                found.append(pattern.pattern)
        return found
