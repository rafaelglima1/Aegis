"""Tests for AEGIS Market State and Context Builder."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from aegis.market_data.provider import Candle
from aegis.market_data.context_builder import MarketStateBuilder, ContextBuilder
from aegis.market_data.validator import InvalidCandleError


def make_candles(count: int = 5, is_closed: bool = True) -> list[Candle]:
    candles = []
    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(count):
        candles.append(
            Candle(
                asset="PETR4",
                timestamp=base_time,
                timeframe="1d",
                open=Decimal("50.00"),
                high=Decimal("55.00"),
                low=Decimal("48.00"),
                close=Decimal("52.00"),
                volume=Decimal("1000"),
                is_closed=is_closed,
            )
        )
    return candles


def test_market_state_deterministic() -> None:
    """AC-04.06: Market State is deterministic for the same input."""
    builder = MarketStateBuilder()
    candles = make_candles()

    state1 = builder.build("PETR4", candles, "1d")
    state2 = builder.build("PETR4", candles, "1d")

    assert state1.asset == state2.asset
    assert state1.timeframe == state2.timeframe
    assert state1.ohlcv == state2.ohlcv
    assert state1.hash == state2.hash


def test_market_state_requires_candles() -> None:
    """AC-04.06: Market State is deterministic for the same input."""
    builder = MarketStateBuilder()
    with pytest.raises(ValueError):
        builder.build("PETR4", [], "1d")


def test_market_state_rejects_look_ahead() -> None:
    """AC-04.07: Context Builder never uses future information."""
    builder = MarketStateBuilder()
    future_candle = Candle(
        asset="PETR4",
        timestamp=datetime(2099, 1, 1, tzinfo=timezone.utc),
        timeframe="1d",
        open=Decimal("50.00"),
        high=Decimal("55.00"),
        low=Decimal("48.00"),
        close=Decimal("52.00"),
        volume=Decimal("1000"),
    )
    with pytest.raises(InvalidCandleError):
        builder.build("PETR4", [future_candle], "1d")


def test_context_builder_reproducible() -> None:
    """AC-04.08: LLM context is reproducible from the recorded market state."""
    builder = ContextBuilder()
    candles = make_candles()
    ms_builder = MarketStateBuilder()
    market_state = ms_builder.build("PETR4", candles, "1d")

    context1 = builder.build(market_state)
    context2 = builder.build(market_state)

    assert context1 == context2


def test_context_builder_includes_required_fields() -> None:
    """AC-04.08: LLM context is reproducible from the recorded market state."""
    builder = ContextBuilder()
    candles = make_candles()
    ms_builder = MarketStateBuilder()
    market_state = ms_builder.build("PETR4", candles, "1d")

    context = builder.build(market_state)
    assert "asset" in context
    assert "timeframe" in context
    assert "timestamp" in context
    assert "ohlcv" in context
    assert "indicators" in context


def test_prompt_context_is_string() -> None:
    """AC-04.08: LLM context is reproducible from the recorded market state."""
    builder = ContextBuilder()
    candles = make_candles()
    ms_builder = MarketStateBuilder()
    market_state = ms_builder.build("PETR4", candles, "1d")

    prompt = builder.build_prompt_context(market_state)
    assert isinstance(prompt, str)
    assert "PETR4" in prompt
