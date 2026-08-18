"""AEGIS Backtest Analytics — breakdown by score, regime, confidence, symbol."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class BucketMetrics:
    """Metrics for a single analytics bucket."""

    name: str = ""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    avg_r: Decimal = Decimal("0")
    expectancy: Decimal = Decimal("0")
    profit_factor: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    avg_holding_time: Decimal = Decimal("0")


@dataclass
class AnalyticsReport:
    """Complete analytics breakdown."""

    by_setup_score: dict[str, BucketMetrics] = field(default_factory=dict)
    by_regime: dict[str, BucketMetrics] = field(default_factory=dict)
    by_confidence: dict[str, BucketMetrics] = field(default_factory=dict)
    by_exit_reason: dict[str, BucketMetrics] = field(default_factory=dict)
    by_symbol: dict[str, BucketMetrics] = field(default_factory=dict)
    score_confidence_matrix: dict[str, dict[str, BucketMetrics]] = field(default_factory=dict)


class BacktestAnalytics:
    """Calculate analytics breakdowns from backtest trades."""

    def analyze(self, trades: list[Any]) -> AnalyticsReport:
        """Generate complete analytics report from trades."""
        report = AnalyticsReport()

        if not trades:
            return report

        # Breakdown by setup score
        report.by_setup_score = self._bucket_by_field(
            trades, "setup_score",
            lambda t: self._score_bucket(t.setup_score)
        )

        # Breakdown by regime
        report.by_regime = self._bucket_by_field(
            trades, "regime",
            lambda t: t.regime
        )

        # Breakdown by confidence
        report.by_confidence = self._bucket_by_field(
            trades, "confidence",
            lambda t: self._confidence_bucket(t.confidence)
        )

        # Breakdown by exit reason
        report.by_exit_reason = self._bucket_by_field(
            trades, "exit_reason",
            lambda t: t.exit_reason or "UNKNOWN"
        )

        # Breakdown by symbol
        report.by_symbol = self._bucket_by_field(
            trades, "symbol",
            lambda t: t.symbol
        )

        # Score x Confidence matrix
        report.score_confidence_matrix = self._build_matrix(trades)

        return report

    def _bucket_by_field(self, trades: list[Any], field_name: str,
                         key_fn: Any) -> dict[str, BucketMetrics]:
        """Group trades by a key function and calculate metrics."""
        buckets: dict[str, list] = {}
        for t in trades:
            key = str(key_fn(t))
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(t)

        return {k: self._calc_bucket_metrics(k, v) for k, v in buckets.items()}

    def _calc_bucket_metrics(self, name: str, trades: list[Any]) -> BucketMetrics:
        """Calculate metrics for a bucket of trades."""
        m = BucketMetrics(name=name)
        if not trades:
            return m

        m.total_trades = len(trades)
        m.winning_trades = sum(1 for t in trades if t.realized_pnl > 0)
        m.losing_trades = sum(1 for t in trades if t.realized_pnl <= 0)

        if m.total_trades > 0:
            m.win_rate = Decimal(str(m.winning_trades)) / Decimal(str(m.total_trades)) * 100

        m.net_pnl = sum(t.realized_pnl for t in trades)

        r_vals = [t.realized_r for t in trades if t.realized_r != 0]
        if r_vals:
            m.avg_r = sum(r_vals) / Decimal(str(len(r_vals)))

        # Expectancy
        win_rate = Decimal(str(m.winning_trades)) / Decimal(str(m.total_trades)) if m.total_trades > 0 else Decimal("0")
        avg_win = sum(t.realized_pnl for t in trades if t.realized_pnl > 0) / Decimal(str(m.winning_trades)) if m.winning_trades > 0 else Decimal("0")
        avg_loss = sum(abs(t.realized_pnl) for t in trades if t.realized_pnl < 0) / Decimal(str(m.losing_trades)) if m.losing_trades > 0 else Decimal("0")
        m.expectancy = (win_rate * avg_win) - ((Decimal("1") - win_rate) * avg_loss)

        # Profit factor
        gross_profit = sum(t.realized_pnl for t in trades if t.realized_pnl > 0)
        gross_loss = sum(abs(t.realized_pnl) for t in trades if t.realized_pnl < 0)
        if gross_loss > 0:
            m.profit_factor = gross_profit / gross_loss

        # Max drawdown
        cumulative = Decimal("0")
        peak = Decimal("0")
        max_dd = Decimal("0")
        for t in sorted(trades, key=lambda x: x.exit_time):
            cumulative += t.realized_pnl
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak if peak > 0 else Decimal("0")
            if dd > max_dd:
                max_dd = dd
        m.max_drawdown = max_dd * 100

        # Avg holding time
        holding = [t.holding_time for t in trades if t.holding_time > 0]
        if holding:
            m.avg_holding_time = Decimal(str(sum(holding))) / Decimal(str(len(holding)))

        return m

    def _score_bucket(self, score: int) -> str:
        """Map setup score to bucket name."""
        if score < 50:
            return "0-49"
        elif score < 65:
            return "50-64"
        elif score < 80:
            return "65-79"
        return "80-100"

    def _confidence_bucket(self, confidence: Decimal) -> str:
        """Map confidence to bucket name."""
        if confidence < Decimal("0.60"):
            return "0.50-0.59"
        elif confidence < Decimal("0.70"):
            return "0.60-0.69"
        elif confidence < Decimal("0.80"):
            return "0.70-0.79"
        elif confidence < Decimal("0.90"):
            return "0.80-0.89"
        return "0.90-1.00"

    def _build_matrix(self, trades: list[Any]) -> dict[str, dict[str, BucketMetrics]]:
        """Build score x confidence matrix."""
        matrix: dict[str, dict[str, list]] = {}
        for t in trades:
            score_bucket = self._score_bucket(t.setup_score)
            conf_bucket = self._confidence_bucket(t.confidence)
            if score_bucket not in matrix:
                matrix[score_bucket] = {}
            if conf_bucket not in matrix[score_bucket]:
                matrix[score_bucket][conf_bucket] = []
            matrix[score_bucket][conf_bucket].append(t)

        result = {}
        for sb, confs in matrix.items():
            result[sb] = {}
            for cb, bucket_trades in confs.items():
                result[sb][cb] = self._calc_bucket_metrics(f"{sb}|{cb}", bucket_trades)
        return result


@dataclass
class BaselineComparison:
    """Compare baseline vs candidate configuration."""

    baseline_metrics: BucketMetrics = field(default_factory=BucketMetrics)
    candidate_metrics: BucketMetrics = field(default_factory=BucketMetrics)
    improvement: dict[str, Decimal] = field(default_factory=dict)
    recommendation: str = "INSUFFICIENT_DATA"
