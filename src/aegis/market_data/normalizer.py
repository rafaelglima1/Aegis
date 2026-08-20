"""AEGIS Phase 2 — Market Data Normalizer.

Converts raw API responses to canonical Candle instances.
Handles symbol normalization and timeframe validation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from aegis.market_data.contracts import Candle
from aegis.market_data.validator import VALID_TIMEFRAMES

# Canonical symbol format: UPPER-CASE, dash-separated (e.g., "BTC-BRL")
_SYMBOL_ALIASES: dict[str, str] = {
    "btcbrl": "BTC-BRL",
    "btc brl": "BTC-BRL",
    "btc_brl": "BTC-BRL",
    "ethbrl": "ETH-BRL",
    "eth brl": "ETH-BRL",
    "eth_brl": "ETH-BRL",
    "solbrl": "SOL-BRL",
    "sol brl": "SOL-BRL",
    "sol_brl": "SOL-BRL",
}


def normalize_symbol(symbol: str) -> str:
    """AC7: Symbol Normalization — canonical format.

    Converts to upper-case, resolves aliases, ensures dash-separator.
    """
    cleaned = symbol.strip().upper()
    lower = symbol.strip().lower()
    if lower in _SYMBOL_ALIASES:
        return _SYMBOL_ALIASES[lower]
    if "-" not in cleaned:
        # Try common patterns: BTCBRL -> BTC-BRL
        for quote in ("BRL", "USDT", "USD", "BTC", "ETH"):
            if cleaned.endswith(quote) and len(cleaned) > len(quote):
                base = cleaned[: -len(quote)]
                return f"{base}-{quote}"
    return cleaned


def normalize_timeframe(timeframe: str) -> str:
    """AC8: Timeframe Normalization — validate and return canonical form."""
    tf = timeframe.strip().lower()
    if tf not in VALID_TIMEFRAMES:
        raise ValueError(
            f"Invalid timeframe '{timeframe}'. Must be one of: {sorted(VALID_TIMEFRAMES)}"
        )
    return tf


class MarketDataNormalizer:
    """Converts raw API data to canonical Candle instances."""

    def normalize_candle(
        self,
        raw: dict[str, Any],
        symbol: str,
        timeframe: str,
        *,
        source: str = "",
    ) -> Candle:
        """Normalize a single raw candle dict to canonical Candle.

        Handles:
        - Epoch int timestamps -> datetime UTC
        - String Decimals -> Decimal
        - Symbol normalization
        - Timeframe validation
        """
        norm_symbol = normalize_symbol(symbol)
        norm_timeframe = normalize_timeframe(timeframe)

        ts = raw.get("timestamp") or raw.get("time") or raw.get("t")
        if ts is None:
            raise ValueError("Missing timestamp in candle data")

        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif isinstance(ts, str):
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        elif isinstance(ts, datetime):
            dt = ts
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            raise TypeError(f"Unsupported timestamp type: {type(ts)}")

        return Candle(
            symbol=norm_symbol,
            timestamp=dt,
            timeframe=norm_timeframe,
            open=Decimal(str(raw.get("open", "0"))),
            high=Decimal(str(raw.get("high", "0"))),
            low=Decimal(str(raw.get("low", "0"))),
            close=Decimal(str(raw.get("close", "0"))),
            volume=Decimal(str(raw.get("volume", "0"))),
            is_closed=bool(raw.get("is_closed", True)),
            source=source,
        )

    def normalize_candles(
        self,
        raw_candles: list[dict[str, Any]],
        symbol: str,
        timeframe: str,
        *,
        source: str = "",
    ) -> list[Candle]:
        """Normalize a list of raw candle dicts to canonical Candles."""
        return [
            self.normalize_candle(c, symbol, timeframe, source=source)
            for c in raw_candles
        ]
