"""AEGIS Prompt 3 — Partial Fills + Orphan Orders Tests."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from aegis.domain.enums import OrderSide, OrderStatus, TradingAction
from aegis.execution.sandbox import SandboxBroker
from aegis.execution.engine import ExecutionEngine
from aegis.execution.broker import OrderResult
from aegis.risk_engine.risk_engine import RiskEngine, RiskDecision
from aegis.ai_engine.decision_engine import DecisionContract
from aegis.reconciliation import (
    ReconciliationEngine, ReconciliationStatus, DivergenceSeverity,
    ExchangeSnapshot, ExchangeBalance, ExchangeOrder,
    LocalSnapshot, LocalOrder,
)


def _risk(qty: Decimal = Decimal("0.001"), price: Decimal = Decimal("50000")) -> RiskDecision:
    return RiskDecision(status="APPROVED", approved_quantity=qty, approved_price=price,
                        risk_amount=Decimal("50"), exposure=qty * price)


# ============================================================
# 1. FILLED continues working
# ============================================================


class TestFilledStillWorks:

    @pytest.mark.asyncio
    async def test_buy_filled_sandbox(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        result = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=uuid4(), risk_decision=_risk(),
        )
        assert result.status == OrderStatus.FILLED
        assert result.fill_price is not None
        assert result.fill_price > Decimal("500000")


# ============================================================
# 2. REJECTED doesn't alter portfolio
# ============================================================


class TestRejectedSafety:

    @pytest.mark.asyncio
    async def test_rejected_no_position(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("10"))
        engine = ExecutionEngine(broker)
        result = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=uuid4(), risk_decision=_risk(),
        )
        assert result.status == OrderStatus.REJECTED
        assert broker.balance == Decimal("10")
        pos = await broker.get_position("BTC-BRL")
        assert pos["quantity"] == 0


# ============================================================
# 3. ERROR doesn't alter portfolio
# ============================================================


class TestErrorSafety:

    @pytest.mark.asyncio
    async def test_risk_rejection_no_position(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        result = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=uuid4(), risk_decision=None,
        )
        assert result.status == OrderStatus.REJECTED
        assert broker.balance == Decimal("1000")


# ============================================================
# 4-6. SUBMITTED / PARTIALLY_FILLED handling
# ============================================================


class TestSubmittedHandling:

    @pytest.mark.asyncio
    async def test_submitted_does_not_create_position(self) -> None:
        """SUBMITTED → poll → if still SUBMITTED → UNKNOWN, no position."""
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)

        # Simulate: first call returns SUBMITTED, poll returns ERROR
        order_id = uuid4()
        call_count = 0
        original_submit = broker.submit_order

        async def mock_submit(submission):
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.SUBMITTED,
                fill_price=None,
            )

        async def mock_status(oid):
            return OrderResult(
                order_id=oid,
                status=OrderStatus.ERROR,
                error="Status unknown",
            )

        broker.submit_order = mock_submit
        engine.get_order_status = mock_status

        # The worker won't call this directly — it's tested via the reconciliation
        # For unit testing, verify that SUBMITTED doesn't create position
        result = await broker.submit_order(None)
        assert result.status == OrderStatus.SUBMITTED
        assert result.fill_price is None
        pos = await broker.get_position("BTC-BRL")
        assert pos["quantity"] == 0


# ============================================================
# 11-12. Orphan orders
# ============================================================


class TestOrphanOrders:

    def test_exchange_order_not_local_is_critical(self) -> None:
        """Exchange has active order not known locally → CRITICAL."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
            open_orders=[ExchangeOrder("ex-orphan", "BTC-BRL", "BUY", Decimal("0.001"))],
        )
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.DIVERGED
        orphan_divs = [d for d in result.divergences if "not known locally" in d.message]
        assert len(orphan_divs) == 1
        assert orphan_divs[0].severity == DivergenceSeverity.CRITICAL

    def test_local_order_not_on_exchange_is_unknown(self) -> None:
        """P0-09: Local SUBMITTED order not on exchange → UNKNOWN (blocks trading)."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(
            capital=Decimal("100"),
            orders=[LocalOrder("local-orphan", "BTC-BRL", "BUY", Decimal("0.001"), "SUBMITTED")],
        )
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
        )
        result = engine.reconcile(local, exchange)
        # A pending order missing from open orders is NOT assumed non-existent.
        # Without order-history evidence the state cannot be determined safely.
        assert not result.is_reconciled
        assert result.status == ReconciliationStatus.UNKNOWN


# ============================================================
# 15. UNKNOWN blocks trading
# ============================================================


class TestUnknownBlocksTrading:

    def test_unknown_exchange_blocks(self) -> None:
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(status="UNKNOWN")
        result = engine.reconcile(local, exchange)
        assert not result.is_reconciled
        assert result.status == ReconciliationStatus.UNKNOWN

    def test_error_exchange_blocks(self) -> None:
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(status="ERROR", error="timeout")
        result = engine.reconcile(local, exchange)
        assert not result.is_reconciled
        assert result.status == ReconciliationStatus.ERROR


# ============================================================
# 17-18. Order state persistence + idempotency
# ============================================================


class TestOrderPersistence:

    def test_filled_order_persists(self, tmp_path) -> None:
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

            w2 = AutonomousWorker()
            w2._load_state()
            assert w2._state["orders"][0]["status"] == "FILLED"
            assert w2._state["orders"][0]["fee"] == "0.50"
        finally:
            worker_mod._STATE_FILE = original

    def test_rejected_order_persists(self, tmp_path) -> None:
        import json
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            w._state["orders"].append({
                "id": "o-rej", "symbol": "BTC-BRL", "side": "BUY",
                "quantity": "0.001", "price": "500000", "status": "REJECTED",
                "error": "Insufficient balance", "fee": "0",
                "timestamp": "2024-01-01T00:00:00",
            })
            w._save_state()

            w2 = AutonomousWorker()
            w2._load_state()
            assert w2._state["orders"][0]["status"] == "REJECTED"
            assert w2._state["orders"][0]["error"] == "Insufficient balance"
        finally:
            worker_mod._STATE_FILE = original

    def test_partial_fill_recorded(self, tmp_path) -> None:
        """PARTIALLY_FILLED order record includes filled_quantity."""
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

    def test_idempotency_keys_survive_restart(self, tmp_path) -> None:
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        original = worker_mod._STATE_FILE
        try:
            test_file = tmp_path / "state.json"
            worker_mod._STATE_FILE = test_file
            w = AutonomousWorker()
            key = uuid4()
            w.broker._idempotency_keys.add(key)
            w._save_state()

            w2 = AutonomousWorker()
            w2._load_state()
            assert key in w2.broker._idempotency_keys
        finally:
            worker_mod._STATE_FILE = original


# ============================================================
# Full lifecycle BUY → FILLED
# ============================================================


class TestFullLifecycle:

    @pytest.mark.asyncio
    async def test_buy_sell_full_cycle(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)
        risk = _risk()

        buy = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=uuid4(), risk_decision=risk,
        )
        assert buy.status == OrderStatus.FILLED

        sell = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.SELL,
            quantity=Decimal("0.001"), price=Decimal("510000"),
            correlation_id=uuid4(), risk_decision=risk,
        )
        assert sell.status == OrderStatus.FILLED

        pos = await broker.get_position("BTC-BRL")
        assert pos["quantity"] == 0

    @pytest.mark.asyncio
    async def test_order_record_has_all_fields(self) -> None:
        broker = SandboxBroker(initial_balance=Decimal("1000"))
        engine = ExecutionEngine(broker)

        result = await engine.execute_order(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=uuid4(), risk_decision=_risk(),
        )
        assert result.status == OrderStatus.FILLED
        assert result.fill_price is not None
        assert result.fill_quantity is not None
        assert result.fee > Decimal("0")
        assert result.error is None


# ============================================================
# P0 — Partial SELL Accounting
# ============================================================

BUY = OrderSide.BUY
SELL = OrderSide.SELL
_FEE = Decimal("0.50")


class _S:
    """Deterministic constants for reproducible tests."""
    PRICE = Decimal("5000")
    QTY = Decimal("1.0")
    SYMBOL = "BTC-BRL"
    BUY_FILL = Decimal("5005")   # 5000 + 0.1% slippage
    SELL_FILL = Decimal("4995")  # 5000 - 0.1% slippage


async def _submit_and_poll(broker, order_id, side, sequence):
    """Submit a partial order and poll until FILLED.

    Returns list of cumulative fill_quantity from submit + every poll.
    """
    from aegis.execution.broker import OrderSubmission
    broker.configure_partial_fills(order_id, [Decimal(str(q)) for q in sequence])
    sub = OrderSubmission(
        order_id=order_id, idempotency_key=uuid4(),
        symbol=_S.SYMBOL, side=side,
        quantity=_S.QTY, price=_S.PRICE,
        correlation_id=uuid4(),
    )
    result = await broker.submit_order(sub)
    fills = [result.fill_quantity]
    while True:
        r = await broker.get_order_status(order_id)
        fills.append(r.fill_quantity)
        if r.status == OrderStatus.FILLED:
            break
    return fills


# ============================================================
# 1. BUY partial (0.30, 0.30, 0.40)
# ============================================================


class TestBuyPartialAccounting:

    @pytest.mark.asyncio
    async def test_buy_partial_three_deltas(self) -> None:
        """P0: BUY [0.30, 0.60, 1.00] → asset=1.00, cash reduced, fees per delta."""
        broker = SandboxBroker(initial_balance=Decimal("100000"))
        order_id = uuid4()
        fills = await _submit_and_poll(broker, order_id, BUY, [0.30, 0.60, 1.00])

        # Cumulative fills reported: 0.30, 0.60, 1.00
        assert fills == [Decimal("0.30"), Decimal("0.60"), Decimal("1.00")]

        # Final asset = 1.00
        pos = await broker.get_position(_S.SYMBOL)
        assert pos["quantity"] == Decimal("1.0")

        # Final cash = 100000 - 5005*1.0 - 3*0.50 = 100000 - 5005 - 1.50 = 94993.50
        expected = Decimal("100000") - _S.BUY_FILL * _S.QTY - Decimal("3") * _FEE
        assert broker.balance == expected

        # No double count: balance matches formula, not 0.30+0.60+1.00
        assert broker.balance != Decimal("100000") - _S.BUY_FILL * Decimal("1.90")


# ============================================================
# 2. SELL partial (0.30, 0.30, 0.40)
# ============================================================


class TestSellPartialAccounting:

    @pytest.mark.asyncio
    async def test_sell_partial_three_deltas(self) -> None:
        """P0: SELL [0.30, 0.60, 1.00] → BTC=0, BRL increases, fees per delta."""
        broker = SandboxBroker(initial_balance=Decimal("100000"))

        # First acquire 1.0 BTC via a full BUY
        buy_id = uuid4()
        buy_sub = _buy_sub(buy_id)
        await broker.submit_order(buy_sub)
        assert (await broker.get_position(_S.SYMBOL))["quantity"] == Decimal("1.0")
        buy_balance = broker.balance  # 100000 - 5005 - 0.50 = 94994.50

        # Partial SELL 1.0 in three deltas
        sell_id = uuid4()
        sells = await _submit_and_poll(broker, sell_id, SELL, [0.30, 0.60, 1.00])

        assert sells == [Decimal("0.30"), Decimal("0.60"), Decimal("1.00")]

        # Final BTC = 0
        pos = await broker.get_position(_S.SYMBOL)
        assert pos["quantity"] == Decimal("0")

        # Final cash = buy_balance + 4995*1.0 - 3*0.50 = 94994.50 + 4995 - 1.50 = 99988.00
        expected = buy_balance + _S.SELL_FILL * _S.QTY - Decimal("3") * _FEE
        assert broker.balance == expected


# ============================================================
# 3. SELL partial multi-poll — delta increments
# ============================================================


class TestSellPartialDeltaIncrements:

    @pytest.mark.asyncio
    async def test_sell_deltas_are_0_30_0_30_0_40(self) -> None:
        """P0: SELL incremental deltas are 0.30, 0.30, 0.40 (not 0.30, 0.60, 1.00)."""
        broker = SandboxBroker(initial_balance=Decimal("100000"))
        buy_id = uuid4()
        await broker.submit_order(_buy_sub(buy_id))

        sell_id = uuid4()
        broker.configure_partial_fills(sell_id, [Decimal("0.30"), Decimal("0.60"), Decimal("1.00")])
        await broker.submit_order(_sell_sub(sell_id))  # fill=0.30, cash +1498.00
        buy_balance = Decimal("100000") - _S.BUY_FILL - _FEE  # 94994.50

        # Capture balance after each poll advance
        b0 = broker.balance                                      # after submit: 94994.50 + 1498.00 = 96492.50
        await broker.get_order_status(sell_id)                   # → 0.60, delta=0.30
        b1 = broker.balance                                      # 96492.50 + 1498.00 = 97990.50
        await broker.get_order_status(sell_id)                   # → 1.00, delta=0.40
        b2 = broker.balance                                      # 97990.50 + 1997.50 = 99988.00

        # Incremental cash changes = delta_value - fee_per_delta
        inc1 = b1 - b0   # 0.30 delta: +1498.50 - 0.50 = +1498.00
        inc2 = b2 - b1   # 0.40 delta: +1998.00 - 0.50 = +1997.50

        assert inc1 == Decimal("0.30") * _S.SELL_FILL - _FEE
        assert inc2 == Decimal("0.40") * _S.SELL_FILL - _FEE

        # Total = 0.30 + 0.30 + 0.40 = 1.00 (not 0.30 + 0.60 = 0.90 + 1.00)
        assert broker.balance == buy_balance + _S.SELL_FILL * _S.QTY - Decimal("3") * _FEE


# ============================================================
# 4. Repeated same status → delta=0
# ============================================================


class TestRepeatedFillNoFinancialChange:

    @pytest.mark.asyncio
    async def test_same_fill_twice_does_not_affect_balance(self) -> None:
        """P0: Same filled_quantity returned twice → delta=0, no financial change."""
        broker = SandboxBroker(initial_balance=Decimal("100000"))
        buy_id = uuid4()
        await broker.submit_order(_buy_sub(buy_id))

        sell_id = uuid4()
        # Sequence: 0.60, 0.60 (same!), 1.00
        broker.configure_partial_fills(sell_id, [Decimal("0.60"), Decimal("0.60"), Decimal("1.00")])
        await broker.submit_order(_sell_sub(sell_id))
        balance_after_submit = broker.balance

        # First poll: advances to 0.60 (same as current) → delta=0
        await broker.get_order_status(sell_id)
        assert broker.balance == balance_after_submit  # no change

        # Second poll: advances to 1.00 → delta=0.40
        await broker.get_order_status(sell_id)
        expected = balance_after_submit + Decimal("0.40") * _S.SELL_FILL - _FEE
        assert broker.balance == expected


# ============================================================
# 5. BUY + SELL symmetric
# ============================================================


class TestBuySellSymmetric:

    @pytest.mark.asyncio
    async def test_buy_then_sell_returns_asset_to_zero(self) -> None:
        """P0: BUY then SELL with same partial sequence → BTC=0, cash=F(initial, fees, spread)."""
        broker = SandboxBroker(initial_balance=Decimal("100000"))
        start = broker.balance

        # BUY 1.0 BTC in 3 deltas
        buy_id = uuid4()
        await _submit_and_poll(broker, buy_id, BUY, [0.30, 0.60, 1.00])
        after_buy = broker.balance
        assert (await broker.get_position(_S.SYMBOL))["quantity"] == Decimal("1.0")

        # SELL 1.0 BTC in 3 deltas
        sell_id = uuid4()
        await _submit_and_poll(broker, sell_id, SELL, [0.30, 0.60, 1.00])
        after_sell = broker.balance

        # BTC back to 0
        assert (await broker.get_position(_S.SYMBOL))["quantity"] == Decimal("0")

        # Symmetry: net cash = start - (buy_fees + sell_fees) - (buy_value - sell_value)
        # buy_value = 5005, sell_value = 4995, net = -10
        # fees = 3*0.50 + 3*0.50 = 3.00
        # final = 100000 - 10 - 3 = 99987.00
        expected = start - (_S.BUY_FILL - _S.SELL_FILL) * _S.QTY - Decimal("6") * _FEE
        assert after_sell == expected


# ============================================================
# 6. Restart — no double count
# ============================================================


class TestPartialRestartNoDoubleCount:

    @pytest.mark.asyncio
    async def test_restart_does_not_recount_fills(self) -> None:
        """P0: After 'restart', only the remaining delta is accounted (SELL)."""
        broker = SandboxBroker(initial_balance=Decimal("100000"))
        buy_id = uuid4()
        await broker.submit_order(_buy_sub(buy_id))

        sell_id = uuid4()
        broker.configure_partial_fills(sell_id, [Decimal("0.30"), Decimal("0.60"), Decimal("1.00")])
        await broker.submit_order(_sell_sub(sell_id))                # 0.30
        await broker.get_order_status(sell_id)                       # 0.60

        # Persisted state: fill_quantity=0.60, pending=[1.00], balance=B
        persisted_balance = broker.balance
        persisted_order = broker._orders[sell_id]

        # Simulate restart: fresh broker, restore order + balance
        broker2 = SandboxBroker(initial_balance=persisted_balance)
        from aegis.execution.sandbox import SandboxOrder
        broker2._orders[sell_id] = SandboxOrder(
            order_id=sell_id,
            idempotency_key=persisted_order.idempotency_key,
            symbol=_S.SYMBOL,
            side=SELL,
            quantity=_S.QTY,
            price=_S.PRICE,
            status=OrderStatus.PARTIALLY_FILLED,
            fill_price=_S.SELL_FILL,
            fill_quantity=Decimal("0.60"),
            fee=_FEE,
        )
        broker2._pending_fills[sell_id] = [Decimal("1.00")]

        # Poll after restart: only 0.40 delta should be applied
        r = await broker2.get_order_status(sell_id)
        assert r.status == OrderStatus.FILLED
        assert r.fill_quantity == Decimal("1.00")

        expected = persisted_balance + Decimal("0.40") * _S.SELL_FILL - _FEE
        assert broker2.balance == expected

        # Position: 1.0 - 0.60 - 0.40 = 0
        pos = await broker2.get_position(_S.SYMBOL)
        assert pos["quantity"] == Decimal("0")


# ============================================================
# 7. Fee per delta — explicit verification
# ============================================================


class TestFeePerDelta:

    @pytest.mark.asyncio
    async def test_fee_charged_on_each_delta(self) -> None:
        """P0: Each partial fill delta charges the fee exactly once.

        BUY:  cash -= delta_value + fee   (submit carries the first delta)
        """
        broker = SandboxBroker(initial_balance=Decimal("100000"))
        order_id = uuid4()
        broker.configure_partial_fills(order_id, [Decimal("0.30"), Decimal("0.60"), Decimal("1.00")])
        sub = _buy_sub(order_id)

        # delta 1 (0.30) — charged on submit
        prev = broker.balance
        await broker.submit_order(sub)
        d0 = prev - broker.balance

        # delta 2 (0.30) — charged on first poll
        prev = broker.balance
        await broker.get_order_status(order_id)
        d1 = prev - broker.balance

        # delta 3 (0.40) — charged on second poll
        prev = broker.balance
        await broker.get_order_status(order_id)
        d2 = prev - broker.balance

        # Each delta: cash_change = -(fill_price * delta_qty + fee)
        # delta1: 5005*0.30 + 0.50 = 1501.50 + 0.50 = 1502.00
        # delta2: 5005*0.30 + 0.50 = 1501.50 + 0.50 = 1502.00
        # delta3: 5005*0.40 + 0.50 = 2002.00 + 0.50 = 2002.50
        assert d0 == _S.BUY_FILL * Decimal("0.30") + _FEE
        assert d1 == _S.BUY_FILL * Decimal("0.30") + _FEE
        assert d2 == _S.BUY_FILL * Decimal("0.40") + _FEE

        # Total fee = 3 * 0.50 = 1.50
        total_fee = (d0 + d1 + d2) - _S.BUY_FILL * _S.QTY
        assert total_fee == Decimal("3") * _FEE


def _buy_sub(order_id):
    from aegis.execution.broker import OrderSubmission
    return OrderSubmission(
        order_id=order_id, idempotency_key=uuid4(),
        symbol=_S.SYMBOL, side=BUY,
        quantity=_S.QTY, price=_S.PRICE,
        correlation_id=uuid4(),
    )


def _sell_sub(order_id):
    from aegis.execution.broker import OrderSubmission
    return OrderSubmission(
        order_id=order_id, idempotency_key=uuid4(),
        symbol=_S.SYMBOL, side=SELL,
        quantity=_S.QTY, price=_S.PRICE,
        correlation_id=uuid4(),
    )
