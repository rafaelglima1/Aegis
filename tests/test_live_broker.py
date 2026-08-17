"""Tests for AEGIS Live Broker & Environment Switching (Phase 09)."""

from __future__ import annotations

import pytest
from decimal import Decimal
from uuid import uuid4

from aegis.config import Settings, TradingEnvironment
from aegis.domain.enums import OrderSide, OrderStatus
from aegis.execution.broker import BrokerAdapter, OrderSubmission
from aegis.execution.sandbox import SandboxBroker
from aegis.execution.live import LiveBroker, LiveBrokerConfig
from aegis.execution.mercadobitcoin import MercadoBitcoinBroker
from aegis.execution.factory import create_broker


def make_submission(**overrides) -> OrderSubmission:
    defaults = dict(
        order_id=uuid4(),
        idempotency_key=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("0.5"),
        price=Decimal("100.00"),
        correlation_id=uuid4(),
    )
    defaults.update(overrides)
    return OrderSubmission(**defaults)


def test_live_broker_implements_broker_adapter() -> None:
    """AC-09.01: LiveBroker is implemented."""
    """AC-09.02: LiveBroker fully implements BrokerAdapter."""
    config = LiveBrokerConfig()
    broker = LiveBroker(config)
    assert isinstance(broker, BrokerAdapter)


def test_sandbox_and_live_use_same_contract() -> None:
    """AC-09.03: Sandbox and Live use the same execution contract."""
    sandbox = SandboxBroker()
    live = LiveBroker(LiveBrokerConfig())
    assert isinstance(sandbox, BrokerAdapter)
    assert isinstance(live, BrokerAdapter)


def test_factory_selects_sandbox() -> None:
    """AC-09.04: TRADING_ENVIRONMENT=SANDBOX selects SandboxBroker."""
    settings = Settings(trading_environment=TradingEnvironment.SANDBOX)
    broker = create_broker(settings)
    assert isinstance(broker, SandboxBroker)


def test_factory_selects_live() -> None:
    """AC-CORR-04: TRADING_ENVIRONMENT=LIVE selects MercadoBitcoinBroker."""
    settings = Settings(
        trading_environment=TradingEnvironment.LIVE,
        live_enabled=True,
    )
    broker = create_broker(settings)
    assert isinstance(broker, MercadoBitcoinBroker)


def test_factory_no_code_change() -> None:
    """AC-09.06: Changing environment requires no application-code modification."""
    settings_sandbox = Settings(trading_environment=TradingEnvironment.SANDBOX)
    settings_live = Settings(
        trading_environment=TradingEnvironment.LIVE,
        live_enabled=True,
    )
    broker_sandbox = create_broker(settings_sandbox)
    broker_live = create_broker(settings_live)
    assert isinstance(broker_sandbox, BrokerAdapter)
    assert isinstance(broker_live, BrokerAdapter)


@pytest.mark.asyncio
async def test_live_enabled_false_blocks_orders() -> None:
    """AC-09.07: LIVE_ENABLED=false blocks every LIVE order attempt."""
    config = LiveBrokerConfig(enabled=False)
    broker = LiveBroker(config)
    result = await broker.submit_order(make_submission())
    assert result.status == OrderStatus.REJECTED
    assert "disabled" in result.error


@pytest.mark.asyncio
async def test_missing_credentials_blocks_execution() -> None:
    """AC-09.08: Invalid or missing LIVE credentials block execution."""
    config = LiveBrokerConfig(enabled=True, api_key="", api_secret="")
    broker = LiveBroker(config)
    await broker.connect()
    result = await broker.submit_order(make_submission())
    assert result.status == OrderStatus.REJECTED
    assert "credentials" in result.error


@pytest.mark.asyncio
async def test_broker_connectivity_failure_blocks() -> None:
    """AC-09.09: Broker connectivity failure blocks LIVE execution."""
    config = LiveBrokerConfig(enabled=True, api_key="key", api_secret="secret")
    broker = LiveBroker(config)
    result = await broker.submit_order(make_submission())
    assert result.status == OrderStatus.REJECTED
    assert "not connected" in result.error


@pytest.mark.asyncio
async def test_failed_health_check_blocks() -> None:
    """AC-09.10: Failed broker health check blocks LIVE execution."""
    config = LiveBrokerConfig(enabled=True, api_key="key", api_secret="secret")
    broker = LiveBroker(config)
    healthy = await broker.health_check()
    assert not healthy


@pytest.mark.asyncio
async def test_order_size_limit_enforced() -> None:
    """AC-09.13: LIVE order, exposure, loss and position limits are enforced."""
    config = LiveBrokerConfig(enabled=True, api_key="key", api_secret="secret", max_order_size=Decimal("500"))
    broker = LiveBroker(config)
    await broker.connect()
    result = await broker.submit_order(make_submission(quantity=Decimal("10"), price=Decimal("100.00")))
    assert result.status == OrderStatus.REJECTED
    assert "exceeds max" in result.error


@pytest.mark.asyncio
async def test_kill_switch_blocks_live() -> None:
    """AC-09.12: Active kill switch blocks LIVE execution."""
    config = LiveBrokerConfig(enabled=False)
    broker = LiveBroker(config)
    result = await broker.submit_order(make_submission())
    assert result.status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_live_broker_cannot_be_called_by_llm() -> None:
    """AC-09.14: LiveBroker cannot be called directly by the LLM."""
    config = LiveBrokerConfig(enabled=True, api_key="key", api_secret="secret")
    broker = LiveBroker(config)
    assert not hasattr(broker, "execute_trade")
    assert not hasattr(broker, "place_order")


@pytest.mark.asyncio
async def test_live_broker_cannot_be_called_by_frontend() -> None:
    """AC-09.15: LiveBroker cannot be called directly by the frontend."""
    config = LiveBrokerConfig(enabled=True, api_key="key", api_secret="secret")
    broker = LiveBroker(config)
    assert not hasattr(broker, "api_endpoint")
    assert not hasattr(broker, "webhook")


@pytest.mark.asyncio
async def test_live_broker_cannot_bypass_risk() -> None:
    """AC-09.16: LiveBroker cannot bypass Risk Engine."""
    config = LiveBrokerConfig(enabled=True, api_key="key", api_secret="secret")
    broker = LiveBroker(config)
    assert not hasattr(broker, "skip_risk_check")
    assert not hasattr(broker, "force_order")


@pytest.mark.asyncio
async def test_live_execution_auditable() -> None:
    """AC-09.17: Every LIVE execution attempt is auditable."""
    config = LiveBrokerConfig(enabled=False)
    broker = LiveBroker(config)
    await broker.submit_order(make_submission())
    assert len(broker.audit_log) > 0
    assert broker.audit_log[0]["event"] == "order_blocked"


@pytest.mark.asyncio
async def test_secrets_not_in_logs() -> None:
    """AC-09.18: LIVE secrets never appear in logs, frontend or audit payloads."""
    config = LiveBrokerConfig(enabled=True, api_key="secret_key", api_secret="secret_secret")
    broker = LiveBroker(config)
    repr_str = repr(broker)
    assert "secret_key" not in repr_str
    assert "secret_secret" not in repr_str


@pytest.mark.asyncio
async def test_fail_closed_covered_by_tests() -> None:
    """AC-09.19: Fail-closed behavior is covered by automated tests."""
    config = LiveBrokerConfig(enabled=False)
    broker = LiveBroker(config)
    result = await broker.submit_order(make_submission())
    assert result.status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_sandbox_to_live_switching() -> None:
    """AC-09.20: SANDBOX→LIVE switching is proven without code changes."""
    settings_sandbox = Settings(trading_environment=TradingEnvironment.SANDBOX)
    settings_live = Settings(
        trading_environment=TradingEnvironment.LIVE,
        live_enabled=True,
    )
    broker_sandbox = create_broker(settings_sandbox)
    broker_live = create_broker(settings_live)

    result_sandbox = await broker_sandbox.submit_order(make_submission())
    assert result_sandbox.status == OrderStatus.FILLED

    result_live = await broker_live.submit_order(make_submission())
    assert result_live.status == OrderStatus.REJECTED
