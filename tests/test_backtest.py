"""Tests for AEGIS Backtest, Metrics & Experiment Registry (Phase 12)."""

from __future__ import annotations

import pytest
from decimal import Decimal
from uuid import uuid4

from aegis.backtest import (
    BacktestEngine,
    Dataset,
    ExperimentConfig,
    BacktestResult,
    ExperimentStatus,
    BacktestMetrics,
)


def make_dataset(**overrides) -> Dataset:
    defaults = dict(symbol="AAPL", version="1.0", data_hash="abc123")
    defaults.update(overrides)
    return Dataset(**defaults)


def make_experiment(**overrides) -> ExperimentConfig:
    defaults = dict(model="gpt-4", prompt_version="v1", seed=42)
    defaults.update(overrides)
    return ExperimentConfig(**defaults)


def test_dataset_has_id_and_version() -> None:
    """AC-12.01: Each dataset has an ID and version."""
    engine = BacktestEngine()
    dataset = make_dataset()
    dataset_id = engine.register_dataset(dataset)
    assert dataset_id == dataset.dataset_id
    assert dataset.version == "1.0"


def test_dataset_has_checksum() -> None:
    """AC-12.02: Each dataset has a checksum."""
    dataset = make_dataset()
    checksum = dataset.compute_checksum(b"test data")
    assert len(checksum) == 64


def test_experiment_has_id() -> None:
    """AC-12.03: Each experiment has an ID."""
    engine = BacktestEngine()
    config = make_experiment()
    experiment_id = engine.register_experiment(config)
    assert experiment_id is not None
    assert engine.get_experiment(experiment_id) == config


def test_model_identity_recorded() -> None:
    """AC-12.04: Model identity is recorded."""
    config = make_experiment(model="gpt-4")
    assert config.model == "gpt-4"


def test_prompt_version_recorded() -> None:
    """AC-12.05: Prompt version is recorded."""
    config = make_experiment(prompt_version="v2")
    assert config.prompt_version == "v2"


def test_experiment_config_recorded() -> None:
    """AC-12.06: Experiment configuration is recorded."""
    config = make_experiment(parameters={"temperature": 0.7})
    assert config.parameters == {"temperature": 0.7}


def test_seed_recorded() -> None:
    """AC-12.07: Seed is recorded when applicable."""
    config = make_experiment(seed=42)
    assert config.seed == 42


@pytest.mark.asyncio
async def test_backtest_results_persisted() -> None:
    """AC-12.08: Backtest results are persisted."""
    engine = BacktestEngine()
    dataset = make_dataset()
    experiment = make_experiment()
    dataset_id = engine.register_dataset(dataset)
    experiment_id = engine.register_experiment(experiment)
    result = await engine.run_backtest(experiment_id, dataset_id)
    assert result.status == ExperimentStatus.COMPLETED
    assert engine.get_result(result.result_id) is not None


@pytest.mark.asyncio
async def test_pnl_calculated() -> None:
    """AC-12.09: P&L is calculated."""
    engine = BacktestEngine()
    dataset = make_dataset()
    experiment = make_experiment()
    dataset_id = engine.register_dataset(dataset)
    experiment_id = engine.register_experiment(experiment)
    trades = [{"pnl": "100"}, {"pnl": "-50"}, {"pnl": "200"}]
    result = await engine.run_backtest(experiment_id, dataset_id, trades)
    assert result.metrics.total_pnl == Decimal("250")


@pytest.mark.asyncio
async def test_drawdown_calculated() -> None:
    """AC-12.10: Drawdown is calculated."""
    engine = BacktestEngine()
    dataset = make_dataset()
    experiment = make_experiment()
    dataset_id = engine.register_dataset(dataset)
    experiment_id = engine.register_experiment(experiment)
    trades = [{"pnl": "100"}, {"pnl": "-50"}, {"pnl": "200"}]
    result = await engine.run_backtest(experiment_id, dataset_id, trades)
    assert result.metrics.max_drawdown >= 0


@pytest.mark.asyncio
async def test_win_rate_calculated() -> None:
    """AC-12.11: Win rate is calculated."""
    engine = BacktestEngine()
    dataset = make_dataset()
    experiment = make_experiment()
    dataset_id = engine.register_dataset(dataset)
    experiment_id = engine.register_experiment(experiment)
    trades = [{"pnl": "100"}, {"pnl": "-50"}, {"pnl": "200"}]
    result = await engine.run_backtest(experiment_id, dataset_id, trades)
    assert result.metrics.win_rate == Decimal("2") / Decimal("3")


@pytest.mark.asyncio
async def test_profit_factor_calculated() -> None:
    """AC-12.12: Profit factor is calculated."""
    engine = BacktestEngine()
    dataset = make_dataset()
    experiment = make_experiment()
    dataset_id = engine.register_dataset(dataset)
    experiment_id = engine.register_experiment(experiment)
    trades = [{"pnl": "100"}, {"pnl": "-50"}, {"pnl": "200"}]
    result = await engine.run_backtest(experiment_id, dataset_id, trades)
    assert result.metrics.profit_factor == Decimal("300") / Decimal("50")


@pytest.mark.asyncio
async def test_sharpe_calculated() -> None:
    """AC-12.13: Sharpe is calculated when applicable."""
    engine = BacktestEngine()
    dataset = make_dataset()
    experiment = make_experiment()
    dataset_id = engine.register_dataset(dataset)
    experiment_id = engine.register_experiment(experiment)
    trades = [{"pnl": "100"}, {"pnl": "-50"}, {"pnl": "200"}]
    result = await engine.run_backtest(experiment_id, dataset_id, trades)
    assert result.metrics.sharpe_ratio is not None


def test_backtest_cannot_submit_real_orders() -> None:
    """AC-12.14: Backtest cannot submit real broker orders."""
    engine = BacktestEngine()
    assert not hasattr(engine, "submit_order")
    assert not hasattr(engine, "execute_trade")
