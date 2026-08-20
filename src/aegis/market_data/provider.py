"""AEGIS market data provider abstraction.

Re-exports canonical Candle from contracts module for backward compatibility.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from aegis.market_data.contracts import Candle


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
