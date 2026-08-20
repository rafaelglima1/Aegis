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


def _make_candle(symbol="BTC-BRL", timeframe="1h", minutes_ago=0,
                 open="100", high="105", low="95", close="102",
                 volume="1000", is_closed=True):
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return Candle(symbol=symbol, timestamp=ts, timeframe=timeframe,
                  open=Decimal(open), high=Decimal(high), low=Decimal(low),
                  close=Decimal(close), volume=Decimal(volume), is_closed=is_closed)


def _make_uptrend_candles(timeframe="1h", count=20, base_price=Decimal("100")):
    candles = []
    price = base_price
    step = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}.get(timeframe, 60)
    for i in range(count):
        change = Decimal("1") if i % 3 != 0 else Decimal("-0.5")
        price = price + change
        candles.append(Candle(
            symbol="BTC-BRL",
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=step * (count - i)),
            timeframe=timeframe,
            open=price - change, high=price + Decimal("2"),
            low=price - Decimal("2"), close=price,
            volume=Decimal("1000")))
    return candles


def _make_downtrend_candles(timeframe="1h", count=20, base_price=Decimal("200")):
    candles = []
    price = base_price
    step = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}.get(timeframe, 60)
    for i in range(count):
        change = Decimal("-1") if i % 3 != 0 else Decimal("0.5")
        price = price + change
        candles.append(Candle(
            symbol="BTC-BRL",
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=step * (count - i)),
            timeframe=timeframe,
            open=price - change, high=price + Decimal("2"),
            low=price - Decimal("2"), close=price,
            volume=Decimal("1000")))
    return candles


def _make_candles_by_timeframe():
    return {
        "1d": _make_uptrend_candles("1d", 20),
        "4h": _make_uptrend_candles("4h", 20),
        "1h": _make_uptrend_candles("1h", 20),
        "15m": _make_uptrend_candles("15m", 20),
    }


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


class TestTimeframeSupport:
    def test_support_1d(self):
        engine = MTFEngine()
        result = engine.analyze("BTC-BRL", {"1d": _make_uptrend_candles("1d", 20)}, datetime.now(timezone.utc))
        assert any(r.timeframe == "1d" for r in result.timeframes)

    def test_support_4h(self):
        engine = MTFEngine()
        result = engine.analyze("BTC-BRL", {"4h": _make_uptrend_candles("4h", 20)}, datetime.now(timezone.utc))
        assert any(r.timeframe == "4h" for r in result.timeframes)

    def test_support_1h(self):
        engine = MTFEngine()
        result = engine.analyze("BTC-BRL", {"1h": _make_uptrend_candles("1h", 20)}, datetime.now(timezone.utc))
        assert any(r.timeframe == "1h" for r in result.timeframes)

    def test_support_15m(self):
        engine = MTFEngine()
        result = engine.analyze("BTC-BRL", {"15m": _make_uptrend_candles("15m", 20)}, datetime.now(timezone.utc))
        assert any(r.timeframe == "15m" for r in result.timeframes)


class TestCandleValidation:
    def test_canonical_candle_used(self):
        candles = _make_uptrend_candles("1h", 10)
        for c in candles:
            assert isinstance(c, Candle)

    def test_ohlcv_validation(self):
        valid = _make_candle(high="105", low="95", open="100", close="102")
        CandleValidator.validate_ohlcv_consistency(valid)
        invalid = _make_candle(high="90", low="95")
        with pytest.raises(Exception):
            CandleValidator.validate_ohlcv_consistency(invalid)

    def test_timestamp_validation(self):
        valid = _make_candle()
        CandleValidator.validate_timestamp_utc(valid)


class TestCacheIntegration:
    def test_cache_isolation_by_symbol(self):
        cache = MarketDataCache()
        cache.put("mb", "BTC-BRL", "1h", [_make_candle(symbol="BTC-BRL")])
        cache.put("mb", "ETH-BRL", "1h", [_make_candle(symbol="ETH-BRL")])
        btc = cache.get("mb", "BTC-BRL", "1h")
        eth = cache.get("mb", "ETH-BRL", "1h")
        assert btc is not None and btc[0].symbol == "BTC-BRL"
        assert eth is not None and eth[0].symbol == "ETH-BRL"

    def test_cache_isolation_by_timeframe(self):
        cache = MarketDataCache()
        cache.put("mb", "BTC-BRL", "1h", [_make_candle(timeframe="1h")])
        cache.put("mb", "BTC-BRL", "4h", [_make_candle(timeframe="4h")])
        h1 = cache.get("mb", "BTC-BRL", "1h")
        h4 = cache.get("mb", "BTC-BRL", "4h")
        assert h1 is not None and h1[0].timeframe == "1h"
        assert h4 is not None and h4[0].timeframe == "4h"

    def test_cache_hit(self):
        cache = MarketDataCache()
        candles = [_make_candle()]
        cache.put("mb", "BTC-BRL", "1h", candles)
        result = cache.get("mb", "BTC-BRL", "1h")
        assert result is not None
        assert len(result) == 1

    def test_cache_miss(self):
        cache = MarketDataCache()
        assert cache.get("mb", "BTC-BRL", "1h") is None


class TestReferenceTime:
    def test_future_candles_excluded(self):
        analyzer = TimeframeAnalyzer()
        past = _make_candle(minutes_ago=120)
        future = _make_candle(minutes_ago=-60)
        ref = datetime.now(timezone.utc)
        result = analyzer.analyze([past, future], ref, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_closed_candles_only(self):
        analyzer = TimeframeAnalyzer()
        closed = _make_candle(is_closed=True, minutes_ago=60)
        open_c = _make_candle(is_closed=False, minutes_ago=30)
        ref = datetime.now(timezone.utc)
        result = analyzer.analyze([closed, open_c], ref, timeframe="1h", role="setup")
        assert result.candle_count == 1

    def test_reference_time_respected(self):
        engine = MTFEngine()
        ref = datetime(2024, 1, 1, tzinfo=timezone.utc)
        future_candles = _make_uptrend_candles("1h", 10)
        result = engine.analyze("BTC-BRL", {"1h": future_candles}, ref)
        h1 = [r for r in result.timeframes if r.timeframe == "1h"][0]
        assert h1.data_quality == "INSUFFICIENT_DATA"


class TestDataQuality:
    def test_insufficient_data(self):
        analyzer = TimeframeAnalyzer()
        one_candle = [_make_candle(minutes_ago=60)]
        ref = datetime.now(timezone.utc)
        result = analyzer.analyze(one_candle, ref, timeframe="1h", role="setup")
        assert result.data_quality == "INSUFFICIENT_DATA"
        assert result.score == 50
        assert result.confidence == Decimal("0")

    def test_no_candles(self):
        analyzer = TimeframeAnalyzer()
        ref = datetime.now(timezone.utc)
        result = analyzer.analyze([], ref, timeframe="1h", role="setup")
        assert result.data_quality == "INSUFFICIENT_DATA"

    def test_gaps_detected(self):
        analyzer = TimeframeAnalyzer()
        c1 = _make_candle(timeframe="1h", minutes_ago=180)
        c2 = _make_candle(timeframe="1h", minutes_ago=60)
        ref = datetime.now(timezone.utc)
        result = analyzer.analyze([c1, c2], ref, timeframe="1h", role="setup")
        assert result.gaps_detected >= 1
        assert result.data_quality == "DEGRADED"

    def test_duplicates_detected(self):
        analyzer = TimeframeAnalyzer()
        c1 = _make_candle(timeframe="1h", minutes_ago=60)
        c2 = _make_candle(timeframe="1h", minutes_ago=60)
        ref = datetime.now(timezone.utc)
        result = analyzer.analyze([c1, c2], ref, timeframe="1h", role="setup")
        assert result.duplicates_detected >= 1
        assert result.data_quality == "DEGRADED"


class TestTimeframeAnalysis:
    def test_timeframe_produces_bias(self):
        analyzer = TimeframeAnalyzer()
        candles = _make_uptrend_candles("1h", 20)
        ref = datetime.now(timezone.utc)
        result = analyzer.analyze(candles, ref, timeframe="1h", role="setup")
        assert result.bias in ("BULLISH", "NEUTRAL", "BEARISH")

    def test_timeframe_produces_deterministic_score(self):
        analyzer = TimeframeAnalyzer()
        candles = _make_uptrend_candles("1h", 20)
        ref = datetime.now(timezone.utc)
        r1 = analyzer.analyze(candles, ref, timeframe="1h", role="setup")
        r2 = analyzer.analyze(candles, ref, timeframe="1h", role="setup")
        assert r1.score == r2.score
        assert 0 <= r1.score <= 100

    def test_timeframe_produces_confidence(self):
        analyzer = TimeframeAnalyzer()
        candles = _make_uptrend_candles("1h", 20)
        ref = datetime.now(timezone.utc)
        result = analyzer.analyze(candles, ref, timeframe="1h", role="setup")
        assert Decimal("0") <= result.confidence <= Decimal("1")

    def test_timeframe_produces_data_quality(self):
        analyzer = TimeframeAnalyzer()
        candles = _make_uptrend_candles("1h", 20)
        ref = datetime.now(timezone.utc)
        result = analyzer.analyze(candles, ref, timeframe="1h", role="setup")
        assert result.data_quality in ("VALID", "DEGRADED", "INSUFFICIENT_DATA", "INVALID")

    def test_uptrend_produces_bullish(self):
        analyzer = TimeframeAnalyzer()
        candles = _make_uptrend_candles("1h", 30)
        ref = datetime.now(timezone.utc)
        result = analyzer.analyze(candles, ref, timeframe="1h", role="setup")
        assert result.bias == "BULLISH"
        assert result.score > 60


class TestMTFAggregation:
    def test_aggregation_deterministic(self):
        engine = MTFEngine()
        candles = _make_candles_by_timeframe()
        ref = datetime.now(timezone.utc)
        r1 = engine.analyze("BTC-BRL", candles, ref)
        r2 = engine.analyze("BTC-BRL", candles, ref)
        assert r1.bias == r2.bias
        assert r1.score == r2.score
        assert r1.confidence == r2.confidence

    def test_aggregation_considers_all_timeframes(self):
        engine = MTFEngine()
        candles = _make_candles_by_timeframe()
        ref = datetime.now(timezone.utc)
        result = engine.analyze("BTC-BRL", candles, ref)
        roles = {r.role for r in result.timeframes}
        assert "macro" in roles
        assert "trend" in roles
        assert "setup" in roles
        assert "timing" in roles

    def test_configurable_weights(self):
        custom = MTFWeights(macro=Decimal("40"), trend=Decimal("30"), setup=Decimal("20"), timing=Decimal("10"))
        engine = MTFEngine(weights=custom)
        assert engine.weights.macro == Decimal("40")

    def test_weights_normalized(self):
        w = MTFWeights()
        norm = w.normalized()
        total = sum(norm.values())
        assert total == Decimal("1")

    def test_conflict_detected(self):
        engine = MTFEngine()
        # Higher TFs strongly bullish, lower TFs strongly bearish
        candles = {
            "1d": _make_uptrend_candles("1d", 30, base_price=Decimal("50")),
            "4h": _make_uptrend_candles("4h", 30, base_price=Decimal("50")),
            "1h": _make_downtrend_candles("1h", 30, base_price=Decimal("500")),
            "15m": _make_downtrend_candles("15m", 30, base_price=Decimal("500")),
        }
        ref = datetime.now(timezone.utc)
        result = engine.analyze("BTC-BRL", candles, ref)
        assert result.conflict is True
        # Higher TFs agree on BULLISH, so higher timeframe protection applies
        assert result.bias == "BULLISH"

    def test_no_conflict_aligned(self):
        engine = MTFEngine()
        candles = _make_candles_by_timeframe()
        ref = datetime.now(timezone.utc)
        result = engine.analyze("BTC-BRL", candles, ref)
        assert result.conflict is False

    def test_higher_timeframe_protection(self):
        engine = MTFEngine()
        candles = {
            "1d": _make_downtrend_candles("1d", 30),
            "4h": _make_downtrend_candles("4h", 30),
            "1h": _make_uptrend_candles("1h", 30),
            "15m": _make_uptrend_candles("15m", 30),
        }
        ref = datetime.now(timezone.utc)
        result = engine.analyze("BTC-BRL", candles, ref)
        assert result.conflict is True
        assert result.bias == "BEARISH"

    def test_final_bias_values(self):
        engine = MTFEngine()
        candles = _make_candles_by_timeframe()
        ref = datetime.now(timezone.utc)
        result = engine.analyze("BTC-BRL", candles, ref)
        assert result.bias in ("LONG_BIAS", "NEUTRAL", "SHORT_BIAS")


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


class TestMultiSymbol:
    def test_analyze_btc(self):
        engine = MTFEngine()
        candles = _make_candles_by_timeframe()
        ref = datetime.now(timezone.utc)
        result = engine.analyze("BTC-BRL", candles, ref)
        assert result.symbol == "BTC-BRL"

    def test_analyze_eth(self):
        engine = MTFEngine()
        candles = _make_candles_by_timeframe()
        ref = datetime.now(timezone.utc)
        result = engine.analyze("ETH-BRL", candles, ref)
        assert result.symbol == "ETH-BRL"


class TestDeterminism:
    def test_same_inputs_same_output(self):
        engine = MTFEngine()
        candles = _make_candles_by_timeframe()
        ref = datetime.now(timezone.utc)
        r1 = engine.analyze("BTC-BRL", candles, ref)
        r2 = engine.analyze("BTC-BRL", candles, ref)
        assert r1.bias == r2.bias
        assert r1.score == r2.score
        assert r1.confidence == r2.confidence

    def test_no_look_ahead(self):
        engine = MTFEngine()
        ref = datetime(2024, 6, 1, tzinfo=timezone.utc)
        candles = _make_uptrend_candles("1h", 20)
        result = engine.analyze("BTC-BRL", {"1h": candles}, ref)
        h1 = [r for r in result.timeframes if r.timeframe == "1h"][0]
        assert h1.data_quality == "INSUFFICIENT_DATA"

    def test_no_datetime_now_in_analysis(self):
        import inspect
        source = inspect.getsource(MTFEngine.analyze)
        assert "datetime.now()" not in source
        source2 = inspect.getsource(TimeframeAnalyzer.analyze)
        assert "datetime.now()" not in source2


class TestMTFResult:
    def test_result_has_all_fields(self):
        engine = MTFEngine()
        candles = _make_candles_by_timeframe()
        ref = datetime.now(timezone.utc)
        result = engine.analyze("BTC-BRL", candles, ref)
        assert result.symbol == "BTC-BRL"
        assert result.reference_time == ref
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
            "1d": _make_uptrend_candles("1d", 20),
            "4h": _make_uptrend_candles("4h", 20),
            "1h": _make_uptrend_candles("1h", 20),
            "15m": [],  # no candles -> INSUFFICIENT_DATA
        }
        ref = datetime.now(timezone.utc)
        result = engine.analyze("BTC-BRL", candles, ref)
        assert result.data_quality in ("DEGRADED", "INSUFFICIENT_DATA")


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
