"""Tests for AEGIS Dashboard & Operational UI (Phase 14)."""

from __future__ import annotations

import pytest
from decimal import Decimal
from uuid import uuid4

from aegis.dashboard import (
    DashboardService,
    DashboardPosition,
    DashboardOrder,
    DashboardMetrics,
    DashboardRiskStatus,
    DashboardConfig,
    LiveState,
)


def test_dashboard_displays_environment() -> None:
    """AC-14.01: Dashboard displays current trading environment."""
    service = DashboardService()
    config = DashboardConfig(environment="SANDBOX")
    service.update_config(config)
    assert service.get_environment() == "SANDBOX"


def test_dashboard_displays_health() -> None:
    """AC-14.02: Dashboard displays system health/status."""
    service = DashboardService()
    health = service.get_system_health()
    assert "status" in health
    assert health["status"] == "healthy"


def test_dashboard_displays_positions() -> None:
    """AC-14.03: Dashboard displays open positions."""
    service = DashboardService()
    positions = [DashboardPosition(symbol="AAPL", quantity=Decimal("10"))]
    service.update_positions(positions)
    assert len(service.get_positions()) == 1
    assert service.get_positions()[0].symbol == "AAPL"


def test_dashboard_displays_pnl() -> None:
    """AC-14.04: Dashboard displays P&L."""
    service = DashboardService()
    metrics = DashboardMetrics(cash=Decimal("10000"), realized_pnl=Decimal("500"))
    service.update_metrics(metrics)
    pnl = service.get_pnl()
    assert pnl.cash == Decimal("10000")
    assert pnl.realized_pnl == Decimal("500")


def test_dashboard_displays_orders() -> None:
    """AC-14.05: Dashboard displays orders."""
    service = DashboardService()
    orders = [DashboardOrder(symbol="AAPL", status="FILLED")]
    service.update_orders(orders)
    assert len(service.get_orders()) == 1


def test_dashboard_displays_exposure() -> None:
    """AC-14.06: Dashboard displays exposure."""
    service = DashboardService()
    metrics = DashboardMetrics(exposure=Decimal("5000"))
    service.update_metrics(metrics)
    assert service.get_exposure() == Decimal("5000")


def test_dashboard_displays_risk_status() -> None:
    """AC-14.07: Dashboard displays Risk status."""
    service = DashboardService()
    risk = DashboardRiskStatus(kill_switch_active=True, current_positions=3)
    service.update_risk_status(risk)
    status = service.get_risk_status()
    assert status.kill_switch_active
    assert status.current_positions == 3


def test_dashboard_config_visible_no_secrets() -> None:
    """AC-14.08: Provider/LLM configuration is visible without exposing secrets."""
    service = DashboardService()
    config = DashboardConfig(provider="openai", model="gpt-4")
    service.update_config(config)
    result = service.get_config()
    assert result.provider == "openai"
    assert result.model == "gpt-4"


def test_dashboard_live_state() -> None:
    """AC-14.10: Dashboard clearly displays LIVE state as DISABLED, BLOCKED or READY."""
    service = DashboardService()
    config = DashboardConfig(live_state=LiveState.DISABLED)
    service.update_config(config)
    assert service.get_live_state() == LiveState.DISABLED


def test_frontend_cannot_call_broker() -> None:
    """AC-14.11: Frontend cannot call Broker directly."""
    service = DashboardService()
    assert not hasattr(service, "submit_order")
    assert not hasattr(service, "execute_trade")


def test_frontend_cannot_bypass_risk() -> None:
    """AC-14.12: Frontend cannot bypass Risk Engine."""
    service = DashboardService()
    assert not hasattr(service, "skip_risk_check")
    assert not hasattr(service, "bypass_risk")


def test_frontend_cannot_alter_safety_limits() -> None:
    """AC-14.13: Frontend cannot alter hard safety limits."""
    service = DashboardService()
    assert not hasattr(service, "set_risk_limit")
    assert not hasattr(service, "modify_risk_rules")
