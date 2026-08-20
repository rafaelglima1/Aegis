"""AEGIS market data package — Phase 2 + Phase 3."""

from aegis.market_data.contracts import Candle
from aegis.market_data.provider import MarketDataProvider
from aegis.market_data.validator import (
    CandleValidator,
    BatchValidator,
    InvalidCandleError,
    CandleValidationError,
    VALID_TIMEFRAMES,
    TIMEFRAME_SECONDS,
)
from aegis.market_data.normalizer import MarketDataNormalizer, normalize_symbol, normalize_timeframe
from aegis.market_data.cache import MarketDataCache
from aegis.market_data.observability import MarketDataMetrics
from aegis.market_data.context_builder import ContextBuilder, MarketStateBuilder
from aegis.market_data.mtf import (
    MTFEngine,
    MTFWeights,
    MTFResult,
    TimeframeAnalyzer,
    TimeframeResult,
    DEFAULT_MTF_CONFIG,
)

__all__ = [
    "Candle",
    "MarketDataProvider",
    "CandleValidator",
    "BatchValidator",
    "InvalidCandleError",
    "CandleValidationError",
    "VALID_TIMEFRAMES",
    "TIMEFRAME_SECONDS",
    "MarketDataNormalizer",
    "normalize_symbol",
    "normalize_timeframe",
    "MarketDataCache",
    "MarketDataMetrics",
    "ContextBuilder",
    "MarketStateBuilder",
    "MTFEngine",
    "MTFWeights",
    "MTFResult",
    "TimeframeAnalyzer",
    "TimeframeResult",
    "DEFAULT_MTF_CONFIG",
]
