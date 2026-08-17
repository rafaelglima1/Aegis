"""AEGIS V1.3 - Correction #9.1 Tests.

Real hot reload mechanism + MercadoBitcoinBroker mocked HTTP tests.
"""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from aegis.domain.enums import OrderSide, OrderStatus, PositionSide
from aegis.execution.broker import OrderSubmission
from aegis.execution.engine import ExecutionEngine
from aegis.risk_engine.risk_engine import RiskDecision, RiskEngine
from aegis.risk_engine.risk_limits import RiskLimits


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _write_env(tmp_path: Path, **kwargs: Any) -> Path:
    """Write a .env.prod file with given key=value pairs."""
    env_file = tmp_path / ".env.prod"
    lines = [f"{k}={v}" for k, v in kwargs.items()]
    env_file.write_text("\n".join(lines) + "\n")
    return env_file


def _make_worker(tmp_path: Path, capital: str = "100.00", max_positions: str = "1") -> Any:
    """Create a worker with a temporary .env.prod file."""
    from aegis.config import Settings
    from aegis.worker import AutonomousWorker, _SETTINGS_FILE

    _write_env(tmp_path, TRADING_CAPITAL=capital, MAX_POSITIONS=max_positions)
    settings = Settings(initial_capital=Decimal(capital), max_positions=int(max_positions))
    worker = AutonomousWorker(settings=settings)

    # Patch _SETTINGS_FILE to point to temp dir
    import aegis.worker as worker_mod
    worker_mod._SETTINGS_FILE = tmp_path / ".env.prod"

    return worker


# ============================================================
# C9.1-TEST-01: Real _reload_config updates max_positions
# ============================================================


class TestRealReloadUpdatesMaxPositions:

    def test_reload_updates_max_positions(self, tmp_path: Path) -> None:
        """AC-C9.1-01: _reload_config() changes max_positions from env file."""
        worker = _make_worker(tmp_path, max_positions="1")
        assert worker.max_positions == 1

        # Write new config
        _write_env(tmp_path, TRADING_CAPITAL="100.00", MAX_POSITIONS="3")

        # Execute real reload mechanism
        worker._reload_config()

        assert worker.max_positions == 3


# ============================================================
# C9.1-TEST-02: Real _reload_config updates Settings.max_positions
# ============================================================


class TestRealReloadUpdatesSettings:

    def test_reload_updates_settings(self, tmp_path: Path) -> None:
        """AC-C9.1-02: _reload_config() creates Settings with correct max_positions."""
        worker = _make_worker(tmp_path, max_positions="1")
        assert worker._settings.max_positions == 1

        _write_env(tmp_path, TRADING_CAPITAL="100.00", MAX_POSITIONS="5")
        worker._reload_config()

        assert worker._settings.max_positions == 5
        assert worker.max_positions == 5


# ============================================================
# C9.1-TEST-03: Real _reload_config updates RiskEngine
# ============================================================


class TestRealReloadUpdatesRiskEngine:

    def test_reload_updates_risk_engine(self, tmp_path: Path) -> None:
        """AC-C9.1-03: Settings, Worker, and RiskEngine stay synchronized."""
        worker = _make_worker(tmp_path, max_positions="1")
        assert worker.risk_engine.limits.max_simultaneous_positions == 1

        _write_env(tmp_path, TRADING_CAPITAL="100.00", MAX_POSITIONS="7")
        worker._reload_config()

        assert worker.max_positions == 7
        assert worker._settings.max_positions == 7
        assert worker.risk_engine.limits.max_simultaneous_positions == 7


# ============================================================
# C9.1-TEST-04: Real hot reload preserves Portfolio cash
# ============================================================


class TestRealReloadPreservesPortfolioCash:

    def test_reload_preserves_portfolio_cash(self, tmp_path: Path) -> None:
        """AC-C9.1-04: Hot reload does not alter Portfolio cash."""
        worker = _make_worker(tmp_path, capital="100.00")

        # Simulate trading: reduce cash
        worker.portfolio.record_fill(
            asset="BTC-BRL", side=PositionSide.LONG,
            quantity=Decimal("0.001"), price=Decimal("50000.00"), fee=Decimal("0.50"),
        )
        original_cash = worker.portfolio.cash
        assert original_cash < Decimal("100.00")

        _write_env(tmp_path, TRADING_CAPITAL="500.00", MAX_POSITIONS="5")
        worker._reload_config()

        # Cash must not change
        assert worker.portfolio.cash == original_cash


# ============================================================
# C9.1-TEST-05: Real hot reload preserves Broker balance
# ============================================================


class TestRealReloadPreservesBrokerBalance:

    def test_reload_preserves_broker_balance(self, tmp_path: Path) -> None:
        """AC-C9.1-05: Hot reload does not alter Broker balance."""
        worker = _make_worker(tmp_path, capital="100.00")

        worker.portfolio.record_fill(
            asset="BTC-BRL", side=PositionSide.LONG,
            quantity=Decimal("0.001"), price=Decimal("50000.00"), fee=Decimal("0.50"),
        )
        original_balance = worker.broker.balance

        _write_env(tmp_path, TRADING_CAPITAL="999.00", MAX_POSITIONS="3")
        worker._reload_config()

        assert worker.broker.balance == original_balance


# ============================================================
# C9.1-TEST-06: TRADING_CAPITAL reload does not overwrite financial state
# ============================================================


class TestReloadCapitalNoOverwrite:

    def test_trading_capital_does_not_overwrite_state(self, tmp_path: Path) -> None:
        """AC-C9.1-07: TRADING_CAPITAL cannot overwrite current financial state."""
        worker = _make_worker(tmp_path, capital="100.00")

        worker.portfolio.record_fill(
            asset="BTC-BRL", side=PositionSide.LONG,
            quantity=Decimal("0.001"), price=Decimal("50000.00"), fee=Decimal("0.50"),
        )
        original_cash = worker.portfolio.cash
        original_balance = worker.broker.balance
        original_pnl = worker.portfolio.total_realized_pnl

        # Change capital in env file
        _write_env(tmp_path, TRADING_CAPITAL="500.00", MAX_POSITIONS="1")
        worker._reload_config()

        assert worker.portfolio.cash == original_cash
        assert worker.broker.balance == original_balance
        assert worker.portfolio.total_realized_pnl == original_pnl
        assert worker.portfolio.total_fees == Decimal("0.50")


# ============================================================
# C9.1-TEST-07: MercadoBitcoinBroker BUY sends side=buy
# ============================================================


class TestMBBrokerBuy:

    def test_buy_sends_side_buy(self) -> None:
        """AC-C9.1-08: MercadoBitcoinBroker BUY sends correct HTTP request."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig

        config = MercadoBitcoinConfig(
            api_key="test_key", api_secret="test_secret", enabled=True,
        )
        broker = MercadoBitcoinBroker(config)

        # Mock HTTP client
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "mb-order-123"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        broker._client = mock_client
        broker._access_token = "fake_token"
        broker._token_expiry = 9999999999.0

        result = run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )))

        assert result.status == OrderStatus.SUBMITTED

        # Verify POST /api/v4/orders was called
        mock_client.post.assert_called()
        call_args = mock_client.post.call_args
        assert "/api/v4/orders" in call_args[0][0]

        # Verify request body contains side=buy
        body = call_args[1].get("json") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("json")
        assert body is not None
        assert body["side"] == "buy"
        assert body["symbol"] == "BTC-BRL"
        assert body["quantity"] == "0.001"
        assert body["limit_price"] == "50000.00"


# ============================================================
# C9.1-TEST-08: MercadoBitcoinBroker SELL with LONG sends side=sell
# ============================================================


class TestMBBrokerSellClosesLong:

    def test_sell_closes_long_sends_side_sell(self) -> None:
        """AC-C9.1-09: MercadoBitcoinBroker SELL closing LONG sends correct request."""
        from unittest.mock import AsyncMock, MagicMock
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig

        config = MercadoBitcoinConfig(
            api_key="test_key", api_secret="test_secret", enabled=True,
        )
        broker = MercadoBitcoinBroker(config)

        mock_client = AsyncMock()

        # Mock GET /api/v4/accounts/balances → return BTC available
        balances_response = MagicMock()
        balances_response.status_code = 200
        balances_response.json.return_value = [
            {"symbol": "BTC", "available": "0.002", "locked": "0"},
        ]

        # Mock POST /api/v4/orders → success
        order_response = MagicMock()
        order_response.status_code = 201
        order_response.json.return_value = {"id": "mb-sell-456"}

        mock_client.get = AsyncMock(return_value=balances_response)
        mock_client.post = AsyncMock(return_value=order_response)
        broker._client = mock_client
        broker._access_token = "fake_token"
        broker._token_expiry = 9999999999.0

        result = run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("51000.00"),
            correlation_id=uuid4(),
        )))

        assert result.status == OrderStatus.SUBMITTED

        # Verify GET balance was called
        mock_client.get.assert_called()
        get_call_args = mock_client.get.call_args
        assert "/api/v4/accounts/balances" in get_call_args[0][0]

        # Verify POST order with side=sell
        mock_client.post.assert_called()
        post_call_args = mock_client.post.call_args
        body = post_call_args[1].get("json")
        assert body["side"] == "sell"
        assert body["symbol"] == "BTC-BRL"
        assert body["quantity"] == "0.001"


# ============================================================
# C9.1-TEST-09: MercadoBitcoinBroker SELL without LONG rejected
# ============================================================


class TestMBBrokerSellWithoutLongRejected:

    def test_sell_without_position_rejected(self) -> None:
        """AC-C9.1-10: SELL without LONG is rejected before POST."""
        from unittest.mock import AsyncMock, MagicMock
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig

        config = MercadoBitcoinConfig(
            api_key="test_key", api_secret="test_secret", enabled=True,
        )
        broker = MercadoBitcoinBroker(config)

        mock_client = AsyncMock()

        # Mock GET /api/v4/accounts/balances → NO BTC
        balances_response = MagicMock()
        balances_response.status_code = 200
        balances_response.json.return_value = [
            {"symbol": "BTC", "available": "0", "locked": "0"},
        ]
        mock_client.get = AsyncMock(return_value=balances_response)
        mock_client.post = AsyncMock()  # Should NOT be called
        broker._client = mock_client
        broker._access_token = "fake_token"
        broker._token_expiry = 9999999999.0

        result = run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )))

        assert result.status == OrderStatus.REJECTED
        assert "no open LONG" in result.error.lower() or "SELL rejected" in result.error

        # POST /api/v4/orders must NOT have been called
        mock_client.post.assert_not_called()


# ============================================================
# C9.1-TEST-10: MercadoBitcoinBroker SELL exceeding position rejected
# ============================================================


class TestMBBrokerSellExceedingPositionRejected:

    def test_sell_exceeding_quantity_rejected(self) -> None:
        """AC-C9.1-11: SELL exceeding available LONG quantity is rejected."""
        from unittest.mock import AsyncMock, MagicMock
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig

        config = MercadoBitcoinConfig(
            api_key="test_key", api_secret="test_secret", enabled=True,
        )
        broker = MercadoBitcoinBroker(config)

        mock_client = AsyncMock()

        # Mock GET → BTC available = 0.001
        balances_response = MagicMock()
        balances_response.status_code = 200
        balances_response.json.return_value = [
            {"symbol": "BTC", "available": "0.001", "locked": "0"},
        ]
        mock_client.get = AsyncMock(return_value=balances_response)
        mock_client.post = AsyncMock()  # Should NOT be called
        broker._client = mock_client
        broker._access_token = "fake_token"
        broker._token_expiry = 9999999999.0

        result = run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.002"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )))

        assert result.status == OrderStatus.REJECTED
        assert "exceeds" in result.error.lower()

        mock_client.post.assert_not_called()


# ============================================================
# C9.1-TEST-11: MercadoBitcoinBroker cannot create SHORT
# ============================================================


class TestMBBrokerNoShort:

    def test_sell_does_not_create_short(self) -> None:
        """AC-C9.1-11: SELL only closes LONG. No SHORT is created."""
        from unittest.mock import AsyncMock, MagicMock
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig

        config = MercadoBitcoinConfig(
            api_key="test_key", api_secret="test_secret", enabled=True,
        )
        broker = MercadoBitcoinBroker(config)

        mock_client = AsyncMock()

        # Mock GET → BTC available = 0 (no position)
        balances_response = MagicMock()
        balances_response.status_code = 200
        balances_response.json.return_value = [
            {"symbol": "BTC", "available": "0", "locked": "0"},
        ]
        mock_client.get = AsyncMock(return_value=balances_response)
        mock_client.post = AsyncMock()
        broker._client = mock_client
        broker._access_token = "fake_token"
        broker._token_expiry = 9999999999.0

        result = run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )))

        # Rejected — no SHORT created
        assert result.status == OrderStatus.REJECTED
        mock_client.post.assert_not_called()


# ============================================================
# C9.1-TEST-12: LIVE tests never use real credentials/network
# ============================================================


class TestLiveNoRealCredentials:

    def test_live_blocked_without_credentials(self) -> None:
        """AC-C9.1-12: MercadoBitcoinBroker blocks orders without credentials."""
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig

        config = MercadoBitcoinConfig(api_key="", api_secret="", enabled=True)
        broker = MercadoBitcoinBroker(config)

        result = run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )))

        assert result.status == OrderStatus.REJECTED
        assert "credential" in result.error.lower() or "missing" in result.error.lower()

    def test_live_blocked_when_disabled(self) -> None:
        """AC-C9.1-12: MercadoBitcoinBroker blocks when LIVE_ENABLED=false."""
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig

        config = MercadoBitcoinConfig(api_key="key", api_secret="secret", enabled=False)
        broker = MercadoBitcoinBroker(config)

        result = run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )))

        assert result.status == OrderStatus.REJECTED
        assert "disabled" in result.error.lower()

    def test_sandbox_live_fail_safe(self) -> None:
        """Factory raises RuntimeError when LIVE without LIVE_ENABLED."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings, TradingEnvironment

        settings = Settings(
            trading_environment=TradingEnvironment.LIVE,
            live_enabled=False,
        )
        with pytest.raises(RuntimeError):
            create_broker(settings)
