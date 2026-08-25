"""AEGIS Persistence & State Consistency Tests."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest


class TestAtomicWrite:

    def test_save_creates_valid_json(self, tmp_path: Path) -> None:
        """_save_state produces valid JSON."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod
        import json as _json

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._save_state()
            assert test_file.exists()
            data = _json.loads(test_file.read_text())
            assert "capital" in data
            assert "positions" in data
            assert "risk_state" in data
        finally:
            worker_mod._STATE_FILE = original

    def test_save_atomic_no_tmp_leftover(self, tmp_path: Path) -> None:
        """Atomic write leaves no .tmp file on success."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._save_state()
            tmp_file = test_file.with_suffix(".tmp")
            assert not tmp_file.exists()
        finally:
            worker_mod._STATE_FILE = original


class TestRiskStatePersistence:

    def test_risk_state_saved(self, tmp_path: Path) -> None:
        """Risk state (daily_pnl, kill_switch) is saved."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod
        import json as _json

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w.risk_engine._daily_pnl = Decimal("-5.00")
            w.risk_engine._kill_switch_active = True
            w.risk_engine._circuit_breaker_active = True
            w._save_state()
            data = _json.loads(test_file.read_text())
            assert data["risk_state"]["daily_pnl"] == "-5.00"
            assert data["risk_state"]["kill_switch_active"] is True
            assert data["risk_state"]["circuit_breaker_active"] is True
        finally:
            worker_mod._STATE_FILE = original

    def test_risk_state_restored(self, tmp_path: Path) -> None:
        """Risk state is restored on load."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod
        import json as _json

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            state = {
                "capital": "100.00",
                "pnl": "0",
                "total_fees": "0",
                "peak_equity": "100.00",
                "risk_peak_equity": "100.00",
                "risk_state": {
                    "daily_pnl": "-5.00",
                    "kill_switch_active": True,
                    "circuit_breaker_active": True,
                },
                "positions": [],
                "orders": [],
                "history": [],
                "decisions": [],
            }
            test_file.write_text(_json.dumps(state))
            worker_mod._STATE_FILE = test_file

            w = AutonomousWorker()
            w._load_state()
            assert w.risk_engine._daily_pnl == Decimal("-5.00")
            assert w.risk_engine._kill_switch_active is True
            assert w.risk_engine._circuit_breaker_active is True
        finally:
            worker_mod._STATE_FILE = original


class TestCorruptionFailSafe:

    def test_corrupted_json_sets_state_invalid(self, tmp_path: Path) -> None:
        """AC1: Corrupted JSON sets _state_valid = False."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            test_file.write_text("NOT VALID JSON {{{")
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._load_state()
            assert w._state_valid is False
        finally:
            worker_mod._STATE_FILE = original

    def test_corrupted_json_does_not_restore_capital(self, tmp_path: Path) -> None:
        """AC2: Corrupted JSON does NOT create artificial capital."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            test_file.write_text("NOT VALID JSON {{{")
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._load_state()
            # Capital stays at default, NOT restored from corrupted file
            assert w.portfolio.cash == Decimal("100.00")
            assert w._state_valid is False
        finally:
            worker_mod._STATE_FILE = original

    def test_corrupted_json_file_preserved(self, tmp_path: Path) -> None:
        """AC3: Corrupted file is NOT deleted or overwritten."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            test_file.write_text("CORRUPTED DATA HERE")
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._load_state()
            # File should still exist with original content
            assert test_file.exists()
            assert test_file.read_text() == "CORRUPTED DATA HERE"
        finally:
            worker_mod._STATE_FILE = original

    def test_incompatible_schema_sets_state_invalid(self, tmp_path: Path) -> None:
        """AC9: Schema missing required 'positions' key sets _state_valid = False."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod
        import json as _json

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            # Missing "positions" key — truly incompatible
            test_file.write_text(_json.dumps({"capital": "100.00"}))
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._load_state()
            assert w._state_valid is False
        finally:
            worker_mod._STATE_FILE = original

    def test_invalid_capital_sets_state_invalid(self, tmp_path: Path) -> None:
        """AC9: Non-numeric capital sets _state_valid = False."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod
        import json as _json

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            test_file.write_text(_json.dumps({
                "capital": "not_a_number",
                "positions": [],
            }))
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._load_state()
            assert w._state_valid is False
        finally:
            worker_mod._STATE_FILE = original

    def test_read_error_sets_state_invalid(self, tmp_path: Path) -> None:
        """AC10: File read error sets _state_valid = False."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            test_file.write_text("data")
            test_file.chmod(0o000)  # No read permissions
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._load_state()
            assert w._state_valid is False
            # Restore permissions for cleanup
            test_file.chmod(0o644)
        finally:
            worker_mod._STATE_FILE = original

    def test_first_boot_starts_fresh(self, tmp_path: Path) -> None:
        """AC4: Missing file on first boot starts fresh with state_valid=True."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "nonexistent.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._load_state()
            assert w._state_valid is True
            assert w.portfolio.cash == Decimal("100.00")
        finally:
            worker_mod._STATE_FILE = original


class TestValidStateRestore:

    def test_valid_json_restores_capital(self, tmp_path: Path) -> None:
        """AC5: Valid JSON restores capital."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod
        import json as _json

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w.portfolio._cash = Decimal("75.50")
            w._save_state()

            w2 = AutonomousWorker()
            w2._load_state()
            assert w2.portfolio.cash == Decimal("75.50")
            assert w2._state_valid is True
        finally:
            worker_mod._STATE_FILE = original

    def test_valid_json_restores_positions(self, tmp_path: Path) -> None:
        """AC6: Valid JSON restores positions."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._state["positions"].append({
                "id": "test-123", "symbol": "BTC-BRL", "status": "OPEN",
                "quantity": "0.001", "entry_price": "50000",
                "current_price": "51000", "entry_fee": "0.50",
                "pnl": "1.00", "side": "LONG",
            })
            w._save_state()

            w2 = AutonomousWorker()
            w2._load_state()
            assert len(w2._state["positions"]) == 1
            assert w2._state["positions"][0]["symbol"] == "BTC-BRL"
            assert w2._state_valid is True
        finally:
            worker_mod._STATE_FILE = original

    def test_valid_json_restores_pnl(self, tmp_path: Path) -> None:
        """AC7: Valid JSON restores P&L."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w.portfolio._total_realized_pnl = Decimal("-3.25")
            w._save_state()

            w2 = AutonomousWorker()
            w2._load_state()
            assert w2.portfolio.total_realized_pnl == Decimal("-3.25")
        finally:
            worker_mod._STATE_FILE = original

    def test_valid_json_restores_risk_state(self, tmp_path: Path) -> None:
        """AC8: Valid JSON restores risk state."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w.risk_engine._daily_pnl = Decimal("-5.00")
            w.risk_engine._kill_switch_active = True
            w._save_state()

            w2 = AutonomousWorker()
            w2._load_state()
            assert w2.risk_engine._daily_pnl == Decimal("-5.00")
            assert w2.risk_engine._kill_switch_active is True
        finally:
            worker_mod._STATE_FILE = original


class TestStateValidFlag:

    def test_state_valid_exposed_in_state(self, tmp_path: Path) -> None:
        """state_valid is exposed in worker.state dict."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "nonexistent.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            assert w.state["state_valid"] is True
        finally:
            worker_mod._STATE_FILE = original

    def test_state_invalid_exposed_in_state(self, tmp_path: Path) -> None:
        """state_valid=False is exposed when state is corrupted."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            test_file.write_text("CORRUPTED")
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._load_state()
            assert w.state["state_valid"] is False
        finally:
            worker_mod._STATE_FILE = original


class TestPersistenceBoundary:

    def test_position_saved_immediately(self, tmp_path: Path) -> None:
        """Position is saved to disk immediately after creation."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod
        import json as _json

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            # Manually append position (simulating what _process_symbol does)
            w._state["positions"].append({
                "id": "test-456", "symbol": "ETH-BRL", "status": "OPEN",
                "quantity": "0.01", "entry_price": "12000",
                "current_price": "12100", "entry_fee": "0.50",
                "pnl": "1.00", "side": "LONG",
            })
            w._save_state()

            # Verify it's on disk
            data = _json.loads(test_file.read_text())
            assert len(data["positions"]) == 1
            assert data["positions"][0]["symbol"] == "ETH-BRL"
        finally:
            worker_mod._STATE_FILE = original
