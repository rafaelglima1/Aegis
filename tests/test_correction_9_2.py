"""AEGIS V1.3 - Correction #9.2 Tests.

Hardening after independent audit of C9.1.
Covers: environment switch safety, position persistence, live safety,
MercadoBitcoin position semantics, Settings reload, SHORT prevention.
"""

from __future__ import annotations

import asyncio
import inspect
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from aegis.domain.enums import (
    OrderSide,
    OrderStatus,
    PositionSide,
    PositionStatus,
    TradingAction,
)
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
    from aegis.worker import AutonomousWorker
    import aegis.worker as worker_mod

    # Reset _SETTINGS_FILE to a non-existent path BEFORE construction
    # so _create_broker() reads empty env and defaults to SANDBOX.
    worker_mod._SETTINGS_FILE = tmp_path / "nonexistent.env"

    _write_env(tmp_path, TRADING_CAPITAL=capital, MAX_POSITIONS=max_positions)
    settings = Settings(initial_capital=Decimal(capital), max_positions=int(max_positions))
    worker = AutonomousWorker(settings=settings)

    # Patch AFTER construction so _reload_config() reads the temp env file
    worker_mod._SETTINGS_FILE = tmp_path / ".env.prod"
    return worker


# ============================================================
# C9.2-TEST-01: Python 3.12 alignment across deployment artifacts
# ============================================================


class TestPython312Alignment:

    def test_dockerfile_targets_312(self) -> None:
        """AC-C9.2-01: Dockerfile uses python:3.12-slim base image."""
        dockerfile = Path("Dockerfile").read_text()
        assert "python:3.12-slim" in dockerfile
        assert "python3.12/site-packages" in dockerfile

    def test_pyproject_requires_312(self) -> None:
        """AC-C9.2-01: pyproject.toml requires Python >= 3.12."""
        import tomllib
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        assert data["project"]["requires-python"] == ">=3.12"

    def test_ruff_targets_312(self) -> None:
        """AC-C9.2-01: Ruff linter targets Python 3.12."""
        import tomllib
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        assert data["tool"]["ruff"]["target-version"] == "py312"

    def test_mypy_targets_312(self) -> None:
        """AC-C9.2-01: Mypy type checker targets Python 3.12."""
        import tomllib
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        assert data["tool"]["mypy"]["python_version"] == "3.12"

    def test_docker_compose_uses_dockerfile(self) -> None:
        """AC-C9.2-01: docker-compose.prod.yml references Dockerfile (not a pre-built image)."""
        compose = Path("docker-compose.prod.yml").read_text()
        assert "Dockerfile" in compose
        # No hardcoded python version in compose — Dockerfile is the source
        assert "python:3.11" not in compose

    def test_no_python_311_references_in_repo(self) -> None:
        """AC-C9.2-01: No python:3.11 or python3.11 references in config/deployment files."""
        skip_files = {"test_correction_9_2.py"}
        for pattern in ["python:3.11", "python3.11"]:
            for f in Path(".").rglob("*"):
                if f.is_file() and ".git" not in str(f) and f.name not in skip_files:
                    if f.suffix in (".toml", ".yml", ".yaml", ".cfg", ".ini") or f.name == "Dockerfile":
                        try:
                            content = f.read_text(encoding="utf-8")
                            assert pattern not in content, f"{pattern} found in {f}"
                        except (UnicodeDecodeError, PermissionError):
                            pass


# ============================================================
# C9.2-TEST-02: Hot reload does NOT silently swap broker type
# ============================================================


class TestHotReloadNoBrokerSwap:

    def test_reload_preserves_sandbox_broker(self, tmp_path: Path) -> None:
        """AC-C9.2-05: Hot reload cannot silently switch SANDBOX broker to LIVE.

        Changing TRADING_ENVIRONMENT in .env.prod should NOT change the
        active broker instance. Broker type is determined at startup only.
        """
        from aegis.execution.sandbox import SandboxBroker
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker

        worker = _make_worker(tmp_path, max_positions="1")
        original_broker_type = type(worker.broker)
        assert original_broker_type is SandboxBroker

        # Change TRADING_ENVIRONMENT to LIVE in env file
        _write_env(tmp_path, TRADING_CAPITAL="100.00", MAX_POSITIONS="1",
                    TRADING_ENVIRONMENT="LIVE", LIVE_ENABLED="true")
        worker._reload_config()

        # Broker must NOT change — environment switch requires restart
        assert type(worker.broker) is original_broker_type
        assert isinstance(worker.broker, SandboxBroker)
        assert not isinstance(worker.broker, MercadoBitcoinBroker)

    def test_reload_updates_settings_not_broker(self, tmp_path: Path) -> None:
        """C9.2-02: _reload_config updates self._settings but does not touch broker."""
        from aegis.config import TradingEnvironment

        worker = _make_worker(tmp_path, max_positions="1")
        original_broker = worker.broker

        _write_env(tmp_path, TRADING_CAPITAL="100.00", MAX_POSITIONS="1",
                    TRADING_ENVIRONMENT="LIVE", LIVE_ENABLED="true")
        worker._reload_config()

        # Settings is updated
        assert worker._settings.trading_environment == TradingEnvironment.LIVE
        assert worker._settings.live_enabled is True
        # But broker instance is the exact same object
        assert worker.broker is original_broker


# ============================================================
# C9.2-TEST-03: Factory selects correct broker type
# ============================================================


class TestFactoryBrokerSelection:

    def test_factory_sandbox_selects_sandbox(self) -> None:
        """AC-C9.2-02: SANDBOX configuration selects SandboxBroker."""
        from aegis.execution.factory import create_broker
        from aegis.execution.sandbox import SandboxBroker
        from aegis.config import Settings, TradingEnvironment

        settings = Settings(trading_environment=TradingEnvironment.SANDBOX)
        broker = create_broker(settings, initial_balance=Decimal("100.00"))
        assert isinstance(broker, SandboxBroker)

    def test_factory_live_enabled_selects_mercadobitcoin(self) -> None:
        """AC-C9.2-03: LIVE + enabled selects MercadoBitcoinBroker (no real HTTP)."""
        from aegis.execution.factory import create_broker
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker
        from aegis.config import Settings, TradingEnvironment

        settings = Settings(
            trading_environment=TradingEnvironment.LIVE,
            live_enabled=True,
            live_api_key="test_key",
            live_api_secret="test_secret",
        )
        broker = create_broker(settings)
        assert isinstance(broker, MercadoBitcoinBroker)
        # No real connection — broker is not connected
        assert broker.is_connected is False
        assert broker.is_enabled is True

    def test_factory_live_disabled_raises(self) -> None:
        """AC-C9.2-04: LIVE disabled raises RuntimeError (fail-closed)."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings, TradingEnvironment

        settings = Settings(
            trading_environment=TradingEnvironment.LIVE,
            live_enabled=False,
        )
        with pytest.raises(RuntimeError, match="LIVE trading is disabled"):
            create_broker(settings)


# ============================================================
# C9.2-TEST-04: LIVE safety semantics
# ============================================================


class TestLiveSafetySemantics:

    def test_default_config_is_sandbox(self) -> None:
        """AC-C9.2-04: Default configuration selects SANDBOX environment."""
        from aegis.config import Settings, TradingEnvironment
        settings = Settings()
        assert settings.trading_environment == TradingEnvironment.SANDBOX
        assert settings.live_enabled is False

    def test_live_disabled_blocks_all_orders(self) -> None:
        """AC-C9.2-04: MercadoBitcoinBroker with enabled=False blocks all orders."""
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig

        config = MercadoBitcoinConfig(api_key="key", api_secret="secret", enabled=False)
        broker = MercadoBitcoinBroker(config)

        for side in [OrderSide.BUY, OrderSide.SELL]:
            result = run(broker.submit_order(OrderSubmission(
                order_id=uuid4(), idempotency_key=uuid4(),
                symbol="BTC-BRL", side=side,
                quantity=Decimal("0.001"), price=Decimal("50000.00"),
                correlation_id=uuid4(),
            )))
            assert result.status == OrderStatus.REJECTED
            assert "disabled" in result.error.lower()

    def test_live_no_real_http_in_test(self) -> None:
        """AC-C9.2-13: MercadoBitcoinBroker tests use mocked HTTP, never real network."""
        from unittest.mock import AsyncMock, MagicMock
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig

        config = MercadoBitcoinConfig(api_key="test", api_secret="test", enabled=True)
        broker = MercadoBitcoinBroker(config)

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "test-order"}
        mock_client.post = AsyncMock(return_value=mock_response)
        broker._client = mock_client
        broker._access_token = "fake"
        broker._token_expiry = 9999999999.0

        result = run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )))
        assert result.status == OrderStatus.SUBMITTED
        # Verify the mock was used, not a real httpx client
        mock_client.post.assert_called_once()

    def test_sandbox_remains_default_operational(self) -> None:
        """AC-C9.2-04: SandboxBroker is the safe default for all test operations."""
        from aegis.execution.sandbox import SandboxBroker
        from aegis.execution.factory import create_broker
        from aegis.config import Settings

        settings = Settings()
        broker = create_broker(settings, initial_balance=Decimal("10000.00"))
        assert isinstance(broker, SandboxBroker)

        # Verify SandboxBroker can execute without any network
        result = run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )))
        assert result.status == OrderStatus.FILLED

    def test_no_accidental_sandbox_to_live(self, tmp_path: Path) -> None:
        """AC-C9.2-08: Hot reload cannot cause SANDBOX -> LIVE transition."""
        from aegis.execution.sandbox import SandboxBroker

        worker = _make_worker(tmp_path, max_positions="1")
        assert isinstance(worker.broker, SandboxBroker)

        # Attempt to switch to LIVE via env file
        _write_env(tmp_path, TRADING_CAPITAL="100.00", MAX_POSITIONS="1",
                    TRADING_ENVIRONMENT="LIVE", LIVE_ENABLED="true")
        worker._reload_config()

        # Broker must still be SandboxBroker
        assert isinstance(worker.broker, SandboxBroker)


# ============================================================
# C9.2-TEST-05: Position persistence during hot reload (AC-C9.1-06)
# ============================================================


class TestPositionPersistenceDuringReload:

    def test_position_survives_real_reload(self, tmp_path: Path) -> None:
        """AC-C9.2-06/07/08/09: Open LONG position survives _reload_config().

        Creates a position, reloads config, verifies ALL invariants:
        - position still exists
        - asset unchanged
        - quantity unchanged
        - average entry unchanged
        - cash unchanged
        - P&L unchanged
        - fees unchanged
        """
        worker = _make_worker(tmp_path, capital="10000.00", max_positions="1")

        # Create an open LONG position
        fill_price = Decimal("50050.00")
        fee = Decimal("0.50")
        worker.portfolio.record_fill(
            asset="BTC-BRL", side=PositionSide.LONG,
            quantity=Decimal("0.001"), price=fill_price, fee=fee,
        )
        worker._state["positions"] = [{
            "id": "pos-1", "symbol": "BTC-BRL", "side": "LONG",
            "quantity": "0.001", "entry_price": str(fill_price),
            "current_price": "50100.00", "entry_fee": str(fee),
            "pnl": "0", "pnl_pct": "0", "status": "OPEN",
            "opened_at": "2025-01-01T00:00:00Z",
        }]

        # Record pre-reload state
        pre_cash = worker.portfolio.cash
        pre_pnl = worker.portfolio.total_realized_pnl
        pre_fees = worker.portfolio.total_fees
        pre_position = worker.portfolio._positions.get("BTC-BRL")
        assert pre_position is not None
        pre_qty = pre_position.quantity
        pre_entry = pre_position.average_entry

        # Execute real reload with different config
        _write_env(tmp_path, TRADING_CAPITAL="500.00", MAX_POSITIONS="1")
        worker._reload_config()

        # Verify position survived
        post_position = worker.portfolio._positions.get("BTC-BRL")
        assert post_position is not None
        assert post_position.quantity == pre_qty
        assert post_position.average_entry == pre_entry
        assert post_position.entry_fee == fee

        # Verify financial state survived
        assert worker.portfolio.cash == pre_cash
        assert worker.portfolio.total_realized_pnl == pre_pnl
        assert worker.portfolio.total_fees == pre_fees

        # Verify state dict position survived
        assert len(worker._state["positions"]) == 1
        assert worker._state["positions"][0]["status"] == "OPEN"
        assert worker._state["positions"][0]["quantity"] == "0.001"


# ============================================================
# C9.2-TEST-06: Settings reload does not reset unrelated config
# ============================================================


class TestSettingsReloadPreservesUnrelated:

    def test_reload_preserves_llm_config(self, tmp_path: Path) -> None:
        """AC-C9.2-11: Hot reload does not reset LLM configuration.

        LLM settings are read via os.getenv at __init__ and are intentionally
        restart-only. This test proves they survive hot reload.
        """
        worker = _make_worker(tmp_path, max_positions="1")
        original_llm_url = worker.llm_base_url
        original_llm_model = worker.llm_model

        _write_env(tmp_path, TRADING_CAPITAL="100.00", MAX_POSITIONS="1")
        worker._reload_config()

        # LLM config must not change (restart-only)
        assert worker.llm_base_url == original_llm_url
        assert worker.llm_model == original_llm_model

    def test_reload_preserves_risk_operational_params(self, tmp_path: Path) -> None:
        """AC-C9.2-11: Hot reload does not reset operational risk parameters.

        When env file doesn't contain a parameter, the current value is preserved
        (fallback to current Worker attribute value).
        """
        worker = _make_worker(tmp_path, max_positions="1")
        # Set non-default operational params
        worker.mandatory_stop = False
        worker.mandatory_take_profit = False
        worker.long_only = True
        worker.min_confidence = Decimal("0.7")

        # Reload with only MAX_POSITIONS changed
        _write_env(tmp_path, TRADING_CAPITAL="100.00", MAX_POSITIONS="1")
        worker._reload_config()

        # Operational params preserved (env fallback uses current value)
        assert worker.mandatory_stop is False
        assert worker.mandatory_take_profit is False
        assert worker.long_only is True
        assert worker.min_confidence == Decimal("0.7")

    def test_reload_preserves_state_dict_positions(self, tmp_path: Path) -> None:
        """AC-C9.2-11: Hot reload does not touch state dict positions list."""
        worker = _make_worker(tmp_path, max_positions="1")
        worker._state["positions"] = [{"id": "test", "status": "OPEN"}]

        _write_env(tmp_path, TRADING_CAPITAL="100.00", MAX_POSITIONS="1")
        worker._reload_config()

        assert len(worker._state["positions"]) == 1
        assert worker._state["positions"][0]["id"] == "test"


# ============================================================
# C9.2-TEST-07: MercadoBitcoin position semantics documentation
# ============================================================


class TestMBPositionSemantics:

    def test_get_position_returns_exchange_balance(self) -> None:
        """AC-C9.2-10: get_position() returns exchange 'available' balance.

        This documents that MercadoBitcoinBroker treats the exchange account
        as exclusively controlled by Aegis. The 'available' balance from
        /api/v4/accounts/balances is used as the position proxy.
        """
        from unittest.mock import AsyncMock, MagicMock
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig

        config = MercadoBitcoinConfig(api_key="test", api_secret="test", enabled=True)
        broker = MercadoBitcoinBroker(config)

        mock_client = AsyncMock()
        balances_response = MagicMock()
        balances_response.status_code = 200
        balances_response.json.return_value = [
            {"symbol": "BTC", "available": "0.5", "locked": "0.1"},
        ]
        mock_client.get = AsyncMock(return_value=balances_response)
        broker._client = mock_client
        broker._access_token = "fake"
        broker._token_expiry = 9999999999.0

        position = run(broker.get_position("BTC-BRL"))

        # Position quantity comes from exchange 'available' balance
        assert position["quantity"] == Decimal("0.5")
        assert position["symbol"] == "BTC-BRL"

    def test_position_semantics_documented_in_source(self) -> None:
        """AC-C9.2-10: MercadoBitcoinBroker class docstring documents position semantics."""
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker
        doc = MercadoBitcoinBroker.__doc__ or ""
        assert "Position semantics" in doc or "position" in doc.lower()

    def test_sandbox_get_position_tracks_trades(self) -> None:
        """SandboxBroker get_position tracks actual fills, not exchange balance.

        Unlike MercadoBitcoinBroker, SandboxBroker maintains local position
        tracking through order fills — BUY adds, SELL subtracts.
        """
        from aegis.execution.sandbox import SandboxBroker

        broker = SandboxBroker(initial_balance=Decimal("10000.00"))

        # BUY 0.001 BTC
        run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000.00"),
            correlation_id=uuid4(),
        )))
        pos = run(broker.get_position("BTC-BRL"))
        assert pos["quantity"] == Decimal("0.001")

        # SELL 0.001 BTC — position goes to zero
        run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("51000.00"),
            correlation_id=uuid4(),
        )))
        pos = run(broker.get_position("BTC-BRL"))
        assert pos["quantity"] == Decimal("0")


# ============================================================
# C9.2-TEST-08: No real network, no real credentials
# ============================================================


class TestNoRealNetworkOrCredentials:

    def test_mercadobitcoin_blocks_without_credentials(self) -> None:
        """AC-C9.2-14: MercadoBitcoinBroker rejects orders when credentials are empty."""
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

    def test_mercadobitcoin_blocks_when_disabled(self) -> None:
        """AC-C9.2-14: MercadoBitcoinBroker rejects orders when enabled=False."""
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

    def test_factory_raises_for_live_without_enabled(self) -> None:
        """AC-C9.2-14: Factory raises RuntimeError for LIVE without LIVE_ENABLED."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings, TradingEnvironment

        settings = Settings(
            trading_environment=TradingEnvironment.LIVE,
            live_enabled=False,
        )
        with pytest.raises(RuntimeError):
            create_broker(settings)


# ============================================================
# C9.2-TEST-09: Full lifecycle — BUY -> SELL -> restart -> verify
# ============================================================


class TestFullLifecycleRegression:

    def test_buy_sell_restart_full_cycle(self, tmp_path: Path) -> None:
        """AC-C9.2-15: Full regression — complete lifecycle survives restart.

        Creates worker, executes BUY, closes via SELL, restarts,
        verifies all financial state is consistent.
        """
        import aegis.worker as worker_mod
        orig_file = worker_mod._STATE_FILE
        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.config import Settings
            from aegis.worker import AutonomousWorker
            from aegis.execution.sandbox import SandboxBroker

            settings = Settings(initial_capital=Decimal("10000.00"))
            w1 = AutonomousWorker(settings=settings)

            # BUY
            buy_result = run(w1.broker.submit_order(OrderSubmission(
                order_id=uuid4(), idempotency_key=uuid4(),
                symbol="BTC-BRL", side=OrderSide.BUY,
                quantity=Decimal("0.001"), price=Decimal("50000.00"),
                correlation_id=uuid4(),
            )))
            assert buy_result.status == OrderStatus.FILLED

            # Record fill in Portfolio
            w1.portfolio.record_fill(
                asset="BTC-BRL", side=PositionSide.LONG,
                quantity=Decimal("0.001"), price=buy_result.fill_price,
                fee=buy_result.fee,
            )
            w1._state["positions"] = [{
                "id": "pos-1", "symbol": "BTC-BRL", "side": "LONG",
                "quantity": "0.001", "entry_price": str(buy_result.fill_price),
                "current_price": "50100.00", "entry_fee": str(buy_result.fee),
                "pnl": "0", "pnl_pct": "0", "status": "OPEN",
                "opened_at": "2025-01-01T00:00:00Z",
            }]
            w1._state["orders"] = []
            w1._state["history"] = []
            w1._state["decisions"] = []
            w1._save_state()

            # SELL through broker
            sell_result = run(w1.broker.submit_order(OrderSubmission(
                order_id=uuid4(), idempotency_key=uuid4(),
                symbol="BTC-BRL", side=OrderSide.SELL,
                quantity=Decimal("0.001"), price=Decimal("51000.00"),
                correlation_id=uuid4(),
            )))
            assert sell_result.status == OrderStatus.FILLED

            # Record close in Portfolio
            realized = w1.portfolio.close_position(
                asset="BTC-BRL", price=sell_result.fill_price, fee=sell_result.fee,
            )
            w1._state["positions"][0]["status"] = "CLOSED"
            w1._save_state()

            # Record state after full cycle
            post_cash = w1.portfolio.cash
            post_pnl = w1.portfolio.total_realized_pnl
            post_fees = w1.portfolio.total_fees
            assert post_pnl != Decimal("0")

            # Restart
            w2 = AutonomousWorker(settings=settings)
            w2._load_state()

            # Verify all financial state survived
            assert w2.portfolio.cash == post_cash
            assert w2.portfolio.total_realized_pnl == post_pnl
            assert w2.portfolio.total_fees == post_fees
            assert w2.broker.balance == post_cash
        finally:
            worker_mod._STATE_FILE = orig_file
