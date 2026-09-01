"""AEGIS Reconciliation — Exchange State & Local State comparison.

Deterministic reconciliation between exchange-reported state and local state.
No automatic correction. Divergence blocks trading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

logger = logging.getLogger("aegis.reconciliation")


# ============================================================
# Enums
# ============================================================


class ReconciliationStatus(str, Enum):
    """Outcome of reconciliation."""
    RECONCILED = "RECONCILED"
    DIVERGED = "DIVERGED"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


class DivergenceSeverity(str, Enum):
    """Severity of a divergence."""
    CRITICAL = "CRITICAL"      # blocks trading
    WARNING = "WARNING"        # logged but may not block
    INFO = "INFO"              # informational


class DeltaKind(str, Enum):
    """Classification of a single reconciliation delta.

    Adapted from Vibe-Trading's reconcile delta kinds — each delta between
    exchange truth and local state is classified so an operator can see WHY
    trading was halted, not just that it was.

    - CASH_MISMATCH:      local cash/BRL does not match exchange BRL.
    - POSITION_MISMATCH:  local asset position does not match exchange balance.
    - ORPHAN_ORDER:       exchange has an open order unknown locally.
    - MID_ORDER_AMBIGUOUS: a local pending/unknown order cannot be resolved
                           against the exchange (may have filled, rejected, or
                           vanished) — the crash/mid-order case.
    """
    CASH_MISMATCH = "CASH_MISMATCH"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    ORPHAN_ORDER = "ORPHAN_ORDER"
    MID_ORDER_AMBIGUOUS = "MID_ORDER_AMBIGUOUS"


# ============================================================
# Exchange Snapshot
# ============================================================


@dataclass
class ExchangeBalance:
    """Balance for a single asset on the exchange."""
    asset: str
    available: Decimal
    locked: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        return self.available + self.locked


@dataclass
class ExchangeOrder:
    """Order as reported by the exchange."""
    exchange_order_id: str
    symbol: str
    side: str           # "BUY" or "SELL"
    quantity: Decimal
    filled_quantity: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    status: str = "UNKNOWN"  # placed, partially_filled, filled, cancelled, expired
    timestamp: str = ""


@dataclass
class ExchangeSnapshot:
    """Snapshot of exchange state at a point in time.

    status:
    - VALID: snapshot was successfully obtained
    - UNKNOWN: exchange could not be queried
    - ERROR: query failed with an error
    """
    status: str = "UNKNOWN"
    balances: list[ExchangeBalance] = field(default_factory=list)
    open_orders: list[ExchangeOrder] = field(default_factory=list)
    error: str | None = None
    timestamp: str = ""

    @property
    def is_valid(self) -> bool:
        return self.status == "VALID"

    def get_balance(self, asset: str) -> ExchangeBalance | None:
        """Get balance for a specific asset."""
        for b in self.balances:
            if b.asset == asset:
                return b
        return None


# ============================================================
# Local Snapshot
# ============================================================


@dataclass
class LocalPosition:
    """Position as tracked locally."""
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    status: str  # "OPEN", "CLOSED"


@dataclass
class LocalOrder:
    """Order as tracked locally."""
    local_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    status: str
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal = Decimal("0")
    exchange_order_id: str = ""


@dataclass
class LocalSnapshot:
    """Snapshot of local worker state for reconciliation.

    P0-07: Financial concepts are separated so reconciliation never compares
    total equity against exchange BRL available.
      - capital          → local cash available (BRL)
      - cash_locked      → BRL reserved/locked by open orders
      - positions        → open asset positions
      - realized_pnl     → realized P&L (informational)
      - unrealized_pnl   → unrealized P&L (informational)
      - equity           → total equity (informational, not compared 1:1)
    """
    capital: Decimal = Decimal("0")
    positions: list[LocalPosition] = field(default_factory=list)
    orders: list[LocalOrder] = field(default_factory=list)
    state_valid: bool = True
    cash_locked: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")


# ============================================================
# Divergence
# ============================================================


@dataclass
class Divergence:
    """A single divergence between local and exchange state."""
    field: str
    local_value: Any
    exchange_value: Any
    severity: DivergenceSeverity = DivergenceSeverity.WARNING
    message: str = ""


@dataclass
class ReconciliationDelta:
    """A classified delta between exchange truth and local state.

    Carries the WHY behind a halted reconciliation, keyed by ``kind`` and
    the affected entity (``cash_brl``, ``position_BTC``, ``order_<id>``).
    """
    kind: DeltaKind
    entity: str
    local_value: Any
    exchange_value: Any
    message: str = ""
    blocks_trading: bool = True


# ============================================================
# Reconciliation Result
# ============================================================


@dataclass
class ReconciliationResult:
    """Result of comparing local state against exchange state."""
    status: ReconciliationStatus = ReconciliationStatus.UNKNOWN
    divergences: list[Divergence] = field(default_factory=list)
    deltas: list[ReconciliationDelta] = field(default_factory=list)
    exchange_snapshot: ExchangeSnapshot | None = None
    local_snapshot: LocalSnapshot | None = None
    error: str | None = None

    @property
    def is_reconciled(self) -> bool:
        return self.status == ReconciliationStatus.RECONCILED

    @property
    def has_critical_divergence(self) -> bool:
        return any(
            d.severity == DivergenceSeverity.CRITICAL
            for d in self.divergences
        )

    @property
    def requires_halt(self) -> bool:
        """Trading must halt when the local state cannot be trusted.

        RECONCILED is the only non-halting state. DIVERGED (known mismatch),
        UNKNOWN (cannot determine), and ERROR all require halting.
        """
        return not self.is_reconciled

    @property
    def halt_reason(self) -> str | None:
        """Human-readable WHY behind a halt, or None when safe to trade.

        Prefers the classified delta that blocks trading; falls back to the
        raw error/status. This is the operational value of DeltaKind — an
        operator sees *which order/asset/cash* caused the halt.
        """
        if not self.requires_halt:
            return None
        if self.error:
            return self.error
        for d in self.deltas:
            if d.blocks_trading:
                return f"{d.kind.value}: {d.entity} — {d.message}"
        return f"status={self.status.value}"

    def summary(self) -> str:
        parts = [f"status={self.status.value}"]
        if self.requires_halt:
            parts.append(f"halt={self.halt_reason}")
        if self.divergences:
            parts.append(f"divergences={len(self.divergences)}")
        if self.deltas:
            kinds = ",".join(d.kind.value for d in self.deltas)
            parts.append(f"deltas=[{kinds}]")
        if self.error:
            parts.append(f"error={self.error}")
        return " ".join(parts)


# ============================================================
# Reconciliation Engine
# ============================================================


class ReconciliationEngine:
    """Deterministic reconciliation between local and exchange state.

    No automatic correction. Divergence or UNKNOWN blocks trading.
    """

    def __init__(self) -> None:
        self._unknown_reason: str | None = None
        self._unknown_kind: DeltaKind | None = None

    def _set_unknown(self, reason: str, kind: DeltaKind | None = None) -> None:
        """Mark that the local model lacks information for a safe verdict.

        P0-07/P0-13: NUNCA assumir zero. When we cannot determine a divergence
        with safety, the result is UNKNOWN and trading is blocked.
        ``kind`` classifies the delta for ``halt_reason`` (default
        MID_ORDER_AMBIGUOUS for order-level, CASH_MISMATCH for balance-level).
        """
        if self._unknown_reason is None:
            self._unknown_reason = reason
            self._unknown_kind = kind

    def reconcile(
        self,
        local: LocalSnapshot,
        exchange: ExchangeSnapshot,
    ) -> ReconciliationResult:
        """Compare local state against exchange state.

        Returns RECONCILED, DIVERGED, UNKNOWN, or ERROR.
        """
        self._unknown_reason = None
        self._unknown_kind = None
        result = ReconciliationResult(
            exchange_snapshot=exchange,
            local_snapshot=local,
        )

        # If exchange snapshot is not valid, we can't reconcile
        if not exchange.is_valid:
            if exchange.status == "ERROR":
                result.status = ReconciliationStatus.ERROR
                result.error = exchange.error or "Exchange query failed"
            else:
                result.status = ReconciliationStatus.UNKNOWN
                result.error = "Exchange state unknown"
            return result

        divergences: list[Divergence] = []

        # Balance reconciliation
        divergences.extend(self._reconcile_balances(local, exchange))

        # Open order reconciliation
        divergences.extend(self._reconcile_orders(local, exchange))

        result.divergences = divergences

        # P0-13: UNKNOWN takes precedence — cannot determine with safety
        if self._unknown_reason is not None:
            result.status = ReconciliationStatus.UNKNOWN
            result.error = self._unknown_reason
            result.deltas = self._build_deltas(divergences)
            return result

        if not divergences:
            result.status = ReconciliationStatus.RECONCILED
        elif any(d.severity == DivergenceSeverity.CRITICAL for d in divergences):
            result.status = ReconciliationStatus.DIVERGED
        else:
            # Warnings only — still RECONCILED but with notes
            result.status = ReconciliationStatus.RECONCILED

        result.deltas = self._build_deltas(divergences)
        return result

    def _build_deltas(
        self,
        divergences: list[Divergence],
    ) -> list[ReconciliationDelta]:
        """Classify divergences + unknown state into classified deltas.

        Each divergence is mapped to a DeltaKind based on its field name.
        An unresolved ``_unknown_reason`` produces a MID_ORDER_AMBIGUOUS or
        CASH_MISMATCH delta depending on the kind passed to ``_set_unknown``.
        """
        deltas: list[ReconciliationDelta] = []

        for d in divergences:
            kind = self._kind_for_divergence(d)
            if kind is not None:
                deltas.append(ReconciliationDelta(
                    kind=kind,
                    entity=d.field,
                    local_value=d.local_value,
                    exchange_value=d.exchange_value,
                    message=d.message,
                    blocks_trading=(d.severity == DivergenceSeverity.CRITICAL),
                ))

        if self._unknown_reason is not None:
            # If the unknown was tagged as CASH_MISMATCH, use that;
            # otherwise default to MID_ORDER_AMBIGUOUS (order-level ambiguity).
            uk = self._unknown_kind or DeltaKind.MID_ORDER_AMBIGUOUS
            deltas.append(ReconciliationDelta(
                kind=uk,
                entity="*",
                local_value="LOCAL_STATE",
                exchange_value="UNKNOWN",
                message=self._unknown_reason,
                blocks_trading=True,
            ))

        return deltas

    @staticmethod
    def _kind_for_divergence(d: Divergence) -> DeltaKind | None:
        """Map a divergence field name to its DeltaKind."""
        if d.field == "cash_brl":
            return DeltaKind.CASH_MISMATCH
        if d.field.startswith("position_"):
            return DeltaKind.POSITION_MISMATCH
        if d.field.startswith("order_") and d.local_value == "NOT_LOCAL":
            return DeltaKind.ORPHAN_ORDER
        return None

    def _reconcile_balances(
        self,
        local: LocalSnapshot,
        exchange: ExchangeSnapshot,
    ) -> list[Divergence]:
        """Compare local cash/positions against exchange balances.

        P0-07: Never compare total equity against exchange BRL available.
        Local cash maps to exchange BRL; positions map to exchange assets.
        Locked balances are accounted for; when the local model cannot explain
        locked funds, the result is UNKNOWN (never assumed to be zero).
        """
        divergences: list[Divergence] = []

        brl_balance = exchange.get_balance("BRL")
        if brl_balance is None:
            divergences.append(Divergence(
                field="cash_brl",
                local_value=str(local.capital),
                exchange_value="UNKNOWN",
                severity=DivergenceSeverity.CRITICAL,
                message="BRL balance not available from exchange",
            ))
            return divergences

        # P0-07 item 2: BRL locked must be explainable locally
        pending_buy_reservation = self._pending_buy_reservation(local)

        if brl_balance.locked > 0:
            if local.cash_locked <= 0 and pending_buy_reservation <= 0:
                # Exchange holds BRL we cannot account for → can't determine safely
                self._set_unknown(
                    "Exchange reports BRL locked but local model has no cash_locked "
                    "or pending BUY reservation to explain it",
                    kind=DeltaKind.CASH_MISMATCH,
                )

        # Local cash vs exchange BRL (available + locked attributable to us)
        local_total_brl = local.capital + local.cash_locked
        exchange_total_brl = brl_balance.available + brl_balance.locked

        # Local pending BUY orders reserve cash that is not yet deducted from
        # local.capital but is held as locked BRL on the exchange. The local
        # total already includes that reservation, so it must match the
        # exchange total (available + locked).
        expected_exchange_total = local_total_brl

        if exchange_total_brl != expected_exchange_total:
            divergences.append(Divergence(
                field="cash_brl",
                local_value=f"{local.capital} available + {local.cash_locked} locked",
                exchange_value=f"{brl_balance.available} available + {brl_balance.locked} locked",
                severity=DivergenceSeverity.CRITICAL,
                message=(
                    f"Local BRL total R$ {local_total_brl} != "
                    f"Exchange BRL total R$ {exchange_total_brl}"
                ),
            ))

        # Check crypto positions against exchange balances (available + locked)
        local_crypto = {
            p.symbol.split("-")[0]: p
            for p in local.positions
            if p.status == "OPEN"
        }

        for asset, local_pos in local_crypto.items():
            exchange_balance = exchange.get_balance(asset)
            if exchange_balance is None:
                divergences.append(Divergence(
                    field=f"position_{asset}",
                    local_value=str(local_pos.quantity),
                    exchange_value="UNKNOWN",
                    severity=DivergenceSeverity.CRITICAL,
                    message=f"Local has {asset} position but exchange balance unknown",
                ))
            else:
                exchange_total_asset = exchange_balance.available + exchange_balance.locked
                if local_pos.quantity > exchange_total_asset:
                    divergences.append(Divergence(
                        field=f"position_{asset}",
                        local_value=str(local_pos.quantity),
                        exchange_value=str(exchange_total_asset),
                        severity=DivergenceSeverity.CRITICAL,
                        message=(
                            f"Local {asset} quantity {local_pos.quantity} > "
                            f"exchange total (available+locked) {exchange_total_asset}"
                        ),
                    ))

        return divergences

    @staticmethod
    def _pending_buy_reservation(local: LocalSnapshot) -> Decimal:
        """Sum of quantities of local pending BUY orders.

        Used to determine whether exchange-locked BRL can be explained by
        local open orders. A non-zero return indicates plausible explanation
        exists (the locked BRL may be reserved for these orders).
        """
        total = Decimal("0")
        for o in local.orders:
            if o.status in ("SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN", "ACKNOWLEDGED", "PENDING"):
                if o.side == "BUY":
                    remaining = o.remaining_quantity if o.remaining_quantity > 0 else o.quantity
                    total += remaining
        return total

    def _reconcile_orders(
        self,
        local: LocalSnapshot,
        exchange: ExchangeSnapshot,
    ) -> list[Divergence]:
        """Compare local orders against exchange open orders.

        Active/open orders on exchange not known locally → CRITICAL.
        Local pending orders not found on exchange → UNKNOWN (P0-09): a pending
        order missing from open orders is NOT assumed non-existent — it may be
        filled/partially filled/rejected. Only the exchange can resolve it.
        """
        divergences: list[Divergence] = []

        local_pending = {
            o.local_order_id: o
            for o in local.orders
            if o.status in ("SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN", "ACKNOWLEDGED", "PENDING")
        }

        exchange_ids = {o.exchange_order_id for o in exchange.open_orders}

        # Local pending orders not on exchange — NOT assumed resolved (P0-09)
        for order_id, local_order in local_pending.items():
            if order_id not in exchange_ids:
                # A SUBMITTED/UNKNOWN/PARTIALLY_FILLED order is not in open
                # orders. Without order-history evidence we cannot determine
                # whether it filled, was rejected, or vanished → UNKNOWN.
                self._set_unknown(
                    f"Local order {order_id} ({local_order.side} "
                    f"{local_order.quantity} {local_order.symbol}) not found in "
                    "exchange open orders and no order-history evidence"
                )

        # Exchange orders not known locally — CRITICAL (active order we don't know about)
        for ex_order in exchange.open_orders:
            if ex_order.exchange_order_id not in local_pending:
                divergences.append(Divergence(
                    field=f"order_{ex_order.exchange_order_id}",
                    local_value="NOT_LOCAL",
                    exchange_value=f"{ex_order.side} {ex_order.quantity} {ex_order.symbol}",
                    severity=DivergenceSeverity.CRITICAL,
                    message=(
                        f"Exchange has active order {ex_order.exchange_order_id} "
                        f"({ex_order.side} {ex_order.quantity} {ex_order.symbol}) "
                        f"not known locally — trading BLOCKED"
                    ),
                ))

        return divergences
