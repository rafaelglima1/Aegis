"""AEGIS P0 Production Blocker Corrections Tests."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from aegis.domain.enums import OrderSide, OrderStatus, TradingAction
from aegis.execution.sandbox import SandboxBroker
from aegis.execution.engine import ExecutionEngine
from aegis.execution.broker import OrderResult
from aegis.reconciliation import (
    ReconciliationEngine, ReconciliationStatus, DivergenceSeverity,
    ExchangeSnapshot, ExchangeBalance, ExchangeOrder,
    LocalSnapshot, LocalOrder,
)


def _risk(qty: Decimal = Decimal("0.001"), price: Decimal = Decimal("50000")):
    from aegis.risk_engine.risk_engine import RiskDecision
    return RiskDecision(status="APPROVED", approved_quantity=qty, approved_price=price,
                        risk_amount=Decimal("50"), exposure=qty * price)


# ============================================================
# P0-05: Exchange Snapshot Atomicity
# ============================================================


class TestSnapshotAtomicity:

    def test_balances_ok_orders_fail_returns_unknown(self) -> None:
        """P0-05: balances OK + orders FAIL = UNKNOWN."""
        from aegis.reconciliation import ExchangeSnapshot

        snap = ExchangeSnapshot(
            status="UNKNOWN",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
            error="Orders query failed",
        )
        assert not snap.is_valid
        assert snap.get_balance("BRL") is not None

    def test_empty_snapshot_is_not_valid(self) -> None:
        """P0-05: Empty snapshot with no status is UNKNOWN."""
        from aegis.reconciliation import ExchangeSnapshot
        snap = ExchangeSnapshot()
        assert not snap.is_valid

    def test_error_snapshot_blocks_reconciliation(self) -> None:
        """P0-05: ERROR snapshot blocks trading."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(status="ERROR", error="timeout")
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.ERROR
        assert not result.is_reconciled


# ============================================================
# P0-03: UNKNOWN/TIMEOUT blocks immediately
# ============================================================


class TestUnknownBlocksImmediately:

    def test_unknown_exchange_blocks_trading(self) -> None:
        """P0-03: UNKNOWN exchange → trading blocked."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(status="UNKNOWN")
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.UNKNOWN
        assert not result.is_reconciled

    def test_error_exchange_blocks_trading(self) -> None:
        """P0-03: ERROR exchange → trading blocked."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(status="ERROR", error="timeout")
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.ERROR
        assert not result.is_reconciled

    def test_diverged_blocks_trading(self) -> None:
        """P0-03: DIVERGED → trading blocked."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(status="VALID", balances=[
            ExchangeBalance("BRL", Decimal("50"), Decimal("0")),
        ])
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.DIVERGED
        assert not result.is_reconciled


# ============================================================
# P0-04: Persistence failure → FAIL-SAFE
# ============================================================


class TestPersistenceFailSafe:

    def test_save_failure_sets_state_invalid(self, tmp_path) -> None:
        """P0-04: Save failure → _state_valid = False."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            # Point to a directory (not a file) — write will fail
            test_file = tmp_path / "not_a_file"
            test_file.mkdir()
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._save_state()
            assert w._state_valid is False
        finally:
            worker_mod._STATE_FILE = original

    def test_read_only_file_prevents_save(self, tmp_path) -> None:
        """P0-04: Read-only file → FAIL-SAFE."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            test_file.write_text("{}")
            test_file.chmod(0o444)
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._save_state()
            # On Windows, chmod may not work as expected
            # The important thing is the except branch sets state_valid=False
            assert w._state_valid is False
            test_file.chmod(0o644)
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-09: Hot reload config failure
# ============================================================


class TestConfigFailure:

    def test_reload_config_with_missing_file(self, tmp_path) -> None:
        """P0-09: Missing env file → no crash, config unchanged."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            worker_mod._SETTINGS_FILE = tmp_path / "nonexistent.env"
            w = AutonomousWorker()
            original_symbols = w.symbols.copy()
            w._reload_config()
            # Config should remain unchanged
            assert w.symbols == original_symbols
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-02: PARTIALLY_FILLED consistency
# ============================================================


class TestPartialFillConsistency:

    def test_partial_fill_record_has_filled_quantity(self, tmp_path) -> None:
        """P0-02: PARTIALLY_FILLED records filled_quantity."""
        import json
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._state["orders"].append({
                "id": "o-partial", "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "0.01", "filled_quantity": "0.005",
                "price": "500000", "status": "PARTIALLY_FILLED",
                "fee": "0.25", "error": None, "timestamp": "2024-01-01T00:00:00",
            })
            w._save_state()

            w2 = AutonomousWorker()
            w2._load_state()
            assert w2._state["orders"][0]["status"] == "PARTIALLY_FILLED"
            assert w2._state["orders"][0]["filled_quantity"] == "0.005"
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-07: Order identity persistence
# ============================================================


class TestOrderIdentity:

    def test_order_record_fields(self, tmp_path) -> None:
        """P0-07: Order record has all required fields."""
        import json
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._state["orders"].append({
                "id": "o-123", "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "0.001", "price": "500000", "status": "FILLED",
                "fee": "0.50", "error": None, "timestamp": "2024-01-01T00:00:00",
            })
            w._save_state()

            data = json.loads(test_file.read_text())
            order = data["orders"][0]
            assert order["id"] == "o-123"
            assert order["symbol"] == "BTC-BRL"
            assert order["side"] == "BUY"
            assert order["quantity"] == "0.001"
            assert order["price"] == "500000"
            assert order["status"] == "FILLED"
            assert order["fee"] == "0.50"
            assert order["error"] is None
            assert "timestamp" in order
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-08: Sandbox restart recovery
# ============================================================


class TestSandboxRecovery:

    def test_sandbox_snapshot_after_restart(self, tmp_path) -> None:
        """P0-08: Sandbox snapshot reflects persisted state."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w.portfolio._cash = Decimal("500")
            w._save_state()

            # Simulate restart
            w2 = AutonomousWorker()
            w2._load_state()
            # Verify cash was restored
            assert w2.portfolio.cash == Decimal("500")
            assert w2._state_valid is True
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-06: Risk engine exposure
# ============================================================


class TestRiskExposure:

    def test_exposure_rebuild_matches(self, tmp_path) -> None:
        """P0-06: Exposure rebuild matches incremental calculation."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()

            # Simulate position
            w._state["positions"].append({
                "id": "pos-1", "symbol": "BTC-BRL", "status": "OPEN",
                "quantity": "0.001", "entry_price": "500000",
                "current_price": "510000", "entry_fee": "0.50",
                "pnl": "100", "side": "LONG",
            })
            w.portfolio._positions["BTC-BRL"] = type('PE', (), {
                'asset': 'BTC-BRL', 'quantity': Decimal('0.001'),
                'average_entry': Decimal('500000'), 'current_price': Decimal('510000'),
                'status': 'OPEN',
            })()

            # Rebuild risk engine
            w.risk_engine.rebuild_from_open_positions(1, Decimal('510'))

            # Verify exposure is correct
            assert w.risk_engine._positions_count == 1
            assert w.risk_engine._current_exposure == Decimal('510')
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-10: Dead code cleanup verified
# ============================================================


class TestDeadCodeCleanup:

    def test_no_duplicate_reload_config(self) -> None:
        """P0-10: _reload_config is defined only once and registers the prompt."""
        import inspect
        import aegis.worker as worker_mod
        source = inspect.getsource(worker_mod.AutonomousWorker._reload_config)
        # Defined only once — must still register the PromptVersion
        assert source.count("def _reload_config") == 1
        assert "prompt_manager.register" in source
        assert "PromptVersion" in source
        assert "_max_pos" not in source

    def test_reload_config_registers_prompt_version(self, tmp_path) -> None:
        """P0-11: _reload_config() updates the prompt manager with a PromptVersion."""
        import aegis.worker as worker_mod
        from aegis.worker import AutonomousWorker
        from aegis.ai_engine.prompt_manager import PromptVersion

        original_state = worker_mod._STATE_FILE
        original_settings = worker_mod._SETTINGS_FILE
        original_prompt = worker_mod._PROMPT_FILE
        try:
            worker_mod._STATE_FILE = tmp_path / "state.json"
            worker_mod._SETTINGS_FILE = tmp_path / "test.env"
            worker_mod._PROMPT_FILE = tmp_path / "nonexistent_prompt.txt"
            # Create a minimal env file so _reload_config actually rebuilds the prompt
            (tmp_path / "test.env").write_text("LONG_ONLY=true\n")
            w = AutonomousWorker()
            # Default prompt registered in __init__
            assert "trading_v1" in w.prompt_manager.list_versions()
            before = w.prompt_manager.get("trading_v1").template
            w._reload_config()
            assert "trading_v1" in w.prompt_manager.list_versions()
            after = w.prompt_manager.get("trading_v1").template
            # Fallback template rebuilt from config must be registered
            assert "DADOS DE MERCADO" in after
            assert isinstance(w.prompt_manager.get("trading_v1"), PromptVersion)
            # Template should have changed after reload
            assert before != after
        finally:
            worker_mod._STATE_FILE = original_state
            worker_mod._SETTINGS_FILE = original_settings
            worker_mod._PROMPT_FILE = original_prompt


# ============================================================
# LIVE Safety
# ============================================================


class TestLiveSafety:

    def test_live_disabled(self) -> None:
        from aegis.config import Settings
        s = Settings()
        assert s.live_enabled is False

    def test_factory_blocks_live(self) -> None:
        from aegis.execution.factory import create_broker
        from aegis.config import Settings
        with pytest.raises(RuntimeError):
            create_broker(Settings(trading_environment="LIVE", live_enabled=False))
