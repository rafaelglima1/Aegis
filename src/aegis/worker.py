"""AEGIS Autonomous Worker — real trading loop."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx

from aegis.ai_engine.decision_engine import DecisionContract, DecisionEngine
from aegis.ai_engine.provider import LLMProvider, LLMResponse
from aegis.ai_engine.prompt_manager import PromptManager, PromptVersion
from aegis.domain.enums import TradingAction, PositionSide
from aegis.risk_engine.risk_engine import RiskEngine
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.execution.sandbox import SandboxBroker
from aegis.execution.engine import ExecutionEngine
from aegis.portfolio.portfolio import Portfolio
from aegis.audit import AuditLogger

logger = logging.getLogger("aegis.worker")


class MercadoBitcoinAPI:
    """Mercado Bitcoin API client."""

    def __init__(self) -> None:
        self.base_url = "https://api.mercadobitcoin.net"
        self.client = httpx.AsyncClient(timeout=30.0)

    async def get_candles(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch candle data from MB v4 API."""
        # Map timeframe to resolution
        tf_map = {"1m": "1m", "5m": "15m", "15m": "15m", "30m": "15m", "1h": "1h", "4h": "3h", "1d": "1d"}
        resolution = tf_map.get(timeframe, "1h")

        # Calculate time range (last 100 candles)
        import time
        now = int(time.time())
        # Approximate seconds per candle
        tf_seconds = {"1m": 60, "15m": 900, "1h": 3600, "3h": 10800, "1d": 86400}
        from_time = now - (limit * tf_seconds.get(resolution, 3600))

        try:
            response = await self.client.get(
                f"{self.base_url}/api/v4/candles/",
                params={
                    "symbol": symbol,
                    "resolution": resolution,
                    "from": from_time,
                    "to": now,
                },
            )
            if response.status_code == 200:
                data = response.json()
                # Convert v4 format to candle list
                candles = []
                if "t" in data:
                    for i in range(len(data["t"])):
                        candles.append({
                            "timestamp": data["t"][i],
                            "open": data["o"][i],
                            "high": data["h"][i],
                            "low": data["l"][i],
                            "close": data["c"][i],
                            "volume": data["v"][i],
                        })
                return candles[:limit]
        except Exception as e:
            logger.error("Failed to fetch candles for %s: %s", symbol, e)
        return []

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        """Get current ticker price from orderbook."""
        try:
            response = await self.client.get(
                f"{self.base_url}/api/v4/{symbol}/orderbook/"
            )
            if response.status_code == 200:
                data = response.json()
                # Get best bid/ask
                asks = data.get("asks", [])
                bids = data.get("bids", [])
                if asks and bids:
                    best_ask = Decimal(asks[0][0])
                    best_bid = Decimal(bids[0][0])
                    mid_price = (best_ask + best_bid) / 2
                    return {"last": str(mid_price), "bid": str(best_bid), "ask": str(best_ask)}
        except Exception as e:
            logger.error("Failed to fetch ticker for %s: %s", symbol, e)
        return {}

    async def close(self) -> None:
        await self.client.aclose()


class SimpleLLMProvider(LLMProvider):
    """Simple LLM provider using httpx."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.client = httpx.AsyncClient(timeout=60.0)

    @property
    def provider_name(self) -> str:
        return "simple"

    async def validate_connection(self) -> bool:
        """Validate the provider connection."""
        try:
            response = await self.client.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"})
            return response.status_code == 200
        except Exception:
            return False

    async def complete(self, prompt: str, model: str | None = None, timeout_seconds: int = 120) -> LLMResponse:
        """Call LLM API."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Build OpenAI-compatible request
        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": "Você é um analista de trading de criptomoedas. Responda APENAS com JSON válido. Todo o texto (thesis, reasoning) deve ser em português."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 16384,
        }

        try:
            # Kilo AI uses /chat/completions endpoint
            url = self.base_url
            if not url.endswith("/chat/completions"):
                url = url.rstrip("/") + "/chat/completions"

            response = await self.client.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )

            if response.status_code == 200:
                data = response.json()
                message = data["choices"][0]["message"]
                content = message.get("content") or ""
                reasoning = message.get("reasoning") or ""

                # If content is empty, try to extract JSON from reasoning
                if not content.strip() and reasoning.strip():
                    content = reasoning

                logger.info("LLM response content: %s", content[:200])
                logger.info("LLM response reasoning: %s", reasoning[:200])
                result = self._parse_response(content)

                # If parse failed and we have reasoning text, try extracting JSON from reasoning
                if result.action == TradingAction.HOLD and result.confidence == Decimal("0") and reasoning.strip():
                    logger.info("Content parse failed, trying reasoning text")
                    result = self._parse_response(reasoning)

                return result
            else:
                logger.error("LLM API error: %s", response.text)
                return LLMResponse(
                    action=TradingAction.HOLD,
                    confidence=Decimal("0"),
                    thesis="LLM API error",
                )
        except Exception as e:
            logger.error("LLM request failed: %s [%s]", e, type(e).__name__, exc_info=True)
            return LLMResponse(
                action=TradingAction.HOLD,
                confidence=Decimal("0"),
                thesis=f"LLM error: {e}",
            )

    def _parse_response(self, content: str) -> LLMResponse:
        """Parse LLM JSON response."""
        logger.info("Parsing LLM content (len=%d): %s", len(content), content[:300])

        if not content or not content.strip():
            logger.warning("Empty LLM content, defaulting to HOLD")
            return LLMResponse(
                action=TradingAction.HOLD,
                confidence=Decimal("0"),
                thesis="Empty LLM response",
            )

        try:
            # Try to extract JSON from response
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            # Try to find JSON object in the content using brace matching
            # Search from the END of content (JSON is typically at the end for reasoning models)
            brace_positions = []
            for i in range(len(content) - 1, -1, -1):
                if content[i] == "{":
                    brace_positions.append(i)

            for brace_start in brace_positions:
                depth = 0
                in_string = False
                escape = False
                for i in range(brace_start, len(content)):
                    c = content[i]
                    if escape:
                        escape = False
                        continue
                    if c == "\\":
                        escape = True
                        continue
                    if c == '"':
                        in_string = not in_string
                        continue
                    if not in_string:
                        if c == "{":
                            depth += 1
                        elif c == "}":
                            depth -= 1
                            if depth == 0:
                                candidate = content[brace_start : i + 1]
                                # Validate it has "action" key
                                if '"action"' in candidate:
                                    content = candidate
                                    break
                else:
                    continue
                break

            data = json.loads(content.strip())

            action_str = data.get("action", "HOLD").upper()
            try:
                action = TradingAction(action_str)
            except ValueError:
                action = TradingAction.HOLD

            return LLMResponse(
                action=action,
                confidence=Decimal(str(data.get("confidence", 0))),
                thesis=data.get("thesis", ""),
                entry_price=Decimal(str(data["entry_price"])) if data.get("entry_price") else None,
                stop_loss=Decimal(str(data["stop_loss"])) if data.get("stop_loss") else None,
                take_profit=Decimal(str(data["take_profit"])) if data.get("take_profit") else None,
                reasoning=data.get("reasoning", ""),
            )
        except Exception as e:
            logger.error("Failed to parse LLM response: %s", e)
            return LLMResponse(
                action=TradingAction.HOLD,
                confidence=Decimal("0"),
                thesis=f"Parse error: {e}",
            )


class AutonomousWorker:
    """Autonomous trading worker.

    Loop:
    1. Fetch candles from Mercado Bitcoin
    2. Build market state
    3. Send to LLM for analysis
    4. Evaluate risk
    5. Execute approved trades
    6. Update state and broadcast
    """

    def __init__(self) -> None:
        # Load config from environment (set by docker-compose env_file)
        self.llm_base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.llm_api_key = os.getenv("LLM_API_KEY", "")
        self.llm_model = os.getenv("LLM_MODEL", "gpt-4")
        self.symbols = os.getenv("TRADING_SYMBOLS", "BTC-BRL,ETH-BRL").split(",")
        self.timeframe = os.getenv("TRADING_TIMEFRAME", "1h")
        self.capital = Decimal(os.getenv("TRADING_CAPITAL", "100.0"))
        self.risk_pct = Decimal(os.getenv("RISK_PER_TRADE_PCT", "1.0")) / Decimal("100")
        self.max_positions = int(os.getenv("MAX_POSITIONS", "1"))
        self.mandatory_stop = os.getenv("MANDATORY_STOP", "true").lower() == "true"
        self.mandatory_take_profit = os.getenv("MANDATORY_TAKE_PROFIT", "true").lower() == "true"
        self.long_only = os.getenv("LONG_ONLY", "true").lower() == "true"
        self.max_daily_loss_pct = Decimal(os.getenv("MAX_DAILY_LOSS_PCT", "5.0")) / Decimal("100")
        self.max_position_size_pct = Decimal(os.getenv("MAX_POSITION_SIZE_PCT", "20.0")) / Decimal("100")
        self.max_exposure_pct = Decimal(os.getenv("MAX_EXPOSURE_PCT", "100.0")) / Decimal("100")
        self.min_confidence = Decimal(os.getenv("MIN_CONFIDENCE", "0.5"))
        self.circuit_breaker_pct = Decimal(os.getenv("CIRCUIT_BREAKER_PCT", "10.0")) / Decimal("100")

        # Components
        self.mb_api = MercadoBitcoinAPI()
        self.llm = SimpleLLMProvider(self.llm_base_url, self.llm_api_key, self.llm_model)
        self.prompt_manager = PromptManager()
        self.risk_engine = RiskEngine(RiskLimits(
            reference_capital=self.capital,
            max_risk_per_trade_pct=self.risk_pct,
            max_simultaneous_positions=self.max_positions,
            circuit_breaker_drawdown_pct=self.circuit_breaker_pct,
            max_daily_loss_pct=self.max_daily_loss_pct,
            max_position_size_pct=self.max_position_size_pct,
            max_exposure_pct=self.max_exposure_pct,
        ))
        self.broker = SandboxBroker()
        self.execution = ExecutionEngine(self.broker)
        self.portfolio = Portfolio()
        self.audit = AuditLogger()

        # Register default prompt
        self.prompt_manager.register(
            PromptVersion(
                version="trading_v1",
                template="""Você é um trader de swing trade de criptomoedas. Analise os dados de mercado e tome uma decisão de trading.

Dados de Mercado:
{market_state}

Portfólio Atual:
{portfolio}

Regras:
- Apenas LONG (sem SHORT)
- Apenas spot (sem alavancagem)
- Máximo 1 posição por vez
- Risco de 1% por trade
- Stop loss e take profit obrigatórios
- Só opera se confiança > 50%

Responda com JSON:
{{
    "action": "LONG" ou "HOLD" ou "CLOSE",
    "confidence": 0.0 a 1.0,
    "thesis": "raciocínio breve",
    "entry_price": número ou null,
    "stop_loss": número ou null,
    "take_profit": número ou null,
    "reasoning": "análise detalhada"
}}""",
                description="Default trading prompt for crypto swing trading",
            )
        )

        # State — all financial values stored as Decimal, not float
        self._running = False
        self._state: dict[str, Any] = {
            "capital": str(self.capital),
            "pnl": "0.00",
            "positions": [],
            "orders": [],
            "history": [],
            "decisions": [],
            "exposure": "0.00",
            "peak_equity": str(self.capital),
            "risk_limits": {
                "reference_capital": str(self.capital),
                "max_risk_per_trade_pct": str(self.risk_pct * 100),
                "max_positions": self.max_positions,
                "circuit_breaker_pct": str(self.circuit_breaker_pct * 100),
                "mandatory_stop": self.mandatory_stop,
                "mandatory_take_profit": self.mandatory_take_profit,
                "long_only": self.long_only,
                "min_confidence": str(self.min_confidence),
                "max_daily_loss_pct": str(self.max_daily_loss_pct * 100),
                "max_position_size_pct": str(self.max_position_size_pct * 100),
                "max_exposure_pct": str(self.max_exposure_pct * 100),
            },
        }
        self._ws_clients: list[Any] = []

    @property
    def state(self) -> dict[str, Any]:
        return self._state.copy()

    def add_ws_client(self, ws: Any) -> None:
        self._ws_clients.append(ws)

    def remove_ws_client(self, ws: Any) -> None:
        if ws in self._ws_clients:
            self._ws_clients.remove(ws)

    async def _broadcast(self, data: dict[str, Any]) -> None:
        if not self._ws_clients:
            return
        message = json.dumps(data, default=str)
        disconnected = []
        for ws in self._ws_clients:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self._ws_clients.remove(ws)

    async def start(self) -> None:
        """Start the autonomous trading loop."""
        self._running = True
        logger.info("Autonomous worker started")
        logger.info("Symbols: %s", self.symbols)
        logger.info("Timeframe: %s", self.timeframe)
        logger.info("Capital: R$ %s", self.capital)
        logger.info("Risk per trade: %s%%", self.risk_pct * 100)

        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error("Worker tick error: %s", e)

            # Wait for next candle close (1 hour)
            await asyncio.sleep(3600)

    async def stop(self) -> None:
        self._running = False
        await self.mb_api.close()
        logger.info("Autonomous worker stopped")

    async def _tick(self) -> None:
        """Main trading loop tick."""
        logger.info("Starting tick...")

        for symbol in self.symbols:
            try:
                await self._process_symbol(symbol.strip())
            except Exception as e:
                logger.error("Error processing %s: %s", symbol, e)

        # Update current_price and P&L for all open positions
        self._update_positions_pnl()

        # Broadcast state update
        await self._broadcast({"type": "state_update", **self._state})

    def _update_positions_pnl(self) -> None:
        """Update P&L for all open positions based on stored current_price."""
        for pos in self._state["positions"]:
            if pos["status"] != "OPEN":
                continue
            entry = Decimal(pos["entry_price"])
            current = Decimal(pos["current_price"])
            qty = Decimal(pos["quantity"])
            pnl = (current - entry) * qty
            pos["pnl"] = str(pnl)
            pos["pnl_pct"] = str((pnl / (entry * qty) * 100) if entry > 0 and qty > 0 else 0)

    async def _process_symbol(self, symbol: str) -> None:
        """Process a single trading symbol."""
        logger.info("Processing %s...", symbol)

        # 1. Fetch candles
        candles = await self.mb_api.get_candles(symbol, self.timeframe, 50)
        if not candles:
            logger.warning("No candles for %s", symbol)
            return

        # 2. Fetch current price
        ticker = await self.mb_api.get_ticker(symbol)
        current_price = Decimal(str(ticker.get("last", "0"))) if ticker else Decimal("0")

        if current_price <= 0:
            logger.warning("Invalid price for %s: %s", symbol, current_price)
            return

        # Update current_price for open positions of this symbol
        for pos in self._state["positions"]:
            if pos["symbol"] == symbol and pos["status"] == "OPEN":
                pos["current_price"] = str(current_price)

        # 3. Build market state
        market_state = self._build_market_state(symbol, candles, current_price)

        # 4. Send to LLM
        portfolio_state = {
            "capital": str(self._state["capital"]),
            "positions": self._state["positions"],
            "pnl": str(self._state["pnl"]),
        }

        prompt = self.prompt_manager.render(
            "trading_v1",
            {
                "market_state": json.dumps(market_state, indent=2, default=str),
                "portfolio": json.dumps(portfolio_state, indent=2, default=str),
            },
        )

        response = await self.llm.complete(prompt, self.llm_model)

        # 5. Create decision contract
        decision = DecisionContract(
            action=response.action,
            confidence=response.confidence,
            thesis=response.thesis,
            entry_price=response.entry_price or current_price,
            stop_loss=response.stop_loss,
            take_profit=response.take_profit,
            reasoning=response.reasoning,
            provider="kilo",
            model=self.llm_model,
        )

        # Record decision
        decision_record = {
            "symbol": symbol,
            "action": decision.action.value,
            "confidence": float(decision.confidence),
            "thesis": decision.thesis,
            "provider": decision.provider,
            "model": decision.model,
            "reasoning": decision.reasoning,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._state["decisions"].append(decision_record)

        logger.info(
            "Decision for %s: %s (confidence: %s)",
            symbol,
            decision.action.value,
            decision.confidence,
        )

        # 6. Risk evaluation
        risk_result = self.risk_engine.evaluate(decision)

        if not risk_result.is_approved:
            logger.info(
                "Risk rejected for %s: %s",
                symbol,
                [v.code for v in risk_result.violations],
            )
            return

        # 7. Execute trade
        if decision.action == TradingAction.LONG:
            order_result = await self.execution.execute_order(
                order_id=uuid4(),
                idempotency_key=uuid4(),
                symbol=symbol,
                side=__import__("aegis.domain.enums", fromlist=["OrderSide"]).OrderSide.BUY,
                quantity=risk_result.approved_quantity,
                price=risk_result.approved_price,
                correlation_id=decision.correlation_id,
                risk_approved=True,
            )

            if order_result.fill_price:
                # Update portfolio
                from aegis.domain.enums import OrderSide
                self.portfolio.record_fill(
                    asset=symbol,
                    side=PositionSide.LONG,
                    quantity=risk_result.approved_quantity,
                    price=order_result.fill_price,
                )

                # Record position
                entry = order_result.fill_price
                pnl = (current_price - entry) * risk_result.approved_quantity
                position = {
                    "id": str(uuid4()),
                    "symbol": symbol,
                    "side": "LONG",
                    "quantity": str(risk_result.approved_quantity),
                    "entry_price": str(entry),
                    "current_price": str(current_price),
                    "pnl": str(pnl),
                    "pnl_pct": str((pnl / (entry * risk_result.approved_quantity) * 100) if entry > 0 else 0),
                    "stop_loss": str(decision.stop_loss) if decision.stop_loss else None,
                    "take_profit": str(decision.take_profit) if decision.take_profit else None,
                    "status": "OPEN",
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                }
                self._state["positions"].append(position)

                # Record order
                order_record = {
                    "id": str(order_result.order_id),
                    "symbol": symbol,
                    "side": "BUY",
                    "quantity": str(risk_result.approved_quantity),
                    "price": str(order_result.fill_price),
                    "status": "FILLED",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._state["orders"].append(order_record)

                # Update risk engine
                self.risk_engine.record_position_open()

                logger.info(
                    "Order filled: %s %s @ R$ %s",
                    risk_result.approved_quantity,
                    symbol,
                    order_result.fill_price,
                )

        elif decision.action == TradingAction.CLOSE:
            # Close existing position
            for pos in self._state["positions"]:
                if pos["symbol"] == symbol and pos["status"] == "OPEN":
                    pos["status"] = "CLOSED"
                    # Calculate P&L — all values stay as Decimal strings
                    entry = Decimal(pos["entry_price"])
                    pnl = (current_price - entry) * Decimal(pos["quantity"])
                    self._state["pnl"] = str(Decimal(self._state["pnl"]) + pnl)
                    self._state["capital"] = str(Decimal(self._state["capital"]) + pnl)

                    # Add to history
                    trade = {
                        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "symbol": symbol,
                        "side": "LONG",
                        "quantity": pos["quantity"],
                        "entry_price": pos["entry_price"],
                        "exit_price": str(current_price),
                        "pnl": str(pnl),
                        "fee": "0",
                    }
                    self._state["history"].append(trade)

                    # Update risk engine
                    self.risk_engine.record_position_close()
                    self.risk_engine.record_daily_pnl(pnl)

                    logger.info("Position closed: %s, P&L: R$ %s", symbol, pnl)
                    break

    def _build_market_state(
        self, symbol: str, candles: list[dict], current_price: Decimal
    ) -> dict[str, Any]:
        """Build market state from candles."""
        if not candles:
            return {"symbol": symbol, "current_price": str(current_price)}

        # Calculate indicators from candles
        closes = [Decimal(str(c.get("close", "0"))) for c in candles if c.get("close")]
        volumes = [Decimal(str(c.get("volume", "0"))) for c in candles if c.get("volume")]

        # Simple moving averages
        sma_20 = sum(closes[-20:]) / min(20, len(closes)) if closes else Decimal("0")
        sma_50 = sum(closes[-50:]) / min(50, len(closes)) if closes else Decimal("0")

        # Volatility (standard deviation of returns)
        if len(closes) > 1:
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
            volatility = (sum(r**2 for r in returns) / len(returns)) ** Decimal("0.5")
        else:
            volatility = Decimal("0")

        # Trend
        trend = "BULLISH" if current_price > sma_20 > sma_50 else "BEARISH" if current_price < sma_20 < sma_50 else "NEUTRAL"

        return {
            "symbol": symbol,
            "current_price": str(current_price),
            "sma_20": str(sma_20),
            "sma_50": str(sma_50),
            "volatility": str(volatility),
            "trend": trend,
            "volume_24h": str(sum(volumes[-24:])) if volumes else "0",
            "candles_count": len(candles),
            "last_5_closes": [str(c) for c in closes[-5:]],
        }


# Singleton
_worker: AutonomousWorker | None = None


def get_worker() -> AutonomousWorker:
    global _worker
    if _worker is None:
        _worker = AutonomousWorker()
    return _worker
