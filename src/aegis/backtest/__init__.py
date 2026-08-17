"""AEGIS Backtest Engine — backtesting, metrics and experiment registry."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from aegis.domain.contracts import utc_now


class ExperimentStatus(Enum):
    """Experiment execution status."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class Dataset:
    """AC-12.01: Each dataset has an ID and version.
    AC-12.02: Each dataset has a checksum."""

    dataset_id: UUID = field(default_factory=uuid4)
    version: str = "1.0"
    symbol: str = ""
    data_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def compute_checksum(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()


@dataclass
class ExperimentConfig:
    """AC-12.06: Experiment configuration is recorded."""

    model: str = ""
    prompt_version: str = ""
    seed: int | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestMetrics:
    """Backtest performance metrics."""

    total_pnl: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    win_rate: Decimal = Decimal("0")
    profit_factor: Decimal = Decimal("0")
    sharpe_ratio: Decimal | None = None
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")


@dataclass
class BacktestResult:
    """AC-12.08: Backtest results are persisted."""

    result_id: UUID = field(default_factory=uuid4)
    experiment_id: UUID = field(default_factory=uuid4)
    dataset_id: UUID = field(default_factory=uuid4)
    status: ExperimentStatus = ExperimentStatus.PENDING
    metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    trades: list[dict[str, Any]] = field(default_factory=list)
    portfolio_snapshots: list[dict[str, Any]] = field(default_factory=list)
    started_at: Any = None
    completed_at: Any = None
    error: str | None = None


class BacktestEngine:
    """AC-12.14: Backtest cannot submit real broker orders."""

    def __init__(self) -> None:
        self._datasets: dict[UUID, Dataset] = {}
        self._experiments: dict[UUID, ExperimentConfig] = {}
        self._results: dict[UUID, BacktestResult] = {}
        self._audit_log: list[dict[str, Any]] = []

    def register_dataset(self, dataset: Dataset) -> UUID:
        """AC-12.01: Each dataset has an ID and version."""
        self._datasets[dataset.dataset_id] = dataset
        self._audit("dataset_registered", {"dataset_id": str(dataset.dataset_id)})
        return dataset.dataset_id

    def register_experiment(self, config: ExperimentConfig) -> UUID:
        """AC-12.03: Each experiment has an ID."""
        experiment_id = uuid4()
        self._experiments[experiment_id] = config
        self._audit("experiment_registered", {"experiment_id": str(experiment_id)})
        return experiment_id

    def get_experiment(self, experiment_id: UUID) -> ExperimentConfig | None:
        return self._experiments.get(experiment_id)

    def get_dataset(self, dataset_id: UUID) -> Dataset | None:
        return self._datasets.get(dataset_id)

    async def run_backtest(
        self,
        experiment_id: UUID,
        dataset_id: UUID,
        trades: list[dict[str, Any]] | None = None,
    ) -> BacktestResult:
        """AC-12.09: P&L is calculated.
        AC-12.10: Drawdown is calculated.
        AC-12.11: Win rate is calculated.
        AC-12.12: Profit factor is calculated.
        AC-12.13: Sharpe is calculated when applicable."""
        experiment = self._experiments.get(experiment_id)
        dataset = self._datasets.get(dataset_id)

        if not experiment or not dataset:
            return BacktestResult(
                status=ExperimentStatus.FAILED,
                error="Experiment or dataset not found",
            )

        result = BacktestResult(
            experiment_id=experiment_id,
            dataset_id=dataset_id,
            status=ExperimentStatus.RUNNING,
            started_at=utc_now(),
        )
        self._results[result.result_id] = result

        if trades:
            result.trades = trades

        metrics = self._calculate_metrics(result.trades)
        result.metrics = metrics
        result.status = ExperimentStatus.COMPLETED
        result.completed_at = utc_now()

        self._audit("backtest_completed", {
            "result_id": str(result.result_id),
            "experiment_id": str(experiment_id),
            "total_pnl": str(metrics.total_pnl),
        })

        return result

    def _calculate_metrics(self, trades: list[dict[str, Any]]) -> BacktestMetrics:
        """AC-12.09-12.13: Calculate all metrics."""
        if not trades:
            return BacktestMetrics()

        pnl_values = [Decimal(str(t.get("pnl", "0"))) for t in trades]
        total_pnl = sum(pnl_values, Decimal("0"))

        wins = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p < 0]

        total_trades = len(trades)
        winning_trades = len(wins)
        losing_trades = len(losses)

        win_rate = Decimal("0")
        if total_trades > 0:
            win_rate = Decimal(str(winning_trades)) / Decimal(str(total_trades))

        profit_factor = Decimal("0")
        total_losses = abs(sum(losses, Decimal("0")))
        if total_losses > 0:
            profit_factor = sum(wins, Decimal("0")) / total_losses

        avg_win = Decimal("0")
        if winning_trades > 0:
            avg_win = sum(wins, Decimal("0")) / Decimal(str(winning_trades))

        avg_loss = Decimal("0")
        if losing_trades > 0:
            avg_loss = sum(losses, Decimal("0")) / Decimal(str(losing_trades))

        max_drawdown = self._calculate_max_drawdown(pnl_values)

        sharpe = None
        if len(pnl_values) > 1:
            mean_return = total_pnl / Decimal(str(len(pnl_values)))
            variance = sum((p - mean_return) ** 2 for p in pnl_values) / Decimal(str(len(pnl_values) - 1))
            if variance > 0:
                sharpe = mean_return / variance

        return BacktestMetrics(
            total_pnl=total_pnl,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            profit_factor=profit_factor,
            sharpe_ratio=sharpe,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            avg_win=avg_win,
            avg_loss=avg_loss,
        )

    def _calculate_max_drawdown(self, pnl_values: list[Decimal]) -> Decimal:
        """AC-12.10: Drawdown is calculated."""
        if not pnl_values:
            return Decimal("0")

        cumulative = Decimal("0")
        peak = Decimal("0")
        max_drawdown = Decimal("0")

        for pnl in pnl_values:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        return max_drawdown

    def get_result(self, result_id: UUID) -> BacktestResult | None:
        return self._results.get(result_id)

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        return self._audit_log.copy()

    def _audit(self, event: str, data: dict[str, Any]) -> None:
        self._audit_log.append({"event": event, "timestamp": str(utc_now()), **data})
