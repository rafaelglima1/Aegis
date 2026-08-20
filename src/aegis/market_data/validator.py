"""AEGIS Phase 2 — Enhanced candle validation.

Validates OHLCV consistency, timestamps, symbol, timeframe,
and batch-level checks (ordering, duplicates, gaps).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from aegis.market_data.contracts import Candle

VALID_TIMEFRAMES = frozenset({"1m", "5m", "15m", "30m", "1h", "4h", "1d"})
TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


class InvalidCandleError(Exception):
    """Raised when candle data is invalid."""

    def __init__(self, message: str, candle: Candle | None = None) -> None:
        self.candle = candle
        super().__init__(message)


class CandleValidationError(Exception):
    """Raised for batch-level validation errors (ordering, gaps, duplicates)."""

    pass


class CandleValidator:
    """Validates individual candle data consistency."""

    @staticmethod
    def validate_positive_prices(candle: Candle) -> None:
        """AC2: OHLCV Validation — all prices must be positive."""
        for field_name in ("open", "high", "low", "close"):
            value = getattr(candle, field_name)
            if value <= Decimal("0"):
                raise InvalidCandleError(
                    f"{field_name} must be > 0, got {value}", candle
                )

    @staticmethod
    def validate_ohlcv_consistency(candle: Candle) -> None:
        """AC2: OHLCV Validation — high >= max(open, close), low <= min(open, close)."""
        if candle.high < candle.low:
            raise InvalidCandleError(
                f"High ({candle.high}) < Low ({candle.low})", candle
            )

        if candle.open < candle.low or candle.open > candle.high:
            raise InvalidCandleError(
                f"Open ({candle.open}) outside High/Low range", candle
            )

        if candle.close < candle.low or candle.close > candle.high:
            raise InvalidCandleError(
                f"Close ({candle.close}) outside High/Low range", candle
            )

        if candle.volume < Decimal("0"):
            raise InvalidCandleError(
                f"Negative volume ({candle.volume})", candle
            )

    @staticmethod
    def validate_timestamp_utc(candle: Candle) -> None:
        """AC3: Timestamp Validation — timezone-aware UTC."""
        if candle.timestamp.tzinfo is None:
            raise InvalidCandleError(
                "Timestamp must be timezone-aware (UTC)", candle
            )
        if candle.timestamp.tzinfo.utcoffset(candle.timestamp).total_seconds() != 0:
            raise InvalidCandleError(
                "Timestamp must be in UTC", candle
            )

    @staticmethod
    def validate_no_future_timestamp(
        candle: Candle,
        reference_time: datetime | None = None,
    ) -> None:
        """AC3: Timestamp Validation — no future timestamps."""
        ref = reference_time or datetime.now(timezone.utc)
        if candle.timestamp > ref:
            raise InvalidCandleError(
                f"Candle timestamp ({candle.timestamp}) is in the future "
                f"(reference: {ref})",
                candle,
            )

    @staticmethod
    def validate_no_look_ahead(
        candle: Candle,
        reference_time: datetime,
    ) -> None:
        """Tests demonstrate absence of look-ahead."""
        if candle.timestamp > reference_time:
            raise InvalidCandleError(
                f"Candle timestamp ({candle.timestamp}) is after reference time ({reference_time})",
                candle,
            )

    @staticmethod
    def validate_symbol(candle: Candle) -> None:
        """AC7: Symbol Validation — non-empty symbol."""
        if not candle.symbol or not candle.symbol.strip():
            raise InvalidCandleError("Symbol must be non-empty", candle)

    @staticmethod
    def validate_timeframe(candle: Candle) -> None:
        """AC8: Timeframe Integrity — valid timeframe string."""
        if candle.timeframe not in VALID_TIMEFRAMES:
            raise InvalidCandleError(
                f"Invalid timeframe '{candle.timeframe}'. "
                f"Must be one of: {sorted(VALID_TIMEFRAMES)}",
                candle,
            )

    @classmethod
    def validate(cls, candle: Candle, reference_time: datetime | None = None) -> None:
        """Run all validations on a single candle."""
        cls.validate_positive_prices(candle)
        cls.validate_ohlcv_consistency(candle)
        cls.validate_timestamp_utc(candle)
        cls.validate_no_future_timestamp(candle, reference_time)
        cls.validate_symbol(candle)
        cls.validate_timeframe(candle)
        if reference_time is not None:
            cls.validate_no_look_ahead(candle, reference_time)

    @staticmethod
    def is_closed(candle: Candle) -> bool:
        """Closed-candle policy is explicitly enforced."""
        return candle.is_closed


class BatchValidator:
    """Validates batch-level properties of candle sequences."""

    @staticmethod
    def validate_duplicates(candles: Sequence[Candle]) -> list[tuple[int, int]]:
        """AC4: Duplicate Detection — find candles with same identity.

        Returns list of (index_a, index_b) pairs of duplicates.
        Identity: symbol + timeframe + timestamp.
        """
        seen: dict[tuple[str, str, datetime], int] = {}
        duplicates: list[tuple[int, int]] = []
        for i, candle in enumerate(candles):
            key = candle.identity
            if key in seen:
                duplicates.append((seen[key], i))
            else:
                seen[key] = i
        return duplicates

    @staticmethod
    def validate_ordering(candles: Sequence[Candle]) -> list[int]:
        """AC5: Ordering Validation — find out-of-order candles.

        Returns list of indices where candle[i].timestamp < candle[i-1].timestamp.
        """
        out_of_order: list[int] = []
        for i in range(1, len(candles)):
            if candles[i].timestamp < candles[i - 1].timestamp:
                out_of_order.append(i)
        return out_of_order

    @staticmethod
    def validate_gaps(
        candles: Sequence[Candle],
    ) -> list[tuple[datetime, datetime]]:
        """AC6: Gap Detection — find missing candles in time sequence.

        Returns list of (expected_timestamp, next_timestamp) for each gap.
        Gap is detected when the difference between consecutive timestamps
        exceeds one timeframe step.
        """
        if len(candles) < 2:
            return []

        tf = candles[0].timeframe
        step_seconds = TIMEFRAME_SECONDS.get(tf)
        if step_seconds is None:
            return []

        step = timedelta(seconds=step_seconds)
        gaps: list[tuple[datetime, datetime]] = []
        for i in range(1, len(candles)):
            expected = candles[i - 1].timestamp + step
            if candles[i].timestamp > expected:
                gaps.append((expected, candles[i].timestamp))
        return gaps
