"""AEGIS Phase 3 — Multi-Timeframe Intelligence Engine.

Deterministic MTF analysis for swing trading context.
Analyses 1D/4H/1H/15M independently, then aggregates into directional bias.
No LLM, no randomness, no I/O — pure computation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from aegis.market_data.contracts import Candle
from aegis.market_data.validator import (
    BatchValidator,
    CandleValidator,
    CandleValidationError,
    TIMEFRAME_SECONDS,
    VALID_TIMEFRAMES,
)

logger = logging.getLogger("aegis.mtf")


# ============================================================
# Weights
# ============================================================


@dataclass(frozen=True)
class MTFWeights:
    """Configurable weights for timeframe aggregation.

    Each weight represents the relative importance of a timeframe.
    Total need not sum to 100 — normalization happens internally.
    """

    macro: Decimal = Decimal("35")    # 1D — macro context
    trend: Decimal = Decimal("30")    # 4H — main trend
    setup: Decimal = Decimal("25")    # 1H — setup
    timing: Decimal = Decimal("10")   # 15M — timing

    @property
    def total(self) -> Decimal:
        return self.macro + self.trend + self.setup + self.timing

    def normalized(self) -> dict[str, Decimal]:
        """Return weights normalized to sum=1."""
        t = self.total
        return {
            "macro": self.macro / t,
            "trend": self.trend / t,
            "setup": self.setup / t,
            "timing": self.timing / t,
        }


# ============================================================
# Default configuration
# ============================================================


DEFAULT_MTF_CONFIG: dict[str, str] = {
    "macro_timeframe": "1d",
    "trend_timeframe": "4h",
    "setup_timeframe": "1h",
    "timing_timeframe": "15m",
}

MTF_ROLE_MAP: dict[str, str] = {
    "1d": "macro",
    "4h": "trend",
    "1h": "setup",
    "15m": "timing",
}


# ============================================================
# Results
# ============================================================


@dataclass
class TimeframeResult:
    """Result of analysing a single timeframe."""

    timeframe: str
    role: str  # macro, trend, setup, timing
    bias: str  # BULLISH, NEUTRAL, BEARISH
    score: int  # 0-100
    confidence: Decimal  # 0.0-1.0
    timestamp: datetime | None = None
    candle_timestamp: datetime | None = None
    data_quality: str = "VALID"  # VALID, DEGRADED, INSUFFICIENT_DATA, INVALID
    reasons: list[str] = field(default_factory=list)
    candle_count: int = 0
    gaps_detected: int = 0
    duplicates_detected: int = 0


@dataclass
class MTFResult:
    """Aggregated multi-timeframe result."""

    symbol: str
    reference_time: datetime
    bias: str  # LONG_BIAS, NEUTRAL, SHORT_BIAS
    score: int  # 0-100 weighted
    confidence: Decimal  # weighted average
    conflict: bool  # True when timeframes disagree strongly
    data_quality: str  # worst quality across timeframes
    timeframes: list[TimeframeResult] = field(default_factory=list)
    weights: MTFWeights = field(default_factory=MTFWeights)
    reasons: list[str] = field(default_factory=list)


# ============================================================
# Timeframe Analyzer
# ============================================================


class TimeframeAnalyzer:
    """Analyses candles for a single timeframe.

    Produces a deterministic TimeframeResult with bias, score,
    confidence, and data quality.
    """

    def analyze(
        self,
        candles: list[Candle],
        reference_time: datetime,
        *,
        timeframe: str = "",
        role: str = "",
    ) -> TimeframeResult:
        """Analyse a list of candles for one timeframe.

        Steps:
        1. Filter out future candles (timestamp > reference_time)
        2. Filter out open candles (is_closed == False)
        3. Check minimum candle count
        4. Run batch validation (duplicates, gaps)
        5. Compute score and bias from closed candles
        """
        tf = timeframe or (candles[0].timeframe if candles else "")
        r = role or MTF_ROLE_MAP.get(tf, "")

        if not candles:
            return TimeframeResult(
                timeframe=tf, role=r, bias="NEUTRAL", score=50,
                confidence=Decimal("0"), data_quality="INSUFFICIENT_DATA",
                reasons=["No candles provided"],
            )

        # Step 1: Filter future candles
        valid_candles = [c for c in candles if c.timestamp <= reference_time]
        excluded_future = len(candles) - len(valid_candles)

        # Step 2: Filter open candles
        closed_candles = [c for c in valid_candles if c.is_closed]
        excluded_open = len(valid_candles) - len(closed_candles)

        # Step 3: Check minimum
        if len(closed_candles) < 2:
            return TimeframeResult(
                timeframe=tf, role=r, bias="NEUTRAL", score=50,
                confidence=Decimal("0"), data_quality="INSUFFICIENT_DATA",
                candle_count=len(closed_candles),
                reasons=[f"Insufficient closed candles: {len(closed_candles)} (need >= 2)"],
            )

        # Step 4: Batch validation
        dups = BatchValidator.validate_duplicates(closed_candles)
        gaps = BatchValidator.validate_gaps(closed_candles)
        ordering = BatchValidator.validate_ordering(closed_candles)

        reasons: list[str] = []
        data_quality = "VALID"

        if dups:
            data_quality = "DEGRADED"
            reasons.append(f"{len(dups)} duplicate(s) detected")
        if gaps:
            data_quality = "DEGRADED"
            reasons.append(f"{len(gaps)} gap(s) detected")
        if ordering:
            data_quality = "DEGRADED"
            reasons.append(f"{len(ordering)} out-of-order candle(s)")
        if excluded_future > 0:
            reasons.append(f"{excluded_future} future candle(s) excluded")
        if excluded_open > 0:
            reasons.append(f"{excluded_open} open candle(s) excluded")

        # Step 5: Compute score and bias
        score, bias, score_reasons = self._compute_score(closed_candles)
        reasons.extend(score_reasons)

        # Confidence: based on data completeness
        confidence = self._compute_confidence(
            len(closed_candles), len(gaps), len(dups),
        )

        return TimeframeResult(
            timeframe=tf,
            role=r,
            bias=bias,
            score=score,
            confidence=confidence,
            timestamp=reference_time,
            candle_timestamp=closed_candles[-1].timestamp,
            data_quality=data_quality,
            reasons=reasons,
            candle_count=len(closed_candles),
            gaps_detected=len(gaps),
            duplicates_detected=len(dups),
        )

    def _compute_score(
        self, candles: list[Candle],
    ) -> tuple[int, str, list[str]]:
        """Compute deterministic score from closed candles.

        Returns (score, bias, reasons).
        Score 0-100:
          - 0-39: BEARISH
          - 40-60: NEUTRAL
          - 61-100: BULLISH
        """
        if len(candles) < 2:
            return 50, "NEUTRAL", ["Too few candles for scoring"]

        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        # Component 1: Trend (close direction over period)
        trend_raw = self._score_trend(closes)

        # Component 2: Momentum (recent vs earlier closes)
        momentum_raw = self._score_momentum(closes)

        # Component 3: Volume trend
        volume_raw = self._score_volume(volumes)

        # Component 4: Price position within range
        position_raw = self._score_price_position(closes, highs, lows)

        # Component 5: Volatility
        volatility_raw = self._score_volatility(closes)

        # Weighted average (each component equally weighted)
        components = [trend_raw, momentum_raw, volume_raw, position_raw, volatility_raw]
        avg = sum(components) / Decimal(str(len(components)))

        # Map [-0.5, 1.0] to [0, 100]
        # -0.5 -> 0, 0 -> 50, 1.0 -> 100
        score = int(((avg + Decimal("0.5")) / Decimal("1.5") * Decimal("100")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        ))
        score = max(0, min(100, score))

        # Classify bias
        if score > 60:
            bias = "BULLISH"
        elif score < 40:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        reasons = [
            f"trend={trend_raw:+.2f}",
            f"momentum={momentum_raw:+.2f}",
            f"volume={volume_raw:+.2f}",
            f"position={position_raw:+.2f}",
            f"volatility={volatility_raw:+.2f}",
        ]

        return score, bias, reasons

    def _score_trend(self, closes: list[Decimal]) -> Decimal:
        """Score based on overall close direction."""
        if len(closes) < 2:
            return Decimal("0")
        first = closes[0]
        last = closes[-1]
        if first == 0:
            return Decimal("0")
        pct_change = (last - first) / first * Decimal("100")
        if pct_change > Decimal("5"):
            return Decimal("1")
        elif pct_change > Decimal("2"):
            return Decimal("0.7")
        elif pct_change > Decimal("0"):
            return Decimal("0.3")
        elif pct_change > Decimal("-2"):
            return Decimal("-0.2")
        elif pct_change > Decimal("-5"):
            return Decimal("-0.4")
        else:
            return Decimal("-0.5")

    def _score_momentum(self, closes: list[Decimal]) -> Decimal:
        """Score based on recent momentum vs earlier."""
        if len(closes) < 10:
            return Decimal("0")
        mid = len(closes) // 2
        first_half_avg = sum(closes[:mid]) / Decimal(str(mid))
        second_half_avg = sum(closes[mid:]) / Decimal(str(len(closes) - mid))
        if first_half_avg == 0:
            return Decimal("0")
        momentum = (second_half_avg - first_half_avg) / first_half_avg * Decimal("100")
        if momentum > Decimal("3"):
            return Decimal("1")
        elif momentum > Decimal("1"):
            return Decimal("0.6")
        elif momentum > Decimal("0"):
            return Decimal("0.2")
        elif momentum > Decimal("-1"):
            return Decimal("-0.1")
        elif momentum > Decimal("-3"):
            return Decimal("-0.4")
        else:
            return Decimal("-0.5")

    def _score_volume(self, volumes: list[Decimal]) -> Decimal:
        """Score based on volume trend."""
        if len(volumes) < 10:
            return Decimal("0")
        mid = len(volumes) // 2
        first_half_avg = sum(volumes[:mid]) / Decimal(str(mid))
        second_half_avg = sum(volumes[mid:]) / Decimal(str(len(volumes) - mid))
        if first_half_avg == 0:
            return Decimal("0")
        vol_ratio = second_half_avg / first_half_avg
        if vol_ratio > Decimal("1.5"):
            return Decimal("0.8")  # increasing volume — confirms trend
        elif vol_ratio > Decimal("1.1"):
            return Decimal("0.4")
        elif vol_ratio > Decimal("0.9"):
            return Decimal("0")
        elif vol_ratio > Decimal("0.5"):
            return Decimal("-0.2")
        else:
            return Decimal("-0.4")  # declining volume — weakens trend

    def _score_price_position(
        self, closes: list[Decimal], highs: list[Decimal], lows: list[Decimal],
    ) -> Decimal:
        """Score based on where current price sits in the range."""
        if not highs or not lows:
            return Decimal("0")
        period_high = max(highs)
        period_low = min(lows)
        if period_high == period_low:
            return Decimal("0")
        current = closes[-1]
        pct = (current - period_low) / (period_high - period_low)
        if pct > Decimal("0.8"):
            return Decimal("0.7")  # near top — bullish
        elif pct > Decimal("0.6"):
            return Decimal("0.3")
        elif pct > Decimal("0.4"):
            return Decimal("0")
        elif pct > Decimal("0.2"):
            return Decimal("-0.3")
        else:
            return Decimal("-0.5")  # near bottom — bearish

    def _score_volatility(self, closes: list[Decimal]) -> Decimal:
        """Score based on volatility (lower is better for swing)."""
        if len(closes) < 5:
            return Decimal("0")
        returns = []
        for i in range(1, len(closes)):
            if closes[i - 1] != 0:
                r = (closes[i] - closes[i - 1]) / closes[i - 1]
                returns.append(r)
        if not returns:
            return Decimal("0")
        avg = sum(returns) / Decimal(str(len(returns)))
        variance = sum((r - avg) ** 2 for r in returns) / Decimal(str(len(returns)))
        # Approximate std dev
        vol = variance ** Decimal("0.5")
        if vol < Decimal("0.01"):
            return Decimal("0.5")  # low vol — good for swing
        elif vol < Decimal("0.03"):
            return Decimal("0.3")
        elif vol < Decimal("0.05"):
            return Decimal("0")
        elif vol < Decimal("0.10"):
            return Decimal("-0.3")
        else:
            return Decimal("-0.5")  # high vol — risky

    def _compute_confidence(
        self, candle_count: int, gaps: int, duplicates: int,
    ) -> Decimal:
        """Compute confidence based on data completeness."""
        # Start at 1.0, reduce for issues
        conf = Decimal("1.0")

        # Penalize few candles
        if candle_count < 10:
            conf *= Decimal("0.7")
        elif candle_count < 20:
            conf *= Decimal("0.85")

        # Penalize gaps
        if gaps > 0:
            conf *= max(Decimal("0.5"), Decimal("1.0") - Decimal(str(gaps)) * Decimal("0.1"))

        # Penalize duplicates
        if duplicates > 0:
            conf *= max(Decimal("0.7"), Decimal("1.0") - Decimal(str(duplicates)) * Decimal("0.05"))

        return conf.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ============================================================
# MTF Engine
# ============================================================


class MTFEngine:
    """Multi-Timeframe Intelligence Engine.

    Analyses a symbol across multiple timeframes (1D, 4H, 1H, 15M),
    then aggregates into a directional bias.

    Pure computation — no I/O, no LLM, no randomness.
    Uses MarketDataCache from Phase 2.
    """

    def __init__(
        self,
        weights: MTFWeights | None = None,
        config: dict[str, str] | None = None,
    ) -> None:
        self._weights = weights or MTFWeights()
        self._config = config or DEFAULT_MTF_CONFIG.copy()
        self._analyzer = TimeframeAnalyzer()

    @property
    def weights(self) -> MTFWeights:
        return self._weights

    @property
    def config(self) -> dict[str, str]:
        return self._config.copy()

    def analyze(
        self,
        symbol: str,
        candles_by_timeframe: dict[str, list[Candle]],
        reference_time: datetime,
    ) -> MTFResult:
        """Run full MTF analysis for a symbol.

        Args:
            symbol: Trading pair (e.g., "BTC-BRL")
            candles_by_timeframe: Dict mapping timeframe -> list of candles
            reference_time: Analysis cutoff time (no future candles)

        Returns:
            MTFResult with aggregated bias, score, confidence, conflict flag.
        """
        tf_results: list[TimeframeResult] = []
        reasons: list[str] = []

        for role, tf in self._config.items():
            role_name = role.replace("_timeframe", "")
            candles = candles_by_timeframe.get(tf, [])

            result = self._analyzer.analyze(
                candles, reference_time,
                timeframe=tf, role=role_name,
            )
            tf_results.append(result)

        # Aggregate
        bias, score, confidence, conflict, agg_reasons = self._aggregate(tf_results)
        reasons.extend(agg_reasons)

        # Worst data quality
        quality_order = {"VALID": 0, "DEGRADED": 1, "INSUFFICIENT_DATA": 2, "INVALID": 3}
        worst_quality = "VALID"
        for r in tf_results:
            if quality_order.get(r.data_quality, 3) > quality_order.get(worst_quality, 0):
                worst_quality = r.data_quality

        return MTFResult(
            symbol=symbol,
            reference_time=reference_time,
            bias=bias,
            score=score,
            confidence=confidence,
            conflict=conflict,
            data_quality=worst_quality,
            timeframes=tf_results,
            weights=self._weights,
            reasons=reasons,
        )

    def _aggregate(
        self, results: list[TimeframeResult],
    ) -> tuple[str, int, Decimal, bool, list[str]]:
        """Aggregate timeframe results into final MTF bias.

        Returns (bias, score, confidence, conflict, reasons).
        """
        reasons: list[str] = []
        weights_norm = self._weights.normalized()

        role_weight_map = {
            "macro": weights_norm["macro"],
            "trend": weights_norm["trend"],
            "setup": weights_norm["setup"],
            "timing": weights_norm["timing"],
        }

        # Weighted score
        weighted_score = Decimal("0")
        weighted_confidence = Decimal("0")
        total_weight = Decimal("0")

        bias_values: list[str] = []

        for r in results:
            w = role_weight_map.get(r.role, Decimal("0"))
            if r.data_quality == "INSUFFICIENT_DATA":
                reasons.append(f"{r.role} ({r.timeframe}): INSUFFICIENT_DATA — excluded from aggregation")
                continue

            weighted_score += Decimal(str(r.score)) * w
            weighted_confidence += r.confidence * w
            total_weight += w
            bias_values.append(r.bias)

        if total_weight == 0:
            return "NEUTRAL", 50, Decimal("0"), False, ["No valid timeframe data"]

        final_score = int((weighted_score / total_weight).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        ))
        final_confidence = (weighted_confidence / total_weight).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Detect conflict
        conflict = self._detect_conflict(results)

        # Determine bias considering conflict protection
        bias = self._determine_bias(results, conflict, final_score)

        if conflict:
            reasons.append("Conflict detected between timeframes")

        return bias, final_score, final_confidence, conflict, reasons

    def _detect_conflict(self, results: list[TimeframeResult]) -> bool:
        """Detect strong disagreement between timeframes.

        Conflict exists when:
        - Higher timeframes (1D, 4H) and lower timeframes (1H, 15M) disagree
        - AND the disagreement involves opposing biases (not just NEUTRAL)
        """
        valid = [r for r in results if r.data_quality != "INSUFFICIENT_DATA"]
        if len(valid) < 2:
            return False

        higher = [r for r in valid if r.role in ("macro", "trend")]
        lower = [r for r in valid if r.role in ("setup", "timing")]

        if not higher or not lower:
            return False

        higher_biases = {r.bias for r in higher}
        lower_biases = {r.bias for r in lower}

        # Conflict: higher and lower have opposing directional biases
        # e.g., higher={BULLISH} and lower={BEARISH}, or vice versa
        bullish_higher = "BULLISH" in higher_biases
        bearish_higher = "BEARISH" in higher_biases
        bullish_lower = "BULLISH" in lower_biases
        bearish_lower = "BEARISH" in lower_biases

        return (bullish_higher and bearish_lower) or (bearish_higher and bullish_lower)

    def _determine_bias(
        self,
        results: list[TimeframeResult],
        conflict: bool,
        final_score: int,
    ) -> str:
        """Determine final bias with higher-timeframe protection.

        If conflict exists, bias is NEUTRAL unless higher timeframes
        are unanimously in one direction.
        """
        valid = [r for r in results if r.data_quality != "INSUFFICIENT_DATA"]
        higher = [r for r in valid if r.role in ("macro", "trend")]

        if conflict:
            # Higher timeframe protection: if higher TFs agree, their bias wins
            higher_biases = [r.bias for r in higher]
            if len(higher_biases) >= 2 and all(b == higher_biases[0] for b in higher_biases):
                return higher_biases[0]  # Higher TFs override conflict
            return "NEUTRAL"

        # No conflict: use score-based classification
        if final_score > 60:
            return "LONG_BIAS"
        elif final_score < 40:
            return "SHORT_BIAS"
        else:
            return "NEUTRAL"
