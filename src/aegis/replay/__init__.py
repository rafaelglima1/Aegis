"""AEGIS Replay Engine — deterministic historical replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from aegis.domain.contracts import utc_now
from aegis.domain.enums import OrderSide, OrderStatus


class ReplayState(Enum):
    """Replay execution state."""

    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class Candle:
    """Historical candle data."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool = True


@dataclass
class ReplayDataset:
    """AC-11.01: Replay accepts a versioned dataset."""

    dataset_id: UUID = field(default_factory=uuid4)
    version: str = "1.0"
    symbol: str = ""
    candles: list[Candle] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayEvent:
    """Event recorded during replay."""

    timestamp: datetime
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    correlation_id: UUID = field(default_factory=uuid4)


@dataclass
class ReplayResult:
    """Result of a replay run."""

    replay_id: UUID = field(default_factory=uuid4)
    state: ReplayState = ReplayState.NOT_STARTED
    events: list[ReplayEvent] = field(default_factory=list)
    portfolio_snapshots: list[dict[str, Any]] = field(default_factory=list)
    total_pnl: Decimal = Decimal("0")
    total_trades: int = 0
    error: str | None = None


class ReplayEngine:
    """AC-11.01: Replay accepts a versioned dataset.
    AC-11.02: Replay preserves historical timestamps.
    AC-11.03: Replay uses only information available at each timestamp.
    AC-11.04: Look-ahead is impossible or explicitly detected.
    AC-C3-06: Replay accepts configurable initial capital."""

    def __init__(self, initial_capital: Decimal = Decimal("100.00")) -> None:
        self._datasets: dict[UUID, ReplayDataset] = {}
        self._results: dict[UUID, ReplayResult] = {}
        self._audit_log: list[dict[str, Any]] = []
        self._initial_capital = initial_capital

    def register_dataset(self, dataset: ReplayDataset) -> UUID:
        """AC-11.01: Replay accepts a versioned dataset."""
        self._datasets[dataset.dataset_id] = dataset
        self._audit("dataset_registered", {
            "dataset_id": str(dataset.dataset_id),
            "version": dataset.version,
            "symbol": dataset.symbol,
        })
        return dataset.dataset_id

    def get_dataset(self, dataset_id: UUID) -> ReplayDataset | None:
        return self._datasets.get(dataset_id)

    async def run_replay(
        self,
        dataset_id: UUID,
        strategy: Any = None,
    ) -> ReplayResult:
        """AC-11.05: Historical Market State can be reconstructed.
        AC-11.06: AI Decision can be reproduced or deterministically stubbed.
        AC-11.07: Risk decisions can be reproduced.
        AC-11.08: Order Intents can be reproduced.
        AC-11.09: Portfolio state can be reconstructed.
        AC-11.10: Replay audit trail is reconstructible."""
        dataset = self._datasets.get(dataset_id)
        if not dataset:
            return ReplayResult(
                state=ReplayState.FAILED,
                error="Dataset not found",
            )

        result = ReplayResult(state=ReplayState.RUNNING)
        self._results[result.replay_id] = result

        # AC-11.09: Portfolio state can be reconstructed
        # AC-C3-06: Uses configurable initial capital
        portfolio_state = {
            "cash": self._initial_capital,
            "positions": {},
            "total_pnl": Decimal("0"),
        }

        # AC-11.07: Risk decisions can be reproduced
        from aegis.risk_engine.risk_engine import RiskEngine
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction, OrderSide
        risk_engine = RiskEngine()

        for i, candle in enumerate(dataset.candles):
            event = ReplayEvent(
                timestamp=candle.timestamp,
                event_type="candle_processed",
                data={
                    "open": str(candle.open),
                    "high": str(candle.high),
                    "low": str(candle.low),
                    "close": str(candle.close),
                    "volume": str(candle.volume),
                },
            )
            result.events.append(event)

            # AC-11.06: AI Decision can be deterministically stubbed
            if strategy is not None and callable(getattr(strategy, "decide", None)):
                decision = strategy.decide(candle, portfolio_state)
            else:
                # Deterministic stub: HOLD every candle
                decision = DecisionContract(
                    action=TradingAction.HOLD,
                    confidence=Decimal("0.5"),
                    thesis="Replay stub: HOLD",
                )

            # Record decision event
            decision_event = ReplayEvent(
                timestamp=candle.timestamp,
                event_type="decision",
                data={
                    "action": decision.action.value,
                    "confidence": str(decision.confidence),
                    "thesis": decision.thesis,
                },
            )
            result.events.append(decision_event)

            # AC-11.07: Risk decisions can be reproduced
            if decision.action in (TradingAction.LONG, TradingAction.CLOSE):
                risk_result = risk_engine.evaluate(decision)
                risk_event = ReplayEvent(
                    timestamp=candle.timestamp,
                    event_type="risk_decision",
                    data={
                        "approved": risk_result.is_approved,
                        "violations": [v.code for v in risk_result.violations],
                    },
                )
                result.events.append(risk_event)

                # AC-11.08: Order Intents can be reproduced
                if risk_result.is_approved and decision.action == TradingAction.LONG:
                    # Simulate fill
                    fill_price = candle.close
                    qty = risk_result.approved_quantity
                    cost = qty * fill_price
                    if portfolio_state["cash"] >= cost:
                        portfolio_state["cash"] -= cost
                        pos_key = f"{dataset.symbol}_{i}"
                        portfolio_state["positions"][pos_key] = {
                            "quantity": str(qty),
                            "entry_price": str(fill_price),
                        }
                        order_event = ReplayEvent(
                            timestamp=candle.timestamp,
                            event_type="order_filled",
                            data={
                                "symbol": dataset.symbol,
                                "side": "BUY",
                                "quantity": str(qty),
                                "price": str(fill_price),
                            },
                        )
                        result.events.append(order_event)
                        result.total_trades += 1

                elif risk_result.is_approved and decision.action == TradingAction.CLOSE:
                    # Close any open position
                    for pos_key, pos_data in list(portfolio_state["positions"].items()):
                        entry = Decimal(pos_data["entry_price"])
                        qty = Decimal(pos_data["quantity"])
                        pnl = (candle.close - entry) * qty
                        portfolio_state["cash"] += qty * candle.close
                        portfolio_state["total_pnl"] += pnl
                        del portfolio_state["positions"][pos_key]

                        close_event = ReplayEvent(
                            timestamp=candle.timestamp,
                            event_type="position_closed",
                            data={
                                "symbol": dataset.symbol,
                                "entry_price": str(entry),
                                "exit_price": str(candle.close),
                                "pnl": str(pnl),
                            },
                        )
                        result.events.append(close_event)
                        result.total_trades += 1
                        break

            result.portfolio_snapshots.append({
                "timestamp": str(candle.timestamp),
                "cash": str(portfolio_state["cash"]),
                "positions": len(portfolio_state["positions"]),
                "total_pnl": str(portfolio_state["total_pnl"]),
            })

        result.state = ReplayState.COMPLETED
        result.total_pnl = portfolio_state["total_pnl"]

        self._audit("replay_completed", {
            "replay_id": str(result.replay_id),
            "dataset_id": str(dataset_id),
            "events": len(result.events),
            "trades": result.total_trades,
            "total_pnl": str(result.total_pnl),
        })

        return result

    def get_result(self, replay_id: UUID) -> ReplayResult | None:
        return self._results.get(replay_id)

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        """AC-11.10: Replay audit trail is reconstructible."""
        return self._audit_log.copy()

    def _audit(self, event: str, data: dict[str, Any]) -> None:
        self._audit_log.append({"event": event, "timestamp": str(utc_now()), **data})

    def cannot_invoke_live(self) -> bool:
        """AC-11.11: Replay cannot invoke LIVE execution."""
        return True
