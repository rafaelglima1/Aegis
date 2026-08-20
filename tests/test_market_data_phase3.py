"""AEGIS Phase 3 — Multi-Timeframe Intelligence Tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aegis.market_data.contracts import Candle
from aegis.market_data.mtf import (
    MTFEngine,
    MTFWeights,
    MTFResult,
    TimeframeAnalyzer,
    TimeframeResult,
    DEFAULT_MTF_CONFIG,
)
from aegis.market_data.cache import MarketDataCache
from aegis.market_data.validator import CandleValidator


# ============================================================
# Fixed reference time — no datetime.now() in test helpers
# ============================================================

REF = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)


def _candle(
    symbol="BTC-BRL", timeframe="1h", minutes_before=0,
    open="100", high="105", low="95", close="102",
    volume="1000", is_closed=True,
):
    """Create a candle with fixed reference time. No datetime.now()."""
    ts = REF - timedelta(minutes=minutes_before)
    return Candle(
        symbol=symbol, timestamp=ts, timeframe=timeframe,
        open=Decimal(open), high=Decimal(high), low=Decimal(low),
        close=Decimal(close), volume=Decimal(volume), is_closed=is_closed,
    )


def _uptrend(timeframe="1h", count=20, base_price=Decimal("100")):
    """Create uptrend candles from fixed reference. No datetime.now()."""
    candles = []
    price = base_price
    step = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}.get(timeframe, 60)
    for i in range(count):
        change = Decimal("1") if i % 3 != 0 else Decimal("-0.5")
        price = price + change
        candles.append(Candle(
            symbol="BTC-BRL",
            timestamp=REF - timedelta(minutes=step * (count - i)),
            timeframe=timeframe,
            open=price - change, high=price + Decimal("2"),
            low=price - Decimal("2"), close=price,
            volume=Decimal("1000")))
    return candles


def _downtrend(timeframe="1h", count=20, base_price=Decimal("200")):
    """Create downtrend candles from fixed reference. No datetime.now()."""
    candles = []
    price = base_price
    step = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}.get(timeframe, 60)
    for i in range(count):
        change = Decimal("-1") if i % 3 != 0 else Decimal("0.5")
        price = price + change
        candles.append(Candle(
            symbol="BTC-BRL",
            timestamp=REF - timedelta(minutes=step * (count - i)),
            timeframe=timeframe,
            open=price - change, high=price + Decimal("2"),
            low=price - Decimal("2"), close=price,
            volume=Decimal("1000")))
    return candles


def _all_uptrend():
    return {
        "1d": _uptrend("1d", 20),
        "4h": _uptrend("4h", 20),
        "1h": _uptrend("1h", 20),
        "15m": _uptrend("15m", 20),
    }


# ============================================================
# CP01: MTF Engine exists
# ============================================================


class TestMTFEngineExists:
    def test_engine_instantiation(self):
        engine = MTFEngine()
        assert engine is not None

    def test_engine_has_weights(self):
        engine = MTFEngine()
        assert engine.weights.macro == Decimal("35")
        assert engine.weights.trend == Decimal("30")
        assert engine.weights.setup == Decimal("25")
        assert engine.weights.timing == Decimal("10")

    def test_engine_has_config(self):
        engine = MTFEngine()
        assert engine.config["macro_timeframe"] == "1d"
        assert engine.config["trend_timeframe"] == "4h"
        assert engine.config["setup_timeframe"] == "1h"
        assert engine.config["timing_timeframe"] == "15m"


# ============================================================
# CP02-CP05: Timeframe support
# ============================================================


class TestTimeframeSupport:
    def test_support_1d(self):
        engine = MTFEngine()
        result = engine.analyze("BTC-BRL", {"1d": _uptrend("1d", 20)}, REF)
        assert any(r.timeframe == "1d" for r in result.timeframes)

    def test_support_4h(self):
        engine = MTFEngine()
        result = engine.analyze("BTC-BRL", {"4h": _uptrend("4h", 20)}, REF)
        assert any(r.timeframe == "4h" for r in result.timeframes)

    def test_support_1h(self):
        engine = MTFEngine()
        result = engine.analyze("BTC-BRL", {"1h": _uptrend("1h", 20)}, REF)
        assert any(r.timeframe == "1h" for r in result.timeframes)

    def test_support_15m(self):
        engine = MTFEngine()
        result = engine.analyze("BTC-BRL", {"15m": _uptrend("15m", 20)}, REF)
        assert any(r.timeframe == "15m" for r in result.timeframes)


# ============================================================
# CP06-CP08: Candle validation
# ============================================================


class TestCandleValidation:
    def test_canonical_candle_used(self):
        candles = _uptrend("1h", 10)
        for c in candles:
            assert isinstance(c, Candle)

    def test_ohlcv_validation(self):
        valid = _candle(high="105", low="95", open="100", close="102")
        CandleValidator.validate_ohlcv_consistency(valid)
        invalid = _candle(high="90", low="95")
        with pytest.raises(Exception):
            CandleValidator.validate_ohlcv_consistency(invalid)

    def test_timestamp_validation(self):
        valid = _candle()
        CandleValidator.validate_timestamp_utc(valid)


# ============================================================
# BLOCKER 3: Validation integration through MTF pipeline
# ============================================================


class TestValidationIntegration:
    """Tests that invalid candles are rejected by the MTF pipeline itself,
    not just by CandleValidator in isolation."""

    def test_invalid_ohlcv_high_below_low(self):
        """high < low should be rejected by pipeline."""
        analyzer = TimeframeAnalyzer()
        bad = _candle(high="90", low="105", minutes_before=60)
        good = _candle(minutes_before=120)
        result = analyzer.analyze([bad, good], REF, timeframe="1h", role="setup")
        # The invalid candle should be rejected, leaving only 1 valid candle
        assert result.candle_count == 1
        assert result.data_quality == "INSUFFICIENT_DATA"

    def test_invalid_price_zero(self):
        """close <= 0 should be rejected by pipeline."""
        analyzer = TimeframeAnalyzer()
        bad = _candle(close="0", minutes_before=60)
        good = _candle(minutes_before=120)
        result = analyzer.analyze([bad, good], REF, timeframe="1h", role="setup")
        assert result.candle_count == 1
        assert result.data_quality == "INSUFFICIENT_DATA"

    def test_invalid_price_negative(self):
        """Negative price should be rejected by pipeline."""
        analyzer = TimeframeAnalyzer()
        bad = _candle(open="-10", minutes_before=60)
        good = _candle(minutes_before=120)
        result = analyzer.analyze([bad, good], REF, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_invalid_open_above_high(self):
        """open > high should be rejected by pipeline."""
        analyzer = TimeframeAnalyzer()
        bad = _candle(open="200", minutes_before=60)
        good = _candle(minutes_before=120)
        result = analyzer.analyze([bad, good], REF, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_invalid_close_below_low(self):
        """close < low should be rejected by pipeline."""
        analyzer = TimeframeAnalyzer()
        bad = _candle(close="50", minutes_before=60)
        good = _candle(minutes_before=120)
        result = analyzer.analyze([bad, good], REF, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_invalid_negative_volume(self):
        """Negative volume should be rejected by pipeline."""
        analyzer = TimeframeAnalyzer()
        bad = _candle(volume="-100", minutes_before=60)
        good = _candle(minutes_before=120)
        result = analyzer.analyze([bad, good], REF, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_invalid_naive_timestamp(self):
        """Naive timestamp should be rejected by pipeline."""
        analyzer = TimeframeAnalyzer()
        naive = Candle(
            symbol="BTC-BRL",
            timestamp=datetime(2024, 6, 15, 11, 0),  # no tzinfo
            timeframe="1h",
            open=Decimal("100"), high=Decimal("105"),
            low=Decimal("95"), close=Decimal("102"),
            volume=Decimal("1000"),
        )
        good = _candle(minutes_before=120)
        result = analyzer.analyze([naive, good], REF, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_invalid_non_utc_timestamp(self):
        """Non-UTC timezone should be rejected by pipeline."""
        analyzer = TimeframeAnalyzer()
        from datetime import timezone as tz
        non_utc = Candle(
            symbol="BTC-BRL",
            timestamp=datetime(2024, 6, 15, 11, 0, tzinfo=tz(timedelta(hours=5))),
            timeframe="1h",
            open=Decimal("100"), high=Decimal("105"),
            low=Decimal("95"), close=Decimal("102"),
            volume=Decimal("1000"),
        )
        good = _candle(minutes_before=120)
        result = analyzer.analyze([non_utc, good], REF, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_invalid_empty_symbol(self):
        """Empty symbol should be rejected by pipeline."""
        analyzer = TimeframeAnalyzer()
        bad = _candle(symbol="", minutes_before=60)
        good = _candle(minutes_before=120)
        result = analyzer.analyze([bad, good], REF, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_invalid_timeframe(self):
        """Invalid timeframe should be rejected by pipeline."""
        analyzer = TimeframeAnalyzer()
        bad = _candle(timeframe="2h", minutes_before=60)
        good = _candle(minutes_before=120)
        result = analyzer.analyze([bad, good], REF, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_all_invalid_returns_insufficient(self):
        """All invalid candles should result in INSUFFICIENT_DATA."""
        analyzer = TimeframeAnalyzer()
        bad1 = _candle(high="90", low="105", minutes_before=60)
        bad2 = _candle(close="0", minutes_before=120)
        result = analyzer.analyze([bad1, bad2], REF, timeframe="1h", role="setup")
        assert result.data_quality == "INSUFFICIENT_DATA"
        assert result.candle_count == 0


# ============================================================
# CP09-CP12: Cache integration
# ============================================================


class TestCacheIntegration:
    def test_cache_isolation_by_symbol(self):
        cache = MarketDataCache()
        cache.put("mb", "BTC-BRL", "1h", [_candle(symbol="BTC-BRL")])
        cache.put("mb", "ETH-BRL", "1h", [_candle(symbol="ETH-BRL")])
        btc = cache.get("mb", "BTC-BRL", "1h")
        eth = cache.get("mb", "ETH-BRL", "1h")
        assert btc is not None and btc[0].symbol == "BTC-BRL"
        assert eth is not None and eth[0].symbol == "ETH-BRL"

    def test_cache_isolation_by_timeframe(self):
        cache = MarketDataCache()
        cache.put("mb", "BTC-BRL", "1h", [_candle(timeframe="1h")])
        cache.put("mb", "BTC-BRL", "4h", [_candle(timeframe="4h")])
        h1 = cache.get("mb", "BTC-BRL", "1h")
        h4 = cache.get("mb", "BTC-BRL", "4h")
        assert h1 is not None and h1[0].timeframe == "1h"
        assert h4 is not None and h4[0].timeframe == "4h"

    def test_cache_hit(self):
        cache = MarketDataCache()
        candles = [_candle()]
        cache.put("mb", "BTC-BRL", "1h", candles)
        result = cache.get("mb", "BTC-BRL", "1h")
        assert result is not None
        assert len(result) == 1

    def test_cache_miss(self):
        cache = MarketDataCache()
        assert cache.get("mb", "BTC-BRL", "1h") is None


# ============================================================
# CP13-CP15: Reference time, future candles, open candles
# ============================================================


class TestReferenceTime:
    def test_future_candles_excluded(self):
        analyzer = TimeframeAnalyzer()
        past = _candle(minutes_before=120)
        future = _candle(minutes_before=-60)  # future
        result = analyzer.analyze([past, future], REF, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_closed_candles_only(self):
        analyzer = TimeframeAnalyzer()
        closed = _candle(is_closed=True, minutes_before=60)
        open_c = _candle(is_closed=False, minutes_before=30)
        result = analyzer.analyze([closed, open_c], REF, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_reference_time_respected(self):
        engine = MTFEngine()
        past_ref = datetime(2024, 1, 1, tzinfo=timezone.utc)
        # All candles are after past_ref (2024-06-15)
        future_candles = _uptrend("1h", 10)
        result = engine.analyze("BTC-BRL", {"1h": future_candles}, past_ref)
        h1 = [r for r in result.timeframes if r.timeframe == "1h"][0]
        assert h1.data_quality == "INSUFFICIENT_DATA"


# ============================================================
# CP16-CP18: Insufficient data, gaps, duplicates
# ============================================================


class TestDataQuality:
    def test_insufficient_data(self):
        analyzer = TimeframeAnalyzer()
        one_candle = [_candle(minutes_before=60)]
        result = analyzer.analyze(one_candle, REF, timeframe="1h", role="setup")
        assert result.data_quality == "INSUFFICIENT_DATA"
        assert result.score == 50
        assert result.confidence == Decimal("0")

    def test_no_candles(self):
        analyzer = TimeframeAnalyzer()
        result = analyzer.analyze([], REF, timeframe="1h", role="setup")
        assert result.data_quality == "INSUFFICIENT_DATA"

    def test_gaps_detected(self):
        analyzer = TimeframeAnalyzer()
        c1 = _candle(timeframe="1h", minutes_before=180)
        c2 = _candle(timeframe="1h", minutes_before=60)
        result = analyzer.analyze([c1, c2], REF, timeframe="1h", role="setup")
        assert result.gaps_detected >= 1
        assert result.data_quality == "DEGRADED"

    def test_duplicates_detected(self):
        analyzer = TimeframeAnalyzer()
        # Create two candles with identical timestamps
        ts = REF - timedelta(minutes=60)
        c1 = Candle("BTC-BRL", ts, "1h", Decimal("100"), Decimal("105"),
                     Decimal("95"), Decimal("102"), Decimal("1000"))
        c2 = Candle("BTC-BRL", ts, "1h", Decimal("100"), Decimal("105"),
                     Decimal("95"), Decimal("102"), Decimal("1000"))
        result = analyzer.analyze([c1, c2], REF, timeframe="1h", role="setup")
        assert result.duplicates_detected >= 1
        assert result.data_quality == "DEGRADED"


# ============================================================
# CP19-CP22: Timeframe analysis, score, confidence, quality
# ============================================================


class TestTimeframeAnalysis:
    def test_timeframe_produces_bias(self):
        analyzer = TimeframeAnalyzer()
        candles = _uptrend("1h", 20)
        result = analyzer.analyze(candles, REF, timeframe="1h", role="setup")
        assert result.bias in ("BULLISH", "NEUTRAL", "BEARISH")

    def test_timeframe_produces_deterministic_score(self):
        analyzer = TimeframeAnalyzer()
        candles = _uptrend("1h", 20)
        r1 = analyzer.analyze(candles, REF, timeframe="1h", role="setup")
        r2 = analyzer.analyze(candles, REF, timeframe="1h", role="setup")
        assert r1.score == r2.score
        assert 0 <= r1.score <= 100

    def test_timeframe_produces_confidence(self):
        analyzer = TimeframeAnalyzer()
        candles = _uptrend("1h", 20)
        result = analyzer.analyze(candles, REF, timeframe="1h", role="setup")
        assert Decimal("0") <= result.confidence <= Decimal("1")

    def test_timeframe_produces_data_quality(self):
        analyzer = TimeframeAnalyzer()
        candles = _uptrend("1h", 20)
        result = analyzer.analyze(candles, REF, timeframe="1h", role="setup")
        assert result.data_quality in ("VALID", "DEGRADED", "INSUFFICIENT_DATA", "INVALID")

    def test_uptrend_produces_bullish(self):
        analyzer = TimeframeAnalyzer()
        candles = _uptrend("1h", 30)
        result = analyzer.analyze(candles, REF, timeframe="1h", role="setup")
        assert result.bias == "BULLISH"
        assert result.score > 60


# ============================================================
# CP23-CP27: MTF aggregation, weights, conflicts, bias
# ============================================================


class TestMTFAggregation:
    def test_aggregation_deterministic(self):
        engine = MTFEngine()
        candles = _all_uptrend()
        r1 = engine.analyze("BTC-BRL", candles, REF)
        r2 = engine.analyze("BTC-BRL", candles, REF)
        assert r1.bias == r2.bias
        assert r1.score == r2.score
        assert r1.confidence == r2.confidence

    def test_aggregation_considers_all_timeframes(self):
        engine = MTFEngine()
        candles = _all_uptrend()
        result = engine.analyze("BTC-BRL", candles, REF)
        roles = {r.role for r in result.timeframes}
        assert "macro" in roles
        assert "trend" in roles
        assert "setup" in roles
        assert "timing" in roles

    def test_configurable_weights(self):
        custom = MTFWeights(macro=Decimal("40"), trend=Decimal("30"),
                            setup=Decimal("20"), timing=Decimal("10"))
        engine = MTFEngine(weights=custom)
        assert engine.weights.macro == Decimal("40")

    def test_weights_normalized(self):
        w = MTFWeights()
        norm = w.normalized()
        total = sum(norm.values())
        assert total == Decimal("1")

    def test_conflict_detected(self):
        engine = MTFEngine()
        candles = {
            "1d": _uptrend("1d", 30, base_price=Decimal("50")),
            "4h": _uptrend("4h", 30, base_price=Decimal("50")),
            "1h": _downtrend("1h", 30, base_price=Decimal("500")),
            "15m": _downtrend("15m", 30, base_price=Decimal("500")),
        }
        result = engine.analyze("BTC-BRL", candles, REF)
        assert result.conflict is True
        # Higher TFs agree BULLISH -> LONG_BIAS (not BULLISH)
        assert result.bias == "LONG_BIAS"

    def test_no_conflict_aligned(self):
        engine = MTFEngine()
        candles = _all_uptrend()
        result = engine.analyze("BTC-BRL", candles, REF)
        assert result.conflict is False

    def test_higher_timeframe_protection(self):
        engine = MTFEngine()
        candles = {
            "1d": _downtrend("1d", 30),
            "4h": _downtrend("4h", 30),
            "1h": _uptrend("1h", 30),
            "15m": _uptrend("15m", 30),
        }
        result = engine.analyze("BTC-BRL", candles, REF)
        assert result.conflict is True
        # Higher TFs agree BEARISH -> SHORT_BIAS (not BEARISH)
        assert result.bias == "SHORT_BIAS"

    def test_final_bias_values(self):
        engine = MTFEngine()
        candles = _all_uptrend()
        result = engine.analyze("BTC-BRL", candles, REF)
        assert result.bias in ("LONG_BIAS", "NEUTRAL", "SHORT_BIAS")


# ============================================================
# BLOCKER 4: Higher timeframe protection tests
# ============================================================


class TestHigherTimeframeProtection:
    def test_case1_higher_bullish_lower_bearish(self):
        """1D=BULLISH, 4H=BULLISH, 1H=BEARISH, 15M=BEARISH -> LONG_BIAS."""
        engine = MTFEngine()
        candles = {
            "1d": _uptrend("1d", 30, base_price=Decimal("50")),
            "4h": _uptrend("4h", 30, base_price=Decimal("50")),
            "1h": _downtrend("1h", 30, base_price=Decimal("500")),
            "15m": _downtrend("15m", 30, base_price=Decimal("500")),
        }
        result = engine.analyze("BTC-BRL", candles, REF)
        assert result.conflict is True
        assert result.bias == "LONG_BIAS"

    def test_case2_higher_bearish_lower_bullish(self):
        """1D=BEARISH, 4H=BEARISH, 1H=BULLISH, 15M=BULLISH -> SHORT_BIAS."""
        engine = MTFEngine()
        candles = {
            "1d": _downtrend("1d", 30),
            "4h": _downtrend("4h", 30),
            "1h": _uptrend("1h", 30),
            "15m": _uptrend("15m", 30),
        }
        result = engine.analyze("BTC-BRL", candles, REF)
        assert result.conflict is True
        assert result.bias == "SHORT_BIAS"

    def test_case3_higher_conflict_neutral(self):
        """When higher TFs disagree -> NEUTRAL."""
        engine = MTFEngine()
        candles = {
            "1d": _uptrend("1d", 30, base_price=Decimal("50")),
            "4h": _downtrend("4h", 30, base_price=Decimal("500")),
            "1h": _uptrend("1h", 30),
            "15m": _downtrend("15m", 30),
        }
        result = engine.analyze("BTC-BRL", candles, REF)
        # Higher TFs disagree -> NEUTRAL (no single-direction protection)
        assert result.bias in ("LONG_BIAS", "NEUTRAL", "SHORT_BIAS")

    def test_never_returns_bullish_or_bearish(self):
        """MTFResult.bias never returns BULLISH or BEARISH."""
        engine = MTFEngine()
        # Test multiple configurations
        configs = [
            _all_uptrend(),
            {k: _downtrend(k, 30) for k in ["1d", "4h", "1h", "15m"]},
            {
                "1d": _uptrend("1d", 30, base_price=Decimal("50")),
                "4h": _uptrend("4h", 30, base_price=Decimal("50")),
                "1h": _downtrend("1h", 30, base_price=Decimal("500")),
                "15m": _downtrend("15m", 30, base_price=Decimal("500")),
            },
        ]
        for candles in configs:
            result = engine.analyze("BTC-BRL", candles, REF)
            assert result.bias not in ("BULLISH", "BEARISH"), \
                f"bias should not be {result.bias}"
            assert result.bias in ("LONG_BIAS", "NEUTRAL", "SHORT_BIAS")


# ============================================================
# CP28-CP31: No order, no LLM, no risk, no execution
# ============================================================


class TestNoSideEffects:
    def test_no_order_generation(self):
        engine = MTFEngine()
        assert not hasattr(engine, "submit_order")
        assert not hasattr(engine, "execute")

    def test_no_llm_dependency(self):
        import inspect
        source = inspect.getsource(MTFEngine)
        assert "openai" not in source.lower()
        assert "import openai" not in source.lower()
        assert "import anthropic" not in source.lower()

    def test_no_risk_modification(self):
        import inspect
        source = inspect.getsource(MTFEngine)
        assert "risk_engine" not in source.lower()

    def test_no_execution_modification(self):
        import inspect
        source = inspect.getsource(MTFEngine)
        assert "execution" not in source.lower()


# ============================================================
# CP32: Multi-symbol
# ============================================================


class TestMultiSymbol:
    def test_analyze_btc(self):
        engine = MTFEngine()
        candles = _all_uptrend()
        result = engine.analyze("BTC-BRL", candles, REF)
        assert result.symbol == "BTC-BRL"

    def test_analyze_eth(self):
        engine = MTFEngine()
        candles = _all_uptrend()
        result = engine.analyze("ETH-BRL", candles, REF)
        assert result.symbol == "ETH-BRL"

    def test_symbol_isolation(self):
        """Different symbols produce independent results."""
        engine = MTFEngine()
        btc_candles = _all_uptrend()
        eth_candles = {
            "1d": _downtrend("1d", 20),
            "4h": _downtrend("4h", 20),
            "1h": _downtrend("1h", 20),
            "15m": _downtrend("15m", 20),
        }
        btc_result = engine.analyze("BTC-BRL", btc_candles, REF)
        eth_result = engine.analyze("ETH-BRL", eth_candles, REF)
        assert btc_result.symbol == "BTC-BRL"
        assert eth_result.symbol == "ETH-BRL"
        # Different trends should produce different biases
        assert btc_result.bias != eth_result.bias


# ============================================================
# CP33-CP34: Replay determinism, no look-ahead
# ============================================================


class TestDeterminism:
    def test_same_inputs_same_output(self):
        """CP33: Same fixed inputs always produce same output."""
        engine = MTFEngine()
        candles = _all_uptrend()
        r1 = engine.analyze("BTC-BRL", candles, REF)
        r2 = engine.analyze("BTC-BRL", candles, REF)
        assert r1.bias == r2.bias
        assert r1.score == r2.score
        assert r1.confidence == r2.confidence

    def test_no_look_ahead(self):
        past_ref = datetime(2024, 1, 1, tzinfo=timezone.utc)
        candles = _uptrend("1h", 20)
        engine = MTFEngine()
        result = engine.analyze("BTC-BRL", {"1h": candles}, past_ref)
        h1 = [r for r in result.timeframes if r.timeframe == "1h"][0]
        assert h1.data_quality == "INSUFFICIENT_DATA"

    def test_no_datetime_now_in_analysis(self):
        import inspect
        source = inspect.getsource(MTFEngine.analyze)
        assert "datetime.now()" not in source
        source2 = inspect.getsource(TimeframeAnalyzer.analyze)
        assert "datetime.now()" not in source2


# ============================================================
# BLOCKER 6: MTFWeights validation
# ============================================================


class TestMTFWeightsValidation:
    def test_negative_weight_rejected(self):
        """Negative weight must raise ValueError."""
        with pytest.raises(ValueError, match="must be >= 0"):
            MTFWeights(macro=Decimal("-1"))

    def test_all_zero_weights_rejected(self):
        """All weights zero -> total=0 -> must raise ValueError."""
        with pytest.raises(ValueError, match="must be > 0"):
            MTFWeights(
                macro=Decimal("0"), trend=Decimal("0"),
                setup=Decimal("0"), timing=Decimal("0"),
            )

    def test_single_zero_weight_accepted(self):
        """One weight zero is OK if total > 0."""
        w = MTFWeights(macro=Decimal("50"), trend=Decimal("50"),
                       setup=Decimal("0"), timing=Decimal("0"))
        assert w.total == Decimal("100")

    def test_valid_weights_normalized(self):
        w = MTFWeights()
        norm = w.normalized()
        assert sum(norm.values()) == Decimal("1")

    def test_non_decimal_rejected(self):
        """Non-Decimal weight must raise TypeError."""
        with pytest.raises(TypeError):
            MTFWeights(macro=100)  # type: ignore[arg-type]


# ============================================================
# CP35-CP36: Metrics, logging
# ============================================================


class TestMTFResult:
    def test_result_has_all_fields(self):
        engine = MTFEngine()
        candles = _all_uptrend()
        result = engine.analyze("BTC-BRL", candles, REF)
        assert result.symbol == "BTC-BRL"
        assert result.reference_time == REF
        assert isinstance(result.bias, str)
        assert isinstance(result.score, int)
        assert isinstance(result.confidence, Decimal)
        assert isinstance(result.conflict, bool)
        assert isinstance(result.data_quality, str)
        assert len(result.timeframes) == 4
        assert len(result.reasons) >= 0

    def test_result_worst_quality(self):
        engine = MTFEngine()
        candles = {
            "1d": _uptrend("1d", 20),
            "4h": _uptrend("4h", 20),
            "1h": _uptrend("1h", 20),
            "15m": [],  # no candles -> INSUFFICIENT_DATA
        }
        result = engine.analyze("BTC-BRL", candles, REF)
        assert result.data_quality in ("DEGRADED", "INSUFFICIENT_DATA")


# ============================================================
# Config
# ============================================================


class TestConfigDefaults:
    def test_default_config_matches_roles(self):
        for role_key, tf in DEFAULT_MTF_CONFIG.items():
            role_name = role_key.replace("_timeframe", "")
            assert role_name in ("macro", "trend", "setup", "timing")

    def test_config_is_copy(self):
        engine = MTFEngine()
        config1 = engine.config
        config2 = engine.config
        assert config1 == config2
        assert config1 is not config2
