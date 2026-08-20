"""Tests for AEGIS market data provider and candle."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from aegis.market_data.provider import Candle


def test_candle_creation() -> None:
    """AC-04.01: Market data can be ingested through the defined abstraction."""
    candle = Candle(
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
    assert candle.symbol == "PETR4"
    assert candle.is_closed is True


def test_candle_to_dict() -> None:
    """AC-04.01: Market data can be ingested through the defined abstraction."""
    candle = Candle(
        symbol="PETR4",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        timeframe="1d",
        open=Decimal("50.00"),
        high=Decimal("55.00"),
        low=Decimal("48.00"),
        close=Decimal("52.00"),
        volume=Decimal("1000"),
    )
    d = candle.to_dict()
    assert d["symbol"] == "PETR4"
    assert d["open"] == "50.00"
    assert d["is_closed"] is True
