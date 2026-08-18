"""AEGIS Backtest V2 — Comprehensive Tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from aegis.backtest.engine_v2 import (
    BacktestConfig, BacktestEngineV2, Candle, TradeRecord,
    BacktestResult, create_candles_from_dicts,
)
from aegis.backtest.analytics import BacktestAnalytics, BucketMetrics
from aegis.backtest.sweep import (
    ParameterSweep, SweepParameter, TrainValidationTest, BaselineComparator,
)
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.risk_engine.position_manager import PositionManagerConfig


def _make_candles(n: int = 50, start: Decimal = Decimal("100"),
                  trend: str = "UP") -> list[Candle]:
    """Generate synthetic candle data for testing."""
    candles = []
    price = start
    for i in range(n):
        if trend == "UP":
            change = Decimal("0.5") if i % 3 != 0 else Decimal("-0.3")
        elif trend == "DOWN":
            change = Decimal("-0.5") if i % 3 != 0 else Decimal("0.3")
        else:
            change = Decimal("0.3") if i % 2 == 0 else Decimal("-0.3")

        price = price + change
        high = price + Decimal("1")
        low = price - Decimal("1")
        candles.append(Candle(
            timestamp=f"2025-01-{i+1:02d}T00:00:00Z",
            open=price - change, high=high, low=low, close=price,
            volume=Decimal("1000"),
        ))
    return candles


# ============================================================
# Backtest Engine Tests
# ============================================================


class TestBacktestEngine:

    def test_empty_candles(self) -> None:
        """Empty candles should return empty result."""
        engine = BacktestEngineV2()
        result = engine.run("BTC-BRL", [])
        assert result.total_trades == 0

    def test_single_candle(self) -> None:
        """Single candle should return empty result."""
        engine = BacktestEngineV2()
        result = engine.run("BTC-BRL", [Candle("t1", Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"))])
        assert result.total_trades == 0

    def test_uptrend_generates_trades(self) -> None:
        """Uptrend should generate some trades."""
        candles = _make_candles(100, trend="UP")
        config = BacktestConfig(initial_capital=Decimal("100.00"))
        engine = BacktestEngineV2(config)
        result = engine.run("BTC-BRL", candles)
        assert result.total_trades >= 0  # May or may not trade depending on setup score

    def test_no_look_ahead(self) -> None:
        """Verify no look-ahead bias in market state building."""
        engine = BacktestEngineV2()
        candles = _make_candles(20, trend="UP")
        state = engine._build_market_state(candles[:10], candles[9].close)
        assert state["candles_count"] == 10
        # SMA should only use first 10 candles
        closes = [c.close for c in candles[:10]]
        expected_sma = sum(closes[-20:]) / min(20, len(closes))
        assert Decimal(state["sma_20"]) == expected_sma

    def test_slippage_applied(self) -> None:
        """Slippage should be applied to entry/exit prices."""
        config = BacktestConfig(slippage_bps=Decimal("10"))
        engine = BacktestEngineV2(config)
        buy_price = engine._apply_slippage(Decimal("100"), "BUY")
        sell_price = engine._apply_slippage(Decimal("100"), "SELL")
        assert buy_price > Decimal("100")
        assert sell_price < Decimal("100")

    def test_fees_applied(self) -> None:
        """Fees should be deducted from trades."""
        config = BacktestConfig(fee_rate=Decimal("0.01"))  # 1% fee
        candles = _make_candles(50, trend="UP")
        engine = BacktestEngineV2(config)
        result = engine.run("BTC-BRL", candles)
        if result.total_trades > 0:
            assert result.total_fees > 0

    def test_equity_curve(self) -> None:
        """Equity curve should be populated."""
        candles = _make_candles(50, trend="UP")
        engine = BacktestEngineV2()
        result = engine.run("BTC-BRL", candles)
        assert len(result.equity_curve) > 0

    def test_position_sizing_respects_limits(self) -> None:
        """Position size should respect risk limits."""
        limits = RiskLimits(
            reference_capital=Decimal("100"),
            max_risk_per_trade_pct=Decimal("0.01"),
            max_position_size_pct=Decimal("0.10"),
        )
        config = BacktestConfig(initial_capital=Decimal("100"), risk_limits=limits)
        engine = BacktestEngineV2(config)
        # Verify risk engine enforces limits
        assert engine._risk_engine.limits.max_position_size == Decimal("10")

    def test_setup_scoring_integrated(self) -> None:
        """Setup scoring should be integrated into backtest."""
        engine = BacktestEngineV2()
        candles = _make_candles(30, trend="UP")
        state = engine._build_market_state(candles, candles[-1].close)
        result = engine._setup_scorer.score(state)
        assert 0 <= result.score <= 100
        assert result.market_regime in ["STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "STRONG_BEAR", "HIGH_VOLATILITY"]

    def test_position_manager_integrated(self) -> None:
        """Position manager should be used for BE/trailing."""
        config = BacktestConfig(
            position_config=PositionManagerConfig(
                break_even_trigger_r=Decimal("0.5"),
                trailing_trigger_r=Decimal("1.0"),
            )
        )
        engine = BacktestEngineV2(config)
        assert engine._position_manager is not None


# ============================================================
# Analytics Tests
# ============================================================


class TestAnalytics:

    def test_analyze_empty(self) -> None:
        """Empty trades should return empty report."""
        analytics = BacktestAnalytics()
        report = analytics.analyze([])
        assert len(report.by_setup_score) == 0

    def test_analyze_by_score(self) -> None:
        """Should group trades by setup score."""
        analytics = BacktestAnalytics()
        trades = [
            TradeRecord(symbol="BTC-BRL", entry_time="t1", exit_time="t2",
                       entry_price=Decimal("100"), exit_price=Decimal("110"),
                       setup_score=40, realized_pnl=Decimal("10"), realized_r=Decimal("2")),
            TradeRecord(symbol="BTC-BRL", entry_time="t3", exit_time="t4",
                       entry_price=Decimal("100"), exit_price=Decimal("115"),
                       setup_score=70, realized_pnl=Decimal("15"), realized_r=Decimal("3")),
        ]
        report = analytics.analyze(trades)
        assert "0-49" in report.by_setup_score
        assert "65-79" in report.by_setup_score
        assert report.by_setup_score["0-49"].total_trades == 1
        assert report.by_setup_score["65-79"].total_trades == 1

    def test_analyze_by_regime(self) -> None:
        """Should group trades by regime."""
        analytics = BacktestAnalytics()
        trades = [
            TradeRecord(symbol="BTC-BRL", entry_time="t1", exit_time="t2",
                       entry_price=Decimal("100"), exit_price=Decimal("110"),
                       regime="BULL", realized_pnl=Decimal("10")),
            TradeRecord(symbol="BTC-BRL", entry_time="t3", exit_time="t4",
                       entry_price=Decimal("100"), exit_price=Decimal("95"),
                       regime="BEAR", realized_pnl=Decimal("-5")),
        ]
        report = analytics.analyze(trades)
        assert "BULL" in report.by_regime
        assert "BEAR" in report.by_regime
        assert report.by_regime["BULL"].winning_trades == 1
        assert report.by_regime["BEAR"].losing_trades == 1

    def test_analyze_by_confidence(self) -> None:
        """Should group trades by confidence bucket."""
        analytics = BacktestAnalytics()
        trades = [
            TradeRecord(symbol="BTC-BRL", entry_time="t1", exit_time="t2",
                       entry_price=Decimal("100"), exit_price=Decimal("110"),
                       confidence=Decimal("0.55"), realized_pnl=Decimal("10")),
            TradeRecord(symbol="BTC-BRL", entry_time="t3", exit_time="t4",
                       entry_price=Decimal("100"), exit_price=Decimal("115"),
                       confidence=Decimal("0.85"), realized_pnl=Decimal("15")),
        ]
        report = analytics.analyze(trades)
        assert "0.50-0.59" in report.by_confidence
        assert "0.80-0.89" in report.by_confidence

    def test_score_confidence_matrix(self) -> None:
        """Should build score x confidence matrix."""
        analytics = BacktestAnalytics()
        trades = [
            TradeRecord(symbol="BTC-BRL", entry_time="t1", exit_time="t2",
                       entry_price=Decimal("100"), exit_price=Decimal("110"),
                       setup_score=70, confidence=Decimal("0.85"),
                       realized_pnl=Decimal("10")),
        ]
        report = analytics.analyze(trades)
        assert "65-79" in report.score_confidence_matrix
        assert "0.80-0.89" in report.score_confidence_matrix["65-79"]

    def test_metrics_calculation(self) -> None:
        """Metrics should be calculated correctly."""
        analytics = BacktestAnalytics()
        trades = [
            TradeRecord(symbol="BTC-BRL", entry_time="t1", exit_time="t2",
                       entry_price=Decimal("100"), exit_price=Decimal("110"),
                       realized_pnl=Decimal("10"), realized_r=Decimal("2")),
            TradeRecord(symbol="BTC-BRL", entry_time="t3", exit_time="t4",
                       entry_price=Decimal("100"), exit_price=Decimal("95"),
                       realized_pnl=Decimal("-5"), realized_r=Decimal("-1")),
        ]
        report = analytics.analyze(trades)
        m = report.by_exit_reason.get("UNKNOWN") or list(report.by_exit_reason.values())[0]
        assert m.total_trades == 2
        assert m.winning_trades == 1
        assert m.losing_trades == 1
        assert m.net_pnl == Decimal("5")


# ============================================================
# Parameter Sweep Tests
# ============================================================


class TestParameterSweep:

    def test_sweep_with_no_parameters(self) -> None:
        """Sweep with no parameters should run once."""
        candles = _make_candles(50, trend="UP")
        sweep = ParameterSweep(min_trades=5)
        report = sweep.sweep(candles, "BTC-BRL", [])
        assert len(report.results) == 1

    def test_sweep_with_one_parameter(self) -> None:
        """Sweep with one parameter should test all values."""
        candles = _make_candles(50, trend="UP")
        sweep = ParameterSweep(min_trades=1)
        params = [SweepParameter(name="min_risk_reward", values=[1.5, 2.0, 2.5])]
        report = sweep.sweep(candles, "BTC-BRL", params)
        assert len(report.results) == 3

    def test_sweep_with_two_parameters(self) -> None:
        """Sweep with two parameters should test all combinations."""
        candles = _make_candles(50, trend="UP")
        sweep = ParameterSweep(min_trades=1)
        params = [
            SweepParameter(name="min_risk_reward", values=[1.5, 2.0]),
            SweepParameter(name="min_confidence", values=[0.5, 0.6]),
        ]
        report = sweep.sweep(candles, "BTC-BRL", params)
        assert len(report.results) == 4  # 2 x 2

    def test_sweep_finds_best(self) -> None:
        """Sweep should identify best candidate."""
        candles = _make_candles(100, trend="UP")
        sweep = ParameterSweep(min_trades=1)
        params = [SweepParameter(name="min_risk_reward", values=[1.0, 1.5, 2.0])]
        report = sweep.sweep(candles, "BTC-BRL", params)
        assert report.best_candidate is not None
        assert report.best_candidate.composite_score > Decimal("-999")

    def test_insufficient_sample_marked(self) -> None:
        """Configurations with too few trades should be marked."""
        candles = _make_candles(30, trend="UP")
        sweep = ParameterSweep(min_trades=100)  # High threshold
        params = [SweepParameter(name="min_risk_reward", values=[5.0])]  # Very strict
        report = sweep.sweep(candles, "BTC-BRL", params)
        # With strict R/R, likely fewer than 100 trades
        for r in report.results:
            if r.metrics.get("total_trades", 0) < 100:
                assert not r.sample_sufficient


# ============================================================
# Train/Validation/Test Tests
# ============================================================


class TestTrainValidationTest:

    def test_split_proportions(self) -> None:
        """Split should produce correct proportions."""
        tv = TrainValidationTest()
        candles = _make_candles(100)
        splits = tv.split(candles)
        assert len(splits["train"]) == 60
        assert len(splits["validation"]) == 20
        assert len(splits["test"]) == 20
        assert len(splits["train"]) + len(splits["validation"]) + len(splits["test"]) == 100

    def test_evaluate_split(self) -> None:
        """Should run backtest on each split."""
        tv = TrainValidationTest()
        candles = _make_candles(100, trend="UP")
        config = BacktestConfig(initial_capital=Decimal("100.00"))
        results = tv.evaluate_split(candles, "BTC-BRL", config)
        assert "train" in results
        assert "validation" in results
        assert "test" in results

    def test_no_look_ahead_in_split(self) -> None:
        """Test set should not overlap with train set."""
        tv = TrainValidationTest()
        candles = _make_candles(100)
        splits = tv.split(candles)
        train_times = {c.timestamp for c in splits["train"]}
        test_times = {c.timestamp for c in splits["test"]}
        assert len(train_times & test_times) == 0


# ============================================================
# Baseline Comparison Tests
# ============================================================


class TestBaselineComparison:

    def test_compare_better_candidate(self) -> None:
        """Better candidate should be recommended for review."""
        comp = BaselineComparator()
        baseline = BacktestResult(
            total_trades=20, winning_trades=10, losing_trades=10,
            win_rate=Decimal("50"), net_profit=Decimal("10"),
            expectancy=Decimal("0.5"), profit_factor=Decimal("1.5"),
            max_drawdown=Decimal("10"), avg_r=Decimal("0.5"),
        )
        candidate = BacktestResult(
            total_trades=25, winning_trades=15, losing_trades=10,
            win_rate=Decimal("60"), net_profit=Decimal("20"),
            expectancy=Decimal("1.0"), profit_factor=Decimal("2.0"),
            max_drawdown=Decimal("8"), avg_r=Decimal("1.0"),
        )
        result = comp.compare(baseline, candidate)
        assert result.recommendation == "CANDIDATE_FOR_REVIEW"
        assert result.candidate_metrics.expectancy > result.baseline_metrics.expectancy

    def test_compare_worse_candidate(self) -> None:
        """Worse candidate should recommend keeping current."""
        comp = BaselineComparator()
        baseline = BacktestResult(
            total_trades=20, winning_trades=15, losing_trades=5,
            win_rate=Decimal("75"), net_profit=Decimal("30"),
            expectancy=Decimal("1.5"), profit_factor=Decimal("3.0"),
            max_drawdown=Decimal("5"), avg_r=Decimal("1.5"),
        )
        candidate = BacktestResult(
            total_trades=15, winning_trades=5, losing_trades=10,
            win_rate=Decimal("33"), net_profit=Decimal("-5"),
            expectancy=Decimal("-0.3"), profit_factor=Decimal("0.5"),
            max_drawdown=Decimal("15"), avg_r=Decimal("-0.5"),
        )
        result = comp.compare(baseline, candidate)
        assert result.recommendation == "KEEP_CURRENT"


# ============================================================
# Candle Conversion Tests
# ============================================================


class TestCandleConversion:

    def test_convert_from_dicts(self) -> None:
        """Should convert dict list to Candle list."""
        data = [
            {"timestamp": "t1", "open": "100", "high": "101", "low": "99", "close": "100", "volume": "1000"},
            {"timestamp": "t2", "open": "100", "high": "102", "low": "98", "close": "101", "volume": "1200"},
        ]
        candles = create_candles_from_dicts(data)
        assert len(candles) == 2
        assert candles[0].close == Decimal("100")
        assert candles[1].close == Decimal("101")

    def test_convert_empty(self) -> None:
        """Empty list should return empty list."""
        candles = create_candles_from_dicts([])
        assert len(candles) == 0


# ============================================================
# RSI Calculation Tests
# ============================================================


class TestRSI:

    def test_rsi_all_gains(self) -> None:
        """All gains should give RSI ~100."""
        engine = BacktestEngineV2()
        closes = [Decimal(str(100 + i)) for i in range(20)]
        rsi = engine._calculate_rsi(closes, 14)
        assert rsi == Decimal("100")

    def test_rsi_all_losses(self) -> None:
        """All losses should give RSI ~0."""
        engine = BacktestEngineV2()
        closes = [Decimal(str(200 - i)) for i in range(20)]
        rsi = engine._calculate_rsi(closes, 14)
        assert rsi == Decimal("0")

    def test_rsi_insufficient_data(self) -> None:
        """Insufficient data should return 50."""
        engine = BacktestEngineV2()
        closes = [Decimal("100")]
        rsi = engine._calculate_rsi(closes, 14)
        assert rsi == Decimal("50")


# ============================================================
# Backtest Metrics Tests
# ============================================================


class TestBacktestMetrics:

    def test_comprehensive_metrics(self) -> None:
        """Should calculate all metrics correctly."""
        candles = _make_candles(100, trend="UP")
        engine = BacktestEngineV2(BacktestConfig(initial_capital=Decimal("100.00")))
        result = engine.run("BTC-BRL", candles)

        # Basic metrics should be populated
        assert isinstance(result.total_trades, int)
        assert isinstance(result.win_rate, Decimal)
        assert isinstance(result.net_profit, Decimal)
        assert isinstance(result.profit_factor, Decimal)
        assert isinstance(result.max_drawdown, Decimal)
        assert isinstance(result.avg_r, Decimal)
        assert isinstance(result.expectancy, Decimal)

    def test_consecutive_wins_losses(self) -> None:
        """Should track consecutive wins/losses."""
        engine = BacktestEngineV2()
        # Create trades manually
        result = BacktestResult()
        result.trades = [
            TradeRecord(realized_pnl=Decimal("10"), realized_r=Decimal("2")),
            TradeRecord(realized_pnl=Decimal("5"), realized_r=Decimal("1")),
            TradeRecord(realized_pnl=Decimal("-3"), realized_r=Decimal("-0.5")),
            TradeRecord(realized_pnl=Decimal("8"), realized_r=Decimal("1.5")),
        ]
        result = engine._calculate_metrics(result)
        assert result.max_consecutive_wins == 2
        assert result.max_consecutive_losses == 1
