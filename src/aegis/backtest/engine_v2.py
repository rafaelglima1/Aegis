"""AEGIS Backtest Engine V2 — deterministic candle-by-candle replay.

Uses production components (SetupScorer, RiskEngine, PositionManager)
for realistic backtesting without look-ahead bias.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable
from uuid import UUID, uuid4

from aegis.domain.contracts import utc_now
from aegis.market_data.contracts import Candle as _CanonicalCandle
from aegis.risk_engine.risk_engine import RiskEngine
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.risk_engine.setup_scorer import SetupScorer, SetupWeights
from aegis.risk_engine.position_manager import PositionManager, PositionManagerConfig

logger = logging.getLogger("aegis.backtest_v2")


def Candle(
    timestamp: str | datetime,
    open: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: Decimal = Decimal("0"),
    *,
    symbol: str = "",
    timeframe: str = "",
    source: str = "",
) -> _CanonicalCandle:
    """Backward-compatible Candle factory for backtesting.

    Accepts both string and datetime timestamps.
    Creates a canonical Candle with legacy positional arguments.
    """
    if isinstance(timestamp, str):
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    elif isinstance(timestamp, datetime):
        ts = timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    else:
        raise TypeError(f"Unsupported timestamp type: {type(timestamp)}")

    return _CanonicalCandle(
        symbol=symbol,
        timestamp=ts,
        timeframe=timeframe,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        is_closed=True,
        source=source,
    )


@dataclass
class BacktestConfig:
    """Configuration for backtest execution."""

    initial_capital: Decimal = Decimal("100.00")
    fee_rate: Decimal = Decimal("0.005")  # 0.5%
    slippage_bps: Decimal = Decimal("10")  # 10 basis points
    min_position_size: Decimal = Decimal("0.001")

    # Risk parameters (overrides RiskLimits)
    risk_limits: RiskLimits | None = None

    # Position management
    position_config: PositionManagerConfig | None = None

    # Setup scoring weights
    setup_weights: SetupWeights | None = None


@dataclass
class TradeRecord:
    """Complete record of a backtest trade."""

    trade_id: UUID = field(default_factory=uuid4)
    symbol: str = ""
    entry_time: str = ""
    exit_time: str = ""
    entry_price: Decimal = Decimal("0")
    exit_price: Decimal = Decimal("0")
    stop_loss: Decimal = Decimal("0")
    take_profit: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")
    side: str = "LONG"
    setup_score: int = 0
    confidence: Decimal = Decimal("0")
    regime: str = "NEUTRAL"
    rsi: Decimal = Decimal("50")
    momentum: Decimal = Decimal("0")
    volume_trend: str = "NORMAL"
    volatility: Decimal = Decimal("0")
    risk_reward: Decimal = Decimal("0")
    position_size: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    realized_r: Decimal = Decimal("0")
    mfe: Decimal = Decimal("0")  # Maximum Favorable Excursion
    mae: Decimal = Decimal("0")  # Maximum Adverse Excursion
    holding_time: int = 0  # candles
    exit_reason: str = ""  # SL, TP, TRAILING, BREAK_EVEN, END_OF_DATA
    fees: Decimal = Decimal("0")


@dataclass
class BacktestResult:
    """Complete backtest result with metrics and trade records."""

    result_id: UUID = field(default_factory=uuid4)
    config: BacktestConfig = field(default_factory=BacktestConfig)
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[Decimal] = field(default_factory=list)

    # Summary metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: Decimal = Decimal("0")
    gross_profit: Decimal = Decimal("0")
    gross_loss: Decimal = Decimal("0")
    net_profit: Decimal = Decimal("0")
    profit_factor: Decimal = Decimal("0")
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")
    expectancy: Decimal = Decimal("0")
    avg_r: Decimal = Decimal("0")
    median_r: Decimal = Decimal("0")
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    max_drawdown: Decimal = Decimal("0")
    recovery_factor: Decimal = Decimal("0")
    avg_holding_time: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")


class BacktestEngineV2:
    """Deterministic backtesting engine using production components.

    Candle-by-candle replay with no look-ahead bias.
    Uses SetupScorer, RiskEngine, PositionManager from production.
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self._config = config or BacktestConfig()
        self._risk_engine = RiskEngine(self._config.risk_limits or RiskLimits())
        self._setup_scorer = SetupScorer(self._config.setup_weights)
        self._position_manager = PositionManager(self._config.position_config)

    def run(self, symbol: str, candles: list[Candle],
            strategy: Callable | None = None) -> BacktestResult:
        """Run backtest on historical candle data.

        Args:
            symbol: Trading symbol
            candles: List of OHLCV candles in chronological order
            strategy: Optional strategy function(candle, market_state, position_state) -> decision

        Returns:
            BacktestResult with all trades and metrics
        """
        if not candles or len(candles) < 2:
            return BacktestResult(config=self._config)

        result = BacktestResult(config=self._config)

        # Initialize state
        capital = self._config.initial_capital
        position = None  # Current open position
        equity_curve = [capital]

        # Process each candle
        for i in range(len(candles)):
            candle = candles[i]
            current_price = candle.close

            # Build market state from candles up to current (no look-ahead)
            market_state = self._build_market_state(candles[:i + 1], current_price)

            # Calculate setup score
            setup_result = self._setup_scorer.score(market_state)

            # Manage existing position
            if position is not None:
                position, exit_info = self._manage_position(
                    position, current_price, candle, setup_result
                )
                if exit_info is not None:
                    # Position closed
                    capital += exit_info["proceeds"]
                    result.trades.append(exit_info["trade"])
                    equity_curve.append(capital)
                    position = None
                else:
                    # Position still open - update equity
                    unrealized = (current_price - position["entry_price"]) * position["quantity"]
                    equity_curve.append(capital + unrealized)
            else:
                # No position - check for entry
                if strategy is not None:
                    decision = strategy(candle, market_state, setup_result)
                else:
                    decision = self._default_decision(market_state, setup_result)

                if decision is not None and decision.get("action") == "LONG":
                    # Validate through risk engine
                    risk_result = self._validate_entry(
                        decision, current_price, market_state, setup_result
                    )
                    if risk_result["approved"]:
                        # Execute entry
                        entry_price = self._apply_slippage(current_price, "BUY")
                        quantity = risk_result["quantity"]
                        cost = entry_price * quantity
                        fee = cost * self._config.fee_rate

                        if capital >= cost + fee:
                            capital -= (cost + fee)
                            position = {
                                "entry_price": entry_price,
                                "stop_loss": decision["stop_loss"],
                                "take_profit": decision["take_profit"],
                                "quantity": quantity,
                                "entry_time": candle.timestamp.isoformat(),
                                "setup_score": setup_result.score,
                                "confidence": decision.get("confidence", Decimal("0")),
                                "regime": setup_result.market_regime,
                                "rsi": Decimal(str(market_state.get("rsi", "50"))),
                                "momentum": Decimal(str(market_state.get("momentum", "0"))),
                                "volume_trend": market_state.get("volume_trend", "NORMAL"),
                                "volatility": Decimal(str(market_state.get("volatility", "0"))),
                                "highest_price": entry_price,
                                "break_even_activated": False,
                                "trailing_activated": False,
                                "current_stop": decision["stop_loss"],
                                "locked_profit_r": Decimal("0"),
                            }

                            # Register with position manager
                            self._position_manager.register_position(
                                symbol, entry_price, decision["stop_loss"],
                                decision["take_profit"], quantity,
                            )

                            equity_curve.append(capital)
                        else:
                            equity_curve.append(capital)
                    else:
                        equity_curve.append(capital)
                else:
                    equity_curve.append(capital)

        # Close any remaining position at last candle close
        if position is not None:
            last_price = candles[-1].close
            exit_price = self._apply_slippage(last_price, "SELL")
            proceeds = exit_price * position["quantity"]
            fee = proceeds * self._config.fee_rate
            capital += (proceeds - fee)

            risk = position["entry_price"] - position["stop_loss"]
            realized_r = (exit_price - position["entry_price"]) / risk if risk > 0 else Decimal("0")

            trade = TradeRecord(
                symbol=symbol,
                entry_time=position["entry_time"],
                exit_time=candles[-1].timestamp.isoformat(),
                entry_price=position["entry_price"],
                exit_price=exit_price,
                stop_loss=position["stop_loss"],
                take_profit=position["take_profit"],
                quantity=position["quantity"],
                setup_score=position["setup_score"],
                confidence=position["confidence"],
                regime=position["regime"],
                rsi=position["rsi"],
                momentum=position["momentum"],
                volume_trend=position["volume_trend"],
                volatility=position["volatility"],
                risk_reward= self._calculate_rr(position),
                position_size=position["quantity"] * position["entry_price"],
                realized_pnl=(exit_price - position["entry_price"]) * position["quantity"] - fee,
                realized_r=realized_r,
                holding_time=len(candles) - 1,
                exit_reason="END_OF_DATA",
                fees=fee,
            )
            result.trades.append(trade)
            equity_curve.append(capital)

        result.equity_curve = equity_curve
        result = self._calculate_metrics(result)
        return result

    def _build_market_state(self, candles: list[Candle], current_price: Decimal) -> dict[str, Any]:
        """Build market state from candles (no look-ahead)."""
        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        # Moving averages
        sma_20 = sum(closes[-20:]) / min(20, len(closes)) if closes else Decimal("0")
        sma_50 = sum(closes[-50:]) / min(50, len(closes)) if closes else Decimal("0")

        # Volatility
        if len(closes) > 1:
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
            volatility = (sum(r**2 for r in returns) / len(returns)) ** Decimal("0.5")
        else:
            volatility = Decimal("0")

        # Trend
        trend = "BULLISH" if current_price > sma_20 > sma_50 else "BEARISH" if current_price < sma_20 < sma_50 else "NEUTRAL"

        # RSI
        rsi = self._calculate_rsi(closes, 14)

        # Momentum
        momentum = Decimal("0")
        if len(closes) >= 10:
            momentum = (closes[-1] - closes[-10]) / closes[-10] * Decimal("100")

        # Volume trend
        volume_trend = "NORMAL"
        if len(volumes) >= 20:
            avg_vol = sum(volumes[-20:]) / Decimal("20")
            recent_vol = sum(volumes[-5:]) / Decimal("5") if len(volumes) >= 5 else avg_vol
            if avg_vol > 0:
                vol_ratio = recent_vol / avg_vol
                if vol_ratio > Decimal("1.5"):
                    volume_trend = "HIGH"
                elif vol_ratio < Decimal("0.5"):
                    volume_trend = "LOW"

        # Price position
        price_position = "MIDDLE"
        if highs and lows:
            period_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
            period_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)
            if period_high > period_low:
                pct = (current_price - period_low) / (period_high - period_low)
                if pct > Decimal("0.8"):
                    price_position = "HIGH"
                elif pct < Decimal("0.2"):
                    price_position = "LOW"

        return {
            "symbol": "",
            "current_price": str(current_price),
            "sma_20": str(sma_20),
            "sma_50": str(sma_50),
            "volatility": str(volatility),
            "trend": trend,
            "rsi": str(rsi),
            "momentum": str(momentum),
            "volume_trend": volume_trend,
            "price_position": price_position,
            "volume_24h": str(sum(volumes[-24:])) if volumes else "0",
            "candles_count": len(candles),
            "last_5_closes": [str(c) for c in closes[-5:]],
        }

    def _calculate_rsi(self, closes: list[Decimal], period: int = 14) -> Decimal:
        """Calculate RSI."""
        if len(closes) < period + 1:
            return Decimal("50")

        gains = []
        losses = []
        for i in range(1, len(closes)):
            change = closes[i] - closes[i-1]
            if change > 0:
                gains.append(change)
                losses.append(Decimal("0"))
            else:
                gains.append(Decimal("0"))
                losses.append(abs(change))

        avg_gain = sum(gains[-period:]) / Decimal(period)
        avg_loss = sum(losses[-period:]) / Decimal(period)

        if avg_loss == 0:
            return Decimal("100")

        rs = avg_gain / avg_loss
        return Decimal("100") - (Decimal("100") / (Decimal("1") + rs))

    def _default_decision(self, market_state: dict[str, Any],
                          setup_result: Any) -> dict[str, Any] | None:
        """Default decision logic when no strategy is provided.

        Uses deterministic rules based on setup score and indicators.
        """
        score = setup_result.score
        trend = market_state.get("trend", "NEUTRAL")
        rsi = Decimal(str(market_state.get("rsi", "50")))

        # Require minimum setup score
        if score < self._risk_engine.limits.setup_score_min:
            return None

        # Require bullish trend
        if trend == "BEARISH":
            return None

        # RSI should not be overbought
        if rsi > Decimal("75"):
            return None

        # Calculate entry/stop/take_profit
        current_price = Decimal(str(market_state.get("current_price", "0")))
        if current_price <= 0:
            return None

        # Use ATR-based stop if available, otherwise fixed percentage
        volatility = Decimal(str(market_state.get("volatility", "0.02")))
        stop_distance = current_price * max(volatility, Decimal("0.01"))
        entry_price = current_price
        stop_loss = entry_price - stop_distance
        take_profit = entry_price + (stop_distance * self._risk_engine.limits.min_risk_reward)

        return {
            "action": "LONG",
            "confidence": Decimal("0.70"),
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }

    def _validate_entry(self, decision: dict[str, Any], current_price: Decimal,
                        market_state: dict[str, Any], setup_result: Any) -> dict[str, Any]:
        """Validate entry through risk engine."""
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction

        contract = DecisionContract(
            action=TradingAction.LONG,
            confidence=decision.get("confidence", Decimal("0.70")),
            thesis="backtest",
            entry_price=decision["entry_price"],
            stop_loss=decision["stop_loss"],
            take_profit=decision["take_profit"],
        )

        risk_result = self._risk_engine.evaluate(
            contract,
            current_price=current_price,
            market_state=market_state,
            symbol="",
            setup_score=setup_result.score,
        )

        return {
            "approved": risk_result.is_approved,
            "quantity": risk_result.approved_quantity,
            "violations": [v.code for v in risk_result.violations],
        }

    def _manage_position(self, position: dict[str, Any], current_price: Decimal,
                         candle: Candle, setup_result: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Manage existing position (SL/TP/BE/trailing).

        Returns:
            (updated_position, exit_info or None)
        """
        entry = position["entry_price"]
        stop = position["current_stop"]
        tp = position["take_profit"]
        qty = position["quantity"]

        # Update highest price
        if current_price > position["highest_price"]:
            position["highest_price"] = current_price

        # Calculate R
        risk = entry - position["stop_loss"]
        unrealized_r = (current_price - entry) / risk if risk > 0 else Decimal("0")

        # Check break-even
        if (not position["break_even_activated"]
                and unrealized_r >= Decimal("0.8")):
            new_stop = entry * Decimal("1.001")
            if new_stop > stop:
                position["break_even_activated"] = True
                position["current_stop"] = new_stop
                stop = new_stop

        # Check trailing
        if unrealized_r >= Decimal("1.2"):
            new_stop = current_price * Decimal("0.98")
            if new_stop > stop:
                position["trailing_activated"] = True
                position["current_stop"] = new_stop
                stop = new_stop

        # Check profit protection
        if unrealized_r >= Decimal("1.5") and position["locked_profit_r"] < Decimal("0.5"):
            lock = entry + (entry - position["stop_loss"]) * Decimal("0.5")
            if lock > stop:
                position["locked_profit_r"] = Decimal("0.5")
                position["current_stop"] = lock
                stop = lock

        if unrealized_r >= Decimal("2.0") and position["locked_profit_r"] < Decimal("1.0"):
            lock = entry + (entry - position["stop_loss"]) * Decimal("1.0")
            if lock > stop:
                position["locked_profit_r"] = Decimal("1.0")
                position["current_stop"] = lock
                stop = lock

        # Check stop loss hit (using low price for intra-candle)
        if candle.low <= stop:
            exit_price = self._apply_slippage(stop, "SELL")
            return self._close_position(position, exit_price, candle.timestamp.isoformat(), "STOP_LOSS")

        # Check take profit hit (using high price for intra-candle)
        if candle.high >= tp:
            exit_price = self._apply_slippage(tp, "SELL")
            return self._close_position(position, exit_price, candle.timestamp.isoformat(), "TAKE_PROFIT")

        return position, None

    def _close_position(self, position: dict[str, Any], exit_price: Decimal,
                        exit_time: str, reason: str) -> tuple[None, dict[str, Any]]:
        """Close position and generate trade record."""
        entry = position["entry_price"]
        qty = position["quantity"]
        proceeds = exit_price * qty
        fee = proceeds * self._config.fee_rate

        risk = entry - position["stop_loss"]
        realized_r = (exit_price - entry) / risk if risk > 0 else Decimal("0")

        trade = TradeRecord(
            symbol="",
            entry_time=position["entry_time"],
            exit_time=exit_time,
            entry_price=entry,
            exit_price=exit_price,
            stop_loss=position["stop_loss"],
            take_profit=position["take_profit"],
            quantity=qty,
            setup_score=position["setup_score"],
            confidence=position["confidence"],
            regime=position["regime"],
            rsi=position["rsi"],
            momentum=position["momentum"],
            volume_trend=position["volume_trend"],
            volatility=position["volatility"],
            risk_reward=self._calculate_rr(position),
            position_size=entry * qty,
            realized_pnl=(exit_price - entry) * qty - fee,
            realized_r=realized_r,
            exit_reason=reason,
            fees=fee,
        )

        return None, {"trade": trade, "proceeds": proceeds - fee}

    def _apply_slippage(self, price: Decimal, side: str) -> Decimal:
        """Apply slippage to price."""
        slippage = price * self._config.slippage_bps / Decimal("10000")
        if side == "BUY":
            return price + slippage
        return price - slippage

    def _calculate_rr(self, position: dict[str, Any]) -> Decimal:
        """Calculate R/R ratio for a position."""
        risk = position["entry_price"] - position["stop_loss"]
        reward = position["take_profit"] - position["entry_price"]
        if risk <= 0:
            return Decimal("0")
        return reward / risk

    def _calculate_metrics(self, result: BacktestResult) -> BacktestResult:
        """Calculate comprehensive performance metrics."""
        trades = result.trades
        if not trades:
            return result

        result.total_trades = len(trades)
        result.winning_trades = sum(1 for t in trades if t.realized_pnl > 0)
        result.losing_trades = sum(1 for t in trades if t.realized_pnl <= 0)

        if result.total_trades > 0:
            result.win_rate = Decimal(str(result.winning_trades)) / Decimal(str(result.total_trades)) * 100

        wins = [t.realized_pnl for t in trades if t.realized_pnl > 0]
        losses = [abs(t.realized_pnl) for t in trades if t.realized_pnl < 0]

        result.gross_profit = sum(wins) if wins else Decimal("0")
        result.gross_loss = sum(losses) if losses else Decimal("0")
        result.net_profit = sum(t.realized_pnl for t in trades)
        result.total_fees = sum(t.fees for t in trades)

        if result.gross_loss > 0:
            result.profit_factor = result.gross_profit / result.gross_loss

        if wins:
            result.avg_win = sum(wins) / Decimal(str(len(wins)))
        if losses:
            result.avg_loss = sum(losses) / Decimal(str(len(losses)))

        # Expectancy
        win_rate = Decimal(str(result.winning_trades)) / Decimal(str(result.total_trades)) if result.total_trades > 0 else Decimal("0")
        loss_rate = Decimal("1") - win_rate
        result.expectancy = (win_rate * result.avg_win) - (loss_rate * result.avg_loss)

        # Average R
        r_values = [t.realized_r for t in trades if t.realized_r != 0]
        if r_values:
            result.avg_r = sum(r_values) / Decimal(str(len(r_values)))
            sorted_r = sorted(r_values)
            mid = len(sorted_r) // 2
            result.median_r = sorted_r[mid]

        # Consecutive wins/losses
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        for t in trades:
            if t.realized_pnl > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
        result.max_consecutive_wins = max_wins
        result.max_consecutive_losses = max_losses

        # Max drawdown from equity curve
        if result.equity_curve:
            peak = result.equity_curve[0]
            max_dd = Decimal("0")
            for eq in result.equity_curve:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak if peak > 0 else Decimal("0")
                if dd > max_dd:
                    max_dd = dd
            result.max_drawdown = max_dd * 100

        # Recovery factor
        if result.max_drawdown > 0:
            result.recovery_factor = result.net_profit / (result.max_drawdown * result.config.initial_capital / Decimal("100"))

        # Average holding time
        holding_times = [t.holding_time for t in trades if t.holding_time > 0]
        if holding_times:
            result.avg_holding_time = Decimal(str(sum(holding_times))) / Decimal(str(len(holding_times)))

        return result


def create_candles_from_dicts(data: list[dict[str, Any]]) -> list[Candle]:
    """Convert dict list to Candle list for backtesting."""
    candles = []
    for d in data:
        candles.append(Candle(
            timestamp=d.get("timestamp", ""),
            open=Decimal(str(d.get("open", "0"))),
            high=Decimal(str(d.get("high", "0"))),
            low=Decimal(str(d.get("low", "0"))),
            close=Decimal(str(d.get("close", "0"))),
            volume=Decimal(str(d.get("volume", "0"))),
        ))
    return candles
