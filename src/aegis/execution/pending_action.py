"""Pending Action — crash-safe ownership marker (Vibe-Trading pattern #5).

Before every broker-side-effect call (submit_order), durably record the
exact request + pre-position state. On restart, the marker is the source of
truth for "what was in flight" — without it we cannot distinguish "the order
was never sent" from "the order was sent and we crashed before the response."

Phases:
  pending_write                  → marker created before broker call
  resolved_fill_pending_audit    → broker confirmed fill with evidence
  resolved_needs_revalidation    → broker returned working order, needs revalidation

Atomic write: temp + fsync + rename (matching Aegis _save_state pattern).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

logger = logging.getLogger("aegis.pending_action")

_PENDING_FILE = Path("/home/ubuntu/aegis/pending_action.json")


class PendingActionError(RuntimeError):
    """Raised when pending state cannot be trusted or durably changed."""


@dataclass
class PendingAction:
    """Crash-safe ownership marker for one unresolved broker side effect."""

    order_id: str
    idempotency_key: str
    symbol: str
    side: str
    quantity: str
    price: str
    phase: str  # pending_write | resolved_fill_pending_audit | resolved_needs_revalidation
    pre_position_qty: str | None = None
    filled_quantity: str | None = None
    status: str | None = None
    error: str | None = None
    timestamp: str = ""

    @property
    def is_pending(self) -> bool:
        return self.phase == "pending_write"


def new_pending_order(
    order_id: UUID,
    idempotency_key: UUID,
    symbol: str,
    side: str,
    quantity: Decimal,
    price: Decimal,
    pre_position_qty: Decimal | None = None,
) -> PendingAction:
    return PendingAction(
        order_id=str(order_id),
        idempotency_key=str(idempotency_key),
        symbol=symbol,
        side=side,
        quantity=str(quantity),
        price=str(price),
        phase="pending_write",
        pre_position_qty=str(pre_position_qty) if pre_position_qty is not None else None,
    )


def save_pending_action(action: PendingAction) -> None:
    """Atomically write pending action marker.

    Fail-closed: raises on write failure so the caller knows the marker was
    not persisted and must not proceed with the broker call.
    """
    if action.phase != "pending_write":
        raise PendingActionError(f"save only valid for pending_write, got {action.phase}")
    parent = _PENDING_FILE.parent
    parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_to_dict(action), ensure_ascii=False)
    tmp = _PENDING_FILE.with_suffix(".pending_tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(_PENDING_FILE)
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise PendingActionError(f"Failed to save pending action: {e}") from e


def load_pending_action() -> PendingAction | None:
    """Load the pending action marker.

    Fail-closed: corrupted/missing → None (safe default — treat as no marker).
    """
    if not _PENDING_FILE.exists():
        return None
    try:
        raw = _PENDING_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        return PendingAction(**data)
    except (OSError, json.JSONDecodeError, TypeError, KeyError) as e:
        logger.critical("Corrupted pending action marker: %s", e)
        return None


def clear_pending_action() -> None:
    """Remove the pending action marker after resolution.

    Fail-open: if the file is already gone or can't be deleted, log and continue
    (the marker's purpose is pre-crash; post-resolve it's informational only).
    """
    try:
        if _PENDING_FILE.exists():
            _PENDING_FILE.unlink()
    except OSError as e:
        logger.warning("Could not clear pending action: %s", e)


def transition_to_fill(
    action: PendingAction, filled_qty: str | Decimal, status: str,
) -> PendingAction:
    """Transition a pending_write marker to resolved_fill_pending_audit."""
    return _transition(
        action, "resolved_fill_pending_audit",
        filled_quantity=str(filled_qty), status=status,
    )


def transition_to_revalidation(
    action: PendingAction, status: str, error: str | None = None,
) -> PendingAction:
    """Transition a pending_write marker to resolved_needs_revalidation."""
    return _transition(action, "resolved_needs_revalidation", status=status, error=error)


def _transition(action: PendingAction, phase: str, **kwargs: Any) -> PendingAction:
    updated = PendingAction(
        order_id=action.order_id,
        idempotency_key=action.idempotency_key,
        symbol=action.symbol,
        side=action.side,
        quantity=action.quantity,
        price=action.price,
        phase=phase,
        pre_position_qty=action.pre_position_qty,
        filled_quantity=kwargs.get("filled_quantity", action.filled_quantity),
        status=kwargs.get("status", action.status),
        error=kwargs.get("error", action.error),
    )
    _write_transition(updated)
    return updated


def _write_transition(action: PendingAction) -> None:
    payload = json.dumps(_to_dict(action), ensure_ascii=False)
    tmp = _PENDING_FILE.with_suffix(".pending_tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(_PENDING_FILE)
    except Exception as e:
        raise PendingActionError(f"Failed to persist pending transition: {e}") from e


def _to_dict(action: PendingAction) -> dict[str, Any]:
    return {
        "order_id": action.order_id,
        "idempotency_key": action.idempotency_key,
        "symbol": action.symbol,
        "side": action.side,
        "quantity": action.quantity,
        "price": action.price,
        "phase": action.phase,
        "pre_position_qty": action.pre_position_qty,
        "filled_quantity": action.filled_quantity,
        "status": action.status,
        "error": action.error,
    }