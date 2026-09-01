"""Preemptive kill-switch action: cancel resting orders, then flatten (Vibe #8).

On a halt episode trip the worker must not just refuse the NEXT order — it must
cancel every resting order, then close every open position (cancel-before-flatten
is deliberate: a resting order left live could fill against our closing trade and
re-open exposure).

No-retry (Vibe #8.5): trading is not idempotent — a broker call that errors is
recorded and NOT retried here (retry could double-trade).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID, uuid4

logger = logging.getLogger("aegis.flatten")

#: Callables injected so this module is testable without a broker.
CancelFn = Callable[[UUID, UUID], Any]
SubmitFn = Callable[..., Any]


class FlattenResult:
    """Result of one flatten episode sweep."""

    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.flattened: list[str] = []
        self.errors: list[str] = []
        self.skipped: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "cancelled": self.cancelled,
            "flattened": self.flattened,
            "errors": self.errors,
            "skipped": self.skipped,
            "ok": self.ok,
        }


class FlattenSweep:
    """Preemptive kill-switch sweep: cancel resting orders, then flatten positions.

    Latch semantics (Vibe #3): the sweep is bound to a halt episode. Each episode
    runs the sweep at most once — a restart does not re-run it (would re-flatten
    an already-flattened book and could flip long → short).
    """

    def __init__(
        self,
        cancel_order: CancelFn,
        submit_close: SubmitFn,
        allow_flatten: bool = True,
    ) -> None:
        self._cancel = cancel_order
        self._submit_close = submit_close
        self._allow_flatten = allow_flatten
        self._completed_episodes: set[str] = set()

    def has_run_for(self, episode: str) -> bool:
        return episode in self._completed_episodes

    async def run(
        self,
        episode: str,
        open_orders: list[dict[str, Any]],
        positions: list[dict[str, Any]],
        current_prices: dict[str, Decimal] | None = None,
    ) -> FlattenResult:
        """Run the sweep once per episode (idempotent across restarts).

        Returns the result. Never retries a failed broker call (no-retry).
        """
        result = FlattenResult()
        if self.has_run_for(episode):
            result.skipped.append(f"episode {episode} already swept")
            return result

        # 1. Cancel every resting order (quiesce the book first).
        for order in open_orders:
            order_id = order.get("order_id") or order.get("id")
            idem = uuid4()
            try:
                cancel_result = await self._cancel(order_id, idem)
                if getattr(cancel_result, "success", False) or (
                    isinstance(cancel_result, dict) and cancel_result.get("success")
                ):
                    result.cancelled.append(str(order_id))
                else:
                    result.errors.append(
                        f"cancel {order_id} failed: {getattr(cancel_result, 'error', None)}"
                    )
            except Exception as e:  # noqa: BLE001
                # No-retry: record and move on, never re-send.
                result.errors.append(f"cancel {order_id} raised: {e}")

        # 2. Flatten every open position (cancel-before-flatten ordering).
        if self._allow_flatten:
            prices = current_prices or {}
            for pos in positions:
                symbol = pos.get("symbol", "")
                qty = Decimal(pos.get("quantity", "0"))
                if qty <= 0:
                    continue
                close_price = prices.get(symbol)
                try:
                    close_result = await self._submit_close(
                        symbol=symbol, quantity=qty, price=close_price,
                    )
                    status = getattr(close_result, "status", None)
                    if status is not None and getattr(status, "value", status) == "FILLED":
                        result.flattened.append(symbol)
                    else:
                        result.errors.append(f"flatten {symbol} not filled")
                except Exception as e:  # noqa: BLE001
                    # No-retry.
                    result.errors.append(f"flatten {symbol} raised: {e}")
        else:
            result.skipped.append("flatten disabled (cancel-only)")

        self._completed_episodes.add(episode)
        return result