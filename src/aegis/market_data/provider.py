"""AEGIS market data provider abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Candle:
    """OHLCV candle data."""

    asset: str
    timestamp: datetime
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "timestamp": self.timestamp.isoformat(),
            "timeframe": self.timeframe,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "is_closed": self.is_closed,
        }


class MarketDataProvider(ABC):
    """Abstract market data provider."""

    @abstractmethod
    async def get_candles(
        self,
        asset: str,
        timeframe: str,
        limit: int = 100,
    ) -> list[Candle]:
        """Get historical candles for an asset."""
        ...

    @abstractmethod
    async def get_latest_candle(
        self,
        asset: str,
        timeframe: str,
    ) -> Candle | None:
        """Get the latest candle for an asset."""
        ...

    @abstractmethod
    async def subscribe(
        self,
        asset: str,
        timeframe: str,
        callback: Any,
    ) -> None:
        """Subscribe to real-time market data."""
        ...
