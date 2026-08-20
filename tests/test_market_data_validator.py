"""Tests for AEGIS candle validation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from aegis.market_data.provider import Candle
from aegis.market_data.validator import CandleValidator, InvalidCandleError


def make_valid_candle(**overrides) -> Candle:
    defaults = dict(
        symbol="PETR4",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        timeframe="1d",
        open=Decimal("50.00"),
        high=Decimal("55.00"),
        low=Decimal("48.00"),
        close=Decimal("52.00"),
        volume=Decimal("1000"),
        is_closed=True,
    )
    defaults.update(overrides)
    return Candle(**defaults)


def test_valid_candle_passes_validation() -> None:
    """AC-04.04: Candles pass consistency validation."""
    candle = make_valid_candle()
    CandleValidator.validate_ohlcv_consistency(candle)


def test_high_less_than_low_raises() -> None:
    """AC-04.04: Candles pass consistency validation."""
    candle = make_valid_candle(high=Decimal("45.00"), low=Decimal("50.00"))
    with pytest.raises(InvalidCandleError):
        CandleValidator.validate_ohlcv_consistency(candle)


def test_open_outside_range_raises() -> None:
    """AC-04.04: Candles pass consistency validation."""
    candle = make_valid_candle(open=Decimal("60.00"))
    with pytest.raises(InvalidCandleError):
        CandleValidator.validate_ohlcv_consistency(candle)


def test_close_outside_range_raises() -> None:
    """AC-04.04: Candles pass consistency validation."""
    candle = make_valid_candle(close=Decimal("40.00"))
    with pytest.raises(InvalidCandleError):
        CandleValidator.validate_ohlcv_consistency(candle)


def test_negative_volume_raises() -> None:
    """AC-04.04: Candles pass consistency validation."""
    candle = make_valid_candle(volume=Decimal("-100"))
    with pytest.raises(InvalidCandleError):
        CandleValidator.validate_ohlcv_consistency(candle)


def test_utc_timestamp_passes() -> None:
    """AC-04.02: Market timestamps are normalized to UTC."""
    candle = make_valid_candle()
    CandleValidator.validate_timestamp_utc(candle)


def test_naive_timestamp_raises() -> None:
    """AC-04.02: Market timestamps are normalized to UTC."""
    candle = make_valid_candle(
        timestamp=datetime(2024, 1, 1, 12, 0, 0)
    )
    with pytest.raises(InvalidCandleError):
        CandleValidator.validate_timestamp_utc(candle)


def test_non_utc_timestamp_raises() -> None:
    """AC-04.02: Market timestamps are normalized to UTC."""
    tz_offset = timezone(offset=__import__("datetime").timedelta(hours=3))
    candle = make_valid_candle(
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz_offset)
    )
    with pytest.raises(InvalidCandleError):
        CandleValidator.validate_timestamp_utc(candle)


def test_look_ahead_prevention() -> None:
    """AC-04.10: Tests demonstrate absence of look-ahead."""
    future_time = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
    candle = make_valid_candle(
        timestamp=datetime(2024, 1, 3, 12, 0, 0, tzinfo=timezone.utc)
    )
    with pytest.raises(InvalidCandleError):
        CandleValidator.validate_no_look_ahead(candle, future_time)


def test_no_look_ahead_with_valid_time() -> None:
    """AC-04.10: Tests demonstrate absence of look-ahead."""
    reference_time = datetime(2024, 1, 3, 12, 0, 0, tzinfo=timezone.utc)
    candle = make_valid_candle(
        timestamp=datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
    )
    CandleValidator.validate_no_look_ahead(candle, reference_time)


def test_is_closed_policy() -> None:
    """AC-04.05: Closed-candle policy is explicitly enforced."""
    closed_candle = make_valid_candle(is_closed=True)
    open_candle = make_valid_candle(is_closed=False)

    assert CandleValidator.is_closed(closed_candle) is True
    assert CandleValidator.is_closed(open_candle) is False


def test_validate_all_checks() -> None:
    """AC-04.04: Candles pass consistency validation."""
    candle = make_valid_candle()
    reference_time = datetime(2024, 12, 31, tzinfo=timezone.utc)
    CandleValidator.validate(candle, reference_time)
