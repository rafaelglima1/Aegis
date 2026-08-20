"""AEGIS Phase 2 — Market Data Foundation Tests.

Tests for unified market data contract, validation, normalization,
cache, observability, and backward compatibility.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from aegis.market_data.contracts import Candle
from aegis.market_data.validator import (
    CandleValidator,
    BatchValidator,
    InvalidCandleError,
    CandleValidationError,
    VALID_TIMEFRAMES,
    TIMEFRAME_SECONDS,
)
from aegis.market_data.normalizer import (
    MarketDataNormalizer,
    normalize_symbol,
    normalize_timeframe,
)
from aegis.market_data.cache import MarketDataCache
from aegis.market_data.observability import MarketDataMetrics


# ============================================================
# Helpers
# ============================================================


def _make_candle(
    symbol: str = "BTC-BRL",
    timeframe: str = "1h",
    minutes_ago: int = 0,
    open: str = "100",
    high: str = "105",
    low: str = "95",
    close: str = "102",
    volume: str = "1000",
    source: str = "test",
) -> Candle:
    """Create a test candle with defaults."""
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return Candle(
        symbol=symbol,
        timestamp=ts,
        timeframe=timeframe,
        open=Decimal(open),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        source=source,
    )


# ============================================================
# CP01: MarketData Contract
# ============================================================


class TestCandleContract:

    def test_candle_has_all_fields(self) -> None:
        """CP01: Candle has symbol, timestamp, timeframe, OHLCV, source."""
        c = _make_candle()
        assert c.symbol == "BTC-BRL"
        assert c.timeframe == "1h"
        assert c.open == Decimal("100")
        assert c.high == Decimal("105")
        assert c.low == Decimal("95")
        assert c.close == Decimal("102")
        assert c.volume == Decimal("1000")
        assert c.source == "test"
        assert c.is_closed is True

    def test_candle_is_frozen(self) -> None:
        """CP01: Candle is immutable."""
        c = _make_candle()
        with pytest.raises(AttributeError):
            c.close = Decimal("200")  # type: ignore[misc]

    def test_candle_identity(self) -> None:
        """CP01: Identity is symbol + timeframe + timestamp."""
        c1 = _make_candle(symbol="BTC-BRL", timeframe="1h")
        c2 = _make_candle(symbol="BTC-BRL", timeframe="1h")
        assert c1.identity == c2.identity

    def test_candle_to_dict(self) -> None:
        """CP01: to_dict serializes correctly."""
        c = _make_candle()
        d = c.to_dict()
        assert d["symbol"] == "BTC-BRL"
        assert d["open"] == "100"
        assert "timestamp" in d

    def test_candle_from_dict(self) -> None:
        """CP01: from_dict constructs Candle from dict."""
        d = {
            "symbol": "ETH-BRL",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "timeframe": "4h",
            "open": "2000",
            "high": "2100",
            "low": "1900",
            "close": "2050",
            "volume": "500",
        }
        c = Candle.from_dict(d)
        assert c.symbol == "ETH-BRL"
        assert c.open == Decimal("2000")
        assert c.timestamp.year == 2024

    def test_candle_from_raw_with_epoch(self) -> None:
        """CP01: from_raw handles epoch int timestamps."""
        c = Candle.from_raw(
            symbol="BTC-BRL",
            timeframe="1h",
            timestamp=1704067200,
            open=100, high=105, low=95, close=102, volume=1000,
        )
        assert c.timestamp.year == 2024
        assert c.open == Decimal("100")


# ============================================================
# CP04-CP06: OHLCV Validation
# ============================================================


class TestOHLCVValidation:

    def test_valid_candle_passes(self) -> None:
        """CP04-CP05: Valid candle passes validation."""
        c = _make_candle()
        CandleValidator.validate(c)

    def test_zero_price_rejected(self) -> None:
        """CP05: open=0 is rejected."""
        c = _make_candle(open="0")
        with pytest.raises(InvalidCandleError, match="must be > 0"):
            CandleValidator.validate_positive_prices(c)

    def test_negative_price_rejected(self) -> None:
        """CP05: negative prices are rejected."""
        c = _make_candle(close="-10")
        with pytest.raises(InvalidCandleError, match="must be > 0"):
            CandleValidator.validate_positive_prices(c)

    def test_high_less_than_low_rejected(self) -> None:
        """CP05: high < low is rejected."""
        c = _make_candle(high="90", low="95")
        with pytest.raises(InvalidCandleError, match="High .* < Low"):
            CandleValidator.validate_ohlcv_consistency(c)

    def test_open_outside_range_rejected(self) -> None:
        """CP05: open outside [low, high] is rejected."""
        c = _make_candle(open="200")
        with pytest.raises(InvalidCandleError, match="Open .* outside"):
            CandleValidator.validate_ohlcv_consistency(c)

    def test_close_outside_range_rejected(self) -> None:
        """CP05: close outside [low, high] is rejected."""
        c = _make_candle(close="200")
        with pytest.raises(InvalidCandleError, match="Close .* outside"):
            CandleValidator.validate_ohlcv_consistency(c)

    def test_negative_volume_rejected(self) -> None:
        """CP06: negative volume is rejected."""
        c = _make_candle(volume="-100")
        with pytest.raises(InvalidCandleError, match="Negative volume"):
            CandleValidator.validate_ohlcv_consistency(c)

    def test_zero_volume_accepted(self) -> None:
        """CP06: zero volume is allowed."""
        c = _make_candle(volume="0")
        CandleValidator.validate_ohlcv_consistency(c)


# ============================================================
# CP07-CP08: Timestamp Validation
# ============================================================


class TestTimestampValidation:

    def test_utc_timestamp_passes(self) -> None:
        """CP07: UTC timestamp is valid."""
        c = _make_candle()
        CandleValidator.validate_timestamp_utc(c)

    def test_naive_timestamp_rejected(self) -> None:
        """CP07: naive timestamp is rejected."""
        c = Candle(
            symbol="BTC-BRL",
            timestamp=datetime(2024, 1, 1),
            timeframe="1h",
            open=Decimal("100"), high=Decimal("105"),
            low=Decimal("95"), close=Decimal("102"),
            volume=Decimal("1000"),
        )
        with pytest.raises(InvalidCandleError, match="timezone-aware"):
            CandleValidator.validate_timestamp_utc(c)

    def test_non_utc_timezone_rejected(self) -> None:
        """CP07: non-UTC timezone is rejected."""
        from datetime import timezone as tz
        c = Candle(
            symbol="BTC-BRL",
            timestamp=datetime(2024, 1, 1, tzinfo=tz(timedelta(hours=5))),
            timeframe="1h",
            open=Decimal("100"), high=Decimal("105"),
            low=Decimal("95"), close=Decimal("102"),
            volume=Decimal("1000"),
        )
        with pytest.raises(InvalidCandleError, match="must be in UTC"):
            CandleValidator.validate_timestamp_utc(c)

    def test_future_timestamp_rejected(self) -> None:
        """CP08: future timestamp is rejected."""
        c = _make_candle(minutes_ago=-60)  # 1 hour in the future
        with pytest.raises(InvalidCandleError, match="future"):
            CandleValidator.validate_no_future_timestamp(c)


# ============================================================
# CP09-CP11: Ordering, Duplicates, Gaps
# ============================================================


class TestBatchValidation:

    def test_ordered_candles_no_issues(self) -> None:
        """CP09: Ordered candles pass ordering check."""
        candles = [_make_candle(minutes_ago=3 - i) for i in range(4)]
        out_of_order = BatchValidator.validate_ordering(candles)
        assert out_of_order == []

    def test_out_of_order_detected(self) -> None:
        """CP09: Out-of-order candles are detected."""
        c1 = _make_candle(minutes_ago=3)
        c2 = _make_candle(minutes_ago=1)
        c3 = _make_candle(minutes_ago=2)  # out of order
        out_of_order = BatchValidator.validate_ordering([c1, c2, c3])
        assert 2 in out_of_order

    def test_no_duplicates(self) -> None:
        """CP10: Unique candles have no duplicates."""
        candles = [_make_candle(minutes_ago=3 - i) for i in range(4)]
        dups = BatchValidator.validate_duplicates(candles)
        assert dups == []

    def test_duplicates_detected(self) -> None:
        """CP10: Duplicate candles are detected."""
        c1 = _make_candle(minutes_ago=0)
        c2 = _make_candle(minutes_ago=0)  # same identity
        dups = BatchValidator.validate_duplicates([c1, c2])
        assert len(dups) == 1

    def test_no_gaps_in_continuous_data(self) -> None:
        """CP11: Continuous hourly data has no gaps."""
        candles = [_make_candle(minutes_ago=(3 - i) * 60) for i in range(4)]
        gaps = BatchValidator.validate_gaps(candles)
        assert gaps == []

    def test_gap_detected(self) -> None:
        """CP11: Missing candle in sequence is detected."""
        c1 = _make_candle(minutes_ago=120)  # 2 hours ago
        c2 = _make_candle(minutes_ago=0)    # now (1 hour missing in between)
        gaps = BatchValidator.validate_gaps([c1, c2])
        assert len(gaps) == 1

    def test_empty_list_no_issues(self) -> None:
        """CP09-CP11: Empty list has no issues."""
        assert BatchValidator.validate_ordering([]) == []
        assert BatchValidator.validate_duplicates([]) == []
        assert BatchValidator.validate_gaps([]) == []


# ============================================================
# CP12-CP13: Timeframe + Symbol
# ============================================================


class TestTimeframeAndSymbol:

    def test_valid_timeframes_accepted(self) -> None:
        """CP12: All valid timeframes pass validation."""
        for tf in VALID_TIMEFRAMES:
            c = _make_candle(timeframe=tf)
            CandleValidator.validate_timeframe(c)

    def test_invalid_timeframe_rejected(self) -> None:
        """CP12: Invalid timeframe is rejected."""
        c = _make_candle(timeframe="2h")
        with pytest.raises(InvalidCandleError, match="Invalid timeframe"):
            CandleValidator.validate_timeframe(c)

    def test_symbol_normalization(self) -> None:
        """CP13: Symbol normalization to canonical form."""
        assert normalize_symbol("btcbrl") == "BTC-BRL"
        assert normalize_symbol("BTC-BRL") == "BTC-BRL"
        assert normalize_symbol("btc_brl") == "BTC-BRL"
        assert normalize_symbol("ETH-BRL") == "ETH-BRL"

    def test_timeframe_normalization(self) -> None:
        """CP12: Timeframe normalization."""
        assert normalize_timeframe("1h") == "1h"
        assert normalize_timeframe(" 1H ") == "1h"
        with pytest.raises(ValueError, match="Invalid timeframe"):
            normalize_timeframe("2h")

    def test_empty_symbol_rejected(self) -> None:
        """CP13: Empty symbol is rejected."""
        c = _make_candle(symbol="")
        with pytest.raises(InvalidCandleError, match="non-empty"):
            CandleValidator.validate_symbol(c)


# ============================================================
# CP18-CP20: Cache
# ============================================================


class TestCache:

    def test_cache_put_and_get(self) -> None:
        """CP18: Cache stores and retrieves candles."""
        cache = MarketDataCache(ttl_seconds=60)
        candles = [_make_candle()]
        cache.put("mb", "BTC-BRL", "1h", candles)
        result = cache.get("mb", "BTC-BRL", "1h")
        assert result is not None
        assert len(result) == 1

    def test_cache_miss(self) -> None:
        """CP18: Cache miss returns None."""
        cache = MarketDataCache()
        assert cache.get("mb", "BTC-BRL", "1h") is None

    def test_cache_isolation(self) -> None:
        """CP19: Different symbols are isolated."""
        cache = MarketDataCache()
        cache.put("mb", "BTC-BRL", "1h", [_make_candle(symbol="BTC-BRL")])
        cache.put("mb", "ETH-BRL", "1h", [_make_candle(symbol="ETH-BRL")])
        btc = cache.get("mb", "BTC-BRL", "1h")
        eth = cache.get("mb", "ETH-BRL", "1h")
        assert btc is not None and btc[0].symbol == "BTC-BRL"
        assert eth is not None and eth[0].symbol == "ETH-BRL"

    def test_cache_timeframe_isolation(self) -> None:
        """CP19: Different timeframes are isolated."""
        cache = MarketDataCache()
        cache.put("mb", "BTC-BRL", "1h", [_make_candle(timeframe="1h")])
        cache.put("mb", "BTC-BRL", "4h", [_make_candle(timeframe="4h")])
        h1 = cache.get("mb", "BTC-BRL", "1h")
        h4 = cache.get("mb", "BTC-BRL", "4h")
        assert h1 is not None and h1[0].timeframe == "1h"
        assert h4 is not None and h4[0].timeframe == "4h"

    def test_cache_provider_isolation(self) -> None:
        """CP19: Different providers are isolated."""
        cache = MarketDataCache()
        cache.put("mb", "BTC-BRL", "1h", [_make_candle(source="mb")])
        cache.put("cb", "BTC-BRL", "1h", [_make_candle(source="cb")])
        mb = cache.get("mb", "BTC-BRL", "1h")
        cb = cache.get("cb", "BTC-BRL", "1h")
        assert mb is not None and mb[0].source == "mb"
        assert cb is not None and cb[0].source == "cb"

    def test_cache_ttl_expiration(self) -> None:
        """CP20: Cache entries expire after TTL."""
        import time
        cache = MarketDataCache(ttl_seconds=0.01)
        cache.put("mb", "BTC-BRL", "1h", [_make_candle()])
        time.sleep(0.02)
        assert cache.get("mb", "BTC-BRL", "1h") is None

    def test_cache_invalidation(self) -> None:
        """CP20: Cache can be invalidated."""
        cache = MarketDataCache()
        cache.put("mb", "BTC-BRL", "1h", [_make_candle()])
        assert cache.invalidate("mb", "BTC-BRL", "1h") is True
        assert cache.get("mb", "BTC-BRL", "1h") is None

    def test_cache_stats(self) -> None:
        """CP29: Cache stats are available."""
        cache = MarketDataCache()
        cache.put("mb", "BTC-BRL", "1h", [_make_candle()])
        cache.get("mb", "BTC-BRL", "1h")
        stats = cache.stats()
        assert stats["entries"] == 1
        assert stats["total_hits"] == 1


# ============================================================
# CP29: Observability
# ============================================================


class TestObservability:

    def test_metrics_snapshot(self) -> None:
        """CP29: Metrics snapshot returns all fields."""
        m = MarketDataMetrics()
        m.fetch_count = 5
        m.cache_hits = 3
        snap = m.snapshot()
        assert snap["fetch_count"] == 5
        assert snap["cache_hits"] == 3
        assert "elapsed_seconds" in snap


# ============================================================
# CP22-CP24: Replay/Backtest/Strategy Compatibility
# ============================================================


class TestCompatibility:

    def test_replay_candle_is_canonical(self) -> None:
        """CP22: Replay Candle is the canonical Candle type."""
        from aegis.replay import Candle as ReplayCandle
        c = ReplayCandle(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("95"),
            close=Decimal("102"),
            volume=Decimal("1000"),
        )
        assert isinstance(c, Candle)
        assert c.open == Decimal("100")

    def test_backtest_candle_is_canonical(self) -> None:
        """CP23: Backtest Candle is the canonical Candle type."""
        from aegis.backtest.engine_v2 import Candle as BacktestCandle
        c = BacktestCandle(
            "2024-01-01T00:00:00Z",
            Decimal("100"), Decimal("105"), Decimal("95"), Decimal("102"),
        )
        assert isinstance(c, Candle)
        assert c.timestamp.year == 2024

    def test_backtest_candle_from_dict(self) -> None:
        """CP23: Backtest create_candles_from_dicts uses canonical Candle."""
        from aegis.backtest.engine_v2 import create_candles_from_dicts
        data = [
            {"timestamp": "2024-01-01T00:00:00Z", "open": "100", "high": "105",
             "low": "95", "close": "102", "volume": "1000"},
        ]
        candles = create_candles_from_dicts(data)
        assert isinstance(candles[0], Candle)

    def test_normalizer_produces_canonical(self) -> None:
        """CP24: Normalizer produces canonical Candle."""
        norm = MarketDataNormalizer()
        raw = {
            "timestamp": 1704067200,
            "open": "100", "high": "105", "low": "95", "close": "102", "volume": "1000",
        }
        c = norm.normalize_candle(raw, "BTC-BRL", "1h", source="mb")
        assert isinstance(c, Candle)
        assert c.symbol == "BTC-BRL"
        assert c.timeframe == "1h"
