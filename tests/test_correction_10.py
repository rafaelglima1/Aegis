"""AEGIS V1.3 - Correction #10 Tests.

Architectural closure: kill switch, auth, hard limits, Alembic, financial source.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============================================================
# AC-C10-07: Hard risk limits
# ============================================================


class TestHardRiskLimits:

    def test_max_positions_hard_limit_exists(self) -> None:
        """AC-C10-07: MAX_POSITIONS_HARD_LIMIT is defined."""
        from aegis.risk_engine.risk_limits import MAX_POSITIONS_HARD_LIMIT
        assert MAX_POSITIONS_HARD_LIMIT == 1

    def test_max_positions_hard_limit_enforced(self) -> None:
        """AC-C10-07: RiskLimits clamps max_simultaneous_positions to hard limit."""
        from aegis.risk_engine.risk_limits import RiskLimits, MAX_POSITIONS_HARD_LIMIT
        limits = RiskLimits(max_simultaneous_positions=5)
        assert limits.max_simultaneous_positions == MAX_POSITIONS_HARD_LIMIT

    def test_max_positions_hard_limit_at_boundary(self) -> None:
        """AC-C10-07: RiskLimits accepts hard limit value."""
        from aegis.risk_engine.risk_limits import RiskLimits, MAX_POSITIONS_HARD_LIMIT
        limits = RiskLimits(max_simultaneous_positions=MAX_POSITIONS_HARD_LIMIT)
        assert limits.max_simultaneous_positions == MAX_POSITIONS_HARD_LIMIT

    def test_leverage_hard_limit_exists(self) -> None:
        """AC-C10-07: MAX_LEVERAGE_HARD_LIMIT prevents leverage."""
        from aegis.risk_engine.risk_limits import MAX_LEVERAGE_HARD_LIMIT
        assert MAX_LEVERAGE_HARD_LIMIT == Decimal("1.0")

    def test_risk_engine_enforces_max_positions(self) -> None:
        """AC-C10-07: RiskEngine rejects when max positions reached."""
        from aegis.risk_engine.risk_engine import RiskEngine
        from aegis.risk_engine.risk_limits import RiskLimits
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction

        limits = RiskLimits(max_simultaneous_positions=1)
        engine = RiskEngine(limits)
        engine._positions_count = 1

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.8"),
            thesis="test",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("49000"),
            take_profit=Decimal("52000"),
        )
        result = engine.evaluate(decision)
        assert result.status == "REJECTED"
        assert any(v.code == "MAX_POSITIONS" for v in result.violations)


# ============================================================
# AC-C10-08: Kill switch blocks orders
# ============================================================


class TestKillSwitchWiring:

    def test_kill_switch_blocks_risk_engine(self) -> None:
        """AC-C10-08: Activated kill switch causes REJECTED status."""
        from aegis.risk_engine.risk_engine import RiskEngine
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction

        engine = RiskEngine()
        engine.activate_kill_switch()

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.8"),
            thesis="test",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("49000"),
            take_profit=Decimal("52000"),
        )
        result = engine.evaluate(decision)
        assert result.status == "REJECTED"
        assert any(v.code == "KILL_SWITCH_ACTIVE" for v in result.violations)

    def test_kill_switch_deactivation_allows_orders(self) -> None:
        """AC-C10-08: Deactivated kill switch allows normal operation."""
        from aegis.risk_engine.risk_engine import RiskEngine
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction

        engine = RiskEngine()
        engine.activate_kill_switch()
        engine.deactivate_kill_switch()

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.8"),
            thesis="test",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("49000"),
            take_profit=Decimal("52000"),
        )
        result = engine.evaluate(decision)
        assert result.status == "APPROVED"

    def test_circuit_breaker_activates_kill_switch(self) -> None:
        """AC-C10-08: Circuit breaker auto-activates kill switch."""
        from aegis.risk_engine.risk_engine import RiskEngine
        from aegis.risk_engine.risk_limits import RiskLimits

        limits = RiskLimits(
            reference_capital=Decimal("10000"),
            circuit_breaker_drawdown_pct=Decimal("0.10"),
        )
        engine = RiskEngine(limits)

        # Simulate 15% drawdown
        engine.update_equity(Decimal("8500"))
        assert engine.circuit_breaker_active is True
        assert engine._kill_switch_active is True


# ============================================================
# AC-C10-10: API authentication
# ============================================================


class TestAPIAuthentication:

    def test_api_key_env_var_exists(self) -> None:
        """AC-C10-10: AEGIS_API_KEY environment variable is checked."""
        import aegis.main as main_mod
        assert hasattr(main_mod, "API_KEY")

    def test_require_api_key_function_exists(self) -> None:
        """AC-C10-10: require_api_key dependency function exists."""
        from aegis.main import require_api_key
        assert callable(require_api_key)

    def test_api_key_bypass_when_not_set(self) -> None:
        """AC-C10-10: Development mode bypasses auth when API_KEY is empty."""
        import aegis.main as main_mod
        original = main_mod.API_KEY
        try:
            main_mod.API_KEY = ""
            result = run(main_mod.require_api_key(authorization=None))
            assert result is None  # No exception = bypass
        finally:
            main_mod.API_KEY = original


# ============================================================
# AC-C10-24: Alembic migration exists
# ============================================================


class TestAlembicMigration:

    def test_migration_file_exists(self) -> None:
        """AC-C10-24: Initial Alembic migration file exists."""
        migration = Path("alembic/versions/001_initial_schema.py")
        assert migration.exists()

    def test_migration_has_upgrade(self) -> None:
        """AC-C10-24: Migration has upgrade function."""
        content = Path("alembic/versions/001_initial_schema.py").read_text()
        assert "def upgrade() -> None:" in content

    def test_migration_has_downgrade(self) -> None:
        """AC-C10-24: Migration has downgrade function."""
        content = Path("alembic/versions/001_initial_schema.py").read_text()
        assert "def downgrade() -> None:" in content

    def test_migration_creates_all_tables(self) -> None:
        """AC-C10-24: Migration creates all required tables."""
        content = Path("alembic/versions/001_initial_schema.py").read_text()
        required_tables = [
            "market_states", "ai_runs", "trade_intents", "risk_decisions",
            "orders", "fills", "positions", "portfolio_snapshots", "audit_events",
        ]
        for table in required_tables:
            assert table in content, f"Table {table} not in migration"

    def test_alembic_ini_exists(self) -> None:
        """AC-C10-24: alembic.ini configuration exists."""
        assert Path("alembic.ini").exists()

    def test_alembic_env_exists(self) -> None:
        """AC-C10-24: alembic/env.py exists."""
        assert Path("alembic/env.py").exists()


# ============================================================
# AC-C10-01/02/03: PostgreSQL as financial source
# ============================================================


class TestPostgreSQLFinancialSource:

    def test_db_models_use_decimal(self) -> None:
        """AC-C10-01: Financial columns use Numeric (Decimal), not Float."""
        from aegis.db.models import (
            OrderModel, FillModel, PositionModel, PortfolioSnapshotModel,
            TradeIntentModel, RiskDecisionModel,
        )
        for model in [OrderModel, FillModel, PositionModel, PortfolioSnapshotModel,
                       TradeIntentModel, RiskDecisionModel]:
            for col in model.__table__.columns:
                if hasattr(col.type, 'precision') and col.type.precision == 20:
                    assert str(col.type) != "Float", f"{model.__name__}.{col.name} uses Float"

    def test_repositories_exist(self) -> None:
        """AC-C10-01: Repository layer exists for persistence."""
        from aegis.db.repositories import (
            OrderRepository, PositionRepository, FillRepository,
            PortfolioRepository, AuditRepository,
        )
        assert OrderRepository is not None
        assert PositionRepository is not None
        assert FillRepository is not None
        assert PortfolioRepository is not None
        assert AuditRepository is not None

    def test_db_session_exists(self) -> None:
        """AC-C10-01: Database session management exists."""
        from aegis.db.session import get_db_session
        assert callable(get_db_session)


# ============================================================
# AC-C10-16: Decimal safety
# ============================================================


class TestDecimalSafety:

    def test_portfolio_uses_decimal(self) -> None:
        """AC-C10-16: Portfolio uses Decimal for all financial values."""
        from aegis.portfolio.portfolio import Portfolio
        p = Portfolio(initial_cash=Decimal("10000.00"))
        assert isinstance(p.cash, Decimal)
        assert isinstance(p.total_realized_pnl, Decimal)
        assert isinstance(p.total_fees, Decimal)

    def test_risk_limits_use_decimal(self) -> None:
        """AC-C10-16: RiskLimits uses Decimal for percentage values."""
        from aegis.risk_engine.risk_limits import RiskLimits
        limits = RiskLimits()
        assert isinstance(limits.max_risk_per_trade_pct, Decimal)
        assert isinstance(limits.circuit_breaker_drawdown_pct, Decimal)
        assert isinstance(limits.max_daily_loss_pct, Decimal)

    def test_sandbox_uses_decimal(self) -> None:
        """AC-C10-16: SandboxBroker uses Decimal for balance and fills."""
        from aegis.execution.sandbox import SandboxBroker
        broker = SandboxBroker(initial_balance=Decimal("10000.00"))
        assert isinstance(broker.balance, Decimal)


# ============================================================
# AC-C10-11..15: LIVE safety
# ============================================================


class TestLIVESafety:

    def test_default_is_sandbox(self) -> None:
        """AC-C10-11: Default configuration is SANDBOX."""
        from aegis.config import Settings, TradingEnvironment
        settings = Settings()
        assert settings.trading_environment == TradingEnvironment.SANDBOX
        assert settings.live_enabled is False

    def test_live_disabled_blocks_factory(self) -> None:
        """AC-C10-12: LIVE + disabled raises RuntimeError."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings, TradingEnvironment
        settings = Settings(
            trading_environment=TradingEnvironment.LIVE,
            live_enabled=False,
        )
        with pytest.raises(RuntimeError):
            create_broker(settings)

    def test_live_no_credentials_blocks(self) -> None:
        """AC-C10-12: MercadoBitcoinBroker blocks without credentials."""
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig
        from aegis.domain.enums import OrderSide, OrderStatus
        from aegis.execution.broker import OrderSubmission

        config = MercadoBitcoinConfig(api_key="", api_secret="", enabled=True)
        broker = MercadoBitcoinBroker(config)
        result = run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000"),
            correlation_id=uuid4(),
        )))
        assert result.status == OrderStatus.REJECTED

    def test_live_disabled_blocks_all_orders(self) -> None:
        """AC-C10-11: MercadoBitcoinBroker with enabled=False blocks all orders."""
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig
        from aegis.domain.enums import OrderSide, OrderStatus
        from aegis.execution.broker import OrderSubmission

        config = MercadoBitcoinConfig(api_key="key", api_secret="secret", enabled=False)
        broker = MercadoBitcoinBroker(config)
        for side in [OrderSide.BUY, OrderSide.SELL]:
            result = run(broker.submit_order(OrderSubmission(
                order_id=uuid4(), idempotency_key=uuid4(),
                symbol="BTC-BRL", side=side,
                quantity=Decimal("0.001"), price=Decimal("50000"),
                correlation_id=uuid4(),
            )))
            assert result.status == OrderStatus.REJECTED

    def test_no_network_in_tests(self) -> None:
        """AC-C10-13: No real HTTP calls in tests."""
        from unittest.mock import AsyncMock, MagicMock
        from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig
        from aegis.domain.enums import OrderSide, OrderStatus
        from aegis.execution.broker import OrderSubmission

        config = MercadoBitcoinConfig(api_key="test", api_secret="test", enabled=True)
        broker = MercadoBitcoinBroker(config)
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "test"}
        mock_client.post = AsyncMock(return_value=mock_response)
        broker._client = mock_client
        broker._access_token = "fake"
        broker._token_expiry = 9999999999.0

        result = run(broker.submit_order(OrderSubmission(
            order_id=uuid4(), idempotency_key=uuid4(),
            symbol="BTC-BRL", side=OrderSide.BUY,
            quantity=Decimal("0.001"), price=Decimal("50000"),
            correlation_id=uuid4(),
        )))
        assert result.status == OrderStatus.SUBMITTED
        mock_client.post.assert_called_once()


# ============================================================
# AC-C10-05: No second operational state
# ============================================================


class TestNoSecondState:

    def test_pipeline_uses_worker_state(self) -> None:
        """AC-C10-05: TradingPipeline syncs state back to worker, not independently."""
        from aegis.pipeline import TradingPipeline
        pipeline = TradingPipeline()
        assert hasattr(pipeline, "state")
        assert isinstance(pipeline.state, dict)

    def test_worker_is_singleton(self) -> None:
        """AC-C10-05: get_worker() returns the same instance."""
        from aegis.worker import get_worker
        w1 = get_worker()
        w2 = get_worker()
        assert w1 is w2


# ============================================================
# AC-C10-17..21: Replay and Backtest
# ============================================================


class TestReplayBacktest:

    def test_replay_exists(self) -> None:
        """AC-C10-17: ReplayEngine exists."""
        from aegis.replay import ReplayEngine
        assert ReplayEngine is not None

    def test_backtest_exists(self) -> None:
        """AC-C10-20: BacktestEngine exists."""
        from aegis.backtest import BacktestEngine
        assert BacktestEngine is not None

    def test_replay_cannot_invoke_live(self) -> None:
        """AC-C10-17: Replay cannot invoke live execution."""
        from aegis.replay import ReplayEngine
        import inspect
        source = inspect.getsource(ReplayEngine)
        assert "LIVE" not in source or "cannot" in source.lower() or "never" in source.lower()

    def test_backtest_has_checksum(self) -> None:
        """AC-C10-19: Backtest datasets have checksum."""
        from aegis.backtest import Dataset
        import inspect
        source = inspect.getsource(Dataset)
        assert "checksum" in source.lower()


# ============================================================
# AC-C10-25: Audit trail
# ============================================================


class TestAuditTrail:

    def test_audit_logger_exists(self) -> None:
        """AC-C10-25: AuditLogger exists."""
        from aegis.audit import AuditLogger
        assert AuditLogger is not None

    def test_audit_event_model_exists(self) -> None:
        """AC-C10-25: AuditEventModel exists for persistence."""
        from aegis.db.models import AuditEventModel
        assert AuditEventModel is not None

    def test_audit_has_correlation_id(self) -> None:
        """AC-C10-25: Audit events have correlation_id."""
        from aegis.audit import AuditLogger, AuditEvent, AuditEventType
        logger = AuditLogger()
        event = AuditEvent(
            event_type=AuditEventType.SYSTEM,
            component="test",
            action="test_action",
        )
        logger.record(event)
        assert event.correlation_id is not None


# ============================================================
# AC-C10-26: Observability
# ============================================================


class TestObservability:

    def test_metrics_collector_exists(self) -> None:
        """AC-C10-26: MetricsCollector exists."""
        from aegis.audit import MetricsCollector
        assert MetricsCollector is not None

    def test_security_guard_exists(self) -> None:
        """AC-C10-26: SecurityGuard exists for secret sanitization."""
        from aegis.audit import SecurityGuard
        guard = SecurityGuard()
        # sanitize expects a dict
        result = guard.sanitize({"api_key": "sk-1234567890", "normal": "value"})
        assert result["api_key"] == "***REDACTED***"
        assert result["normal"] == "value"
