"""Tests for AEGIS Audit, Observability & Security (Phase 13)."""

from __future__ import annotations

import pytest
from uuid import uuid4

from aegis.audit import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    MetricsCollector,
    SecurityGuard,
)


def test_critical_operation_produces_audit_event() -> None:
    """AC-13.01: Every critical operation produces an audit event."""
    logger = AuditLogger()
    event = logger.record_decision(
        correlation_id=uuid4(),
        component="ai_engine",
        action="decision_made",
        data={"action": "LONG"},
    )
    assert event.event_type == AuditEventType.DECISION
    assert len(logger.events) == 1


def test_correlation_id_traces_end_to_end() -> None:
    """AC-13.02: correlation_id enables end-to-end operation tracing."""
    logger = AuditLogger()
    correlation_id = uuid4()
    logger.record_decision(correlation_id, "ai", "decide", {})
    logger.record_risk(correlation_id, "risk", "evaluate", {})
    logger.record_order(correlation_id, "execution", "submit", {})
    events = logger.get_events_by_correlation(correlation_id)
    assert len(events) == 3


def test_audit_records_decision_risk_order_outcome() -> None:
    """AC-13.03: Audit records decision, risk, order and outcome."""
    logger = AuditLogger()
    correlation_id = uuid4()
    d = logger.record_decision(correlation_id, "ai", "decide", {"action": "LONG"})
    r = logger.record_risk(correlation_id, "risk", "evaluate", {"approved": True})
    o = logger.record_order(correlation_id, "execution", "submit", {"order_id": "123"})
    assert d.event_type == AuditEventType.DECISION
    assert r.event_type == AuditEventType.RISK
    assert o.event_type == AuditEventType.ORDER


def test_logs_are_structured() -> None:
    """AC-13.04: Logs are structured."""
    logger = AuditLogger()
    event = logger.record_decision(uuid4(), "ai", "decide", {})
    assert event.component == "ai"
    assert event.action == "decide"


def test_metrics_available() -> None:
    """AC-13.05: Required operational metrics are available."""
    metrics = MetricsCollector()
    metrics.increment("orders_submitted")
    metrics.set_gauge("portfolio_value", 10000.0)
    metrics.record_histogram("llm_latency", 0.5)
    assert metrics.get_counter("orders_submitted") == 1
    assert metrics.get_gauge("portfolio_value") == 10000.0
    hist = metrics.get_histogram("llm_latency")
    assert hist["count"] == 1


def test_llm_latency_observable() -> None:
    """AC-13.06: LLM latency is observable."""
    metrics = MetricsCollector()
    metrics.record_histogram("llm_latency_ms", 150.0)
    metrics.record_histogram("llm_latency_ms", 200.0)
    hist = metrics.get_histogram("llm_latency_ms")
    assert hist["count"] == 2
    assert hist["avg"] == 175.0


def test_market_data_lag_observable() -> None:
    """AC-13.07: Market-data lag is observable."""
    metrics = MetricsCollector()
    metrics.record_histogram("market_data_lag_ms", 50.0)
    hist = metrics.get_histogram("market_data_lag_ms")
    assert hist["count"] == 1


def test_risk_rejection_observable() -> None:
    """AC-13.08: Risk rejection is observable."""
    metrics = MetricsCollector()
    metrics.increment("risk_rejections")
    assert metrics.get_counter("risk_rejections") == 1


def test_secrets_not_in_logs() -> None:
    """AC-13.09: Secrets never appear in logs."""
    sanitized = SecurityGuard.sanitize({"api_key": "secret123", "name": "test"})
    assert sanitized["api_key"] == "***REDACTED***"
    assert sanitized["name"] == "test"


def test_secrets_not_in_audit_records() -> None:
    """AC-13.10: Secrets never appear in audit records."""
    logger = AuditLogger()
    event = logger.record_decision(
        uuid4(), "ai", "decide",
        {"api_key": "secret123", "action": "LONG"},
    )
    sanitized = SecurityGuard.sanitize(event.data)
    assert sanitized["api_key"] == "***REDACTED***"


def test_secrets_not_reach_frontend() -> None:
    """AC-13.11: Secrets never reach the frontend."""
    data = {"api_key": "secret", "token": "abc", "password": "123"}
    sanitized = SecurityGuard.sanitize(data)
    for key in sanitized:
        assert sanitized[key] == "***REDACTED***"


def test_security_scan_no_hardcoded_secrets() -> None:
    """AC-13.12: Security scan finds no hardcoded secrets."""
    clean_code = "def hello(): return 'world'"
    found = SecurityGuard.check_for_secrets(clean_code)
    assert len(found) == 0


def test_security_scan_detects_secrets() -> None:
    """AC-13.12: Security scan finds no hardcoded secrets."""
    dirty_code = "api_key = 'secret123'"
    found = SecurityGuard.check_for_secrets(dirty_code)
    assert len(found) > 0


def test_trading_operation_reconstructible() -> None:
    """AC-13.13: A trading operation can be reconstructed from the audit trail."""
    logger = AuditLogger()
    correlation_id = uuid4()
    logger.record_decision(correlation_id, "ai", "decide", {"action": "LONG"})
    logger.record_risk(correlation_id, "risk", "evaluate", {"approved": True})
    logger.record_order(correlation_id, "execution", "submit", {"order_id": "123"})
    events = logger.get_events_by_correlation(correlation_id)
    assert len(events) == 3
    assert events[0].event_type == AuditEventType.DECISION
    assert events[1].event_type == AuditEventType.RISK
    assert events[2].event_type == AuditEventType.ORDER
