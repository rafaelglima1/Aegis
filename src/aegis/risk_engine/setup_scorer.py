"""AEGIS Setup Scorer — deterministic setup quality scoring.

Calculates a 0-100 score based on technical indicators.
Score is computed by the engine, NOT by the LLM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

logger = logging.getLogger("aegis.setup_scorer")


@dataclass(frozen=True)
class SetupWeights:
    """Configurable weights for setup score components."""

    trend: Decimal = Decimal("20")       # Trend alignment
    price_sma20: Decimal = Decimal("10") # Price vs SMA20
    price_sma50: Decimal = Decimal("10") # Price vs SMA50
    sma_alignment: Decimal = Decimal("10") # SMA20 vs SMA50
    rsi: Decimal = Decimal("15")         # RSI signal
    momentum: Decimal = Decimal("10")    # Momentum
    volume: Decimal = Decimal("10")      # Volume trend
    volatility: Decimal = Decimal("5")   # Volatility
    structure: Decimal = Decimal("10")   # Price structure
    rr: Decimal = Decimal("10")          # Risk/Reward


@dataclass
class SetupResult:
    """Result of setup scoring."""

    score: int = 0
    trend_score: int = 0
    price_sma20_score: int = 0
    price_sma50_score: int = 0
    sma_alignment_score: int = 0
    rsi_score: int = 0
    momentum_score: int = 0
    volume_score: int = 0
    volatility_score: int = 0
    structure_score: int = 0
    rr_score: int = 0
    reasons: list[str] = field(default_factory=list)
    market_regime: str = "NEUTRAL"


class SetupScorer:
    """Deterministic setup quality scorer.

    Combines multiple technical indicators into a single 0-100 score.
    Each component contributes based on configurable weights.
    Missing data is handled gracefully — score is reduced, not zeroed.
    """

    def __init__(self, weights: SetupWeights | None = None) -> None:
        self._weights = weights or SetupWeights()

    def score(self, market_state: dict[str, Any],
              decision_rr: Decimal | None = None) -> SetupResult:
        """Calculate setup score from market state and decision R/R.

        Args:
            market_state: Dict with indicators from _build_market_state()
            decision_rr: Calculated R/R ratio from decision (optional)

        Returns:
            SetupResult with score 0-100 and component breakdown
        """
        result = SetupResult()

        total_weight = sum([
            self._weights.trend,
            self._weights.price_sma20,
            self._weights.price_sma50,
            self._weights.sma_alignment,
            self._weights.rsi,
            self._weights.momentum,
            self._weights.volume,
            self._weights.volatility,
            self._weights.structure,
            self._weights.rr,
        ])

        if total_weight <= 0:
            return result

        # Trend score (0-1)
        trend_raw = self._score_trend(market_state)
        result.trend_score = int(trend_raw * self._weights.trend / total_weight * 100)
        result.reasons.extend(trend_raw.reasons if hasattr(trend_raw, 'reasons') else [])

        # Price vs SMA20 (0-1)
        price_sma20_raw = self._score_price_sma20(market_state)
        result.price_sma20_score = int(price_sma20_raw * self._weights.price_sma20 / total_weight * 100)

        # Price vs SMA50 (0-1)
        price_sma50_raw = self._score_price_sma50(market_state)
        result.price_sma50_score = int(price_sma50_raw * self._weights.price_sma50 / total_weight * 100)

        # SMA alignment (0-1)
        sma_align_raw = self._score_sma_alignment(market_state)
        result.sma_alignment_score = int(sma_align_raw * self._weights.sma_alignment / total_weight * 100)

        # RSI (0-1)
        rsi_raw = self._score_rsi(market_state)
        result.rsi_score = int(rsi_raw * self._weights.rsi / total_weight * 100)

        # Momentum (0-1)
        momentum_raw = self._score_momentum(market_state)
        result.momentum_score = int(momentum_raw * self._weights.momentum / total_weight * 100)

        # Volume (0-1)
        volume_raw = self._score_volume(market_state)
        result.volume_score = int(volume_raw * self._weights.volume / total_weight * 100)

        # Volatility (0-1)
        vol_raw = self._score_volatility(market_state)
        result.volatility_score = int(vol_raw * self._weights.volatility / total_weight * 100)

        # Structure (0-1)
        struct_raw = self._score_structure(market_state)
        result.structure_score = int(struct_raw * self._weights.structure / total_weight * 100)

        # R/R (0-1)
        rr_raw = self._score_rr(decision_rr)
        result.rr_score = int(rr_raw * self._weights.rr / total_weight * 100)

        # Sum component scores
        result.score = (
            result.trend_score
            + result.price_sma20_score
            + result.price_sma50_score
            + result.sma_alignment_score
            + result.rsi_score
            + result.momentum_score
            + result.volume_score
            + result.volatility_score
            + result.structure_score
            + result.rr_score
        )

        # Clamp to 0-100
        result.score = max(0, min(100, result.score))

        # Market regime
        result.market_regime = self._classify_regime(market_state)

        return result

    def _score_trend(self, state: dict[str, Any]) -> Decimal:
        """Score trend alignment. +1 for BULLISH, 0 for NEUTRAL, -1 for BEARISH."""
        trend = state.get("trend", "NEUTRAL")
        if trend == "BULLISH":
            return Decimal("1")
        elif trend == "BEARISH":
            return Decimal("-0.5")  # Penalty, not just zero
        return Decimal("0")

    def _score_price_sma20(self, state: dict[str, Any]) -> Decimal:
        """Score price position relative to SMA20."""
        try:
            price = Decimal(str(state.get("current_price", "0")))
            sma20 = Decimal(str(state.get("sma_20", "0")))
            if sma20 <= 0 or price <= 0:
                return Decimal("0")
            if price > sma20:
                return Decimal("1")
            elif price < sma20:
                return Decimal("-0.3")
            return Decimal("0.5")
        except Exception:
            return Decimal("0")

    def _score_price_sma50(self, state: dict[str, Any]) -> Decimal:
        """Score price position relative to SMA50."""
        try:
            price = Decimal(str(state.get("current_price", "0")))
            sma50 = Decimal(str(state.get("sma_50", "0")))
            if sma50 <= 0 or price <= 0:
                return Decimal("0")
            if price > sma50:
                return Decimal("1")
            elif price < sma50:
                return Decimal("-0.3")
            return Decimal("0.5")
        except Exception:
            return Decimal("0")

    def _score_sma_alignment(self, state: dict[str, Any]) -> Decimal:
        """Score SMA20/SMA50 alignment."""
        try:
            sma20 = Decimal(str(state.get("sma_20", "0")))
            sma50 = Decimal(str(state.get("sma_50", "0")))
            if sma20 <= 0 or sma50 <= 0:
                return Decimal("0")
            if sma20 > sma50:
                return Decimal("1")  # Bullish alignment
            elif sma20 < sma50:
                return Decimal("-0.5")  # Bearish alignment
            return Decimal("0.5")
        except Exception:
            return Decimal("0")

    def _score_rsi(self, state: dict[str, Any]) -> Decimal:
        """Score RSI signal.

        30-50: Neutral/bearish (low score)
        50-70: Bullish momentum (high score)
        >70: Overbought (penalty)
        <30: Oversold (potential reversal, moderate score)
        """
        try:
            rsi = Decimal(str(state.get("rsi", "50")))
            if rsi >= Decimal("70"):
                return Decimal("0.3")  # Overbought penalty
            elif rsi >= Decimal("50"):
                return Decimal("1")  # Bullish zone
            elif rsi >= Decimal("30"):
                return Decimal("0.5")  # Neutral
            else:
                return Decimal("0.4")  # Oversold - potential reversal
        except Exception:
            return Decimal("0")

    def _score_momentum(self, state: dict[str, Any]) -> Decimal:
        """Score momentum."""
        try:
            momentum = Decimal(str(state.get("momentum", "0")))
            if momentum > Decimal("5"):
                return Decimal("1")  # Strong positive
            elif momentum > Decimal("1"):
                return Decimal("0.7")  # Mild positive
            elif momentum > Decimal("-1"):
                return Decimal("0.3")  # Neutral
            elif momentum > Decimal("-5"):
                return Decimal("0")  # Mild negative
            else:
                return Decimal("-0.5")  # Strong negative
        except Exception:
            return Decimal("0")

    def _score_volume(self, state: dict[str, Any]) -> Decimal:
        """Score volume trend."""
        vol_trend = state.get("volume_trend", "NORMAL")
        if vol_trend == "HIGH":
            return Decimal("1")  # High volume confirms
        elif vol_trend == "LOW":
            return Decimal("0.3")  # Low volume - weak confirmation
        return Decimal("0.6")  # Normal

    def _score_volatility(self, state: dict[str, Any]) -> Decimal:
        """Score volatility. Moderate is best, extreme is penalized."""
        try:
            vol = Decimal(str(state.get("volatility", "0")))
            if vol <= 0:
                return Decimal("0")
            # Optimal volatility: 0.01 to 0.05
            if vol <= Decimal("0.01"):
                return Decimal("0.3")  # Too low - no movement
            elif vol <= Decimal("0.05"):
                return Decimal("1")  # Optimal
            elif vol <= Decimal("0.10"):
                return Decimal("0.6")  # High but acceptable
            else:
                return Decimal("0.2")  # Too volatile
        except Exception:
            return Decimal("0")

    def _score_structure(self, state: dict[str, Any]) -> Decimal:
        """Score price structure (position in range)."""
        pos = state.get("price_position", "MIDDLE")
        if pos == "HIGH":
            return Decimal("0.3")  # Near resistance
        elif pos == "LOW":
            return Decimal("0.7")  # Near support (good for LONG)
        return Decimal("0.5")  # Middle

    def _score_rr(self, rr: Decimal | None) -> Decimal:
        """Score risk/reward ratio."""
        if rr is None or rr <= 0:
            return Decimal("0")
        if rr >= Decimal("3"):
            return Decimal("1")  # Excellent
        elif rr >= Decimal("2"):
            return Decimal("0.8")  # Good
        elif rr >= Decimal("1.5"):
            return Decimal("0.6")  # Acceptable
        elif rr >= Decimal("1"):
            return Decimal("0.3")  # Marginal
        return Decimal("0")  # Poor

    def _classify_regime(self, state: dict[str, Any]) -> str:
        """Classify market regime from indicators."""
        trend = state.get("trend", "NEUTRAL")
        try:
            vol = Decimal(str(state.get("volatility", "0")))
            rsi = Decimal(str(state.get("rsi", "50")))
            momentum = Decimal(str(state.get("momentum", "0")))

            if vol > Decimal("0.10"):
                return "HIGH_VOLATILITY"

            if trend == "BULLISH":
                if momentum > Decimal("3") and rsi > Decimal("60"):
                    return "STRONG_BULL"
                return "BULL"
            elif trend == "BEARISH":
                if momentum < Decimal("-3") and rsi < Decimal("40"):
                    return "STRONG_BEAR"
                return "BEAR"
            return "NEUTRAL"
        except Exception:
            return "NEUTRAL"
