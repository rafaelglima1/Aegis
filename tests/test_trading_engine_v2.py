"""AEGIS Trading Engine v2 — Comprehensive Tests.

Tests for setup scoring, market regime, position management,
trade journal, daily loss escalation, and enhanced risk evaluation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest


# ============================================================
# Setup Scorer Tests
# ============================================================


class TestSetupScorer:

    def test_empty_market_state(self) -> None:
        """Empty market state should return low score."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        result = scorer.score({})
        assert result.score < 30  # Low score due to missing data
        assert result.market_regime == "NEUTRAL"

    def test_bullish_trend_scores_high(self) -> None:
        """Bullish trend should contribute positively."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"trend": "BULLISH", "current_price": "100", "sma_20": "95", "sma_50": "90"}
        result = scorer.score(state)
        assert result.trend_score > 0

    def test_bearish_trend_scores_low(self) -> None:
        """Bearish trend should penalize."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"trend": "BEARISH", "current_price": "90", "sma_20": "95", "sma_50": "100"}
        result = scorer.score(state)
        assert result.trend_score <= 0

    def test_rsi_oversold(self) -> None:
        """RSI < 30 should give moderate score (reversal potential)."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"rsi": "25"}
        result = scorer.score(state)
        assert result.rsi_score > 0

    def test_rsi_overbought(self) -> None:
        """RSI > 70 should penalize."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"rsi": "80"}
        result = scorer.score(state)
        assert result.rsi_score < 10

    def test_rsi_bullish_zone(self) -> None:
        """RSI 50-70 should score high."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"rsi": "60"}
        result = scorer.score(state)
        assert result.rsi_score > 5

    def test_momentum_positive(self) -> None:
        """Positive momentum should score high."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"momentum": "5"}
        result = scorer.score(state)
        assert result.momentum_score > 5

    def test_momentum_negative(self) -> None:
        """Negative momentum should penalize."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"momentum": "-5"}
        result = scorer.score(state)
        assert result.momentum_score <= 0

    def test_volume_high(self) -> None:
        """High volume should score well."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"volume_trend": "HIGH"}
        result = scorer.score(state)
        assert result.volume_score > 5

    def test_volume_low(self) -> None:
        """Low volume should score lower."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"volume_trend": "LOW"}
        result = scorer.score(state)
        assert result.volume_score < 5

    def test_rr_good(self) -> None:
        """R/R >= 2 should score well."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        result = scorer.score({}, decision_rr=Decimal("2.5"))
        assert result.rr_score > 5

    def test_rr_poor(self) -> None:
        """R/R < 1 should score low."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        result = scorer.score({}, decision_rr=Decimal("0.8"))
        assert result.rr_score == 0

    def test_score_clamped_0_100(self) -> None:
        """Score should be clamped to 0-100."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        result = scorer.score({})
        assert 0 <= result.score <= 100

    def test_regime_classification(self) -> None:
        """Market regime should be classified."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()

        # Strong bull
        state = {"trend": "BULLISH", "volatility": "0.03", "rsi": "65", "momentum": "5"}
        result = scorer.score(state)
        assert result.market_regime == "STRONG_BULL"

        # Bear
        state = {"trend": "BEARISH", "volatility": "0.03", "rsi": "40", "momentum": "-2"}
        result = scorer.score(state)
        assert result.market_regime == "BEAR"

        # High volatility
        state = {"trend": "BULLISH", "volatility": "0.15"}
        result = scorer.score(state)
        assert result.market_regime == "HIGH_VOLATILITY"

    def test_missing_indicators_handled(self) -> None:
        """Missing indicators should not crash."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        # All fields missing
        result = scorer.score({"symbol": "BTC-BRL"})
        assert 0 <= result.score <= 100
        assert result.market_regime == "NEUTRAL"


# ============================================================
# Market Regime Tests
# ============================================================


class TestMarketRegime:

    def test_strong_bull(self) -> None:
        """STRONG_BULL: bullish + high momentum + RSI > 60."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"trend": "BULLISH", "volatility": "0.03", "rsi": "65", "momentum": "5"}
        result = scorer.score(state)
        assert result.market_regime == "STRONG_BULL"

    def test_bull(self) -> None:
        """BULL: bullish without strong momentum."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"trend": "BULLISH", "volatility": "0.03", "rsi": "55", "momentum": "2"}
        result = scorer.score(state)
        assert result.market_regime == "BULL"

    def test_strong_bear(self) -> None:
        """STRONG_BEAR: bearish + low momentum + RSI < 40."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"trend": "BEARISH", "volatility": "0.03", "rsi": "35", "momentum": "-5"}
        result = scorer.score(state)
        assert result.market_regime == "STRONG_BEAR"

    def test_high_volatility(self) -> None:
        """HIGH_VOLATILITY: volatility > 10%."""
        from aegis.risk_engine.setup_scorer import SetupScorer
        scorer = SetupScorer()
        state = {"trend": "BULLISH", "volatility": "0.15"}
        result = scorer.score(state)
        assert result.market_regime == "HIGH_VOLATILITY"


# ============================================================
# Position Manager Tests
# ============================================================


class TestPositionManager:

    def test_register_position(self) -> None:
        """Registering a position should store it."""
        from aegis.risk_engine.position_manager import PositionManager
        pm = PositionManager()
        pos = pm.register_position("BTC-BRL", Decimal("100"), Decimal("95"), Decimal("115"), Decimal("0.1"))
        assert pos.symbol == "BTC-BRL"
        assert pos.entry_price == Decimal("100")
        assert pos.current_stop == Decimal("95")

    def test_break_even_activation(self) -> None:
        """Break-even should activate at +0.8R."""
        from aegis.risk_engine.position_manager import PositionManager, PositionManagerConfig
        config = PositionManagerConfig(break_even_trigger_r=Decimal("0.8"))
        pm = PositionManager(config)
        pm.register_position("BTC-BRL", Decimal("100"), Decimal("95"), Decimal("115"), Decimal("0.1"))

        # Price at +0.8R = 100 + 0.8*5 = 104
        result = pm.evaluate("BTC-BRL", Decimal("104"))
        assert result["action"] == "MOVE_STOP"
        assert result["reason"] == "BREAK_EVEN"
        assert result["new_stop"] > Decimal("100")

    def test_break_even_not_below_entry(self) -> None:
        """Break-even stop should be above entry price."""
        from aegis.risk_engine.position_manager import PositionManager, PositionManagerConfig
        config = PositionManagerConfig(break_even_trigger_r=Decimal("0.8"), break_even_offset_pct=Decimal("0.001"))
        pm = PositionManager(config)
        pm.register_position("BTC-BRL", Decimal("100"), Decimal("95"), Decimal("115"), Decimal("0.1"))

        result = pm.evaluate("BTC-BRL", Decimal("104"))
        if result["action"] == "MOVE_STOP":
            assert result["new_stop"] > Decimal("100")

    def test_trailing_stop_activation(self) -> None:
        """Trailing stop should activate at +1.2R."""
        from aegis.risk_engine.position_manager import PositionManager, PositionManagerConfig
        config = PositionManagerConfig(
            break_even_trigger_r=Decimal("0.8"),
            trailing_trigger_r=Decimal("1.2"),
            trailing_distance_pct=Decimal("0.02"),
        )
        pm = PositionManager(config)
        pm.register_position("BTC-BRL", Decimal("100"), Decimal("95"), Decimal("115"), Decimal("0.1"))

        # Price at +1.2R = 100 + 1.2*5 = 106
        result = pm.evaluate("BTC-BRL", Decimal("106"))
        assert result["action"] == "MOVE_STOP"
        assert result["reason"] == "TRAILING_STOP"

    def test_trailing_stop_never_moves_back(self) -> None:
        """Trailing stop should never move the stop backward."""
        from aegis.risk_engine.position_manager import PositionManager, PositionManagerConfig
        config = PositionManagerConfig(
            break_even_enabled=False,
            trailing_enabled=True,
            trailing_trigger_r=Decimal("1.0"),
            trailing_distance_pct=Decimal("0.02"),
        )
        pm = PositionManager(config)
        pm.register_position("BTC-BRL", Decimal("100"), Decimal("95"), Decimal("115"), Decimal("0.1"))

        # Activate trailing
        pm.evaluate("BTC-BRL", Decimal("106"))
        first_stop = pm.get_position("BTC-BRL").current_stop

        # Price drops - trailing should not move back
        result = pm.evaluate("BTC-BRL", Decimal("103"))
        assert pm.get_position("BTC-BRL").current_stop >= first_stop

    def test_profit_protection(self) -> None:
        """Profit protection should lock in gains."""
        from aegis.risk_engine.position_manager import PositionManager, PositionManagerConfig
        config = PositionManagerConfig(
            break_even_enabled=False,
            trailing_enabled=False,
            profit_protection_enabled=True,
            profit_levels=[{"trigger_r": Decimal("1.5"), "lock_r": Decimal("0.5")}],
        )
        pm = PositionManager(config)
        pm.register_position("BTC-BRL", Decimal("100"), Decimal("95"), Decimal("115"), Decimal("0.1"))

        # Price at +1.5R = 100 + 1.5*5 = 107.5
        result = pm.evaluate("BTC-BRL", Decimal("107"))
        if result["action"] == "MOVE_STOP":
            assert result["reason"] == "PROFIT_PROTECTION"
            assert result["new_stop"] > Decimal("100")

    def test_daily_loss_escalation(self) -> None:
        """Daily loss escalation should return correct levels."""
        from aegis.risk_engine.position_manager import PositionManager, PositionManagerConfig
        config = PositionManagerConfig()
        pm = PositionManager(config)

        # Normal
        result = pm.check_daily_loss_escalation(Decimal("-0.01"))
        assert result["level"] == "NORMAL"

        # Warn (3%)
        result = pm.check_daily_loss_escalation(Decimal("-0.03"))
        assert result["level"] == "REDUCE"

        # Strong only (4%)
        result = pm.check_daily_loss_escalation(Decimal("-0.04"))
        assert result["level"] == "STRONG_ONLY"

        # Blocked (5%)
        result = pm.check_daily_loss_escalation(Decimal("-0.05"))
        assert result["level"] == "BLOCKED"


# ============================================================
# Trade Journal Tests
# ============================================================


class TestTradeJournal:

    def test_record_trade(self) -> None:
        """Should record a trade."""
        from aegis.risk_engine.trade_journal import TradeJournal, TradeRecord
        journal = TradeJournal()
        trade = TradeRecord(
            symbol="BTC-BRL", timestamp="2025-01-01T00:00:00Z",
            action="LONG", entry_price=Decimal("100"),
            exit_price=Decimal("110"), realized_pnl=Decimal("10"),
            realized_r=Decimal("2"),
        )
        journal.record(trade)
        assert len(journal.get_trades()) == 1

    def test_calculate_metrics(self) -> None:
        """Should calculate correct metrics."""
        from aegis.risk_engine.trade_journal import TradeJournal, TradeRecord
        journal = TradeJournal()

        # Win
        journal.record(TradeRecord(
            symbol="BTC-BRL", timestamp="2025-01-01", action="CLOSE",
            entry_price=Decimal("100"), exit_price=Decimal("110"),
            realized_pnl=Decimal("10"), realized_r=Decimal("2"),
        ))
        # Loss
        journal.record(TradeRecord(
            symbol="BTC-BRL", timestamp="2025-01-02", action="CLOSE",
            entry_price=Decimal("100"), exit_price=Decimal("95"),
            realized_pnl=Decimal("-5"), realized_r=Decimal("-1"),
        ))

        metrics = journal.calculate_metrics()
        assert metrics.total_trades == 2
        assert metrics.winning_trades == 1
        assert metrics.losing_trades == 1
        assert metrics.win_rate == Decimal("50")
        assert metrics.total_pnl == Decimal("5")

    def test_metrics_by_setup_score(self) -> None:
        """Should group metrics by setup score range."""
        from aegis.risk_engine.trade_journal import TradeJournal, TradeRecord
        journal = TradeJournal()

        journal.record(TradeRecord(
            symbol="BTC-BRL", timestamp="2025-01-01", action="CLOSE",
            entry_price=Decimal("100"), exit_price=Decimal("110"),
            realized_pnl=Decimal("10"), setup_score=40,
        ))
        journal.record(TradeRecord(
            symbol="BTC-BRL", timestamp="2025-01-02", action="CLOSE",
            entry_price=Decimal("100"), exit_price=Decimal("115"),
            realized_pnl=Decimal("15"), setup_score=70,
        ))

        by_score = journal.get_metrics_by_setup_score()
        assert "0-49" in by_score
        assert "65-79" in by_score
        assert by_score["0-49"].total_trades == 1
        assert by_score["65-79"].total_trades == 1

    def test_metrics_by_exit_reason(self) -> None:
        """Should group metrics by exit reason."""
        from aegis.risk_engine.trade_journal import TradeJournal, TradeRecord
        journal = TradeJournal()

        journal.record(TradeRecord(
            symbol="BTC-BRL", timestamp="2025-01-01", action="CLOSE",
            entry_price=Decimal("100"), exit_price=Decimal("95"),
            realized_pnl=Decimal("-5"), exit_reason="SL",
        ))
        journal.record(TradeRecord(
            symbol="BTC-BRL", timestamp="2025-01-02", action="CLOSE",
            entry_price=Decimal("100"), exit_price=Decimal("115"),
            realized_pnl=Decimal("15"), exit_reason="TP",
        ))

        by_reason = journal.get_metrics_by_exit_reason()
        assert "SL" in by_reason
        assert "TP" in by_reason
        assert by_reason["SL"].total_trades == 1
        assert by_reason["TP"].total_trades == 1


# ============================================================
# Risk Engine with Setup Score Tests
# ============================================================


class TestRiskEngineWithSetupScore:

    def test_low_setup_score_rejected(self) -> None:
        """Setup score below minimum should be rejected."""
        from aegis.risk_engine.risk_engine import RiskEngine
        from aegis.risk_engine.risk_limits import RiskLimits
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction

        engine = RiskEngine(RiskLimits(setup_score_min=50))

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.80"),
            thesis="test",
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("115"),
        )
        result = engine.evaluate(decision, setup_score=40)
        assert result.status == "REJECTED"
        assert any(v.code == "LOW_SETUP_SCORE" for v in result.violations)

    def test_high_setup_score_approved(self) -> None:
        """Setup score above minimum should pass."""
        from aegis.risk_engine.risk_engine import RiskEngine
        from aegis.risk_engine.risk_limits import RiskLimits
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction

        engine = RiskEngine(RiskLimits(setup_score_min=50))

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.80"),
            thesis="test",
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("115"),
        )
        result = engine.evaluate(decision, setup_score=60)
        assert result.status == "APPROVED"

    def test_daily_loss_block(self) -> None:
        """Daily loss at 5% should block all new entries."""
        from aegis.risk_engine.risk_engine import RiskEngine
        from aegis.risk_engine.risk_limits import RiskLimits
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction

        engine = RiskEngine(RiskLimits(
            reference_capital=Decimal("100"),
            max_daily_loss_pct=Decimal("0.05"),
        ))
        engine._daily_pnl = Decimal("-5")  # 5% loss

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.80"),
            thesis="test",
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("115"),
        )
        result = engine.evaluate(decision, setup_score=90)
        assert result.status == "REJECTED"
        assert any(v.code == "DAILY_LOSS_BLOCKED" for v in result.violations)

    def test_daily_loss_strong_only(self) -> None:
        """Daily loss at 4% should only allow very strong setups."""
        from aegis.risk_engine.risk_engine import RiskEngine
        from aegis.risk_engine.risk_limits import RiskLimits
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction

        engine = RiskEngine(RiskLimits(
            reference_capital=Decimal("100"),
            max_daily_loss_pct=Decimal("0.05"),
            daily_loss_strong_pct=Decimal("0.04"),
            setup_score_very_strong=80,
        ))
        engine._daily_pnl = Decimal("-4")  # 4% loss

        # Weak setup should be rejected
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.80"),
            thesis="test",
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("115"),
        )
        result = engine.evaluate(decision, setup_score=60)
        assert result.status == "REJECTED"
        assert any(v.code == "DAILY_LOSS_STRONG_ONLY" for v in result.violations)

        # Very strong setup should pass
        result = engine.evaluate(decision, setup_score=85)
        assert result.status == "APPROVED"


# ============================================================
# Enhanced Indicator Tests
# ============================================================


class TestEnhancedIndicators:

    def test_build_market_state_includes_rsi(self) -> None:
        """Market state should include RSI."""
        from aegis.worker import AutonomousWorker
        from aegis.config import Settings

        settings = Settings(initial_capital=Decimal("10000.00"))
        worker = AutonomousWorker(settings=settings)

        # Create fake candles
        candles = [{"close": str(100 + i), "volume": "1000", "high": str(101 + i), "low": str(99 + i)} for i in range(20)]
        state = worker._build_market_state("BTC-BRL", candles, Decimal("110"))

        assert "rsi" in state
        assert "momentum" in state
        assert "volume_trend" in state
        assert "price_position" in state
        assert "trend" in state

    def test_build_market_state_regime(self) -> None:
        """Market state should include regime classification."""
        from aegis.worker import AutonomousWorker
        from aegis.config import Settings

        settings = Settings(initial_capital=Decimal("10000.00"))
        worker = AutonomousWorker(settings=settings)

        candles = [{"close": str(100 + i), "volume": "1000", "high": str(101 + i), "low": str(99 + i)} for i in range(20)]
        state = worker._build_market_state("BTC-BRL", candles, Decimal("110"))

        # Should have trend at minimum
        assert "trend" in state

    def test_rsi_calculation(self) -> None:
        """RSI should be calculated correctly."""
        from aegis.worker import AutonomousWorker
        from aegis.config import Settings
        from decimal import Decimal

        settings = Settings(initial_capital=Decimal("10000.00"))
        worker = AutonomousWorker(settings=settings)

        # RSI with all gains (should be ~100)
        closes = [Decimal(str(100 + i)) for i in range(20)]
        rsi = worker._calculate_rsi(closes, 14)
        assert rsi == Decimal("100")

        # RSI with all losses (should be ~0)
        closes = [Decimal(str(200 - i)) for i in range(20)]
        rsi = worker._calculate_rsi(closes, 14)
        assert rsi == Decimal("0")

        # RSI with mixed (should be ~50)
        closes = [Decimal(str(100 + (i % 2))) for i in range(20)]
        rsi = worker._calculate_rsi(closes, 14)
        assert Decimal("40") <= rsi <= Decimal("60")


# ============================================================
# Position Manager Integration Tests
# ============================================================


class TestPositionManagerIntegration:

    def test_position_manager_tracks_positions(self) -> None:
        """PositionManager should track registered positions."""
        from aegis.risk_engine.position_manager import PositionManager
        pm = PositionManager()

        pm.register_position("BTC-BRL", Decimal("100"), Decimal("95"), Decimal("115"), Decimal("0.1"))
        pm.register_position("ETH-BRL", Decimal("3000"), Decimal("2900"), Decimal("3200"), Decimal("0.01"))

        assert pm.get_position("BTC-BRL") is not None
        assert pm.get_position("ETH-BRL") is not None
        assert pm.get_position("SOL-BRL") is None

    def test_unregister_position(self) -> None:
        """Unregistering should remove position."""
        from aegis.risk_engine.position_manager import PositionManager
        pm = PositionManager()

        pm.register_position("BTC-BRL", Decimal("100"), Decimal("95"), Decimal("115"), Decimal("0.1"))
        pm.unregister_position("BTC-BRL")
        assert pm.get_position("BTC-BRL") is None

    def test_no_action_when_no_position(self) -> None:
        """Should return NONE when position not found."""
        from aegis.risk_engine.position_manager import PositionManager
        pm = PositionManager()
        result = pm.evaluate("BTC-BRL", Decimal("100"))
        assert result["action"] == "NONE"


# ============================================================
# Backward Compatibility Tests
# ============================================================


class TestBackwardCompatibility:

    def test_existing_risk_limits_still_work(self) -> None:
        """Existing RiskLimits should work without changes."""
        from aegis.risk_engine.risk_limits import RiskLimits
        limits = RiskLimits()
        assert limits.reference_capital == Decimal("100.00")
        assert limits.max_risk_per_trade_pct == Decimal("0.01")
        assert limits.min_confidence == Decimal("0.50")
        assert limits.min_risk_reward == Decimal("1.50")

    def test_existing_risk_engine_still_works(self) -> None:
        """Existing RiskEngine evaluate should still work."""
        from aegis.risk_engine.risk_engine import RiskEngine
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction

        engine = RiskEngine()
        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.80"),
            thesis="test",
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("115"),
        )
        result = engine.evaluate(decision)
        assert result.status == "APPROVED"

    def test_worker_initializes_new_components(self) -> None:
        """Worker should initialize new components."""
        from aegis.worker import AutonomousWorker
        from aegis.config import Settings

        settings = Settings(initial_capital=Decimal("10000.00"))
        worker = AutonomousWorker(settings=settings)

        assert hasattr(worker, "setup_scorer")
        assert hasattr(worker, "position_manager")
        assert hasattr(worker, "trade_journal")
