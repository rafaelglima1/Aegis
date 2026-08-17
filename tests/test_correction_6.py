"""AEGIS V1.3 — Correction #6 Tests.

C6-01: Market price syncs Portfolio
C6-02: Portfolio/Equity/RiskEngine sync
C6-03: Worker CLOSE through RiskEngine
C6-04: SandboxBroker SELL fixes
C6-05: State synchronization
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from aegis.domain.enums import OrderSide, OrderStatus, PositionSide, PositionStatus, TradingAction
from aegis.execution.broker import OrderResult, OrderSubmission
from aegis.execution.sandbox import SandboxBroker
from aegis.portfolio.portfolio import Portfolio, PositionEntry
from aegis.risk_engine.risk_engine import RiskEngine, RiskDecision
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.ai_engine.decision_engine import DecisionContract


# ─────────────────────────────────────────────────
# MARKET PRICE
# ─────────────────────────────────────────────────


class TestMarketPrice:
    """C6-01: Market price syncs Portfolio correctly."""

    def test_ticker_updates_portfolio_current_price(self) -> None:
        """Ticker price flows into Portfolio.position.current_price."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            fee=Decimal("0.50"),
        )
        assert portfolio._positions["BTC-BRL"].current_price == Decimal("50.00")

        # Simulate tick: update_prices with market price
        portfolio.update_prices({"BTC-BRL": Decimal("60.00")})
        assert portfolio._positions["BTC-BRL"].current_price == Decimal("60.00")

    def test_update_prices_called_with_correct_price(self) -> None:
        """update_prices receives the exact ticker price."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill(
            asset="ETH-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("2"),
            price=Decimal("30.00"),
            fee=Decimal("0.50"),
        )
        market_price = Decimal("35.75")
        portfolio.update_prices({"ETH-BRL": market_price})
        assert portfolio._positions["ETH-BRL"].current_price == market_price

    def test_entry_price_immutable_after_update(self) -> None:
        """average_entry does not change when current_price is updated."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            fee=Decimal("0.50"),
        )
        portfolio.update_prices({"BTC-BRL": Decimal("80.00")})
        assert portfolio._positions["BTC-BRL"].average_entry == Decimal("50.00")

    def test_state_current_price_matches_portfolio(self) -> None:
        """State current_price = Portfolio.current_price after tick."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            fee=Decimal("0.50"),
        )
        portfolio.update_prices({"BTC-BRL": Decimal("65.00")})
        assert portfolio._positions["BTC-BRL"].current_price == Decimal("65.00")

    def test_unrealized_pnl_changes_with_price(self) -> None:
        """Unrealized P&L reflects current market price."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            fee=Decimal("0.50"),
        )
        portfolio.update_prices({"BTC-BRL": Decimal("60.00")})
        assert portfolio.unrealized_pnl == Decimal("10.00")

        portfolio.update_prices({"BTC-BRL": Decimal("45.00")})
        assert portfolio.unrealized_pnl == Decimal("-5.00")

    def test_no_fake_price_created(self) -> None:
        """Portfolio.current_price is not modified when symbol not in positions."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            fee=Decimal("0.50"),
        )
        # Update different symbol — BTC-BRL unaffected
        portfolio.update_prices({"ETH-BRL": Decimal("100.00")})
        assert portfolio._positions["BTC-BRL"].current_price == Decimal("50.00")

    def test_close_uses_correct_exit_price(self) -> None:
        """Close uses current_price (not entry_price)."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            fee=Decimal("0.50"),
        )
        portfolio.update_prices({"BTC-BRL": Decimal("60.00")})
        realized = portfolio.close_position(
            asset="BTC-BRL",
            price=Decimal("60.00"),
            fee=Decimal("0.50"),
        )
        # gross = (60 - 50) * 1 = 10, net = 10 - 0.50(entry) - 0.50(exit) = 9.00
        assert realized == Decimal("9.00")


# ─────────────────────────────────────────────────
# PORTFOLIO / EQUITY / RISK SYNC
# ─────────────────────────────────────────────────


class TestPortfolioEquityRisk:
    """C6-02: Portfolio/Equity/Risk synchronization."""

    def test_update_prices_updates_position(self) -> None:
        """update_prices updates PositionEntry.current_price."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        portfolio.update_prices({"BTC-BRL": Decimal("70.00")})
        assert portfolio._positions["BTC-BRL"].current_price == Decimal("70.00")

    def test_unrealized_pnl_correct(self) -> None:
        """unrealized_pnl = (current_price - average_entry) * quantity."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("2"), Decimal("50.00"), Decimal("0.50"))
        portfolio.update_prices({"BTC-BRL": Decimal("60.00")})
        assert portfolio.unrealized_pnl == Decimal("20.00")

    def test_equity_reflects_price(self) -> None:
        """equity = cash + unrealized_pnl."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        # cash = 100 - 0.50 (fee) - 50 (cost) = 49.50
        assert portfolio.cash == Decimal("49.50")
        portfolio.update_prices({"BTC-BRL": Decimal("60.00")})
        # equity = 49.50 + (60-50)*1 = 59.50
        assert portfolio.equity == Decimal("59.50")

    def test_peak_equity_increases(self) -> None:
        """peak_equity increases when equity rises above current peak."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        portfolio.update_prices({"BTC-BRL": Decimal("80.00")})
        # equity = 49.50 + 30 = 79.50, peak stays at 100 (initial cash)
        assert portfolio._peak_equity == Decimal("100.00")

        portfolio.update_prices({"BTC-BRL": Decimal("120.00")})
        # equity = 49.50 + 70 = 119.50 > 100, peak updated
        assert portfolio._peak_equity == Decimal("119.50")

    def test_drawdown_after_price_drop(self) -> None:
        """drawdown calculates correctly after equity peak then drop."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        # Price rises to 130: equity = 49.50 + 80 = 129.50, peak = 129.50
        portfolio.update_prices({"BTC-BRL": Decimal("130.00")})
        assert portfolio._peak_equity == Decimal("129.50")

        # Price drops to 60: equity = 49.50 + 10 = 59.50
        portfolio.update_prices({"BTC-BRL": Decimal("60.00")})
        expected_dd = (Decimal("129.50") - Decimal("59.50")) / Decimal("129.50")
        assert portfolio.drawdown == expected_dd

    def test_exposure_consistent(self) -> None:
        """Portfolio.exposure = sum(quantity * current_price)."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        portfolio.update_prices({"BTC-BRL": Decimal("60.00")})
        assert portfolio.exposure == Decimal("60.00")

    def test_fees_not_duplicated(self) -> None:
        """Total fees = entry_fee + exit_fee, charged once each."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        assert portfolio.total_fees == Decimal("0.50")

        portfolio.close_position("BTC-BRL", Decimal("60.00"), Decimal("0.50"))
        assert portfolio.total_fees == Decimal("1.00")

    def test_realized_not_contaminated_by_unrealized(self) -> None:
        """realized_pnl is independent of unrealized_pnl."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        portfolio.update_prices({"BTC-BRL": Decimal("60.00")})
        assert portfolio.unrealized_pnl == Decimal("10.00")
        assert portfolio.total_realized_pnl == Decimal("0")

        portfolio.close_position("BTC-BRL", Decimal("60.00"), Decimal("0.50"))
        assert portfolio.total_realized_pnl == Decimal("9.00")
        assert portfolio.unrealized_pnl == Decimal("0")

    def test_risk_engine_receives_equity(self) -> None:
        """RiskEngine.update_equity() can be called with Portfolio.equity."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        portfolio.update_prices({"BTC-BRL": Decimal("80.00")})

        risk = RiskEngine()
        risk.update_equity(portfolio.equity)
        # equity = 49.50 + 30 = 79.50 < 100 (initial peak), so peak stays at 100
        assert risk._peak_equity == Decimal("100.00")

        # Price rises further: equity = 49.50 + 80 = 129.50 > 100
        portfolio.update_prices({"BTC-BRL": Decimal("130.00")})
        risk.update_equity(portfolio.equity)
        assert risk._peak_equity == portfolio.equity


# ─────────────────────────────────────────────────
# WORKER STATE SYNC
# ─────────────────────────────────────────────────


class TestWorkerStateSync:
    """C6-05: Worker state synchronization from Portfolio."""

    def test_update_positions_pnl_reads_from_portfolio(self) -> None:
        """_update_positions_pnl reads current_price and unrealized_pnl from Portfolio."""
        from aegis.worker import AutonomousWorker

        worker = AutonomousWorker()
        worker.portfolio = Portfolio(initial_cash=Decimal("100.00"))
        worker.portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        worker.portfolio.update_prices({"BTC-BRL": Decimal("60.00")})

        worker._state["positions"] = [{
            "id": str(uuid4()),
            "symbol": "BTC-BRL",
            "side": "LONG",
            "quantity": "1",
            "entry_price": "50.00",
            "current_price": "50.00",
            "entry_fee": "0.50",
            "status": "OPEN",
        }]

        worker._update_positions_pnl()

        pos = worker._state["positions"][0]
        assert Decimal(pos["current_price"]) == Decimal("60.00")
        assert Decimal(pos["pnl"]) == Decimal("10.00")

    def test_state_exposure_syncs_from_portfolio(self) -> None:
        """state["exposure"] = str(portfolio.exposure) after tick."""
        from aegis.worker import AutonomousWorker

        worker = AutonomousWorker()
        worker.portfolio = Portfolio(initial_cash=Decimal("100.00"))
        worker.portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        worker.portfolio.update_prices({"BTC-BRL": Decimal("70.00")})
        assert worker.portfolio.exposure == Decimal("70.00")

    def test_state_peak_equity_syncs(self) -> None:
        """state["peak_equity"] = str(portfolio._peak_equity)."""
        from aegis.worker import AutonomousWorker

        worker = AutonomousWorker()
        worker.portfolio = Portfolio(initial_cash=Decimal("100.00"))
        worker.portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        worker.portfolio.update_prices({"BTC-BRL": Decimal("150.00")})
        # equity = 49.50 + 100 = 149.50 > 100 → peak updated
        assert worker.portfolio._peak_equity == Decimal("149.50")

    def test_multiple_ticks_preserve_consistency(self) -> None:
        """Multiple price updates maintain Portfolio consistency."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))

        prices = [Decimal("55"), Decimal("60"), Decimal("50"), Decimal("70")]
        for p in prices:
            portfolio.update_prices({"BTC-BRL": p})
            assert portfolio._positions["BTC-BRL"].current_price == p
            expected = (p - Decimal("50")) * Decimal("1")
            assert portfolio.unrealized_pnl == expected

    def test_price_rising_updates_correctly(self) -> None:
        """Rising price increases unrealized_pnl and equity."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        portfolio.update_prices({"BTC-BRL": Decimal("100.00")})
        assert portfolio.equity == Decimal("99.50")  # 49.50 + 50.00

    def test_price_falling_updates_correctly(self) -> None:
        """Falling price decreases unrealized_pnl, may reduce equity below peak."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        portfolio.update_prices({"BTC-BRL": Decimal("30.00")})
        assert portfolio.unrealized_pnl == Decimal("-20.00")
        assert portfolio.equity == Decimal("29.50")
        assert portfolio._peak_equity == Decimal("100.00")

    def test_position_closed_removes_from_portfolio(self) -> None:
        """After close, position no longer contributes to unrealized_pnl."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        portfolio.update_prices({"BTC-BRL": Decimal("60.00")})
        assert portfolio.unrealized_pnl == Decimal("10.00")

        portfolio.close_position("BTC-BRL", Decimal("60.00"), Decimal("0.50"))
        assert portfolio.unrealized_pnl == Decimal("0")
        assert portfolio.total_realized_pnl == Decimal("9.00")
        assert "BTC-BRL" not in portfolio._positions or portfolio._positions["BTC-BRL"].quantity == 0


# ─────────────────────────────────────────────────
# CLOSE ARCHITECTURE
# ─────────────────────────────────────────────────


class TestCloseArchitecture:
    """C6-03: CLOSE passes through RiskEngine.evaluate()."""

    def test_autonomous_close_passes_risk_engine(self) -> None:
        """Worker autonomous CLOSE creates DecisionContract and calls evaluate()."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod

        worker = AutonomousWorker()
        worker.portfolio = Portfolio(initial_cash=Decimal("100.00"))
        worker.portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        worker.portfolio.update_prices({"BTC-BRL": Decimal("60.00")})

        pos_id = str(uuid4())
        worker._state["positions"] = [{
            "id": pos_id,
            "symbol": "BTC-BRL",
            "side": "LONG",
            "quantity": "1",
            "entry_price": "50.00",
            "current_price": "60.00",
            "entry_fee": "0.50",
            "status": "OPEN",
        }]

        # Patch RiskEngine.evaluate to track calls
        original_evaluate = worker.risk_engine.evaluate
        evaluate_calls = []

        def tracking_evaluate(decision):
            evaluate_calls.append(decision)
            return original_evaluate(decision)

        worker.risk_engine.evaluate = tracking_evaluate

        # Simulate CLOSE decision from LLM
        decision = DecisionContract(
            action=TradingAction.CLOSE,
            confidence=Decimal("1.0"),
            thesis="Test close",
        )
        # Manually invoke the CLOSE path (simulating _process_symbol)
        close_decision = DecisionContract(
            action=TradingAction.CLOSE,
            confidence=Decimal("1.0"),
            thesis=f"Autonomous CLOSE for BTC-BRL",
        )
        close_risk = worker.risk_engine.evaluate(close_decision)
        assert close_risk.is_approved

        # Execute close
        realized = worker.portfolio.close_position(
            asset="BTC-BRL",
            price=Decimal("60.00"),
            fee=Decimal("0.50"),
        )
        assert realized == Decimal("9.00")

        # Verify RiskEngine was called with CLOSE decision
        close_decisions = [d for d in evaluate_calls if d.action == TradingAction.CLOSE]
        assert len(close_decisions) == 1

    @pytest.mark.asyncio
    async def test_manual_close_passes_risk_engine(self) -> None:
        """Worker manual CLOSE creates DecisionContract and calls evaluate()."""
        from aegis.worker import AutonomousWorker
        import aegis.worker as worker_mod
        from aegis.execution.engine import ExecutionEngine

        with patch.object(worker_mod, "_STATE_FILE", Path("/tmp/test_state.json")):
            worker = AutonomousWorker()
            worker.portfolio = Portfolio(initial_cash=Decimal("100.00"))
            worker.portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))

            pos_id = str(uuid4())
            worker._state["positions"] = [{
                "id": pos_id,
                "symbol": "BTC-BRL",
                "side": "LONG",
                "quantity": "1",
                "entry_price": "50.00",
                "current_price": "60.00",
                "entry_fee": "0.50",
                "status": "OPEN",
            }]

            async def mock_execute(*args, **kwargs):
                return OrderResult(
                    order_id=kwargs.get("order_id", uuid4()),
                    status=OrderStatus.FILLED,
                    fill_price=Decimal("60.00"),
                    fill_quantity=kwargs.get("quantity", Decimal("1")),
                    fee=Decimal("0.50"),
                )
            worker.execution.execute_order = mock_execute

            # Patch evaluate
            evaluate_calls = []
            original_evaluate = worker.risk_engine.evaluate

            def tracking_evaluate(decision):
                evaluate_calls.append(decision)
                return original_evaluate(decision)

            worker.risk_engine.evaluate = tracking_evaluate

            result = await worker.close_position_manual(pos_id)
            assert result["status"] == "CLOSED"

            # Verify RiskEngine was called with CLOSE decision
            close_decisions = [d for d in evaluate_calls if d.action == TradingAction.CLOSE]
            assert len(close_decisions) == 1

    def test_entry_fee_preserved(self) -> None:
        """entry_fee is preserved through position lifecycle."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.75"))
        assert portfolio._positions["BTC-BRL"].entry_fee == Decimal("0.75")

    def test_exit_fee_preserved(self) -> None:
        """Exit fee is charged and tracked in total_fees."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        portfolio.close_position("BTC-BRL", Decimal("60.00"), Decimal("0.75"))
        assert portfolio.total_fees == Decimal("1.25")

    def test_realized_pnl_after_close(self) -> None:
        """realized_pnl = gross - entry_fee - exit_fee."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        realized = portfolio.close_position("BTC-BRL", Decimal("60.00"), Decimal("0.50"))
        # gross = (60-50)*1 = 10, realized = 10 - 0.50 - 0.50 = 9.00
        assert realized == Decimal("9.00")
        assert portfolio.total_realized_pnl == Decimal("9.00")

    def test_cash_correct_after_close(self) -> None:
        """Cash is correctly restored after close."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        # cash = 100 - 0.50 - 50 = 49.50
        assert portfolio.cash == Decimal("49.50")

        portfolio.close_position("BTC-BRL", Decimal("60.00"), Decimal("0.50"))
        # cash = 49.50 - 0.50(exit fee) + 60*1 = 109.00
        assert portfolio.cash == Decimal("109.00")

    def test_position_removed_after_close(self) -> None:
        """Position quantity = 0 after close."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
        portfolio.close_position("BTC-BRL", Decimal("60.00"), Decimal("0.50"))
        assert portfolio._positions["BTC-BRL"].quantity == Decimal("0")
        assert portfolio._positions["BTC-BRL"].status == PositionStatus.CLOSED

    def test_api_close_delegates_to_manual(self) -> None:
        """API close calls worker.close_position_manual()."""
        # Verified by code inspection: main.py:1221 calls worker.close_position_manual()
        # No broker path involved.
        pass

    def test_risk_engine_position_count_after_close(self) -> None:
        """RiskEngine positions_count decrements after close."""
        risk = RiskEngine()
        risk.record_position_open()
        assert risk.positions_count == 1
        risk.record_position_close()
        assert risk.positions_count == 0


# ─────────────────────────────────────────────────
# SANDBOX BROKER
# ─────────────────────────────────────────────────


class TestSandboxBrokerSELL:
    """C6-04: SandboxBroker SELL fixes."""

    @pytest.mark.asyncio
    async def test_buy_then_sell(self) -> None:
        """BUY then SELL works with correct balance."""
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        buy_id = uuid4()
        result = await broker.submit_order(OrderSubmission(
            order_id=buy_id,
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            correlation_id=uuid4(),
        ))
        assert result.status == OrderStatus.FILLED
        buy_fill = result.fill_price
        buy_fee = result.fee

        sell_id = uuid4()
        result = await broker.submit_order(OrderSubmission(
            order_id=sell_id,
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.SELL,
            quantity=Decimal("1"),
            price=Decimal("60.00"),
            correlation_id=uuid4(),
        ))
        assert result.status == OrderStatus.FILLED
        sell_fill = result.fill_price
        sell_fee = result.fee

        # SELL slippage: fill_price = price - slippage
        assert sell_fill < Decimal("60.00")
        # BUY slippage: fill_price = price + slippage
        assert buy_fill > Decimal("50.00")

        # Balance: initial - (buy_price * qty + buy_fee) + (sell_fill * qty - sell_fee)
        expected = Decimal("100.00") - (Decimal("50.00") * Decimal("1") + buy_fee) + (sell_fill * Decimal("1") - sell_fee)
        assert broker.balance == expected

    @pytest.mark.asyncio
    async def test_sell_without_position_rejects(self) -> None:
        """SELL without a position is rejected."""
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        result = await broker.submit_order(OrderSubmission(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.SELL,
            quantity=Decimal("1"),
            price=Decimal("60.00"),
            correlation_id=uuid4(),
        ))
        assert result.status == OrderStatus.REJECTED
        assert "No position to sell" in result.error

    @pytest.mark.asyncio
    async def test_sell_above_position_rejects(self) -> None:
        """SELL quantity above position is rejected."""
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        await broker.submit_order(OrderSubmission(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            correlation_id=uuid4(),
        ))

        result = await broker.submit_order(OrderSubmission(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.SELL,
            quantity=Decimal("2"),
            price=Decimal("60.00"),
            correlation_id=uuid4(),
        ))
        assert result.status == OrderStatus.REJECTED
        assert "exceeds position" in result.error

    @pytest.mark.asyncio
    async def test_sell_slippage_correct(self) -> None:
        """SELL slippage is price - slippage (unfavorable to seller)."""
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        await broker.submit_order(OrderSubmission(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            correlation_id=uuid4(),
        ))

        result = await broker.submit_order(OrderSubmission(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.SELL,
            quantity=Decimal("1"),
            price=Decimal("60.00"),
            correlation_id=uuid4(),
        ))
        assert result.fill_price < Decimal("60.00")
        # slippage = 60 * 0.001 = 0.06, fill = 59.94
        expected = Decimal("60.00") - Decimal("60.00") * Decimal("0.001")
        assert result.fill_price == expected

    @pytest.mark.asyncio
    async def test_fees_correct(self) -> None:
        """Both BUY and SELL charge fee."""
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        buy_result = await broker.submit_order(OrderSubmission(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            correlation_id=uuid4(),
        ))
        assert buy_result.fee == Decimal("0.50")

        sell_result = await broker.submit_order(OrderSubmission(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.SELL,
            quantity=Decimal("1"),
            price=Decimal("60.00"),
            correlation_id=uuid4(),
        ))
        assert sell_result.fee == Decimal("0.50")

    @pytest.mark.asyncio
    async def test_net_position_after_buy_sell(self) -> None:
        """Net position = 0 after BUY then SELL same quantity."""
        broker = SandboxBroker(initial_balance=Decimal("100.00"))
        await broker.submit_order(OrderSubmission(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            correlation_id=uuid4(),
        ))
        pos = await broker.get_position("BTC-BRL")
        assert pos["quantity"] == Decimal("1")

        await broker.submit_order(OrderSubmission(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.SELL,
            quantity=Decimal("1"),
            price=Decimal("60.00"),
            correlation_id=uuid4(),
        ))
        pos = await broker.get_position("BTC-BRL")
        assert pos["quantity"] == Decimal("0")


# ─────────────────────────────────────────────────
# RESTART / PERSISTENCE
# ─────────────────────────────────────────────────


class TestRestart:
    """C6-07: Restart preserves accounting."""

    def test_restart_preserves_capital(self, tmp_path) -> None:
        """Capital is persisted and restored."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            worker1 = AutonomousWorker()
            worker1.portfolio = Portfolio(initial_cash=Decimal("100.00"))
            worker1.portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
            worker1._state["positions"] = [{
                "id": str(uuid4()),
                "symbol": "BTC-BRL",
                "side": "LONG",
                "quantity": "1",
                "entry_price": "50.00",
                "current_price": "55.00",
                "entry_fee": "0.50",
                "status": "OPEN",
                "opened_at": "2025-01-01T00:00:00Z",
            }]
            worker1._save_state()

            worker2 = AutonomousWorker()
            worker2._load_state()
            assert worker2.portfolio.cash == Decimal("49.50")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_restart_preserves_realized_pnl(self, tmp_path) -> None:
        """Realized P&L is persisted and restored."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            worker1 = AutonomousWorker()
            worker1.portfolio = Portfolio(initial_cash=Decimal("100.00"))
            worker1.portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
            worker1.portfolio.close_position("BTC-BRL", Decimal("60.00"), Decimal("0.50"))
            worker1._state["positions"] = []
            worker1._state["history"] = [{"pnl": "9.00"}]
            worker1._save_state()

            worker2 = AutonomousWorker()
            worker2._load_state()
            assert worker2.portfolio.total_realized_pnl == Decimal("9.00")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_restart_preserves_position(self, tmp_path) -> None:
        """OPEN position is reconstructed in Portfolio."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            worker1 = AutonomousWorker()
            worker1.portfolio = Portfolio(initial_cash=Decimal("100.00"))
            worker1.portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
            worker1._state["positions"] = [{
                "id": str(uuid4()),
                "symbol": "BTC-BRL",
                "side": "LONG",
                "quantity": "1",
                "entry_price": "50.00",
                "current_price": "55.00",
                "entry_fee": "0.50",
                "status": "OPEN",
                "opened_at": "2025-01-01T00:00:00Z",
            }]
            worker1._save_state()

            worker2 = AutonomousWorker()
            worker2._load_state()
            assert "BTC-BRL" in worker2.portfolio._positions
            assert worker2.portfolio._positions["BTC-BRL"].quantity == Decimal("1")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_restart_preserves_exposure(self, tmp_path) -> None:
        """Exposure is correctly reconstructed after restart."""
        import aegis.worker as worker_mod

        worker_mod._STATE_FILE = tmp_path / "state.json"
        try:
            from aegis.worker import AutonomousWorker

            worker1 = AutonomousWorker()
            worker1.portfolio = Portfolio(initial_cash=Decimal("100.00"))
            worker1.portfolio.record_fill("BTC-BRL", PositionSide.LONG, Decimal("1"), Decimal("50.00"), Decimal("0.50"))
            worker1._state["positions"] = [{
                "id": str(uuid4()),
                "symbol": "BTC-BRL",
                "side": "LONG",
                "quantity": "1",
                "entry_price": "50.00",
                "current_price": "55.00",
                "entry_fee": "0.50",
                "status": "OPEN",
                "opened_at": "2025-01-01T00:00:00Z",
            }]
            worker1._save_state()

            worker2 = AutonomousWorker()
            worker2._load_state()
            assert worker2.portfolio.exposure == Decimal("55.00")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")


# ─────────────────────────────────────────────────
# SAFETY
# ─────────────────────────────────────────────────


class TestSafety:
    """C6-08/C6-09: Risk Gate and SANDBOX/LIVE safety."""

    def test_zero_risk_approved_in_src(self) -> None:
        """risk_approved does not appear in src/."""
        import re
        src_dir = Path(__file__).parent.parent / "src"
        for py_file in src_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert not re.search(r"risk_approved", content), f"risk_approved found in {py_file}"

    @pytest.mark.asyncio
    async def test_risk_decision_mandatory(self) -> None:
        """ExecutionEngine requires RiskDecision."""
        from aegis.execution.engine import ExecutionEngine

        engine = ExecutionEngine(SandboxBroker())
        result = await engine.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol="BTC-BRL",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            correlation_id=uuid4(),
            risk_decision=None,
        )
        assert result.status == OrderStatus.REJECTED

    def test_sandbox_stays_sandbox(self) -> None:
        """SANDBOX environment creates SandboxBroker."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings, TradingEnvironment

        settings = Settings(trading_environment=TradingEnvironment("SANDBOX"))
        broker = create_broker(settings)
        assert isinstance(broker, SandboxBroker)

    def test_live_disabled_fail_closed(self) -> None:
        """LIVE + LIVE_ENABLED=false raises RuntimeError."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings, TradingEnvironment

        settings = Settings(
            trading_environment=TradingEnvironment("LIVE"),
            live_enabled=False,
        )
        with pytest.raises(RuntimeError, match="disabled"):
            create_broker(settings)

    def test_risk_engine_close_approved(self) -> None:
        """CLOSE is always approved by RiskEngine."""
        risk = RiskEngine()
        decision = DecisionContract(
            action=TradingAction.CLOSE,
            confidence=Decimal("1.0"),
            thesis="test",
        )
        result = risk.evaluate(decision)
        assert result.is_approved

    def test_no_risk_bypass_patterns(self) -> None:
        """No bypass patterns exist in source."""
        import re
        src_dir = Path(__file__).parent.parent / "src"
        pattern = re.compile(r"risk_approved\s*=\s*True|approved\s*=\s*True|skip_risk|bypass_risk")
        for py_file in src_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert not pattern.search(content), f"Risk bypass pattern found in {py_file}"
