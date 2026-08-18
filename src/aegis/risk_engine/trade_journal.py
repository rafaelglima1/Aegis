"""AEGIS Trade Journal — records and analyzes trade performance.

Stores trade data for analysis and optimization.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

logger = logging.getLogger("aegis.trade_journal")


@dataclass
class TradeRecord:
    """Complete record of a trade."""

    symbol: str
    timestamp: str
    action: str  # LONG, CLOSE
    entry_price: Decimal
    exit_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    setup_score: int = 0
    confidence: Decimal = Decimal("0")
    regime: str = "NEUTRAL"
    rsi: Decimal = Decimal("50")
    momentum: Decimal = Decimal("0")
    volume_trend: str = "NORMAL"
    volatility: Decimal = Decimal("0")
    risk_reward: Decimal = Decimal("0")
    position_size: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    realized_r: Decimal = Decimal("0")
    mfe: Decimal = Decimal("0")  # Maximum Favorable Excursion
    mae: Decimal = Decimal("0")  # Maximum Adverse Excursion
    holding_time: str = ""
    exit_reason: str = ""  # SL, TP, TRAILING, BREAK_EVEN, MANUAL, RISK
    fees: Decimal = Decimal("0")


@dataclass
class PerformanceMetrics:
    """Aggregated performance metrics."""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: Decimal = Decimal("0")
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")
    expectancy: Decimal = Decimal("0")
    avg_r: Decimal = Decimal("0")
    profit_factor: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    total_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")


class TradeJournal:
    """Records trades and calculates performance metrics."""

    def __init__(self) -> None:
        self._trades: list[TradeRecord] = []

    def record(self, trade: TradeRecord) -> None:
        """Record a completed trade."""
        self._trades.append(trade)
        logger.info(
            "Trade recorded: %s %s entry=%s exit=%s pnl=%s r=%s score=%d",
            trade.symbol, trade.action, trade.entry_price,
            trade.exit_price, trade.realized_pnl, trade.realized_r,
            trade.setup_score,
        )

    def get_trades(self, symbol: str | None = None) -> list[TradeRecord]:
        """Get trades, optionally filtered by symbol."""
        if symbol:
            return [t for t in self._trades if t.symbol == symbol]
        return self._trades.copy()

    def calculate_metrics(self, trades: list[TradeRecord] | None = None) -> PerformanceMetrics:
        """Calculate performance metrics from trades."""
        if trades is None:
            trades = self._trades

        if not trades:
            return PerformanceMetrics()

        metrics = PerformanceMetrics()
        metrics.total_trades = len(trades)
        metrics.total_pnl = sum(t.realized_pnl for t in trades)
        metrics.total_fees = sum(t.fees for t in trades)

        wins = [t for t in trades if t.realized_pnl > 0]
        losses = [t for t in trades if t.realized_pnl <= 0]

        metrics.winning_trades = len(wins)
        metrics.losing_trades = len(losses)

        if metrics.total_trades > 0:
            metrics.win_rate = Decimal(str(metrics.winning_trades)) / Decimal(str(metrics.total_trades)) * 100

        if wins:
            metrics.avg_win = sum(t.realized_pnl for t in wins) / Decimal(str(len(wins)))

        if losses:
            metrics.avg_loss = sum(t.realized_pnl for t in losses) / Decimal(str(len(losses)))

        # Expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
        win_rate = Decimal(str(metrics.winning_trades)) / Decimal(str(metrics.total_trades)) if metrics.total_trades > 0 else Decimal("0")
        loss_rate = Decimal("1") - win_rate
        metrics.expectancy = (win_rate * metrics.avg_win) + (loss_rate * metrics.avg_loss)

        # Average R
        r_values = [t.realized_r for t in trades if t.realized_r != 0]
        if r_values:
            metrics.avg_r = sum(r_values) / Decimal(str(len(r_values)))

        # Profit factor
        gross_profit = sum(t.realized_pnl for t in wins) if wins else Decimal("0")
        gross_loss = abs(sum(t.realized_pnl for t in losses)) if losses else Decimal("0")
        if gross_loss > 0:
            metrics.profit_factor = gross_profit / gross_loss

        # Max drawdown
        equity = Decimal("0")
        peak = Decimal("0")
        max_dd = Decimal("0")
        for t in sorted(trades, key=lambda x: x.timestamp):
            equity += t.realized_pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else Decimal("0")
            if dd > max_dd:
                max_dd = dd
        metrics.max_drawdown = max_dd * 100

        return metrics

    def get_metrics_by_setup_score(self) -> dict[str, PerformanceMetrics]:
        """Calculate metrics grouped by setup score range."""
        ranges = {
            "0-49": [t for t in self._trades if t.setup_score < 50],
            "50-64": [t for t in self._trades if 50 <= t.setup_score < 65],
            "65-79": [t for t in self._trades if 65 <= t.setup_score < 80],
            "80-100": [t for t in self._trades if t.setup_score >= 80],
        }
        return {k: self.calculate_metrics(v) for k, v in ranges.items() if v}

    def get_metrics_by_exit_reason(self) -> dict[str, PerformanceMetrics]:
        """Calculate metrics grouped by exit reason."""
        reasons = {}
        for t in self._trades:
            reason = t.exit_reason or "UNKNOWN"
            if reason not in reasons:
                reasons[reason] = []
            reasons[reason].append(t)
        return {k: self.calculate_metrics(v) for k, v in reasons.items()}

    def get_metrics_by_regime(self) -> dict[str, PerformanceMetrics]:
        """Calculate metrics grouped by market regime."""
        regimes = {}
        for t in self._trades:
            regime = t.regime or "NEUTRAL"
            if regime not in regimes:
                regimes[regime] = []
            regimes[regime].append(t)
        return {k: self.calculate_metrics(v) for k, v in regimes.items()}
