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


@dataclass
class LocalSnapshot:
    """Snapshot of local worker state for reconciliation."""
    capital: Decimal = Decimal("0")
    positions: list[LocalPosition] = field(default_factory=list)
    orders: list[LocalOrder] = field(default_factory=list)
    state_valid: bool = True


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


# ============================================================
# Reconciliation Result
# ============================================================


@dataclass
class ReconciliationResult:
    """Result of comparing local state against exchange state."""
    status: ReconciliationStatus = ReconciliationStatus.UNKNOWN
    divergences: list[Divergence] = field(default_factory=list)
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

    def summary(self) -> str:
        parts = [f"status={self.status.value}"]
        if self.divergences:
            parts.append(f"divergences={len(self.divergences)}")
        if self.error:
            parts.append(f"error={self.error}")
        return " ".join(parts)


# ============================================================
# Reconciliation Engine
# ============================================================


class ReconciliationEngine:
    """Deterministic reconciliation between local and exchange state.

    No automatic correction. Divergence blocks trading.
    """

    def reconcile(
        self,
        local: LocalSnapshot,
        exchange: ExchangeSnapshot,
    ) -> ReconciliationResult:
        """Compare local state against exchange state.

        Returns RECONCILED, DIVERGED, UNKNOWN, or ERROR.
        """
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

        if not divergences:
            result.status = ReconciliationStatus.RECONCILED
        elif any(d.severity == DivergenceSeverity.CRITICAL for d in divergences):
            result.status = ReconciliationStatus.DIVERGED
        else:
            # Warnings only — still RECONCILED but with notes
            result.status = ReconciliationStatus.RECONCILED

        return result

    def _reconcile_balances(
        self,
        local: LocalSnapshot,
        exchange: ExchangeSnapshot,
    ) -> list[Divergence]:
        """Compare local capital/positions against exchange balances."""
        divergences: list[Divergence] = []

        # Derive BRL balance from local portfolio
        # For spot: BRL balance = capital - total position value
        # We compare available BRL on exchange vs local capital
        brl_balance = exchange.get_balance("BRL")
        if brl_balance is not None:
            # Exchange BRL available should roughly match local capital
            # (exact match depends on fees, timing)
            if brl_balance.available != local.capital:
                divergences.append(Divergence(
                    field="capital",
                    local_value=str(local.capital),
                    exchange_value=str(brl_balance.available),
                    severity=DivergenceSeverity.CRITICAL,
                    message=(
                        f"Local capital R$ {local.capital} != "
                        f"Exchange BRL available R$ {brl_balance.available}"
                    ),
                ))
        else:
            # BRL balance unknown on exchange
            divergences.append(Divergence(
                field="capital",
                local_value=str(local.capital),
                exchange_value="UNKNOWN",
                severity=DivergenceSeverity.CRITICAL,
                message="BRL balance not available from exchange",
            ))

        # Check crypto positions
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
            elif exchange_balance.available < local_pos.quantity:
                divergences.append(Divergence(
                    field=f"position_{asset}",
                    local_value=str(local_pos.quantity),
                    exchange_value=str(exchange_balance.available),
                    severity=DivergenceSeverity.CRITICAL,
                    message=(
                        f"Local {asset} quantity {local_pos.quantity} > "
                        f"exchange available {exchange_balance.available}"
                    ),
                ))

        return divergences

    def _reconcile_orders(
        self,
        local: LocalSnapshot,
        exchange: ExchangeSnapshot,
    ) -> list[Divergence]:
        """Compare local orders against exchange open orders."""
        divergences: list[Divergence] = []

        local_pending = {
            o.local_order_id: o
            for o in local.orders
            if o.status in ("SUBMITTED", "PENDING")
        }

        exchange_ids = {o.exchange_order_id for o in exchange.open_orders}

        # Local orders not found on exchange
        for order_id, local_order in local_pending.items():
            if order_id not in exchange_ids:
                divergences.append(Divergence(
                    field=f"order_{order_id}",
                    local_value=f"{local_order.side} {local_order.quantity} {local_order.symbol}",
                    exchange_value="NOT_FOUND",
                    severity=DivergenceSeverity.WARNING,
                    message=f"Local order {order_id} not found on exchange",
                ))

        # Exchange orders not known locally
        for ex_order in exchange.open_orders:
            if ex_order.exchange_order_id not in local_pending:
                divergences.append(Divergence(
                    field=f"order_{ex_order.exchange_order_id}",
                    local_value="NOT_LOCAL",
                    exchange_value=f"{ex_order.side} {ex_order.quantity} {ex_order.symbol}",
                    severity=DivergenceSeverity.WARNING,
                    message=(
                        f"Exchange order {ex_order.exchange_order_id} "
                        f"({ex_order.side} {ex_order.quantity} {ex_order.symbol}) "
                        f"not known locally"
                    ),
                ))

        return divergences
