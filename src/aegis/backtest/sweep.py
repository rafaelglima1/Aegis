"""AEGIS Backtest Parameter Sweep and Train/Validation/Test framework."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import uuid4

from aegis.backtest.engine_v2 import BacktestConfig, BacktestEngineV2, Candle, BacktestResult
from aegis.backtest.analytics import BacktestAnalytics, BucketMetrics, BaselineComparison
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.risk_engine.position_manager import PositionManagerConfig

logger = logging.getLogger("aegis.parameter_sweep")


@dataclass
class SweepParameter:
    """A single parameter to sweep."""
    name: str
    values: list[Any]


@dataclass
class SweepResult:
    """Result of a single parameter combination."""
    result_id: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    composite_score: Decimal = Decimal("0")
    sample_sufficient: bool = False


@dataclass
class SweepReport:
    """Complete sweep report."""
    results: list[SweepResult] = field(default_factory=list)
    best_candidate: SweepResult | None = None
    baseline: SweepResult | None = None
    min_trades: int = 30


class ParameterSweep:
    """Configurable parameter sweep framework."""

    def __init__(self, min_trades: int = 30) -> None:
        self._min_trades = min_trades

    def sweep(self, candles: list[Candle], symbol: str,
              parameters: list[SweepParameter],
              base_config: BacktestConfig | None = None) -> SweepReport:
        """Run parameter sweep over all combinations."""
        report = SweepReport(min_trades=self._min_trades)
        combinations = self._generate_combinations(parameters)
        logger.info("Sweep: %d combinations", len(combinations))

        for combo in combinations:
            config = self._apply_params(base_config or BacktestConfig(), combo)
            engine = BacktestEngineV2(config)
            result = engine.run(symbol, candles)

            sufficient = result.total_trades >= self._min_trades
            composite = self._composite_score(result)

            report.results.append(SweepResult(
                result_id=str(uuid4()),
                params=combo,
                metrics={
                    "total_trades": result.total_trades,
                    "net_profit": str(result.net_profit),
                    "win_rate": str(result.win_rate),
                    "expectancy": str(result.expectancy),
                    "profit_factor": str(result.profit_factor),
                    "max_drawdown": str(result.max_drawdown),
                    "avg_r": str(result.avg_r),
                },
                composite_score=composite,
                sample_sufficient=sufficient,
            ))

        valid = [r for r in report.results if r.sample_sufficient]
        if valid:
            report.best_candidate = max(valid, key=lambda r: r.composite_score)
        return report

    def _generate_combinations(self, parameters: list[SweepParameter]) -> list[dict[str, Any]]:
        """Generate all parameter combinations."""
        if not parameters:
            return [{}]
        combos = [{}]
        for param in parameters:
            new = []
            for c in combos:
                for v in param.values:
                    new.append({**c, param.name: v})
            combos = new
        return combos

    def _apply_params(self, base: BacktestConfig, params: dict[str, Any]) -> BacktestConfig:
        """Apply parameter values to config."""
        config = copy.deepcopy(base)
        if "setup_score_min" in params:
            if config.risk_limits is None:
                config.risk_limits = RiskLimits()
            object.__setattr__(config.risk_limits, "setup_score_min", params["setup_score_min"])
        if "min_risk_reward" in params:
            if config.risk_limits is None:
                config.risk_limits = RiskLimits()
            object.__setattr__(config.risk_limits, "min_risk_reward", Decimal(str(params["min_risk_reward"])))
        if "min_confidence" in params:
            if config.risk_limits is None:
                config.risk_limits = RiskLimits()
            object.__setattr__(config.risk_limits, "min_confidence", Decimal(str(params["min_confidence"])))
        if "break_even_trigger_r" in params:
            if config.position_config is None:
                config.position_config = PositionManagerConfig()
            object.__setattr__(config.position_config, "break_even_trigger_r", Decimal(str(params["break_even_trigger_r"])))
        if "trailing_trigger_r" in params:
            if config.position_config is None:
                config.position_config = PositionManagerConfig()
            object.__setattr__(config.position_config, "trailing_trigger_r", Decimal(str(params["trailing_trigger_r"])))
        return config

    def _composite_score(self, result: BacktestResult) -> Decimal:
        """Composite evaluation score favoring robustness."""
        if result.total_trades < self._min_trades:
            return Decimal("-999")
        e_score = result.expectancy * Decimal("10")
        pf_score = min(result.profit_factor, Decimal("5")) * Decimal("5")
        r_score = result.avg_r * Decimal("3")
        dd_pen = result.max_drawdown * Decimal("2")
        bonus = Decimal("2") if result.total_trades >= 50 else Decimal("0")
        return (e_score + pf_score + r_score - dd_pen + bonus).quantize(Decimal("0.01"))


class TrainValidationTest:
    """Train/Validation/Test split framework."""

    def __init__(self, train_pct: Decimal = Decimal("0.60"),
                 val_pct: Decimal = Decimal("0.20"),
                 test_pct: Decimal = Decimal("0.20")) -> None:
        self._train = train_pct
        self._val = val_pct
        self._test = test_pct

    def split(self, candles: list[Candle]) -> dict[str, list[Candle]]:
        """Split candles into train/validation/test."""
        n = len(candles)
        t = int(n * self._train)
        v = t + int(n * self._val)
        return {"train": candles[:t], "validation": candles[t:v], "test": candles[v:]}

    def evaluate_split(self, candles: list[Candle], symbol: str,
                       config: BacktestConfig) -> dict[str, BacktestResult]:
        """Run backtest on each split."""
        results = {}
        for name, data in self.split(candles).items():
            if data:
                engine = BacktestEngineV2(config)
                results[name] = engine.run(symbol, data)
            else:
                results[name] = BacktestResult(config=config)
        return results


class BaselineComparator:
    """Compare baseline vs candidate configuration."""

    def compare(self, baseline: BacktestResult,
                candidate: BacktestResult) -> BaselineComparison:
        """Compare two backtest results."""
        comp = BaselineComparison()
        comp.baseline_metrics = BucketMetrics(
            name="baseline", total_trades=baseline.total_trades,
            winning_trades=baseline.winning_trades, losing_trades=baseline.losing_trades,
            win_rate=baseline.win_rate, net_pnl=baseline.net_profit,
            avg_r=baseline.avg_r, expectancy=baseline.expectancy,
            profit_factor=baseline.profit_factor, max_drawdown=baseline.max_drawdown,
        )
        comp.candidate_metrics = BucketMetrics(
            name="candidate", total_trades=candidate.total_trades,
            winning_trades=candidate.winning_trades, losing_trades=candidate.losing_trades,
            win_rate=candidate.win_rate, net_pnl=candidate.net_profit,
            avg_r=candidate.avg_r, expectancy=candidate.expectancy,
            profit_factor=candidate.profit_factor, max_drawdown=candidate.max_drawdown,
        )
        if baseline.net_profit != 0:
            comp.improvement["net_pnl_pct"] = (
                (candidate.net_profit - baseline.net_profit) / abs(baseline.net_profit) * 100
            )
        comp.improvement["win_rate_diff"] = candidate.win_rate - baseline.win_rate
        comp.improvement["expectancy_diff"] = candidate.expectancy - baseline.expectancy
        comp.improvement["drawdown_diff"] = candidate.max_drawdown - baseline.max_drawdown

        if candidate.expectancy > baseline.expectancy and candidate.max_drawdown <= baseline.max_drawdown * Decimal("1.2"):
            comp.recommendation = "CANDIDATE_FOR_REVIEW"
        elif candidate.expectancy > baseline.expectancy:
            comp.recommendation = "CANDIDATE_HIGHER_RISK"
        else:
            comp.recommendation = "KEEP_CURRENT"
        return comp
