"""AEGIS candle validation — consistency checks and closed-candle policy."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from aegis.market_data.provider import Candle


class InvalidCandleError(Exception):
    """Raised when candle data is invalid."""

    def __init__(self, message: str, candle: Candle | None = None) -> None:
        self.candle = candle
        super().__init__(message)


class CandleValidator:
    """Validates candle data consistency."""

    @staticmethod
    def validate_ohlcv_consistency(candle: Candle) -> None:
        """AC-04.04: Candles pass consistency validation."""
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
        """AC-04.02: Market timestamps are normalized to UTC."""
        if candle.timestamp.tzinfo is None:
            raise InvalidCandleError(
                "Timestamp must be timezone-aware (UTC)", candle
            )
        if candle.timestamp.tzinfo.utcoffset(candle.timestamp).total_seconds() != 0:
            raise InvalidCandleError(
                "Timestamp must be in UTC", candle
            )

    @staticmethod
    def validate_no_look_ahead(
        candle: Candle,
        reference_time: datetime,
    ) -> None:
        """AC-04.10: Tests demonstrate absence of look-ahead."""
        if candle.timestamp > reference_time:
            raise InvalidCandleError(
                f"Candle timestamp ({candle.timestamp}) is after reference time ({reference_time})",
                candle,
            )

    @classmethod
    def validate(cls, candle: Candle, reference_time: datetime | None = None) -> None:
        """Run all validations on a candle."""
        cls.validate_ohlcv_consistency(candle)
        cls.validate_timestamp_utc(candle)
        if reference_time is not None:
            cls.validate_no_look_ahead(candle, reference_time)

    @staticmethod
    def is_closed(candle: Candle) -> bool:
        """AC-04.05: Closed-candle policy is explicitly enforced."""
        return candle.is_closed
