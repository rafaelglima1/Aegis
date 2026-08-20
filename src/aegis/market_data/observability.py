"""AEGIS Phase 2 — Structured Observability for market data.

Provides structured logging for market data operations:
fetch, validate, normalize, cache hit/miss, provider errors.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("aegis.market_data")


@dataclass
class MarketDataMetrics:
    """Accumulated metrics for observability."""

    fetch_count: int = 0
    fetch_errors: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    candles_normalized: int = 0
    candles_validated: int = 0
    candles_rejected: int = 0
    duplicates_detected: int = 0
    gaps_detected: int = 0
    ordering_violations: int = 0
    provider_timeouts: int = 0
    provider_http_errors: int = 0
    provider_malformed: int = 0
    _start_time: float = field(default_factory=time.monotonic)

    def snapshot(self) -> dict[str, Any]:
        """Return current metrics as dict."""
        elapsed = time.monotonic() - self._start_time
        return {
            "fetch_count": self.fetch_count,
            "fetch_errors": self.fetch_errors,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "candles_normalized": self.candles_normalized,
            "candles_validated": self.candles_validated,
            "candles_rejected": self.candles_rejected,
            "duplicates_detected": self.duplicates_detected,
            "gaps_detected": self.gaps_detected,
            "ordering_violations": self.ordering_violations,
            "provider_timeouts": self.provider_timeouts,
            "provider_http_errors": self.provider_http_errors,
            "provider_malformed": self.provider_malformed,
            "elapsed_seconds": round(elapsed, 2),
        }

    def log_snapshot(self) -> None:
        """Log current metrics at INFO level."""
        snap = self.snapshot()
        logger.info("MarketData metrics: %s", snap)


def log_fetch(
    provider: str,
    symbol: str,
    timeframe: str,
    limit: int,
    candle_count: int,
    latency_ms: float,
) -> None:
    """Log a successful market data fetch."""
    logger.info(
        "Fetch OK: provider=%s symbol=%s tf=%s limit=%d candles=%d latency=%.1fms",
        provider,
        symbol,
        timeframe,
        limit,
        candle_count,
        latency_ms,
    )


def log_fetch_error(
    provider: str,
    symbol: str,
    timeframe: str,
    error: Exception,
) -> None:
    """Log a failed market data fetch."""
    logger.warning(
        "Fetch ERROR: provider=%s symbol=%s tf=%s error=%s",
        provider,
        symbol,
        timeframe,
        error,
    )


def log_cache_hit(provider: str, symbol: str, timeframe: str) -> None:
    """Log a cache hit."""
    logger.debug(
        "Cache HIT: provider=%s symbol=%s tf=%s",
        provider,
        symbol,
        timeframe,
    )


def log_cache_miss(provider: str, symbol: str, timeframe: str) -> None:
    """Log a cache miss."""
    logger.debug(
        "Cache MISS: provider=%s symbol=%s tf=%s",
        provider,
        symbol,
        timeframe,
    )


def log_validation_error(candle: Any, error: Exception) -> None:
    """Log a candle validation error."""
    logger.warning(
        "Validation ERROR: symbol=%s ts=%s error=%s",
        getattr(candle, "symbol", "?"),
        getattr(candle, "timestamp", "?"),
        error,
    )
