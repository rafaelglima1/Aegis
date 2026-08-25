"""AEGIS Execution End-to-End Tests.

Tests for the complete order lifecycle:
SIGNAL → RISK → ORDER → BROKER → STATUS → FILL → PERSIST → RECONCILIATE
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from aegis.domain.enums import OrderSide, OrderStatus, TradingAction
from aegis.execution.broker import OrderSubmission, OrderResult
from aegis.execution.sandbox import SandboxBroker
from aegis.execution.engine import ExecutionEngine
from aegis.risk_engine.risk_engine import RiskEngine, RiskDecision
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.ai_engine.decision_engine import DecisionContract


def _make_risk_approved(
    quantity: Decimal = Decimal("0.001"),
    price: Decimal = Decimal("50000"),
) -> RiskDecision:
    return RiskDecision(
        status="APPROVED",
        approved_quantity=quantity,
        approved_price=price,
        risk_amount=Decimal("50"),
        exposure=quantity * price,
    )


def _make_decision(
    action: TradingAction = TradingAction.LONG,
    symbol: str = "BTC-BRL",
) -> DecisionContract:
    return DecisionContract(
        action=action,
        confidence=Decimal("0.85"),
        thesis="test",
        entry_price=Decimal("50000"),
        stop_loss=Decimal("49000"),
        take_profit=Decimal("52000"),
    )


# ============================================================
# Order Lifecycle: BUY → FILLED
# ============================================================


class TestOrderLifecycleBuy:

    @pytest.mark.asyncio
    async def test_buy_fills_immediately_in_sandbox(self) -> None:
        """AC5: SUBMITTED is NOT treated as FILLED — sandbox fills directly."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        risk = _make_risk_approved()
        decision = _make_decision()

        result = await engine.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("0.001"),
            price=Decimal("500000"),
            correlation_id=decision.correlation_id,
            risk_decision=risk,
        )

        assert result.status == OrderStatus.FILLED
        assert result.fill_price is not None
        assert result.fill_price > Decimal("500000")  # slippage applied

    @pytest.mark.asyncio
    async def test_buy_updates_balance(self) -> None:
        """AC7: FILLED updates capital correctly."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        risk = _make_risk_approved()
        decision = _make_decision()

        result = await engine.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("0.001"),
            price=Decimal("500000"),
            correlation_id=decision.correlation_id,
            risk_decision=risk,
        )

        # Balance should decrease
        assert broker.balance < Decimal("1000")
        # Position should exist
        pos = await broker.get_position("BTC-BRL")
        assert pos["quantity"] > 0

    @pytest.mark.asyncio
    async def test_buy_rejected_insufficient_balance(self) -> None:
        """AC8: REJECTED does not create position."""
        broker = SandboxBroker(initial_balance=Decimal("10"))
        engine = ExecutionEngine(broker)
        risk = _make_risk_approved()
        decision = _make_decision()

        result = await engine.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("0.001"),
            price=Decimal("500000"),
            correlation_id=decision.correlation_id,
            risk_decision=risk,
        )

        assert result.status == OrderStatus.REJECTED
        pos = await broker.get_position("BTC-BRL")
        assert pos["quantity"] == 0


# ============================================================
# Order Lifecycle: SELL → FILLED
# ============================================================


class TestOrderLifecycleSell:

    @pytest.mark.asyncio
    async def test_sell_fills_after_buy(self) -> None:
        """Full BUY → SELL lifecycle."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        risk = _make_risk_approved()
        decision = _make_decision()

        # BUY
        buy_result = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )
        assert buy_result.status == OrderStatus.FILLED

        # SELL
        sell_result = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("510000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )
        assert sell_result.status == OrderStatus.FILLED
        assert sell_result.fill_price is not None

        # Position should be zero
        pos = await broker.get_position("BTC-BRL")
        assert pos["quantity"] == 0

    @pytest.mark.asyncio
    async def test_sell_rejected_no_position(self) -> None:
        """AC8: SELL rejected when no position exists."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        risk = _make_risk_approved()
        decision = _make_decision()

        result = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )
        assert result.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_sell_exceeds_position_rejected(self) -> None:
        """AC9: SELL quantity > position rejected."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        risk = _make_risk_approved()
        decision = _make_decision()

        # BUY 0.001
        await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )

        # Try to SELL 0.002 (more than position)
        result = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.002"), price=Decimal("500000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )
        assert result.status == OrderStatus.REJECTED


# ============================================================
# Idempotency
# ============================================================


class TestIdempotency:

    @pytest.mark.asyncio
    async def test_same_idempotency_key_returns_same_result(self) -> None:
        """AC1: Same idempotency key returns same result, not duplicate."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        risk = _make_risk_approved()
        decision = _make_decision()

        key = uuid4()
        r1 = await engine.execute_order(
            order_id=uuid4(), idempotency_key=key,
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )

        # Second call with same key but different order_id → REJECTED (duplicate prevention)
        r2 = await engine.execute_order(
            order_id=uuid4(), idempotency_key=key,
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )

        assert r1.status == OrderStatus.FILLED
        assert r2.status == OrderStatus.REJECTED
        # Balance should only reflect one purchase
        assert broker.balance < Decimal("1000")

    @pytest.mark.asyncio
    async def test_different_keys_allow_new_orders(self) -> None:
        """AC2: Different idempotency keys allow new orders."""
        broker = SandboxBroker(initial_balance=Decimal("10000"))
        engine = ExecutionEngine(broker)
        risk = _make_risk_approved()
        decision = _make_decision()

        r1 = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )
        r2 = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )

        # Both should succeed independently
        assert r1.status == OrderStatus.FILLED
        assert r2.status == OrderStatus.FILLED


# ============================================================
# Risk Gate
# ============================================================


class TestRiskGate:

    @pytest.mark.asyncio
    async def test_risk_rejection_blocks_order(self) -> None:
        """AC17: Risk Engine is the authority — rejected order never reaches broker."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)

        # No risk decision = rejected
        result = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=uuid4(), risk_decision=None,
        )
        assert result.status == OrderStatus.REJECTED
        assert broker.balance == Decimal("1000")  # unchanged

    @pytest.mark.asyncio
    async def test_risk_not_approved_blocks_order(self) -> None:
        """REJECTED risk decision blocks order."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)

        risk = RiskDecision(status="REJECTED")
        result = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=uuid4(), risk_decision=risk,
        )
        assert result.status == OrderStatus.REJECTED


# ============================================================
# Reconciliation
# ============================================================


class TestReconciliation:

    @pytest.mark.asyncio
    async def test_sandbox_snapshot_matches_state(self) -> None:
        """Sandbox snapshot is consistent with internal state."""
        broker = SandboxBroker(initial_balance=Decimal("100"))
        snap = await broker.get_exchange_snapshot()
        assert snap is not None
        assert snap.is_valid
        brl = snap.get_balance("BRL")
        assert brl is not None
        assert brl.available == Decimal("100")

    @pytest.mark.asyncio
    async def test_snapshot_after_buy(self) -> None:
        """Snapshot reflects trades."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        risk = _make_risk_approved()
        decision = _make_decision()

        await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )

        snap = await broker.get_exchange_snapshot()
        btc = snap.get_balance("BTC")
        assert btc is not None
        assert btc.available > 0
        # BRL should be less than initial
        brl = snap.get_balance("BRL")
        assert brl is not None
        assert brl.available < Decimal("1000")

    @pytest.mark.asyncio
    async def test_sandbox_reconciled_with_self(self) -> None:
        """Sandbox always reconciles with itself."""
        from aegis.reconciliation import (
            ReconciliationEngine, LocalSnapshot, LocalPosition,
        )
        broker = SandboxBroker(initial_balance=Decimal("100"))
        snap = await broker.get_exchange_snapshot()
        local = LocalSnapshot(capital=Decimal("100"))
        engine = ReconciliationEngine()
        result = engine.reconcile(local, snap)
        assert result.is_reconciled


# ============================================================
# Sandbox Lifecycle
# ============================================================


class TestSandboxLifecycle:

    @pytest.mark.asyncio
    async def test_full_buy_sell_cycle(self) -> None:
        """Complete BUY → SELL cycle in sandbox."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        risk = _make_risk_approved()
        decision = _make_decision()
        initial = broker.balance

        # BUY
        buy = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )
        assert buy.status == OrderStatus.FILLED

        # SELL
        sell = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("510000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )
        assert sell.status == OrderStatus.FILLED

        # Position should be closed
        pos = await broker.get_position("BTC-BRL")
        assert pos["quantity"] == 0

        # Balance should reflect the round trip
        assert broker.balance != initial

    @pytest.mark.asyncio
    async def test_order_status_query(self) -> None:
        """get_order_status returns correct status."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        risk = _make_risk_approved()
        decision = _make_decision()

        order_id = uuid4()
        result = await engine.execute_order(
            order_id=order_id, idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=decision.correlation_id, risk_decision=risk,
        )
        assert result.status == OrderStatus.FILLED

        # Query status
        status = await broker.get_order_status(order_id)
        assert status.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_cancel_order(self) -> None:
        """Cancel order in sandbox."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        sub = OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=uuid4(),
        )
        result = await broker.submit_order(sub)
        # Sandbox fills immediately, so cancel should fail
        cancel = await broker.cancel_order(sub.order_id, uuid4())
        assert cancel.success is False


# ============================================================
# Live Safety
# ============================================================


class TestLiveSafety:

    def test_live_disabled_by_default(self) -> None:
        """AC20: LIVE_ENABLED=False."""
        from aegis.config import Settings
        s = Settings()
        assert s.live_enabled is False

    def test_live_factory_rejects(self) -> None:
        """AC19: MercadoBitcoinBroker not created when live_enabled=False."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings
        with pytest.raises(RuntimeError):
            create_broker(Settings(trading_environment="LIVE", live_enabled=False))


# ============================================================
# Persistence
# ============================================================


class TestPersistence:

    def test_orders_persisted_after_fill(self, tmp_path) -> None:
        """AC10: Order state persisted after fill."""
        import json
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._state["orders"].append({
                "id": "test-order-123",
                "symbol": "BTC-BRL",
                "side": "BUY",
                "quantity": "0.001",
                "price": "500000",
                "status": "FILLED",
                "timestamp": "2024-01-01T00:00:00",
            })
            w._save_state()
            data = json.loads(test_file.read_text())
            assert len(data["orders"]) == 1
            assert data["orders"][0]["status"] == "FILLED"
        finally:
            worker_mod._STATE_FILE = original

    def test_idempotency_keys_persisted(self, tmp_path) -> None:
        """AC11: Idempotency keys survive restart."""
        import json
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod
        from uuid import UUID

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            key = uuid4()
            w.broker._idempotency_keys.add(key)
            w._save_state()
            data = json.loads(test_file.read_text())
            assert str(key) in data["idempotency_keys"]

            # Load into fresh worker
            w2 = AutonomousWorker()
            w2._load_state()
            assert key in w2.broker._idempotency_keys
        finally:
            worker_mod._STATE_FILE = original

    def test_last_trade_time_persisted(self, tmp_path) -> None:
        """Risk state including per-symbol trade times is persisted."""
        import json
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            from datetime import datetime as _dt
            w.risk_engine._last_trade_time["BTC-BRL"] = _dt(2024, 6, 15, tzinfo=timezone.utc)
            w._save_state()
            data = json.loads(test_file.read_text())
            assert "BTC-BRL" in data["risk_state"]["last_trade_time"]

            w2 = AutonomousWorker()
            w2._load_state()
            assert "BTC-BRL" in w2.risk_engine._last_trade_time
        finally:
            worker_mod._STATE_FILE = original


from datetime import datetime as _dt, timezone
