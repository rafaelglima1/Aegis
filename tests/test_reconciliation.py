"""AEGIS Exchange State & Reconciliation Tests."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from aegis.reconciliation import (
    DeltaKind,
    DivergenceSeverity,
    ExchangeBalance,
    ExchangeOrder,
    ExchangeSnapshot,
    LocalOrder,
    LocalPosition,
    LocalSnapshot,
    ReconciliationDelta,
    ReconciliationEngine,
    ReconciliationResult,
    ReconciliationStatus,
)

# ============================================================
# Exchange Snapshot
# ============================================================


class TestExchangeSnapshot:

    def test_valid_snapshot(self) -> None:
        """P1-01: Exchange snapshot with valid data."""
        snap = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
            open_orders=[],
        )
        assert snap.is_valid
        assert snap.get_balance("BRL") is not None
        assert snap.get_balance("BRL").available == Decimal("100")

    def test_unknown_snapshot(self) -> None:
        """P1-02: Exchange unavailable -> UNKNOWN."""
        snap = ExchangeSnapshot(status="UNKNOWN")
        assert not snap.is_valid
        assert snap.get_balance("BRL") is None

    def test_error_snapshot(self) -> None:
        """P1-02: Exchange error -> ERROR."""
        snap = ExchangeSnapshot(status="ERROR", error="Connection refused")
        assert not snap.is_valid
        assert snap.error == "Connection refused"

    def test_get_balance_missing_asset(self) -> None:
        """P1-07: Unknown asset returns None."""
        snap = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
        )
        assert snap.get_balance("BTC") is None


# ============================================================
# Reconciliation Engine
# ============================================================


class TestReconciliationEngine:

    def test_reconciled_when_balances_match(self) -> None:
        """P1-05: Local capital == exchange -> RECONCILED."""
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
        )
        engine = ReconciliationEngine()
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.RECONCILED
        assert result.is_reconciled

    def test_diverged_when_capital_mismatch(self) -> None:
        """P1-06: Local capital != exchange -> DIVERGED."""
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("50"), Decimal("0"))],
        )
        engine = ReconciliationEngine()
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.DIVERGED
        assert not result.is_reconciled
        assert len(result.divergences) >= 1
        brl_div = [d for d in result.divergences if d.field == "cash_brl"]
        assert len(brl_div) == 1
        assert brl_div[0].severity == DivergenceSeverity.CRITICAL

    def test_unknown_exchange_blocks_trading(self) -> None:
        """P1-03/P1-13: Unknown exchange -> UNKNOWN."""
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(status="UNKNOWN")
        engine = ReconciliationEngine()
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.UNKNOWN
        assert not result.is_reconciled

    def test_error_exchange_blocks_trading(self) -> None:
        """P1-03/P1-12: Exchange error -> ERROR."""
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(status="ERROR", error="Timeout")
        engine = ReconciliationEngine()
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.ERROR
        assert not result.is_reconciled
        assert result.error == "Timeout"

    def test_unknown_asset_on_exchange(self) -> None:
        """P1-07: Local position with unknown exchange balance -> DIVERGED."""
        local = LocalSnapshot(
            capital=Decimal("100"),
            positions=[LocalPosition("BTC-BRL", Decimal("0.001"), Decimal("50000"), "OPEN")],
        )
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
        )
        engine = ReconciliationEngine()
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.DIVERGED
        pos_divs = [d for d in result.divergences if "BTC" in d.field]
        assert len(pos_divs) >= 1

    def test_position_exceeds_exchange(self) -> None:
        """P1-08: Local quantity > exchange available -> DIVERGED CRITICAL."""
        local = LocalSnapshot(
            capital=Decimal("100"),
            positions=[LocalPosition("BTC-BRL", Decimal("0.01"), Decimal("50000"), "OPEN")],
        )
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[
                ExchangeBalance("BRL", Decimal("100"), Decimal("0")),
                ExchangeBalance("BTC", Decimal("0.005"), Decimal("0")),
            ],
        )
        engine = ReconciliationEngine()
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.DIVERGED
        btc_divs = [d for d in result.divergences if "BTC" in d.field]
        assert len(btc_divs) >= 1
        assert btc_divs[0].severity == DivergenceSeverity.CRITICAL

    def test_unknown_open_order(self) -> None:
        """P1-09: Exchange order unknown locally → CRITICAL divergence."""
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
            open_orders=[
                ExchangeOrder("ex-123", "BTC-BRL", "BUY", Decimal("0.001")),
            ],
        )
        engine = ReconciliationEngine()
        result = engine.reconcile(local, exchange)
        # Active exchange order not known locally is CRITICAL → blocks trading
        assert result.status == ReconciliationStatus.DIVERGED
        assert len(result.divergences) >= 1
        orphan_divs = [d for d in result.divergences if "not known locally" in d.message]
        assert len(orphan_divs) == 1
        assert orphan_divs[0].severity == DivergenceSeverity.CRITICAL

    def test_local_order_not_on_exchange(self) -> None:
        """P0-09: Local pending order not found on exchange -> UNKNOWN (not assumed resolved)."""
        local = LocalSnapshot(
            capital=Decimal("100"),
            orders=[LocalOrder("local-123", "BTC-BRL", "BUY", Decimal("0.001"), "SUBMITTED")],
        )
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
        )
        engine = ReconciliationEngine()
        result = engine.reconcile(local, exchange)
        # A pending order missing from open orders is NOT assumed non-existent.
        # Without order-history evidence we cannot determine its state → UNKNOWN.
        assert result.status == ReconciliationStatus.UNKNOWN
        assert not result.is_reconciled
        assert result.error is not None and "local-123" in result.error

    def test_no_secrets_in_result(self) -> None:
        """P1-17: No secrets in reconciliation result."""
        result = ReconciliationResult(
            status=ReconciliationStatus.RECONCILED,
            divergences=[],
        )
        s = str(result)
        assert "api_key" not in s.lower()
        assert "api_secret" not in s.lower()


# ============================================================
# Sandbox Broker Snapshot
# ============================================================


class TestSandboxBrokerSnapshot:

    @pytest.mark.asyncio
    async def test_sandbox_snapshot_valid(self) -> None:
        """Sandbox broker produces valid snapshot."""
        from aegis.execution.sandbox import SandboxBroker

        broker = SandboxBroker(initial_balance=Decimal("100"))
        snap = await broker.get_exchange_snapshot()
        assert snap is not None
        assert snap.is_valid
        brl = snap.get_balance("BRL")
        assert brl is not None
        assert brl.available == Decimal("100")

    @pytest.mark.asyncio
    async def test_sandbox_snapshot_after_trade(self) -> None:
        """Sandbox snapshot reflects trades."""
        from aegis.domain.enums import OrderSide
        from aegis.execution.broker import OrderSubmission
        from aegis.execution.sandbox import SandboxBroker

        broker = SandboxBroker(initial_balance=Decimal("1000"))
        sub = OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("500000"),
            correlation_id=uuid4(),
        )
        await broker.submit_order(sub)

        snap = await broker.get_exchange_snapshot()
        assert snap.is_valid
        btc = snap.get_balance("BTC")
        assert btc is not None
        assert btc.available > 0


# ============================================================
# DeltaKind Classification (borrowed from Vibe-Trading)
# ============================================================


class TestDeltaKindClassification:

    def test_reconciled_has_no_deltas_and_no_halt(self) -> None:
        """RECONCILED → no deltas, no halt, halt_reason=None."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
        )
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.RECONCILED
        assert result.deltas == []
        assert result.requires_halt is False
        assert result.halt_reason is None

    def test_cash_mismatch_delta_kind(self) -> None:
        """Cash divergence → CASH_MISMATCH delta, requires_halt."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("80"), Decimal("0"))],
        )
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.DIVERGED
        assert result.requires_halt is True
        kinds = {d.kind for d in result.deltas}
        assert DeltaKind.CASH_MISMATCH in kinds
        assert result.halt_reason is not None
        assert "CASH_MISMATCH" in result.halt_reason
        assert "cash_brl" in result.halt_reason

    def test_position_mismatch_delta_kind(self) -> None:
        """Position > exchange balance → POSITION_MISMATCH delta."""
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
        assert result.requires_halt is True
        kinds = {d.kind for d in result.deltas}
        assert DeltaKind.POSITION_MISMATCH in kinds

    def test_orphan_order_delta_kind(self) -> None:
        """Exchange order unknown locally → ORPHAN_ORDER delta."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
            open_orders=[ExchangeOrder("ex-orphan", "BTC-BRL", "BUY", Decimal("0.001"))],
        )
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.DIVERGED
        assert result.requires_halt is True
        kinds = {d.kind for d in result.deltas}
        assert DeltaKind.ORPHAN_ORDER in kinds
        assert "ex-orphan" in result.halt_reason

    def test_mid_order_ambiguous_delta_kind(self) -> None:
        """Local pending order not on exchange → MID_ORDER_AMBIGUOUS (UNKNOWN)."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(
            capital=Decimal("100"),
            orders=[LocalOrder("local-1", "BTC-BRL", "BUY", Decimal("0.001"), "SUBMITTED")],
        )
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("100"), Decimal("0"))],
        )
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.UNKNOWN
        assert result.requires_halt is True
        kinds = {d.kind for d in result.deltas}
        assert DeltaKind.MID_ORDER_AMBIGUOUS in kinds
        assert result.halt_reason is not None
        assert "local-1" in result.halt_reason

    def test_brl_locked_unknown_delta_kind_is_cash_mismatch(self) -> None:
        """BRL locked not explainable → CASH_MISMATCH (not MID_ORDER)."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("50"), Decimal("50"))],
        )
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.UNKNOWN
        assert result.requires_halt is True
        kinds = {d.kind for d in result.deltas}
        assert DeltaKind.CASH_MISMATCH in kinds
        assert DeltaKind.MID_ORDER_AMBIGUOUS not in kinds

    def test_error_snapshot_requires_halt(self) -> None:
        """ERROR snapshot → requires_halt True, halt_reason = error."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(status="ERROR", error="Exchange query failed")
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.ERROR
        assert result.requires_halt is True
        assert result.halt_reason == "Exchange query failed"

    def test_unknown_snapshot_requires_halt(self) -> None:
        """UNKNOWN snapshot → requires_halt True."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(status="UNKNOWN")
        result = engine.reconcile(local, exchange)
        assert result.status == ReconciliationStatus.UNKNOWN
        assert result.requires_halt is True

    def test_summary_includes_deltas(self) -> None:
        """summary() exposes the classified delta kinds."""
        engine = ReconciliationEngine()
        local = LocalSnapshot(capital=Decimal("100"))
        exchange = ExchangeSnapshot(
            status="VALID",
            balances=[ExchangeBalance("BRL", Decimal("80"), Decimal("0"))],
        )
        result = engine.reconcile(local, exchange)
        s = result.summary()
        assert "deltas=[" in s
        assert "CASH_MISMATCH" in s
        assert "halt=" in s

    def test_reconciliation_delta_blocks_trading_flag(self) -> None:
        """ReconciliationDelta carries blocks_trading."""
        delta = ReconciliationDelta(
            kind=DeltaKind.ORPHAN_ORDER,
            entity="order_ex-1",
            local_value="NOT_LOCAL",
            exchange_value="BUY 0.001 BTC-BRL",
            message="Exchange has active order unknown locally",
        )
        assert delta.blocks_trading is True
        assert delta.kind == DeltaKind.ORPHAN_ORDER
        assert delta.entity == "order_ex-1"
