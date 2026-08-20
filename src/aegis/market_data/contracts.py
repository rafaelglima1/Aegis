"""AEGIS Phase 2 — Unified Market Data Contract.

Canonical Candle used by Strategy, Replay, Backtest, and Sandbox.
Single source of truth for OHLCV data across all consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class Candle:
    """Canonical OHLCV candle.

    Identity: symbol + timeframe + timestamp
    Precision: Decimal for all financial fields (no floats).
    Immutability: frozen dataclass.
    """

    symbol: str
    timestamp: datetime
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool = True
    source: str = ""

    @property
    def identity(self) -> tuple[str, str, datetime]:
        """Logical identity for deduplication: symbol + timeframe + timestamp."""
        return (self.symbol, self.timeframe, self.timestamp)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict with string Decimals and ISO timestamp."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "timeframe": self.timeframe,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "is_closed": self.is_closed,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Candle:
        """Construct Candle from dict with string/numeric values.

        Coerces Decimal fields via Decimal(str(...)) for safety.
        Timestamp must be a datetime or ISO string.
        """
        ts = data["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        return cls(
            symbol=str(data.get("symbol", data.get("asset", ""))),
            timestamp=ts,
            timeframe=str(data.get("timeframe", "")),
            open=Decimal(str(data.get("open", "0"))),
            high=Decimal(str(data.get("high", "0"))),
            low=Decimal(str(data.get("low", "0"))),
            close=Decimal(str(data.get("close", "0"))),
            volume=Decimal(str(data.get("volume", "0"))),
            is_closed=bool(data.get("is_closed", True)),
            source=str(data.get("source", "")),
        )

    @classmethod
    def from_raw(
        cls,
        symbol: str,
        timeframe: str,
        timestamp: int | float | str | datetime,
        open: Any,
        high: Any,
        low: Any,
        close: Any,
        volume: Any,
        *,
        is_closed: bool = True,
        source: str = "",
    ) -> Candle:
        """Construct Candle from raw values (API responses, DB rows).

        Handles int timestamps (epoch seconds), string Decimals, etc.
        """
        if isinstance(timestamp, (int, float)):
            ts = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        elif isinstance(timestamp, str):
            ts = datetime.fromisoformat(timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        elif isinstance(timestamp, datetime):
            ts = timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        else:
            raise TypeError(f"Unsupported timestamp type: {type(timestamp)}")

        return cls(
            symbol=symbol,
            timestamp=ts,
            timeframe=timeframe,
            open=Decimal(str(open)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(close)),
            volume=Decimal(str(volume)),
            is_closed=is_closed,
            source=source,
        )
