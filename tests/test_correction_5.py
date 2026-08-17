"""AEGIS V1.3 Correction #5 — Financial Consistency & Restart Recovery Tests.

Tests for findings C5-01, C5-02, C5-03.
All tests use mocks — no real credentials or live API calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from aegis.domain.enums import OrderSide, OrderStatus, PositionSide, PositionStatus, TradingAction
from aegis.execution.broker import OrderResult, OrderSubmission
from aegis.execution.engine import ExecutionEngine
from aegis.execution.sandbox import SandboxBroker
from aegis.portfolio.portfolio import Portfolio, PositionEntry
from aegis.risk_engine.risk_engine import RiskEngine, RiskDecision
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.ai_engine.decision_engine import DecisionContract
from aegis.pipeline import TradingPipeline
from aegis.worker import AutonomousWorker


# ============================================================
# C5-01: Pipeline Capital After LONG
# ============================================================

class TestPipelineCapitalAfterLong:
    """state["capital"] and state["pnl"] must come from Portfolio after LONG."""

    def _run_long(self, capital=Decimal("100.00")) -> TradingPipeline:
        """Helper: execute a single LONG through the pipeline."""
        portfolio = Portfolio(initial_cash=capital)
        broker = SandboxBroker()
        risk = RiskEngine(RiskLimits(
            reference_capital=capital,
            max_position_size_pct=Decimal("0.50"),
            max_risk_per_trade_pct=Decimal("0.10"),
        ))
        pipeline = TradingPipeline(broker=broker, portfolio=portfolio, risk_engine=risk)

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.8"),
            thesis="Test LONG",
            entry_price=Decimal("50.00"),
            stop_loss=Decimal("45.00"),
            take_profit=Decimal("60.00"),
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pipeline.run("BTC-BRL", decision))
        finally:
            loop.close()
        return pipeline

    def test_c5_01_01_initial_capital_equals_portfolio_cash(self) -> None:
        """Before any trade, state.capital == portfolio.cash."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        pipeline = TradingPipeline(
            broker=SandboxBroker(),
            portfolio=portfolio,
            risk_engine=RiskEngine(RiskLimits(reference_capital=Decimal("100.00"))),
        )
        assert Decimal(pipeline.state["capital"]) == portfolio.cash

    def test_c5_01_02_long_updates_portfolio_cash(self) -> None:
        """After LONG, portfolio.cash must be reduced by cost + fee."""
        pipeline = self._run_long()
        # Portfolio started at 100, buy 1 unit @ 50 + slippage + 0.50 fee
        assert pipeline._portfolio.cash < Decimal("100.00")

    def test_c5_01_03_state_capital_equals_portfolio_cash_after_long(self) -> None:
        """After LONG, state.capital must equal portfolio.cash."""
        pipeline = self._run_long()
        assert Decimal(pipeline.state["capital"]) == pipeline._portfolio.cash

    def test_c5_01_04_close_updates_capital_from_portfolio(self) -> None:
        """After CLOSE, state.capital must equal portfolio.cash."""
        pipeline = self._run_long()

        # Mock broker for close
        mock_broker = AsyncMock()
        mock_broker.submit_order = AsyncMock(return_value=OrderResult(
            order_id=uuid4(),
            status=OrderStatus.FILLED,
            fill_price=Decimal("60.00"),
            fill_quantity=Decimal("1"),
            fee=Decimal("0.50"),
        ))
        pipeline._broker = mock_broker
        pipeline._execution = ExecutionEngine(mock_broker)

        pos_id = pipeline.state["positions"][0]["id"]
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pipeline.close_position(pos_id))
        finally:
            loop.close()

        assert Decimal(pipeline.state["capital"]) == pipeline._portfolio.cash

    def test_c5_01_05_state_pnl_equals_portfolio_realized_pnl(self) -> None:
        """After CLOSE, state.pnl must equal portfolio.total_realized_pnl."""
        pipeline = self._run_long()

        mock_broker = AsyncMock()
        mock_broker.submit_order = AsyncMock(return_value=OrderResult(
            order_id=uuid4(),
            status=OrderStatus.FILLED,
            fill_price=Decimal("60.00"),
            fill_quantity=Decimal("1"),
            fee=Decimal("0.50"),
        ))
        pipeline._broker = mock_broker
        pipeline._execution = ExecutionEngine(mock_broker)

        pos_id = pipeline.state["positions"][0]["id"]
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pipeline.close_position(pos_id))
        finally:
            loop.close()

        assert Decimal(pipeline.state["pnl"]) == pipeline._portfolio.total_realized_pnl

    def test_c5_01_06_multiple_operations_maintain_consistency(self) -> None:
        """After multiple LONG/CLOSE cycles, capital stays consistent."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        broker = SandboxBroker()
        risk = RiskEngine(RiskLimits(
            reference_capital=Decimal("100.00"),
            max_position_size_pct=Decimal("0.50"),
            max_risk_per_trade_pct=Decimal("0.10"),
        ))
        pipeline = TradingPipeline(broker=broker, portfolio=portfolio, risk_engine=risk)

        loop = asyncio.new_event_loop()
        try:
            # LONG
            decision1 = DecisionContract(
                action=TradingAction.LONG,
                confidence=Decimal("0.8"),
                thesis="Open 1",
                entry_price=Decimal("50.00"),
                stop_loss=Decimal("45.00"),
                take_profit=Decimal("60.00"),
            )
            loop.run_until_complete(pipeline.run("BTC-BRL", decision1))

            # Verify sync after LONG
            assert Decimal(pipeline.state["capital"]) == pipeline._portfolio.cash

            # Close with mock
            mock_broker = AsyncMock()
            mock_broker.submit_order = AsyncMock(return_value=OrderResult(
                order_id=uuid4(),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=Decimal("1"),
                fee=Decimal("0.50"),
            ))
            pipeline._broker = mock_broker
            pipeline._execution = ExecutionEngine(mock_broker)

            pos_id = pipeline.state["positions"][0]["id"]
            loop.run_until_complete(pipeline.close_position(pos_id))

            # Verify sync after CLOSE
            assert Decimal(pipeline.state["capital"]) == pipeline._portfolio.cash
            assert Decimal(pipeline.state["pnl"]) == pipeline._portfolio.total_realized_pnl

            # Capital should have changed (profit minus fees)
            assert Decimal(pipeline.state["capital"]) != Decimal("100.00")
        finally:
            loop.close()

    def test_c5_01_07_fees_reflect_in_capital(self) -> None:
        """Fees must reduce capital; capital reflects fee deduction."""
        pipeline = self._run_long()
        # portfolio.cash = 100 - (50 * slippage) - 0.50 fee
        # capital must match exactly
        assert Decimal(pipeline.state["capital"]) == pipeline._portfolio.cash
        assert pipeline._portfolio.total_fees > Decimal("0")

    def test_c5_01_08_no_incremental_update_based_on_state(self) -> None:
        """Capital must NOT be computed as state + pnl. It must come from portfolio.cash."""
        pipeline = self._run_long()

        mock_broker = AsyncMock()
        mock_broker.submit_order = AsyncMock(return_value=OrderResult(
            order_id=uuid4(),
            status=OrderStatus.FILLED,
            fill_price=Decimal("60.00"),
            fill_quantity=Decimal("1"),
            fee=Decimal("0.50"),
        ))
        pipeline._broker = mock_broker
        pipeline._execution = ExecutionEngine(mock_broker)

        pos_id = pipeline.state["positions"][0]["id"]
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pipeline.close_position(pos_id))
        finally:
            loop.close()

        # Verify it's NOT doing: capital = initial + pnl (which might lose precision)
        # Instead it must equal portfolio.cash exactly
        assert Decimal(pipeline.state["capital"]) == pipeline._portfolio.cash


# ============================================================
# C5-02: Close Price Not Using entry_price
# ============================================================

class TestClosePriceNotEntryPrice:
    """Pipeline.close_position must use current_price, not entry_price."""

    def test_c5_02_09_close_does_not_use_entry_price(self) -> None:
        """CLOSE must use current_price from position, not entry_price."""
        pipeline = TradingPipeline(
            broker=SandboxBroker(),
            portfolio=Portfolio(initial_cash=Decimal("100.00")),
            risk_engine=RiskEngine(RiskLimits(
                reference_capital=Decimal("100.00"),
                max_position_size_pct=Decimal("0.50"),
                max_risk_per_trade_pct=Decimal("0.10"),
            )),
        )

        # Open a LONG
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.8"),
            thesis="Open",
            entry_price=Decimal("50.00"),
            stop_loss=Decimal("45.00"),
            take_profit=Decimal("60.00"),
        )
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pipeline.run("BTC-BRL", decision))

            # Update current_price to simulate market movement
            pipeline._state["positions"][0]["current_price"] = "65.00"

            # Mock broker to capture the SELL price
            sent_prices = []

            async def capture_submit(submission):
                sent_prices.append(submission.price)
                return OrderResult(
                    order_id=uuid4(),
                    status=OrderStatus.FILLED,
                    fill_price=submission.price,
                    fill_quantity=submission.quantity,
                    fee=Decimal("0.50"),
                )

            mock_broker = AsyncMock()
            mock_broker.submit_order = capture_submit
            pipeline._broker = mock_broker
            pipeline._execution = ExecutionEngine(mock_broker)

            pos_id = pipeline.state["positions"][0]["id"]
            loop.run_until_complete(pipeline.close_position(pos_id))

            # SELL price must be 65.00 (current_price), NOT 50.00 (entry_price)
            assert len(sent_prices) == 1
            assert sent_prices[0] == Decimal("65.00")
        finally:
            loop.close()

    def test_c5_02_10_pnl_varies_with_exit_price(self) -> None:
        """P&L must change based on exit price, not be always ~0 (fees only)."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        broker = SandboxBroker()
        risk = RiskEngine(RiskLimits(
            reference_capital=Decimal("100.00"),
            max_position_size_pct=Decimal("0.50"),
            max_risk_per_trade_pct=Decimal("0.10"),
        ))
        pipeline = TradingPipeline(broker=broker, portfolio=portfolio, risk_engine=risk)

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.8"),
            thesis="Open",
            entry_price=Decimal("50.00"),
            stop_loss=Decimal("45.00"),
            take_profit=Decimal("60.00"),
        )
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pipeline.run("BTC-BRL", decision))

            # Close at profit (60.00 > 50.00)
            mock_broker = AsyncMock()
            mock_broker.submit_order = AsyncMock(return_value=OrderResult(
                order_id=uuid4(),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=Decimal("1"),
                fee=Decimal("0.50"),
            ))
            pipeline._broker = mock_broker
            pipeline._execution = ExecutionEngine(mock_broker)

            pos_id = pipeline.state["positions"][0]["id"]
            loop.run_until_complete(pipeline.close_position(pos_id))

            # P&L must reflect profit (not just fees)
            assert pipeline._portfolio.total_realized_pnl > Decimal("0")
        finally:
            loop.close()

    def test_c5_02_11_entry_fee_preserved(self) -> None:
        """Entry fee must be preserved in accounting."""
        pipeline = TradingPipeline(
            broker=SandboxBroker(),
            portfolio=Portfolio(initial_cash=Decimal("100.00")),
            risk_engine=RiskEngine(RiskLimits(
                reference_capital=Decimal("100.00"),
                max_position_size_pct=Decimal("0.50"),
                max_risk_per_trade_pct=Decimal("0.10"),
            )),
        )
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.8"),
            thesis="Open",
            entry_price=Decimal("50.00"),
            stop_loss=Decimal("45.00"),
            take_profit=Decimal("60.00"),
        )
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pipeline.run("BTC-BRL", decision))
            assert pipeline._portfolio.total_fees > Decimal("0")
        finally:
            loop.close()

    def test_c5_02_12_exit_fee_preserved(self) -> None:
        """Exit fee must be applied during close."""
        pipeline = TradingPipeline(
            broker=SandboxBroker(),
            portfolio=Portfolio(initial_cash=Decimal("100.00")),
            risk_engine=RiskEngine(RiskLimits(
                reference_capital=Decimal("100.00"),
                max_position_size_pct=Decimal("0.50"),
                max_risk_per_trade_pct=Decimal("0.10"),
            )),
        )
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.8"),
            thesis="Open",
            entry_price=Decimal("50.00"),
            stop_loss=Decimal("45.00"),
            take_profit=Decimal("60.00"),
        )
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pipeline.run("BTC-BRL", decision))

            fees_before = pipeline._portfolio.total_fees

            mock_broker = AsyncMock()
            mock_broker.submit_order = AsyncMock(return_value=OrderResult(
                order_id=uuid4(),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=Decimal("1"),
                fee=Decimal("0.75"),
            ))
            pipeline._broker = mock_broker
            pipeline._execution = ExecutionEngine(mock_broker)

            pos_id = pipeline.state["positions"][0]["id"]
            loop.run_until_complete(pipeline.close_position(pos_id))

            # Exit fee must have been applied
            assert pipeline._portfolio.total_fees == fees_before + Decimal("0.75")
        finally:
            loop.close()

    def test_c5_02_13_cash_final_correct(self) -> None:
        """Cash after close must be: initial - buy_cost - entry_fee + sell_proceeds - exit_fee."""
        portfolio = Portfolio(initial_cash=Decimal("100.00"))
        broker = SandboxBroker()
        risk = RiskEngine(RiskLimits(
            reference_capital=Decimal("100.00"),
            max_position_size_pct=Decimal("0.50"),
            max_risk_per_trade_pct=Decimal("0.10"),
        ))
        pipeline = TradingPipeline(broker=broker, portfolio=portfolio, risk_engine=risk)

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.8"),
            thesis="Open",
            entry_price=Decimal("50.00"),
            stop_loss=Decimal("45.00"),
            take_profit=Decimal("60.00"),
        )
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pipeline.run("BTC-BRL", decision))

            # Get exact values after LONG
            cash_after_long = pipeline._portfolio.cash
            fees_after_long = pipeline._portfolio.total_fees

            # Close
            mock_broker = AsyncMock()
            mock_broker.submit_order = AsyncMock(return_value=OrderResult(
                order_id=uuid4(),
                status=OrderStatus.FILLED,
                fill_price=Decimal("60.00"),
                fill_quantity=Decimal("1"),
                fee=Decimal("0.50"),
            ))
            pipeline._broker = mock_broker
            pipeline._execution = ExecutionEngine(mock_broker)

            pos_id = pipeline.state["positions"][0]["id"]
            loop.run_until_complete(pipeline.close_position(pos_id))

            # Cash = cash_after_long + (60.00 * 1) - 0.50
            expected_cash = cash_after_long + Decimal("60.00") - Decimal("0.50")
            assert pipeline._portfolio.cash == expected_cash
        finally:
            loop.close()

    def test_c5_02_14_manual_close_consistent_with_autonomous(self) -> None:
        """Worker.close_position_manual must produce consistent accounting."""
        from aegis.worker import AutonomousWorker

        worker = AutonomousWorker()
        worker.portfolio = Portfolio(initial_cash=Decimal("100.00"))
        worker._state = {
            "capital": "100.00",
            "pnl": "0.00",
            "positions": [{
                "id": str(uuid4()),
                "symbol": "BTC-BRL",
                "side": "LONG",
                "quantity": "1",
                "entry_price": "50.00",
                "current_price": "60.00",
                "entry_fee": "0.50",
                "pnl": "10.00",
                "pnl_pct": "20.0",
                "status": "OPEN",
                "opened_at": "2025-01-01T00:00:00Z",
            }],
            "orders": [],
            "history": [],
            "decisions": [],
        }
        pos_id = worker._state["positions"][0]["id"]

        result = worker.close_position_manual(pos_id)

        assert result["status"] == "CLOSED"
        assert Decimal(result["capital"]) == worker.portfolio.cash
        assert Decimal(result["pnl"]) == worker.portfolio.total_realized_pnl

    def test_c5_02_15_no_valid_price_no_fake(self) -> None:
        """If current_price is missing, close_position falls back to entry_price, not a fake."""
        pipeline = TradingPipeline(
            broker=SandboxBroker(),
            portfolio=Portfolio(initial_cash=Decimal("100.00")),
            risk_engine=RiskEngine(RiskLimits(
                reference_capital=Decimal("100.00"),
                max_position_size_pct=Decimal("0.50"),
                max_risk_per_trade_pct=Decimal("0.10"),
            )),
        )
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.8"),
            thesis="Open",
            entry_price=Decimal("50.00"),
            stop_loss=Decimal("45.00"),
            take_profit=Decimal("60.00"),
        )
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pipeline.run("BTC-BRL", decision))

            # Remove current_price from position
            pos = pipeline._state["positions"][0]
            if "current_price" in pos:
                del pos["current_price"]

            sent_prices = []

            async def capture_submit(submission):
                sent_prices.append(submission.price)
                return OrderResult(
                    order_id=uuid4(),
                    status=OrderStatus.FILLED,
                    fill_price=submission.price,
                    fill_quantity=submission.quantity,
                    fee=Decimal("0.50"),
                )

            mock_broker = AsyncMock()
            mock_broker.submit_order = capture_submit
            pipeline._broker = mock_broker
            pipeline._execution = ExecutionEngine(mock_broker)

            pos_id = pos["id"]
            loop.run_until_complete(pipeline.close_position(pos_id))

            # Should fall back to entry_price (which includes SandboxBroker slippage),
            # not a random/fake price
            assert sent_prices[0] == Decimal("50.05")
        finally:
            loop.close()


# ============================================================
# C5-03: Portfolio Reconstruction After Restart
# ============================================================

class TestPortfolioReconstruction:
    """Portfolio must be fully reconstructed from persisted state after restart."""

    def _make_worker_with_position(self, capital=Decimal("100.00"), tmp_path=None):
        """Create a worker with a simulated open position."""
        from aegis.worker import AutonomousWorker

        if tmp_path is not None:
            # Patch _STATE_FILE to use temp directory
            import aegis.worker as worker_mod
            original = worker_mod._STATE_FILE
            worker_mod._STATE_FILE = tmp_path / "worker_state.json"
            self._original_state_file = original

        worker = AutonomousWorker()
        worker.portfolio = Portfolio(initial_cash=capital)
        worker._state = {
            "capital": str(capital),
            "pnl": "0.00",
            "positions": [{
                "id": str(uuid4()),
                "symbol": "BTC-BRL",
                "side": "LONG",
                "quantity": "1",
                "entry_price": "50.00",
                "current_price": "60.00",
                "entry_fee": "0.50",
                "pnl": "10.00",
                "pnl_pct": "20.0",
                "stop_loss": "45.00",
                "take_profit": "65.00",
                "status": "OPEN",
                "opened_at": "2025-01-01T00:00:00Z",
            }],
            "orders": [],
            "history": [],
            "decisions": [],
            "exposure": "60.00",
            "peak_equity": str(capital),
            "risk_limits": {},
        }
        return worker

    def _simulate_trading_and_save(self, worker):
        """Simulate a trade: record fill, save state."""
        from aegis.domain.enums import PositionSide
        worker.portfolio.record_fill(
            asset="BTC-BRL",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            price=Decimal("50.00"),
            fee=Decimal("0.50"),
        )
        worker._state["capital"] = str(worker.portfolio.cash)
        worker._state["pnl"] = str(worker.portfolio.total_realized_pnl)
        worker._save_state()

    def test_c5_03_16_open_position_persisted(self, tmp_path) -> None:
        """After save, state file contains OPEN positions."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)

            saved = json.loads(worker_mod._STATE_FILE.read_text(encoding="utf-8"))
            open_positions = [p for p in saved["positions"] if p["status"] == "OPEN"]
            assert len(open_positions) == 1
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_17_capital_persisted(self, tmp_path) -> None:
        """After save, state file contains capital."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)

            saved = json.loads(worker_mod._STATE_FILE.read_text(encoding="utf-8"))
            assert "capital" in saved
            assert Decimal(saved["capital"]) == worker.portfolio.cash
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_18_pnl_persisted(self, tmp_path) -> None:
        """After save, state file contains realized P&L."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)

            saved = json.loads(worker_mod._STATE_FILE.read_text(encoding="utf-8"))
            assert "pnl" in saved
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_19_fees_persisted(self, tmp_path) -> None:
        """After save, state file contains total fees."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)

            saved = json.loads(worker_mod._STATE_FILE.read_text(encoding="utf-8"))
            assert "total_fees" in saved
            assert Decimal(saved["total_fees"]) == worker.portfolio.total_fees
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_20_position_quantity_survives(self, tmp_path) -> None:
        """After restart, position quantity is preserved."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(capital=Decimal("200.00"), tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("200.00"))
            worker2._load_state()

            open_pos = [p for p in worker2._state["positions"] if p["status"] == "OPEN"]
            assert len(open_pos) == 1
            assert Decimal(open_pos[0]["quantity"]) == Decimal("1")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_21_position_entry_price_survives(self, tmp_path) -> None:
        """After restart, position entry_price is preserved."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(capital=Decimal("200.00"), tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("200.00"))
            worker2._load_state()

            open_pos = [p for p in worker2._state["positions"] if p["status"] == "OPEN"]
            assert Decimal(open_pos[0]["entry_price"]) == Decimal("50.00")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_22_entry_fee_survives(self, tmp_path) -> None:
        """After restart, entry_fee is preserved."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(capital=Decimal("200.00"), tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("200.00"))
            worker2._load_state()

            open_pos = [p for p in worker2._state["positions"] if p["status"] == "OPEN"]
            assert Decimal(open_pos[0].get("entry_fee", "0")) == Decimal("0.50")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_23_cash_consistent_after_restart(self, tmp_path) -> None:
        """After restart, portfolio.cash matches persisted capital."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(capital=Decimal("200.00"), tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)
            saved_cash = worker.portfolio.cash

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("200.00"))
            worker2._load_state()

            assert worker2.portfolio.cash == saved_cash
            assert Decimal(worker2._state["capital"]) == saved_cash
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_24_realized_pnl_consistent_after_restart(self, tmp_path) -> None:
        """After restart, portfolio.total_realized_pnl matches persisted value."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(capital=Decimal("200.00"), tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)
            saved_pnl = worker.portfolio.total_realized_pnl

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("200.00"))
            worker2._load_state()

            assert worker2.portfolio.total_realized_pnl == saved_pnl
            assert Decimal(worker2._state["pnl"]) == saved_pnl
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_25_risk_engine_reconstructs_open_position(self, tmp_path) -> None:
        """After restart, RiskEngine knows about open positions."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(capital=Decimal("200.00"), tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("200.00"))
            worker2._load_state()

            assert worker2.risk_engine.positions_count == 1
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_26_exposure_reconstructed(self, tmp_path) -> None:
        """After restart, risk engine exposure matches persisted positions."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(capital=Decimal("200.00"), tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("200.00"))
            worker2._load_state()

            # Exposure = qty * current_price = 1 * 60.00 = 60.00
            assert worker2.risk_engine._current_exposure == Decimal("60.00")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_27_portfolio_positions_reconstructed(self, tmp_path) -> None:
        """After restart, Portfolio._positions contains OPEN positions."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(capital=Decimal("200.00"), tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("200.00"))
            worker2._load_state()

            assert "BTC-BRL" in worker2.portfolio._positions
            pos = worker2.portfolio._positions["BTC-BRL"]
            assert pos.quantity == Decimal("1")
            assert pos.average_entry == Decimal("50.00")
            assert pos.status == PositionStatus.OPEN
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_28_close_works_after_restart(self, tmp_path) -> None:
        """After restart, closing a position updates portfolio correctly."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(capital=Decimal("200.00"), tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)
            saved_cash = worker.portfolio.cash

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("200.00"))
            worker2._load_state()

            pos_id = worker2._state["positions"][0]["id"]
            result = worker2.close_position_manual(pos_id)

            assert result["status"] == "CLOSED"
            assert Decimal(result["capital"]) == worker2.portfolio.cash
            assert worker2.portfolio.cash > saved_cash  # Profit
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_29_no_double_fee_after_restart(self, tmp_path) -> None:
        """Closing after restart must not charge entry fee again."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(capital=Decimal("200.00"), tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("200.00"))
            worker2._load_state()

            entry_fee = Decimal(worker2._state["positions"][0].get("entry_fee", "0"))
            assert entry_fee == Decimal("0.50")

            pos_id = worker2._state["positions"][0]["id"]
            worker2.close_position_manual(pos_id)

            # Total fees = entry_fee (0.50) + exit_fee (0.50) = 1.00
            # NOT entry_fee charged twice
            assert worker2.portfolio.total_fees == entry_fee + Decimal("0.50")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_30_multiple_trades_survive_restart(self, tmp_path) -> None:
        """Multiple trades in history survive restart."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = self._make_worker_with_position(capital=Decimal("200.00"), tmp_path=tmp_path)
            self._simulate_trading_and_save(worker)
            worker._state["history"] = [
                {"symbol": "BTC-BRL", "side": "LONG", "pnl": "5.00"},
                {"symbol": "ETH-BRL", "side": "LONG", "pnl": "-2.00"},
            ]
            worker._save_state()

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("200.00"))
            worker2._load_state()

            assert len(worker2._state["history"]) == 2
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_31_restart_without_positions_maintains_capital(self, tmp_path) -> None:
        """Restart with no persisted state keeps initial capital."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = AutonomousWorker()
            worker.portfolio = Portfolio(initial_cash=Decimal("150.00"))
            worker._save_state()

            worker2 = AutonomousWorker()
            worker2.portfolio = Portfolio(initial_cash=Decimal("150.00"))
            worker2._load_state()

            assert worker2.portfolio.cash == Decimal("150.00")
            assert Decimal(worker2._state["capital"]) == Decimal("150.00")
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")

    def test_c5_03_32_backward_compatibility_no_capital_field(self, tmp_path) -> None:
        """Old state files without capital field still load correctly."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            old_state = {
                "positions": [{
                    "id": str(uuid4()),
                    "symbol": "BTC-BRL",
                    "side": "LONG",
                    "quantity": "1",
                    "entry_price": "50.00",
                    "current_price": "55.00",
                    "status": "OPEN",
                    "opened_at": "2025-01-01T00:00:00Z",
                }],
                "orders": [],
                "history": [],
                "decisions": [],
            }
            worker_mod._STATE_FILE.write_text(json.dumps(old_state), encoding="utf-8")

            worker = AutonomousWorker()
            worker.portfolio = Portfolio(initial_cash=Decimal("100.00"))
            worker._load_state()

            assert worker.portfolio.cash == Decimal("100.00")
            assert worker.risk_engine.positions_count == 1
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")


# ============================================================
# Safety: SANDBOX/LIVE, Risk Gate, No Bypass
# ============================================================

class TestCorrection5Safety:
    """Regression safety checks for Correction #5 changes."""

    def test_sandbox_stays_sandbox(self) -> None:
        """SANDBOX environment must create SandboxBroker."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings
        settings = Settings(trading_environment="SANDBOX")
        broker = create_broker(settings)
        assert isinstance(broker, SandboxBroker)

    def test_live_disabled_fail_closed(self) -> None:
        """LIVE + LIVE_ENABLED=false must raise RuntimeError."""
        from aegis.execution.factory import create_broker
        from aegis.config import Settings
        settings = Settings(trading_environment="LIVE", live_enabled=False)
        with pytest.raises(RuntimeError, match="LIVE trading is disabled"):
            create_broker(settings)

    def test_no_risk_approved_in_engine(self) -> None:
        """ExecutionEngine must not accept risk_approved boolean."""
        import inspect
        from aegis.execution.engine import ExecutionEngine
        sig = inspect.signature(ExecutionEngine.execute_order)
        assert "risk_approved" not in sig.parameters

    def test_no_risk_approved_in_orchestrator(self) -> None:
        """ExecutionOrchestrator must not accept risk_approved boolean."""
        import inspect
        from aegis.execution.orchestrator import ExecutionOrchestrator
        sig = inspect.signature(ExecutionOrchestrator.submit_order)
        assert "risk_approved" not in sig.parameters

    def test_risk_gate_requires_risk_decision(self) -> None:
        """ExecutionEngine must reject when risk_decision is None."""
        broker = SandboxBroker()
        engine = ExecutionEngine(broker)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                engine.execute_order(
                    order_id=uuid4(),
                    idempotency_key=uuid4(),
                    symbol="BTC-BRL",
                    side=OrderSide.BUY,
                    quantity=Decimal("0.01"),
                    price=Decimal("50000"),
                    correlation_id=uuid4(),
                )
            )
        finally:
            loop.close()
        assert result.status == OrderStatus.REJECTED

    def test_close_through_worker_routes_portfolio(self, tmp_path) -> None:
        """Manual close must route through Portfolio.close_position."""
        import aegis.worker as worker_mod
        worker_mod._STATE_FILE = tmp_path / "worker_state.json"
        try:
            worker = AutonomousWorker()
            worker.portfolio = Portfolio(initial_cash=Decimal("100.00"))

            # Register the position in Portfolio via record_fill
            worker.portfolio.record_fill(
                asset="BTC-BRL",
                side=PositionSide.LONG,
                quantity=Decimal("1"),
                price=Decimal("50.00"),
                fee=Decimal("0.50"),
            )

            worker._state = {
                "capital": str(worker.portfolio.cash),
                "pnl": "0.00",
                "positions": [{
                    "id": str(uuid4()),
                    "symbol": "BTC-BRL",
                    "side": "LONG",
                    "quantity": "1",
                    "entry_price": "50.00",
                    "current_price": "60.00",
                    "entry_fee": "0.50",
                    "status": "OPEN",
                    "opened_at": "2025-01-01T00:00:00Z",
                }],
                "orders": [],
                "history": [],
                "decisions": [],
            }
            pos_id = worker._state["positions"][0]["id"]

            cash_before = worker.portfolio.cash

            result = worker.close_position_manual(pos_id)

            # Portfolio must have been called (cash changed)
            assert worker.portfolio.cash != cash_before
            assert result["status"] == "CLOSED"
        finally:
            worker_mod._STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")
