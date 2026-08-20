"""AEGIS Phase 2 — Market Data Cache.

In-memory TTL cache with provider/symbol/timeframe isolation.
Prevents cross-contamination between assets or timeframes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from aegis.market_data.contracts import Candle


@dataclass
class CacheEntry:
    """Single cache entry with TTL metadata."""

    candles: list[Candle]
    created_at: float = field(default_factory=time.monotonic)
    hit_count: int = 0


class MarketDataCache:
    """In-memory cache for market data candles.

    Keyed by: provider + symbol + timeframe + interval.
    TTL-based expiration.
    No cross-contamination between different keys.
    """

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        """Initialize cache.

        Args:
            ttl_seconds: Time-to-live for cache entries (default 5 minutes).
        """
        self._ttl = ttl_seconds
        self._store: dict[str, CacheEntry] = {}

    @staticmethod
    def _make_key(
        provider: str,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> str:
        """Generate cache key. Isolates by provider/symbol/timeframe/limit."""
        return f"{provider}:{symbol}:{timeframe}:{limit}"

    def get(
        self,
        provider: str,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> list[Candle] | None:
        """Retrieve cached candles if valid (not expired).

        Returns None on miss or expiration.
        """
        key = self._make_key(provider, symbol, timeframe, limit)
        entry = self._store.get(key)
        if entry is None:
            return None

        elapsed = time.monotonic() - entry.created_at
        if elapsed > self._ttl:
            del self._store[key]
            return None

        entry.hit_count += 1
        return entry.candles

    def put(
        self,
        provider: str,
        symbol: str,
        timeframe: str,
        candles: list[Candle],
        limit: int = 100,
    ) -> None:
        """Store candles in cache."""
        key = self._make_key(provider, symbol, timeframe, limit)
        self._store[key] = CacheEntry(candles=candles)

    def invalidate(
        self,
        provider: str,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> bool:
        """Invalidate a specific cache entry. Returns True if removed."""
        key = self._make_key(provider, symbol, timeframe, limit)
        return self._store.pop(key, None) is not None

    def clear(self) -> int:
        """Clear all cache entries. Returns count of entries removed."""
        count = len(self._store)
        self._store.clear()
        return count

    @property
    def size(self) -> int:
        """Current number of cached entries."""
        return len(self._store)

    def stats(self) -> dict[str, Any]:
        """Cache statistics for observability."""
        total_hits = sum(e.hit_count for e in self._store.values())
        return {
            "entries": len(self._store),
            "total_hits": total_hits,
            "ttl_seconds": self._ttl,
        }
