"""AEGIS V1.3 Financial Consistency, Risk State & Configuration Integrity Tests.

Tests for Correction #2: AC-FIN-01 through AC-FIN-20.
All tests use mocks — no real credentials or live API calls.
"""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from aegis.config import Settings, TradingEnvironment
from aegis.domain.enums import OrderSide, OrderStatus, PositionSide, TradingAction
from aegis.execution.broker import BrokerAdapter, OrderResult, OrderSubmission
from aegis.execution.engine import ExecutionEngine
from aegis.execution.sandbox import SandboxBroker
from aegis.portfolio.portfolio import Portfolio
from aegis.risk_engine.risk_engine import RiskEngine, RiskDecision
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.pipeline import TradingPipeline
from aegis.ai_engine.decision_engine import DecisionContract
from aegis.audit import AuditLogger, AuditEventType


# ============================================================
# BLOCKER A — Single Source of Truth: Capital
# ============================================================

class TestCapitalSingleSource:
    """AC-FIN-01, AC-FIN-02, AC-FIN-03."""

    def test_settings_has_initial_capital(self) -> None:
        """AC-FIN-01: Settings exposes initial_capital as a single source of truth."""
        settings = Settings()
        assert hasattr(settings, "initial_capital")
        assert settings.initial_capital == Decimal("100.00")

    def test_portfolio_uses_config_capital(self) -> None:
        """AC-FIN-02: Portfolio starts with the configured capital."""
        settings = Settings()
        portfolio = Portfolio(initial_cash=settings.initial_capital)
        assert portfolio.cash == Decimal("100.00")

    def test_risk_engine_uses_config_capital(self) -> None:
        """AC-FIN-03: Risk Engine uses the same configured capital."""
        settings = Settings()
        risk = RiskEngine(RiskLimits(reference_capital=settings.initial_capital))
        assert risk.limits.reference_capital == Decimal("100.00")

    def test_no_hardcoded_10000_in_portfolio(self) -> None:
        """AC-FIN-01: Portfolio default must NOT be 10000."""
        p = Portfolio()
        assert p.cash != Decimal("10000.00")
        assert p.cash == Decimal("100.00")

    def test_config_capital_flows_to_all_components(self) -> None:
        """AC-FIN-01/02/03: Config capital flows to Portfolio and Risk."""
        settings = Settings(initial_capital=Decimal("100.00"))
        portfolio = Portfolio(initial_cash=settings.initial_capital)
        risk = RiskEngine(RiskLimits(reference_capital=settings.initial_capital))

        assert portfolio.cash == risk.limits.reference_capital == Decimal("100.00")


# ============================================================
# BLOCKER B — Single Source of Truth: max_positions
# ============================================================

class TestMaxPositionsSingleSource:
    """AC-FIN-04, AC-FIN-05, AC-FIN-06, AC-FIN-07."""

    def test_settings_has_max_positions(self) -> None:
        """AC-FIN-04: Settings exposes max_positions."""
        settings = Settings()
        assert hasattr(settings, "max_positions")
        assert settings.max_positions == 1

    def test_default_max_positions_is_1(self) -> None:
        """AC-FIN-05: Default max_positions is 1."""
        settings = Settings()
        assert settings.max_positions == 1

    def test_risk_engine_consumes_max_positions(self) -> None:
        """AC-FIN-07: Risk Engine enforces max_positions from config."""
        settings = Settings(max_positions=1)
        risk = RiskEngine(RiskLimits(max_simultaneous_positions=settings.max_positions))

        # 0 positions → approved
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.85"),
            thesis="test",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("48000"),
            take_profit=Decimal("55000"),
        )
        result = risk.evaluate(decision)
        assert result.is_approved

        # 1 position → rejected (max_positions=1)
        risk.record_position_open()
        result = risk.evaluate(decision)
        assert not result.is_approved
        violation_codes = [v.code for v in result.violations]
        assert "MAX_POSITIONS" in violation_codes

    def test_no_hardcoded_max_positions_in_risk(self) -> None:
        """AC-FIN-04: max_positions must come from RiskLimits, not be hardcoded."""
        # RiskLimits with max=3 → RiskEngine should respect it
        risk = RiskEngine(RiskLimits(max_simultaneous_positions=3))
        for _ in range(2):
            risk.record_position_open()
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.85"),
            thesis="test",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("48000"),
            take_profit=Decimal("55000"),
        )
        result = risk.evaluate(decision)
        assert result.is_approved


# ============================================================
# BLOCKER C — Fee Propagation
# ============================================================

class TestFeePropagation:
    """AC-FIN-08, AC-FIN-09."""

    @pytest.mark.asyncio
    async def test_broker_fee_reaches_portfolio(self) -> None:
        """AC-FIN-08: Fee from broker propagates to Portfolio."""
        broker = SandboxBroker(initial_balance=Decimal("500.00"))
        engine = ExecutionEngine(broker)

        approved = RiskDecision(status="APPROVED")
        result = await engine.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
            correlation_id=uuid4(),
            risk_decision=approved,
        )
        assert result.status == OrderStatus.FILLED
        assert result.fee > Decimal("0")

        portfolio = Portfolio(initial_cash=Decimal("500.00"))
        portfolio.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("0.001"),
            price=result.fill_price,
            fee=result.fee,
        )
        assert portfolio.total_fees == result.fee
        assert portfolio.cash < Decimal("500.00")

    def test_realized_pnl_considers_fees(self) -> None:
        """AC-FIN-09: Realized P&L considers fees."""
        p = Portfolio(initial_cash=Decimal("500.00"))
        p.record_fill(
            asset="AAPL",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            price=Decimal("100.00"),
            fee=Decimal("0.10"),
        )
        realized = p.close_position("AAPL", Decimal("110.00"), fee=Decimal("0.11"))
        # gross = (110-100)*1 = 10, net = 10 - 0.10 - 0.11 = 9.79
        assert realized == Decimal("9.79")


# ============================================================
# BLOCKER D — Risk Engine Restart Recovery
# ============================================================

class TestRiskEngineRestart:
    """AC-FIN-12, AC-FIN-13."""

    def test_risk_engine_rebuild_from_positions(self) -> None:
        """AC-FIN-12: Risk Engine reconstructs state from persisted positions."""
        risk = RiskEngine()
        assert risk.positions_count == 0

        # Simulate restart: rebuild from 1 open position
        risk.rebuild_from_open_positions(count=1, exposure=Decimal("50000"))
        assert risk.positions_count == 1

        # New order should be rejected (max_positions=1)
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.85"),
            thesis="test",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("48000"),
            take_profit=Decimal("55000"),
        )
        result = risk.evaluate(decision)
        assert not result.is_approved

    def test_restart_does_not_zero_positions(self) -> None:
        """AC-FIN-13: Restart does not artificially zero positions_count."""
        risk = RiskEngine()
        risk.record_position_open()
        assert risk.positions_count == 1

        # Simulate restart
        risk.rebuild_from_open_positions(count=1)
        assert risk.positions_count == 1

    def test_rebuild_zero_positions_allows_trading(self) -> None:
        """After restart with 0 open positions, trading is allowed."""
        risk = RiskEngine()
        risk.record_position_open()
        risk.record_position_close()
        assert risk.positions_count == 0

        # Rebuild with 0 positions
        risk.rebuild_from_open_positions(count=0)
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.85"),
            thesis="test",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("48000"),
            take_profit=Decimal("55000"),
        )
        result = risk.evaluate(decision)
        assert result.is_approved


# ============================================================
# HARDENING E — Risk Gate Bypass Protection
# ============================================================

class TestRiskGateHardening:
    """AC-FIN-14, AC-FIN-15."""

    @pytest.mark.asyncio
    async def test_risk_decision_blocks_broker(self) -> None:
        """AC-FIN-14: ExecutionEngine rejects when RiskDecision is REJECTED."""
        mock_broker = AsyncMock(spec=BrokerAdapter)
        engine = ExecutionEngine(mock_broker)

        rejected = RiskDecision(
            status="REJECTED",
            violations=[],
            reasons=["test"],
        )

        result = await engine.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
            correlation_id=uuid4(),
            risk_decision=rejected,
        )

        assert result.status == OrderStatus.REJECTED
        mock_broker.submit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_risk_decision_allows_broker(self) -> None:
        """AC-FIN-15: ExecutionEngine calls broker when RiskDecision is APPROVED."""
        mock_broker = AsyncMock(spec=BrokerAdapter)
        mock_broker.submit_order = AsyncMock(return_value=OrderResult(
            order_id=uuid4(),
            status=OrderStatus.FILLED,
            fill_price=Decimal("50000"),
            fill_quantity=Decimal("0.001"),
            fee=Decimal("0.50"),
        ))

        engine = ExecutionEngine(mock_broker)

        approved = RiskDecision(
            status="APPROVED",
            approved_quantity=Decimal("0.001"),
            approved_price=Decimal("50000"),
        )

        result = await engine.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
            correlation_id=uuid4(),
            risk_decision=approved,
        )

        assert result.status == OrderStatus.FILLED
        mock_broker.submit_order.assert_called_once()


# ============================================================
# TEST QUALITY F — Real Risk Gate Tests (Risk → Execution → Broker)
# ============================================================

class TestRealRiskGateFlow:
    """AC-FIN-16: Real Risk → Execution → Broker flow tests."""

    @pytest.mark.asyncio
    async def test_reject_blocks_broker_through_execution(self) -> None:
        """Risk REJECT → Execution does not call Broker.submit_order."""
        mock_broker = AsyncMock(spec=BrokerAdapter)
        engine = ExecutionEngine(mock_broker)
        risk = RiskEngine()

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.85"),
            thesis="test",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("48000"),
            take_profit=Decimal("55000"),
        )

        # Risk rejects (max_positions)
        risk.record_position_open()
        risk_result = risk.evaluate(decision)
        assert not risk_result.is_approved

        # Execution blocks
        result = await engine.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=risk_result.approved_quantity,
            price=risk_result.approved_price,
            correlation_id=uuid4(),
            risk_decision=risk_result,
        )
        assert result.status == OrderStatus.REJECTED
        mock_broker.submit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_approved_allows_broker_through_execution(self) -> None:
        """Risk APPROVED → Execution calls Broker.submit_order."""
        mock_broker = AsyncMock(spec=BrokerAdapter)
        mock_broker.submit_order = AsyncMock(return_value=OrderResult(
            order_id=uuid4(),
            status=OrderStatus.FILLED,
            fill_price=Decimal("50000"),
            fill_quantity=Decimal("0.001"),
            fee=Decimal("0.50"),
        ))

        engine = ExecutionEngine(mock_broker)
        risk = RiskEngine()

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.85"),
            thesis="test",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("48000"),
            take_profit=Decimal("55000"),
        )

        risk_result = risk.evaluate(decision)
        assert risk_result.is_approved

        result = await engine.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=risk_result.approved_quantity,
            price=risk_result.approved_price,
            correlation_id=uuid4(),
            risk_decision=risk_result,
        )

        assert result.status == OrderStatus.FILLED
        mock_broker.submit_order.assert_called_once()


# ============================================================
# RESTART TEST — Full flow
# ============================================================

class TestRestartRecovery:
    """AC-FIN-17: Full restart test."""

    def test_restart_preserves_position_count(self) -> None:
        """AC-FIN-17: After restart, Risk Engine preserves position count."""
        # Phase 1: Open a position
        risk = RiskEngine(RiskLimits(max_simultaneous_positions=1))
        risk.record_position_open()
        assert risk.positions_count == 1

        # Phase 2: Simulate restart — create new RiskEngine, rebuild
        risk_after_restart = RiskEngine(RiskLimits(max_simultaneous_positions=1))
        risk_after_restart.rebuild_from_open_positions(count=1)
        assert risk_after_restart.positions_count == 1

        # Phase 3: New order is rejected
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.85"),
            thesis="test",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("48000"),
            take_profit=Decimal("55000"),
        )
        result = risk_after_restart.evaluate(decision)
        assert not result.is_approved

    def test_worker_state_save_load(self) -> None:
        """Worker state save/load preserves positions for restart."""
        state = {
            "positions": [
                {
                    "id": str(uuid4()),
                    "symbol": "BTC-BRL",
                    "side": "LONG",
                    "quantity": "0.001",
                    "entry_price": "50000",
                    "status": "OPEN",
                }
            ],
            "orders": [],
            "history": [],
            "decisions": [],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(state, f, default=str)
            state_file = Path(f.name)

        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            open_count = sum(1 for p in loaded["positions"] if p.get("status") == "OPEN")
            assert open_count == 1

            risk = RiskEngine()
            risk.rebuild_from_open_positions(open_count)
            assert risk.positions_count == 1
        finally:
            state_file.unlink()


# ============================================================
# ACCOUNTING TEST — Deterministic values
# ============================================================

class TestAccountingDeterministic:
    """AC-FIN-18: Accounting/fees tests with deterministic values."""

    def test_accounting_with_fees(self) -> None:
        """AC-FIN-18: Entry fee and exit fee are correctly accounted."""
        # capital=100, entry=100, qty=0.2, exit=110, entry_fee=0.10, exit_fee=0.11
        p = Portfolio(initial_cash=Decimal("500.00"))
        p.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("0.2"),
            price=Decimal("100.00"),
            fee=Decimal("0.10"),
        )
        realized = p.close_position("BTC-BRL", Decimal("110.00"), fee=Decimal("0.11"))
        # gross = (110-100)*0.2 = 2.00
        # net = 2.00 - 0.10 - 0.11 = 1.79
        assert realized == Decimal("1.79")
        assert p.total_fees == Decimal("0.21")

    def test_portfolio_equity_formula(self) -> None:
        """Verify: equity = cash + unrealized_pnl."""
        p = Portfolio(initial_cash=Decimal("100.00"))
        p.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
        )
        p.update_prices({"BTC-BRL": Decimal("51000")})

        expected_equity = p.cash + p.unrealized_pnl
        assert p.equity == expected_equity


# ============================================================
# UNREALIZED P&L TEST
# ============================================================

class TestUnrealizedPnL:
    """AC-FIN-10, AC-FIN-15."""

    def test_unrealized_pnl_basic(self) -> None:
        """AC-FIN-10: Unrealized P&L updates with price changes."""
        p = Portfolio(initial_cash=Decimal("500.00"))
        p.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            price=Decimal("100.00"),
        )
        p.update_prices({"BTC-BRL": Decimal("110.00")})
        assert p.unrealized_pnl == Decimal("10.00")

    def test_unrealized_pnl_changes_with_price(self) -> None:
        """AC-FIN-10: Unrealized P&L changes when price changes."""
        p = Portfolio(initial_cash=Decimal("500.00"))
        p.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            price=Decimal("100.00"),
        )
        p.update_prices({"BTC-BRL": Decimal("110.00")})
        assert p.unrealized_pnl == Decimal("10.00")

        p.update_prices({"BTC-BRL": Decimal("95.00")})
        assert p.unrealized_pnl == Decimal("-5.00")


# ============================================================
# CASH / EQUITY / EXPOSURE CONSISTENCY
# ============================================================

class TestCashEquityExposure:
    """AC-FIN-11."""

    def test_equity_equals_cash_plus_unrealized(self) -> None:
        """AC-FIN-11: equity = cash + unrealized_pnl."""
        p = Portfolio(initial_cash=Decimal("100.00"))
        p.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
        )
        p.update_prices({"BTC-BRL": Decimal("51000")})

        assert p.equity == p.cash + p.unrealized_pnl

    def test_exposure_is_quantity_times_price(self) -> None:
        """AC-FIN-11: exposure = quantity * current_price."""
        p = Portfolio(initial_cash=Decimal("500.00"))
        p.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
        )
        p.update_prices({"BTC-BRL": Decimal("50000")})
        assert p.exposure == Decimal("50.00")

    def test_initial_cash_matches_config(self) -> None:
        """AC-FIN-02/11: Initial cash is configurable and consistent."""
        settings = Settings(initial_capital=Decimal("100.00"))
        p = Portfolio(initial_cash=settings.initial_capital)
        assert p.cash == Decimal("100.00")
        assert p.equity == Decimal("100.00")
        assert p.exposure == Decimal("0")


# ============================================================
# SECURITY — No credentials introduced
# ============================================================

class TestSecurity:
    """AC-FIN-19."""

    def test_no_real_credentials_in_config(self) -> None:
        """AC-FIN-19: No real credentials in config defaults."""
        settings = Settings()
        assert settings.llm_api_key == ""
        assert settings.live_api_key == ""
        assert settings.live_api_secret == ""
        assert settings.sandbox_api_key == ""
        assert settings.sandbox_api_secret == ""
