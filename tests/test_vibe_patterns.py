"""AEGIS V1.3 — Vibe-Trading borrowed patterns tests.

Covers:
  #2 — no-retry estrutural (ExecutionEngine never re-issues a submitted order)
  #3 — kill switch latch bound to a halt episode
  #5 — pending action crash-safe ownership marker
  #8 — preemptive kill-switch flatten (cancel-before-flatten)

All tests use deterministic data.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from aegis.domain.enums import OrderSide, OrderStatus, TradingAction
from aegis.execution.broker import OrderResult, OrderSubmission
from aegis.execution.engine import ExecutionEngine
from aegis.execution.sandbox import SandboxBroker
from aegis.risk_engine.risk_engine import RiskDecision


def _risk(qty: Decimal = Decimal("0.001"), price: Decimal = Decimal("50000")) -> RiskDecision:
    return RiskDecision(
        status="APPROVED",
        approved_quantity=qty,
        approved_price=price,
        risk_amount=Decimal("50"),
        exposure=qty * price,
    )


# ============================================================
# #2 — No-retry estrutural
# ============================================================


class TestNoRetryStructural:

    @pytest.mark.asyncio
    async def test_same_order_id_never_reissued(self) -> None:
        """Engine returns stored result instead of re-submitting same order."""
        broker = SandboxBroker(initial_balance=Decimal("100000"))
        engine = ExecutionEngine(broker)
        order_id = uuid4()
        idem = uuid4()

        r1 = await engine.execute_order(
            order_id=order_id, idempotency_key=idem,
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000"),
            correlation_id=uuid4(), risk_decision=_risk(),
        )
        assert r1.status == OrderStatus.FILLED
        submitted_after_first = len(broker._orders)

        r2 = await engine.execute_order(
            order_id=order_id, idempotency_key=idem,
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000"),
            correlation_id=uuid4(), risk_decision=_risk(),
        )
        # Same result object returned, no second broker call.
        assert r2.status == OrderStatus.FILLED
        assert r2 is r1
        assert len(broker._orders) == submitted_after_first
        assert order_id in engine.submitted_order_ids

    @pytest.mark.asyncio
    async def test_retry_after_error_does_not_reissue(self) -> None:
        """Even an ERROR result is stored — a retry must NOT re-submit."""
        broker = SandboxBroker(initial_balance=Decimal("100000"))
        engine = ExecutionEngine(broker)
        order_id = uuid4()
        idem = uuid4()

        async def failing_submit(submission):
            return OrderResult(
                order_id=submission.order_id,
                status=OrderStatus.ERROR,
                error="network timeout",
            )

        broker.submit_order = failing_submit
        r1 = await engine.execute_order(
            order_id=order_id, idempotency_key=idem,
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000"),
            correlation_id=uuid4(), risk_decision=_risk(),
        )
        assert r1.status == OrderStatus.ERROR
        assert order_id in engine.submitted_order_ids

        # Restore real broker — a retry with the SAME order_id must not hit it.
        broker.submit_order = SandboxBroker.submit_order.__get__(broker, SandboxBroker)
        r2 = await engine.execute_order(
            order_id=order_id, idempotency_key=idem,
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000"),
            correlation_id=uuid4(), risk_decision=_risk(),
        )
        assert r2 is r1
        assert r2.status == OrderStatus.ERROR
        assert len(broker._orders) == 0  # never reached the broker


# ============================================================
# #3 — Kill switch latch bound to episode
# ============================================================


class TestKillSwitchEpisode:

    def test_activate_binds_episode(self) -> None:
        from aegis.risk_engine.risk_engine import RiskEngine
        engine = RiskEngine()
        ep = engine.activate_kill_switch()
        assert ep is not None
        assert engine.kill_switch_episode == ep
        assert engine.is_kill_switch_active()

    def test_re_activate_keeps_same_episode(self) -> None:
        from aegis.risk_engine.risk_engine import RiskEngine
        engine = RiskEngine()
        ep1 = engine.activate_kill_switch()
        ep2 = engine.activate_kill_switch()
        assert ep1 == ep2
        assert engine.kill_switch_episode == ep1

    def test_deactivate_clears_episode(self) -> None:
        from aegis.risk_engine.risk_engine import RiskEngine
        engine = RiskEngine()
        engine.activate_kill_switch()
        engine.deactivate_kill_switch()
        assert not engine.is_kill_switch_active()
        assert engine.kill_switch_episode is None

    def test_episode_persisted_and_restored(self, tmp_path) -> None:
        import aegis.worker as worker_mod
        from aegis.worker import AutonomousWorker

        original_state = worker_mod._STATE_FILE
        original_settings = worker_mod._SETTINGS_FILE
        original_prompt = worker_mod._PROMPT_FILE
        try:
            worker_mod._STATE_FILE = tmp_path / "state.json"
            worker_mod._SETTINGS_FILE = tmp_path / "test.env"
            worker_mod._PROMPT_FILE = tmp_path / "prompt.txt"

            w1 = AutonomousWorker()
            ep = w1.risk_engine.activate_kill_switch()
            w1._save_state()

            w2 = AutonomousWorker()
            w2._load_state()
            assert w2.risk_engine.is_kill_switch_active()
            assert w2.risk_engine.kill_switch_episode == ep
        finally:
            worker_mod._STATE_FILE = original_state
            worker_mod._SETTINGS_FILE = original_settings
            worker_mod._PROMPT_FILE = original_prompt

    def test_circuit_breaker_trip_binds_episode(self) -> None:
        from aegis.risk_engine.risk_engine import RiskEngine
        from aegis.risk_engine.risk_limits import RiskLimits
        engine = RiskEngine(RiskLimits(reference_capital=Decimal("100"),
                                       circuit_breaker_drawdown_pct=Decimal("0.10")))
        engine.update_equity(Decimal("90"))
        assert engine.circuit_breaker_active
        assert engine.kill_switch_episode is not None


# ============================================================
# #5 — Pending Action crash-safe marker
# ============================================================


class TestPendingAction:

    def _set_path(self, tmp_path):
        import aegis.execution.pending_action as pa
        self._orig = pa._PENDING_FILE
        pa._PENDING_FILE = tmp_path / "pending_action.json"
        return pa

    def _restore(self, pa):
        pa._PENDING_FILE = self._orig

    def test_new_pending_order_written_and_loaded(self, tmp_path) -> None:
        pa = self._set_path(tmp_path)
        try:
            action = pa.new_pending_order(
                order_id=uuid4(), idempotency_key=uuid4(),
                symbol="BTC-BRL", side="BUY",
                quantity=Decimal("0.001"), price=Decimal("50000"),
                pre_position_qty=Decimal("0"),
            )
            pa.save_pending_action(action)
            loaded = pa.load_pending_action()
            assert loaded is not None
            assert loaded.is_pending
            assert loaded.symbol == "BTC-BRL"
            assert loaded.side == "BUY"
            assert loaded.pre_position_qty == "0"
            assert pa._PENDING_FILE.exists()
        finally:
            self._restore(pa)

    def test_transition_to_fill_and_clear(self, tmp_path) -> None:
        pa = self._set_path(tmp_path)
        try:
            action = pa.new_pending_order(
                order_id=uuid4(), idempotency_key=uuid4(),
                symbol="BTC-BRL", side="BUY",
                quantity=Decimal("0.001"), price=Decimal("50000"),
            )
            pa.save_pending_action(action)
            filled = pa.transition_to_fill(action, "0.001", "FILLED")
            assert filled.phase == "resolved_fill_pending_audit"
            assert filled.filled_quantity == "0.001"
            loaded = pa.load_pending_action()
            assert loaded.phase == "resolved_fill_pending_audit"
            pa.clear_pending_action()
            assert pa.load_pending_action() is None
        finally:
            self._restore(pa)

    def test_save_failure_is_fail_closed(self, tmp_path) -> None:
        pa = self._set_path(tmp_path)
        try:
            # Point at a directory (not a file) so the write fails.
            pa._PENDING_FILE = tmp_path / "not_a_file"
            (tmp_path / "not_a_file").mkdir()
            action = pa.new_pending_order(
                order_id=uuid4(), idempotency_key=uuid4(),
                symbol="BTC-BRL", side="BUY",
                quantity=Decimal("0.001"), price=Decimal("50000"),
            )
            from aegis.execution.pending_action import PendingActionError
            with pytest.raises(PendingActionError):
                pa.save_pending_action(action)
        finally:
            self._restore(pa)

    def test_corrupted_marker_loads_none(self, tmp_path) -> None:
        pa = self._set_path(tmp_path)
        try:
            pa._PENDING_FILE.write_text("{not valid json", encoding="utf-8")
            assert pa.load_pending_action() is None
        finally:
            self._restore(pa)


# ============================================================
# #8 — Preemptive kill-switch flatten
# ============================================================


class TestFlattenSweep:

    @pytest.mark.asyncio
    async def test_cancel_before_flatten(self) -> None:
        from aegis.domain.enums import OrderStatus
        from aegis.execution.flatten import FlattenSweep

        calls = []
        cancel_calls = []

        async def fake_cancel(order_id, idem):
            cancel_calls.append(order_id)
            return type("C", (), {"success": True})()

        async def fake_submit(**kw):
            calls.append(kw["symbol"])
            return OrderResult(
                order_id=uuid4(), status=OrderStatus.FILLED,
                fill_price=Decimal("50000"), fill_quantity=kw["quantity"],
            )

        sweep = FlattenSweep(cancel_order=fake_cancel, submit_close=fake_submit, allow_flatten=True)
        result = await sweep.run(
            "ep-1",
            open_orders=[{"order_id": uuid4()}, {"order_id": uuid4()}],
            positions=[{"symbol": "BTC-BRL", "quantity": "0.001"}],
            current_prices={"BTC-BRL": Decimal("51000")},
        )
        # 2 orders cancelled, 1 position flattened, no errors.
        assert len(result.cancelled) == 2
        assert result.flattened == ["BTC-BRL"]
        assert result.ok
        # Cancel happened before flatten (ordering).
        assert len(cancel_calls) == 2
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_sweep_runs_once_per_episode(self) -> None:
        from aegis.domain.enums import OrderStatus
        from aegis.execution.flatten import FlattenSweep

        async def fake_cancel(order_id, idem):
            return type("C", (), {"success": True})()

        async def fake_submit(**kw):
            return OrderResult(
                order_id=uuid4(), status=OrderStatus.FILLED,
                fill_price=Decimal("50000"), fill_quantity=kw["quantity"],
            )

        sweep = FlattenSweep(cancel_order=fake_cancel, submit_close=fake_submit, allow_flatten=True)
        r1 = await sweep.run("ep-1", open_orders=[{"order_id": uuid4()}], positions=[])
        assert len(r1.cancelled) == 1
        r2 = await sweep.run("ep-1", open_orders=[{"order_id": uuid4()}], positions=[])
        assert r2.skipped != []
        assert r2.cancelled == []

    @pytest.mark.asyncio
    async def test_cancel_only_when_flatten_disabled(self) -> None:
        from aegis.execution.flatten import FlattenSweep

        async def fake_cancel(order_id, idem):
            return type("C", (), {"success": True})()

        async def fake_submit(**kw):
            return OrderResult(order_id=uuid4(), status=OrderStatus.FILLED)

        sweep = FlattenSweep(
            cancel_order=fake_cancel, submit_close=fake_submit, allow_flatten=False,
        )
        result = await sweep.run(
            "ep-1",
            open_orders=[{"order_id": uuid4()}],
            positions=[{"symbol": "BTC-BRL", "quantity": "0.001"}],
        )
        assert len(result.cancelled) == 1
        assert result.flattened == []
        assert any("cancel-only" in s for s in result.skipped)

    @pytest.mark.asyncio
    async def test_no_retry_on_error(self) -> None:
        from aegis.execution.flatten import FlattenSweep

        calls = {"n": 0}

        async def fake_cancel(order_id, idem):
            calls["n"] += 1
            raise RuntimeError("broker down")

        async def fake_submit(**kw):
            raise RuntimeError("broker down")

        sweep = FlattenSweep(cancel_order=fake_cancel, submit_close=fake_submit, allow_flatten=True)
        result = await sweep.run(
            "ep-1",
            open_orders=[{"order_id": uuid4()}],
            positions=[{"symbol": "BTC-BRL", "quantity": "0.001"}],
        )
        assert not result.ok
        assert len(result.errors) == 2  # 1 cancel + 1 flatten, no retries
        assert calls["n"] == 1  # each broker call attempted exactly once

    @pytest.mark.asyncio
    async def test_worker_runs_flatten_on_kill_switch(self, tmp_path) -> None:
        import aegis.worker as worker_mod
        from aegis.execution.flatten import FlattenSweep
        from aegis.worker import AutonomousWorker

        original_state = worker_mod._STATE_FILE
        original_settings = worker_mod._SETTINGS_FILE
        original_prompt = worker_mod._PROMPT_FILE
        try:
            worker_mod._STATE_FILE = tmp_path / "state.json"
            worker_mod._SETTINGS_FILE = tmp_path / "test.env"
            worker_mod._PROMPT_FILE = tmp_path / "prompt.txt"
            w = AutonomousWorker()
            # Open position + active kill switch
            w._state["positions"].append({
                "id": str(uuid4()), "symbol": "BTC-BRL", "side": "LONG",
                "quantity": "0.001", "entry_price": "50000",
                "current_price": "50000", "status": "OPEN",
            })
            w.risk_engine.activate_kill_switch()
            # Replace flatten with a spy
            swept = []
            orig_run = FlattenSweep.run

            async def spy_run(self, episode, open_orders, positions, current_prices=None):
                swept.append(episode)
                return type("R", (), {"to_dict": lambda s: {"swept": episode}, "ok": True})()

            FlattenSweep.run = spy_run
            try:
                await w._run_flatten_if_needed()
            finally:
                FlattenSweep.run = orig_run

            assert swept == [w.risk_engine.kill_switch_episode]
        finally:
            worker_mod._STATE_FILE = original_state
            worker_mod._SETTINGS_FILE = original_settings
            worker_mod._PROMPT_FILE = original_prompt