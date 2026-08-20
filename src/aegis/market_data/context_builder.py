"""AEGIS Market State and Context Builder."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import uuid4

from aegis.domain.contracts import MarketState, utc_now
from aegis.market_data.contracts import Candle
from aegis.market_data.validator import CandleValidator, InvalidCandleError


class MarketStateBuilder:
    """Builds deterministic MarketState from candles."""

    def build(
        self,
        asset: str,
        candles: list[Candle],
        timeframe: str,
        source: str = "unknown",
    ) -> MarketState:
        """AC-04.06: Market State is deterministic for the same input."""
        if not candles:
            raise ValueError("No candles provided")

        latest = candles[-1]
        reference_time = utc_now()

        for candle in candles:
            CandleValidator.validate(candle, reference_time)

        ohlcv = self._compute_ohlcv_summary(candles)
        indicators = self._compute_basic_indicators(candles)
        hash_value = self._compute_hash(candles, asset, timeframe)

        return MarketState(
            market_state_id=uuid4(),
            asset=asset,
            timestamp=latest.timestamp,
            timeframe=timeframe,
            ohlcv=ohlcv,
            indicators=indicators,
            market_context={"candle_count": len(candles)},
            data_quality="GOOD",
            source=source,
            hash=hash_value,
        )

    def _compute_ohlcv_summary(self, candles: list[Candle]) -> dict[str, Any]:
        return {
            "open": str(candles[0].open),
            "high": str(max(c.high for c in candles)),
            "low": str(min(c.low for c in candles)),
            "close": str(candles[-1].close),
            "volume": str(sum(c.volume for c in candles)),
        }

    def _compute_basic_indicators(self, candles: list[Candle]) -> dict[str, Any]:
        closes = [c.close for c in candles]
        if len(closes) >= 2:
            sma_short = sum(closes[-5:]) / min(5, len(closes[-5:]))
            sma_long = sum(closes[-10:]) / min(10, len(closes[-10:]))
            return {
                "sma_short": str(sma_short),
                "sma_long": str(sma_long),
                "momentum": str(closes[-1] - closes[0]),
            }
        return {}

    def _compute_hash(
        self, candles: list[Candle], asset: str, timeframe: str
    ) -> str:
        data = f"{asset}:{timeframe}:{[c.to_dict() for c in candles]}"
        return sha256(data.encode()).hexdigest()


class ContextBuilder:
    """Builds LLM context from MarketState."""

    def build(
        self,
        market_state: MarketState,
        additional_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """AC-04.08: LLM context is reproducible from the recorded market state."""
        context: dict[str, Any] = {
            "asset": market_state.asset,
            "timeframe": market_state.timeframe,
            "timestamp": market_state.timestamp.isoformat(),
            "ohlcv": market_state.ohlcv,
            "indicators": market_state.indicators,
            "data_quality": market_state.data_quality,
            "source": market_state.source,
        }

        if additional_context:
            context.update(additional_context)

        return context

    def build_prompt_context(
        self,
        market_state: MarketState,
    ) -> str:
        """Build a string context for LLM prompts."""
        return (
            f"Asset: {market_state.asset}\n"
            f"Timeframe: {market_state.timeframe}\n"
            f"Timestamp: {market_state.timestamp.isoformat()}\n"
            f"OHLCV: {market_state.ohlcv}\n"
            f"Indicators: {market_state.indicators}\n"
        )
