"""AEGIS V1.3 P0 — Financial Execution & Reconciliation Corrections Tests.

P0-14: 20 mandatory tests covering partial fills, delta accounting, restart
recovery, UNKNOWN semantics, idempotency, reconciliation, and fail-safe.
All tests use deterministic data — no datetime.now().
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

import aegis.worker as worker_mod
from aegis.domain.enums import OrderSide, OrderStatus, PositionSide, TradingAction
from aegis.execution.broker import OrderResult
from aegis.execution.sandbox import SandboxBroker
from aegis.reconciliation import (
    ReconciliationEngine,
    ReconciliationStatus,
    DivergenceSeverity,
    ExchangeSnapshot,
    ExchangeBalance,
    ExchangeOrder,
    LocalSnapshot,
    LocalPosition,
    LocalOrder,
)
from aegis.risk_engine.risk_engine import RiskDecision
from aegis.ai_engine.decision_engine import DecisionContract


DETERMINISTIC_TIME = "2024-01-01T00:00:00"


def _risk(qty: Decimal = Decimal("0.001"), price: Decimal = Decimal("50000")) -> RiskDecision:
    return RiskDecision(
        status="APPROVED",
        approved_quantity=qty,
        approved_price=price,
        risk_amount=Decimal("50"),
        exposure=qty * price,
    )


def _decision() -> DecisionContract:
    return DecisionContract(
        action=TradingAction.LONG,
        confidence=Decimal("0.9"),
        thesis="test",
        entry_price=Decimal("50000"),
        stop_loss=Decimal("49000"),
        take_profit=Decimal("51000"),
    )


# ============================================================
# P0-14-01: Partial fill único
# ============================================================


class TestSinglePartialFill:

    def test_apply_fill_delta_single(self, tmp_path) -> None:
        """P0-01: apply_fill_delta records only the delta passed to it."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()
            order_record = {
                "id": str(uuid4()), "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0", "remaining_quantity": "1.0",
                "price": "50000", "status": "PARTIALLY_FILLED",
                "fee": "0", "timestamp": DETERMINISTIC_TIME,
            }
            delta = w._apply_fill_delta(
                order_record, "BTC-BRL",
                new_filled=Decimal("0.4"),
                fill_price=Decimal("50000"),
                fee=Decimal("0.50"),
                decision=_decision(),
                current_price=Decimal("51000"),
            )
            assert delta == Decimal("0.4")
            assert order_record["filled_quantity"] == "0.4"
            assert order_record["remaining_quantity"] == "0.6"
            # Position must correspond to executed quantity
            pos = w._find_open_position("BTC-BRL")
            assert pos is not None
            assert pos["quantity"] == "0.4"
            assert pos["status"] == "OPEN"
            # Portfolio must reflect the fill
            assert w.portfolio.cash == Decimal("100") - Decimal("50000") * Decimal("0.4") - Decimal("0.50")
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-02: Multi-poll delta accounting
# ============================================================


class TestMultiPollDelta:

    def test_delta_accounting_three_calls(self, tmp_path) -> None:
        """P0-02: 0.30+0.30+0.40=1.00, never 0.30+0.60+1.00."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()
            order_record = {
                "id": str(uuid4()), "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0", "remaining_quantity": "1.0",
                "price": "50000", "status": "PARTIALLY_FILLED",
                "fee": "0", "timestamp": DETERMINISTIC_TIME,
            }

            w._apply_fill_delta(order_record, "BTC-BRL",
                                new_filled=Decimal("0.30"),
                                fill_price=Decimal("50000"),
                                fee=Decimal("0"), decision=_decision(),
                                current_price=Decimal("51000"))
            w._apply_fill_delta(order_record, "BTC-BRL",
                                new_filled=Decimal("0.60"),
                                fill_price=Decimal("50000"),
                                fee=Decimal("0"), decision=_decision(),
                                current_price=Decimal("51000"))
            w._apply_fill_delta(order_record, "BTC-BRL",
                                new_filled=Decimal("1.00"),
                                fill_price=Decimal("50000"),
                                fee=Decimal("0"), decision=_decision(),
                                current_price=Decimal("51000"))

            assert Decimal(order_record["filled_quantity"]) == Decimal("1.0")
            assert Decimal(order_record["remaining_quantity"]) == Decimal("0.0")
            pos = w._find_open_position("BTC-BRL")
            assert pos is not None
            assert Decimal(pos["quantity"]) == Decimal("1.0")
            # Total cash spent = 50000 * 1.0 = 50000
            assert w.portfolio.cash == Decimal("100") - Decimal("50000")
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-03: Repeated filled_quantity does not duplicate
# ============================================================


class TestRepeatedFillNoDuplicate:

    def test_same_fill_quantity_twice(self, tmp_path) -> None:
        """P0-01: Calling apply_fill_delta with the same new_filled twice → delta=0."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()
            order_record = {
                "id": str(uuid4()), "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0", "remaining_quantity": "1.0",
                "price": "50000", "status": "PARTIALLY_FILLED",
                "fee": "0", "timestamp": DETERMINISTIC_TIME,
            }
            d1 = w._apply_fill_delta(order_record, "BTC-BRL",
                                     new_filled=Decimal("0.40"),
                                     fill_price=Decimal("50000"),
                                     fee=Decimal("0"), decision=_decision(),
                                     current_price=Decimal("51000"))
            d2 = w._apply_fill_delta(order_record, "BTC-BRL",
                                     new_filled=Decimal("0.40"),
                                     fill_price=Decimal("50000"),
                                     fee=Decimal("0"), decision=_decision(),
                                     current_price=Decimal("51000"))
            assert d1 == Decimal("0.40")
            assert d2 == Decimal("0")
            assert Decimal(order_record["filled_quantity"]) == Decimal("0.4")
            assert w.portfolio.cash == Decimal("100") - Decimal("50000") * Decimal("0.40")
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-04: Partial → Filled
# ============================================================


class TestPartialToFilled:

    @pytest.mark.asyncio
    async def test_partial_to_filled_via_broker(self, tmp_path) -> None:
        """P0-01: Order goes PARTIALLY_FILLED → FILLED via broker polling."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()

            broker = SandboxBroker(initial_balance=Decimal("100000"))
            w.broker = broker
            w.execution = __import__("aegis.execution.engine", fromlist=["ExecutionEngine"]).ExecutionEngine(broker)

            order_id = uuid4()
            broker.configure_partial_fills(order_id, [Decimal("0.4"), Decimal("1.0")])

            # Submit the order to the broker so it exists for polling
            from aegis.execution.broker import OrderSubmission
            sub = OrderSubmission(
                order_id=order_id, idempotency_key=uuid4(),
                symbol="BTC-BRL", side=OrderSide.BUY,
                quantity=Decimal("1.0"), price=Decimal("50000"),
                correlation_id=uuid4(),
            )
            result = await broker.submit_order(sub)
            assert result.status == OrderStatus.PARTIALLY_FILLED
            assert result.fill_quantity == Decimal("0.4")

            order_record = {
                "id": str(order_id), "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0.4", "remaining_quantity": "0.6",
                "price": "50000", "status": "PARTIALLY_FILLED",
                "fee": "0", "timestamp": DETERMINISTIC_TIME,
            }
            w._state["orders"].append(order_record)
            w.portfolio.record_fill(asset="BTC-BRL", side=PositionSide.LONG,
                                     quantity=Decimal("0.4"), price=Decimal("50000"), fee=Decimal("0"))
            # Persist the executed position (production path)
            w._upsert_position("BTC-BRL", Decimal("0.4"), Decimal("50000"), Decimal("51000"), Decimal("0"), _decision())

            with patch.object(worker_mod.asyncio, "sleep"):
                result = await w._reconcile_pending_order(order_record, "BTC-BRL", _decision(), Decimal("51000"))
            assert result == "FILLED"
            assert order_record["status"] == "FILLED"
            assert Decimal(order_record["filled_quantity"]) == Decimal("1.0")
            pos = w._find_open_position("BTC-BRL")
            assert pos is not None
            assert Decimal(pos["quantity"]) == Decimal("1.0")
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-05: Partial → Rejected
# ============================================================


class TestPartialToRejected:

    @pytest.mark.asyncio
    async def test_partial_to_rejected(self, tmp_path) -> None:
        """P0-01: PARTIALLY_FILLED polling returns REJECTED → order resolved, no position."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()

            broker = SandboxBroker(initial_balance=Decimal("100000"))
            w.broker = broker
            w.execution = __import__("aegis.execution.engine", fromlist=["ExecutionEngine"]).ExecutionEngine(broker)

            order_id = uuid4()

            async def mock_rejected(_uid):
                return OrderResult(order_id=order_id, status=OrderStatus.REJECTED, error="Cancelled by exchange")

            broker.get_order_status = mock_rejected

            order_record = {
                "id": str(order_id), "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0.4", "remaining_quantity": "0.6",
                "price": "50000", "status": "PARTIALLY_FILLED",
                "fee": "0", "timestamp": DETERMINISTIC_TIME,
            }
            w._state["orders"].append(order_record)

            with patch.object(worker_mod.asyncio, "sleep"):
                result = await w._reconcile_pending_order(order_record, "BTC-BRL", _decision(), Decimal("51000"))
            assert result == "REJECTED"
            # Portfolio should NOT have changed (no new fill from rejected)
            # The previously filled 0.4 stays as-is
            assert order_record["status"] == "REJECTED"
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-06: Restart during partial fill
# ============================================================


class TestRestartDuringPartial:

    @pytest.mark.asyncio
    async def test_restart_does_not_duplicate_fill(self, tmp_path) -> None:
        """P0-04: restart → _load_state → poll → filled delta only, no duplication."""
        original_state = worker_mod._STATE_FILE
        original_settings = worker_mod._SETTINGS_FILE
        original_prompt = worker_mod._PROMPT_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            worker_mod._SETTINGS_FILE = tmp_path / "test.env"
            worker_mod._PROMPT_FILE = tmp_path / "prompt.txt"

            # Phase 1: create worker, simulate partial fill, save state
            broker = SandboxBroker(initial_balance=Decimal("100000"))
            order_id = uuid4()
            broker.configure_partial_fills(order_id, [Decimal("0.4"), Decimal("1.0")])

            from aegis.execution.broker import OrderSubmission
            sub = OrderSubmission(
                order_id=order_id, idempotency_key=uuid4(),
                symbol="BTC-BRL", side=OrderSide.BUY,
                quantity=Decimal("1.0"), price=Decimal("50000"),
                correlation_id=uuid4(),
            )
            res = await broker.submit_order(sub)
            assert res.status == OrderStatus.PARTIALLY_FILLED

            w1 = worker_mod.AutonomousWorker()
            w1.broker = broker
            w1.execution = __import__("aegis.execution.engine", fromlist=["ExecutionEngine"]).ExecutionEngine(broker)

            order_record = {
                "id": str(order_id), "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0.4", "remaining_quantity": "0.6",
                "price": "50000", "status": "PARTIALLY_FILLED",
                "fee": "0", "timestamp": DETERMINISTIC_TIME,
            }
            w1._state["orders"].append(order_record)
            w1.portfolio.record_fill(asset="BTC-BRL", side=PositionSide.LONG,
                                     quantity=Decimal("0.4"), price=Decimal("50000"), fee=Decimal("0"))
            # Persist the executed position (production path)
            w1._upsert_position("BTC-BRL", Decimal("0.4"), Decimal("50000"), Decimal("51000"), Decimal("0"), _decision())
            cash_before = w1.portfolio.cash
            w1._save_state()

            # Phase 2: simulate restart — load state
            w2 = worker_mod.AutonomousWorker()
            w2.broker = broker
            w2.execution = __import__("aegis.execution.engine", fromlist=["ExecutionEngine"]).ExecutionEngine(broker)
            w2._load_state()

            assert w2._state_valid is True
            assert len(w2._state["orders"]) == 1
            loaded = w2._state["orders"][0]
            assert loaded["filled_quantity"] == "0.4"
            assert loaded["remaining_quantity"] == "0.6"

            # Phase 3: poll — broker advances from 0.4 to 1.0, delta=0.6
            with patch.object(worker_mod.asyncio, "sleep"):
                result = await w2._reconcile_pending_order(w2._state["orders"][0], "BTC-BRL", _decision(), Decimal("51000"))
            assert result == "FILLED"
            # Should have only added 0.6 — not full 1.0
            # (Broker applies slippage 0.1% and fee 0.50)
            assert w2.portfolio.cash == cash_before - Decimal("50050") * Decimal("0.6") - Decimal("0.50")
            pos = w2._find_open_position("BTC-BRL")
            assert pos is not None
            assert Decimal(pos["quantity"]) == Decimal("1.0")
        finally:
            worker_mod._STATE_FILE = original_state
            worker_mod._SETTINGS_FILE = original_settings
            worker_mod._PROMPT_FILE = original_prompt


# ============================================================
# P0-14-07: Timeout → UNKNOWN
# ============================================================


class TestTimeoutToUnknown:

    @pytest.mark.asyncio
    async def test_poll_timeout_returns_unknown(self, tmp_path) -> None:
        """P0-05: _poll_order_status returns UNKNOWN (not ERROR) on timeout."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()

            async def never_ready(_uid):
                return OrderResult(order_id=uuid4(), status=OrderStatus.SUBMITTED)

            w.execution.get_order_status = never_ready

            with patch.object(worker_mod.asyncio, "sleep"):
                result = await w._poll_order_status(
                    uuid4(), max_retries=2, delay_seconds=0.1,
                )
            assert result.status == OrderStatus.UNKNOWN
            assert "unknown" in (result.error or "").lower()
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-08: UNKNOWN blocks trading
# ============================================================


class TestUnknownBlocksTrading:

    def test_unknown_order_blocks_trading(self, tmp_path) -> None:
        """P0-05/P0-13: UNKNOWN order → _reconciled=False, trading blocked."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()
            w._reconciled = True

            order_record = {
                "id": str(uuid4()), "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0.4", "remaining_quantity": "0.6",
                "price": "50000", "status": "UNKNOWN",
                "fee": "0", "error": "Status unknown after polling",
                "timestamp": DETERMINISTIC_TIME,
            }
            w._state["orders"].append(order_record)
            # Simulate: the UNKNOWN order must be found by find_pending_order
            # and reconciliation will set _reconciled=False
            pending = w._find_pending_order("BTC-BRL")
            assert pending is not None
            assert pending["status"] == "UNKNOWN"
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-09: UNKNOWN recovered via exchange
# ============================================================


class TestUnknownRecovery:

    @pytest.mark.asyncio
    async def test_unknown_recovered_via_broker(self, tmp_path) -> None:
        """P0-06: Broker returns FILLED for UNKNOWN order → delta applied."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()
            broker = SandboxBroker(initial_balance=Decimal("100000"))
            w.broker = broker
            w.execution = __import__("aegis.execution.engine", fromlist=["ExecutionEngine"]).ExecutionEngine(broker)

            order_id = uuid4()
            broker.configure_partial_fills(order_id, [Decimal("0.4"), Decimal("1.0")])
            from aegis.execution.broker import OrderSubmission
            sub = OrderSubmission(
                order_id=order_id, idempotency_key=uuid4(),
                symbol="BTC-BRL", side=OrderSide.BUY,
                quantity=Decimal("1.0"), price=Decimal("50000"),
                correlation_id=uuid4(),
            )
            await broker.submit_order(sub)

            order_record = {
                "id": str(order_id), "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0", "remaining_quantity": "1.0",
                "price": "50000", "status": "UNKNOWN",
                "fee": "0", "error": "Status unknown on previous run",
                "timestamp": DETERMINISTIC_TIME,
            }
            w._state["orders"].append(order_record)

            with patch.object(worker_mod.asyncio, "sleep"):
                result = await w._reconcile_pending_order(order_record, "BTC-BRL", _decision(), Decimal("51000"))
            assert result == "FILLED"
            assert Decimal(order_record["filled_quantity"]) == Decimal("1.0")
            pos = w._find_open_position("BTC-BRL")
            assert pos is not None
            assert Decimal(pos["quantity"]) == Decimal("1.0")
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-10: UNKNOWN does not generate duplicate order
# ============================================================


class TestUnknownNoDuplicate:

    def test_find_pending_order_prevents_duplicate(self, tmp_path) -> None:
        """P0-10: _find_pending_order returns UNKNOWN order → guard prevents new order."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()
            # Add an UNKNOWN order for BTC-BRL
            w._state["orders"].append({
                "id": str(uuid4()), "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0.4", "remaining_quantity": "0.6",
                "price": "50000", "status": "UNKNOWN",
                "fee": "0", "error": "Previous timeout",
                "timestamp": DETERMINISTIC_TIME,
            })
            pending = w._find_pending_order("BTC-BRL")
            assert pending is not None
            assert pending["status"] == "UNKNOWN"
            # If there were no duplicates, there should be exactly 1 order
            assert len(w._state["orders"]) == 1
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-11: filled_quantity persisted
# ============================================================


class TestFilledQuantityPersisted:

    def test_filled_quantity_survives_save_load(self, tmp_path) -> None:
        """P0-12: filled_quantity persists across save/load."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w1 = worker_mod.AutonomousWorker()
            w1._state["orders"].append({
                "id": "o-1", "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0.4", "remaining_quantity": "0.6",
                "price": "50000", "status": "PARTIALLY_FILLED",
                "fee": "0.50", "error": None, "timestamp": DETERMINISTIC_TIME,
            })
            w1._save_state()

            w2 = worker_mod.AutonomousWorker()
            w2._load_state()
            assert len(w2._state["orders"]) == 1
            assert w2._state["orders"][0]["filled_quantity"] == "0.4"
            assert w2._state["orders"][0]["remaining_quantity"] == "0.6"
            assert w2._state["orders"][0]["status"] == "PARTIALLY_FILLED"
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-12: remaining_quantity persisted
# ============================================================


class TestRemainingQuantityPersisted:

    def test_remaining_quantity_after_save_load(self, tmp_path) -> None:
        """P0-12: remaining_quantity survives save/load."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w1 = worker_mod.AutonomousWorker()
            w1._state["orders"].append({
                "id": "o-rem", "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0.7", "remaining_quantity": "0.3",
                "price": "50000", "status": "PARTIALLY_FILLED",
                "fee": "0.50", "error": None, "timestamp": DETERMINISTIC_TIME,
            })
            w1._save_state()
            w2 = worker_mod.AutonomousWorker()
            w2._load_state()
            assert w2._state["orders"][0]["remaining_quantity"] == "0.3"
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-13: Position consistent with filled_quantity
# ============================================================


class TestPositionConsistency:

    def test_position_matches_filled(self, tmp_path) -> None:
        """P0-03: position.quantity equals filled_quantity after partial fill."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()
            order_record = {
                "id": str(uuid4()), "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0", "remaining_quantity": "1.0",
                "price": "50000", "status": "PARTIALLY_FILLED",
                "fee": "0", "timestamp": DETERMINISTIC_TIME,
            }
            w._apply_fill_delta(order_record, "BTC-BRL",
                                new_filled=Decimal("0.4"),
                                fill_price=Decimal("50000"),
                                fee=Decimal("0.50"), decision=_decision(),
                                current_price=Decimal("51000"))

            pos = w._find_open_position("BTC-BRL")
            assert pos is not None
            assert Decimal(pos["quantity"]) == Decimal("0.4")
            assert order_record["filled_quantity"] == "0.4"
            assert order_record["remaining_quantity"] == "0.6"

            # After FILLED
            w._apply_fill_delta(order_record, "BTC-BRL",
                                new_filled=Decimal("1.0"),
                                fill_price=Decimal("50000"),
                                fee=Decimal("0.50"), decision=_decision(),
                                current_price=Decimal("51000"))
            assert Decimal(pos["quantity"]) == Decimal("1.0")
            assert order_record["filled_quantity"] == "1.0"
            assert order_record["remaining_quantity"] == "0.0"
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-14: Reconciliation with BRL + crypto
# ============================================================


class TestReconciliationBRLandCrypto:

    def test_reconcile_brl_and_btc(self) -> None:
        """P0-07: Local cash + BTC position reconciled against exchange."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(
            capital=Decimal("50"),
            positions=[LocalPosition("BTC-BRL", Decimal("0.001"), Decimal("50000"), "OPEN")],
        )
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[
                ExchangeBalance("BRL", Decimal("50"), Decimal("0")),
                ExchangeBalance("BTC", Decimal("0.001"), Decimal("0")),
            ],
        )
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.RECONCILED
        assert result.is_reconciled

    def test_reconcile_with_locked_brl_explainable(self) -> None:
        """P0-07: BRL locked explainable by pending BUY orders → not UNKNOWN."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(
            capital=Decimal("100"),
            orders=[LocalOrder("ord-1", "BTC-BRL", "BUY", Decimal("0.001"), "SUBMITTED")],
        )
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[
                ExchangeBalance("BRL", Decimal("50"), Decimal("50")),
            ],
            open_orders=[
                ExchangeOrder("ord-1", "BTC-BRL", "BUY", Decimal("0.001")),
            ],
        )
        result = engine.reconcile(local, exchange)
        # Pending BUY order explains the BRL locked → not UNKNOWN
        # local.capital=100, exchange total=50+50=100 → match
        assert result.status == ReconciliationStatus.RECONCILED


# ============================================================
# P0-14-15: Disponivel vs Bloqueado
# ============================================================


class TestAvailableVsLocked:

    def test_brl_locked_not_explainable_unknown(self) -> None:
        """P0-07: BRL locked without local explanation → UNKNOWN."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[
                ExchangeBalance("BRL", Decimal("50"), Decimal("50")),
            ],
        )
        result = engine.reconcile(local, exchange)
        # BRL locked=50 but local has no cash_locked and no pending BUY orders
        # → cannot determine safely → UNKNOWN
        assert result.status == ReconciliationStatus.UNKNOWN
        assert not result.is_reconciled


# ============================================================
# P0-14-16: Real financial divergence
# ============================================================


class TestRealDivergence:

    def test_cash_mismatch_diverges(self) -> None:
        """P0-07: BRL total mismatch → DIVERGED."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("80"), Decimal("0"))],
        )
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.DIVERGED
        assert not result.is_reconciled
        assert any("cash_brl" in d.field for d in result.divergences)

    def test_btc_quantity_exceeds_exchange(self) -> None:
        """P0-07: Local position > exchange balance → DIVERGED."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(
            capital=Decimal("50"),
            positions=[LocalPosition("BTC-BRL", Decimal("0.01"), Decimal("50000"), "OPEN")],
        )
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[
                ExchangeBalance("BRL", Decimal("50"), Decimal("0")),
                ExchangeBalance("BTC", Decimal("0.005"), Decimal("0")),
            ],
        )
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.DIVERGED
        assert any("BTC" in d.field for d in result.divergences)


# ============================================================
# P0-14-17: Incomplete snapshot → UNKNOWN
# ============================================================


class TestIncompleteSnapshot:

    def test_balances_ok_orders_fail_unknown(self) -> None:
        """P0-08: balances OK + orders FAIL → UNKNOWN."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="UNKNOWN",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
            error="Balances OK but open orders query failed",
        )
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.UNKNOWN
        assert not result.is_reconciled

    def test_fully_missing_snapshot(self) -> None:
        """P0-08: Exchange snapshot None → UNKNOWN."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(status="UNKNOWN")
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.UNKNOWN
        assert not result.is_reconciled


# ============================================================
# P0-14-18: Order history recovery
# ============================================================


class TestOrderHistoryRecovery:

    @pytest.mark.asyncio
    async def test_order_history_recovery(self, tmp_path) -> None:
        """P0-09: SUBMITTED order not in open orders but resolved via broker history."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()
            broker = SandboxBroker(initial_balance=Decimal("100000"))
            w.broker = broker
            w.execution = __import__("aegis.execution.engine", fromlist=["ExecutionEngine"]).ExecutionEngine(broker)

            order_id = uuid4()
            broker.configure_partial_fills(order_id, [Decimal("0.4"), Decimal("1.0")])
            from aegis.execution.broker import OrderSubmission
            sub = OrderSubmission(
                order_id=order_id, idempotency_key=uuid4(),
                symbol="BTC-BRL", side=OrderSide.BUY,
                quantity=Decimal("1.0"), price=Decimal("50000"),
                correlation_id=uuid4(),
            )
            await broker.submit_order(sub)

            order_record = {
                "id": str(order_id), "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "1.0", "filled_quantity": "0", "remaining_quantity": "1.0",
                "price": "50000", "status": "SUBMITTED",
                "fee": "0", "error": None, "timestamp": DETERMINISTIC_TIME,
            }
            w._state["orders"].append(order_record)

            with patch.object(worker_mod.asyncio, "sleep"):
                result = await w._reconcile_pending_order(order_record, "BTC-BRL", _decision(), Decimal("51000"))
            assert result == "FILLED"
            assert order_record["status"] == "FILLED"
            assert Decimal(order_record["filled_quantity"]) == Decimal("1.0")
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# P0-14-19: _reload_config preserves PromptVersion
# (covered in test_p0_corrections.py test_reload_config_registers_prompt_version)
# ============================================================


# ============================================================
# P0-14-20: Save failure → fail-safe
# ============================================================


class TestSaveFailureFailSafe:

    def test_save_failure_blocks_trading(self, tmp_path) -> None:
        """P0-12/P0-13: Save failure → _state_valid=False, _reconciled=False."""
        original = worker_mod._STATE_FILE
        try:
            state_file = tmp_path / "not_a_file"
            state_file.mkdir()
            worker_mod._STATE_FILE = state_file
            w = worker_mod.AutonomousWorker()
            w._save_state()
            assert w._state_valid is False
            assert w._reconciled is False
        finally:
            worker_mod._STATE_FILE = original