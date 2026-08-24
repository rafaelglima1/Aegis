"""AEGIS Persistence & State Consistency Tests."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest


class TestAtomicWrite:

    def test_save_creates_valid_json(self, tmp_path: Path) -> None:
        """_save_state produces valid JSON."""
        from aegis.worker import AutonomousWorker, _STATE_FILE
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

    def test_save_atomic_no_corruption(self, tmp_path: Path) -> None:
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
            # Create saved state with risk_state
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


class TestCorruptionRecovery:

    def test_corrupted_json_starts_fresh(self, tmp_path: Path) -> None:
        """Corrupted JSON file is handled gracefully."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            test_file.write_text("NOT VALID JSON {{{")
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            # Should not raise, should start fresh
            assert w.portfolio.cash == Decimal("100.00")
        finally:
            worker_mod._STATE_FILE = original

    def test_missing_file_starts_fresh(self, tmp_path: Path) -> None:
        """Missing state file starts fresh."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "nonexistent.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            assert w.portfolio.cash == Decimal("100.00")
        finally:
            worker_mod._STATE_FILE = original


class TestStateConsistency:

    def test_positions_persisted(self, tmp_path: Path) -> None:
        """Open positions survive save/load cycle."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            # Simulate an open position
            w._state["positions"].append({
                "id": "test-123",
                "symbol": "BTC-BRL",
                "status": "OPEN",
                "quantity": "0.001",
                "entry_price": "50000",
                "current_price": "51000",
                "entry_fee": "0.50",
                "pnl": "1.00",
                "side": "LONG",
            })
            w._save_state()

            # Load into fresh worker
            w2 = AutonomousWorker()
            w2._load_state()
            assert len(w2._state["positions"]) == 1
            assert w2._state["positions"][0]["symbol"] == "BTC-BRL"
            assert w2._state["positions"][0]["status"] == "OPEN"
        finally:
            worker_mod._STATE_FILE = original

    def test_capital_persisted(self, tmp_path: Path) -> None:
        """Capital survives save/load cycle."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

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
        finally:
            worker_mod._STATE_FILE = original

    def test_pnl_persisted(self, tmp_path: Path) -> None:
        """P&L survives save/load cycle."""
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

    def test_peak_equity_persisted(self, tmp_path: Path) -> None:
        """Peak equity survives save/load cycle."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w.portfolio._peak_equity = Decimal("150.00")
            w.risk_engine._peak_equity = Decimal("150.00")
            w._save_state()

            w2 = AutonomousWorker()
            w2._load_state()
            assert w2.portfolio._peak_equity == Decimal("150.00")
            assert w2.risk_engine._peak_equity == Decimal("150.00")
        finally:
            worker_mod._STATE_FILE = original
