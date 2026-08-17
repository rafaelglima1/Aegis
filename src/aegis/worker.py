"""AEGIS Autonomous Worker — real trading loop."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from aegis.ai_engine.decision_engine import DecisionContract, DecisionEngine
from aegis.ai_engine.provider import LLMProvider, LLMResponse
from aegis.ai_engine.prompt_manager import PromptManager, PromptVersion
from aegis.domain.enums import TradingAction, PositionSide
from aegis.risk_engine.risk_engine import RiskEngine
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.execution.engine import ExecutionEngine
from aegis.portfolio.portfolio import Portfolio
from aegis.audit import AuditLogger

logger = logging.getLogger("aegis.worker")

_SETTINGS_FILE = Path("/home/ubuntu/aegis/.env.prod")
_PROMPT_FILE = Path("/home/ubuntu/aegis/prompt_template.txt")
_STATE_FILE = Path("/home/ubuntu/aegis/worker_state.json")


def _read_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    if _SETTINGS_FILE.exists():
        for line in _SETTINGS_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


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
        self.broker = self._create_broker()
        self.execution = ExecutionEngine(self.broker)
        # AC-FIN-02: Portfolio starts with configured capital
        self.portfolio = Portfolio(initial_cash=self.capital)
        self.audit = AuditLogger()

        # Register default prompt (will be rebuilt dynamically by _reload_config)
        self.prompt_manager.register(
            PromptVersion(
                version="trading_v1",
                template="""Você é um trader de swing trade de criptomoedas. Analise os dados de mercado e tome uma decisão de trading.

Dados de Mercado:
{market_state}

Portfólio Atual:
{portfolio}

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
                description="Default trading prompt (overridden by _reload_config)",
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

    def close_position_manual(self, position_id: str) -> dict[str, Any]:
        """Close a position manually from the dashboard API.

        Routes through RiskEngine.evaluate() then Portfolio.close_position().
        Returns dict with status, realized P&L, and errors.

        Pre-existing positions must have current_price stored from the last tick.
        """
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction

        # Find position by ID
        target = None
        for pos in self._state["positions"]:
            if pos.get("id") == position_id and pos.get("status") == "OPEN":
                target = pos
                break

        if target is None:
            return {"status": "NOT_FOUND", "error": f"Position {position_id} not found"}

        symbol = target["symbol"]

        # C6-03: CLOSE must pass through RiskEngine.evaluate() first
        close_decision = DecisionContract(
            action=TradingAction.CLOSE,
            confidence=Decimal("1.0"),
            thesis=f"Manual CLOSE for {symbol}",
        )
        close_risk = self.risk_engine.evaluate(close_decision)
        if not close_risk.is_approved:
            return {"status": "REJECTED", "error": "Risk rejected CLOSE", "violations": [v.code for v in close_risk.violations]}

        entry_price = Decimal(target["entry_price"])
        qty = Decimal(target["quantity"])
        current_price = Decimal(target.get("current_price", target["entry_price"]))
        entry_fee = Decimal(target.get("entry_fee", "0"))

        # AC-C3-01/02: Exit fee from broker config — use same rate as autonomous CLOSE
        exit_fee = Decimal("0.50")

        # Route through Portfolio (single source of truth)
        realized = self.portfolio.close_position(
            asset=symbol,
            price=current_price,
            fee=exit_fee,
        )

        target["status"] = "CLOSED"

        # Update state from Portfolio (single source of truth)
        self._state["capital"] = str(self.portfolio.cash)
        self._state["pnl"] = str(self.portfolio.total_realized_pnl)

        # Add to history
        trade = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "symbol": symbol,
            "side": "LONG",
            "quantity": target["quantity"],
            "entry_price": target["entry_price"],
            "exit_price": str(current_price),
            "pnl": str(realized),
            "fee": str(exit_fee),
        }
        self._state["history"].append(trade)

        # Update risk engine
        self.risk_engine.record_position_close()
        self.risk_engine.record_daily_pnl(realized)

        # Persist
        self._save_state()

        logger.info(
            "Manual close: %s, realized: R$ %s, capital: R$ %s",
            symbol,
            realized,
            self.portfolio.cash,
        )

        return {
            "status": "CLOSED",
            "pnl": str(realized),
            "capital": str(self.portfolio.cash),
        }

    def _create_broker(self) -> Any:
        """Create broker via factory based on environment configuration.

        SANDBOX -> SandboxBroker
        LIVE + LIVE_ENABLED=true -> MercadoBitcoinBroker
        LIVE + LIVE_ENABLED=false -> RuntimeError
        """
        from aegis.execution.factory import create_broker
        from aegis.config import Settings, TradingEnvironment

        env = _read_env_file()
        trading_env = env.get("TRADING_ENVIRONMENT", "SANDBOX")
        live_enabled = env.get("LIVE_ENABLED", "false").lower() == "true"
        live_api_key = env.get("MB_API_KEY", "")
        live_api_secret = env.get("MB_API_SECRET", "")

        settings = Settings(
            trading_environment=TradingEnvironment(trading_env),
            live_enabled=live_enabled,
            live_api_key=live_api_key,
            live_api_secret=live_api_secret,
        )
        return create_broker(settings)

    def _save_state(self) -> None:
        """Persist worker state to JSON file for restart recovery.
        AC-FIN-12: Positions survive restart.
        AC-C5-03: Portfolio financial state persists across restart."""
        try:
            state_to_save = {
                "capital": str(self.portfolio.cash),
                "pnl": str(self.portfolio.total_realized_pnl),
                "total_fees": str(self.portfolio.total_fees),
                "positions": self._state["positions"],
                "orders": self._state["orders"],
                "history": self._state["history"],
                "decisions": self._state["decisions"][-50:],
            }
            _STATE_FILE.write_text(json.dumps(state_to_save, default=str), encoding="utf-8")
        except Exception as e:
            logger.error("Failed to save state: %s", e)

    def _load_state(self) -> None:
        """Load persisted state and reconstruct Risk Engine + Portfolio.
        AC-FIN-12: Risk Engine reconstructs positions after restart.
        AC-FIN-13: Restart does not artificially zero positions_count.
        AC-C5-03: Portfolio reconstructed from persisted financial state."""
        if not _STATE_FILE.exists():
            logger.info("No persisted state found, starting fresh")
            return

        try:
            saved = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            self._state["positions"] = saved.get("positions", [])
            self._state["orders"] = saved.get("orders", [])
            self._state["history"] = saved.get("history", [])
            self._state["decisions"] = saved.get("decisions", [])

            # AC-C5-03: Reconstruct Portfolio from persisted financial state
            saved_capital = Decimal(saved.get("capital", str(self.capital)))
            saved_pnl = Decimal(saved.get("pnl", "0"))
            saved_fees = Decimal(saved.get("total_fees", "0"))

            self.portfolio._cash = saved_capital
            self.portfolio._total_realized_pnl = saved_pnl
            self.portfolio._total_fees = saved_fees

            # Reconstruct Portfolio position entries from OPEN positions
            for pos in self._state["positions"]:
                if pos.get("status") == "OPEN":
                    from aegis.domain.enums import PositionSide, PositionStatus
                    from aegis.portfolio.portfolio import PositionEntry
                    symbol = pos["symbol"]
                    entry = PositionEntry(
                        asset=symbol,
                        side=PositionSide.LONG,
                        status=PositionStatus.OPEN,
                        quantity=Decimal(pos.get("quantity", "0")),
                        average_entry=Decimal(pos.get("entry_price", "0")),
                        current_price=Decimal(pos.get("current_price", pos.get("entry_price", "0"))),
                        entry_fee=Decimal(pos.get("entry_fee", "0")),
                    )
                    self.portfolio._positions[symbol] = entry

            # Sync state from Portfolio
            self._state["capital"] = str(self.portfolio.cash)
            self._state["pnl"] = str(self.portfolio.total_realized_pnl)

            # Reconstruct Risk Engine state from persisted OPEN positions
            open_count = sum(1 for p in self._state["positions"] if p.get("status") == "OPEN")
            total_exposure = sum(
                Decimal(p.get("quantity", "0")) * Decimal(p.get("current_price", p.get("entry_price", "0")))
                for p in self._state["positions"]
                if p.get("status") == "OPEN"
            )
            self.risk_engine.rebuild_from_open_positions(open_count, total_exposure)
            logger.info(
                "State restored: %d open positions, capital R$ %s, P&L R$ %s, exposure R$ %s",
                open_count,
                self.portfolio.cash,
                self.portfolio.total_realized_pnl,
                total_exposure,
            )
        except Exception as e:
            logger.error("Failed to load state: %s", e)

    def _reload_config(self) -> None:
        """Re-read .env.prod and update all settings + risk engine."""
        env = _read_env_file()
        if not env:
            return

        # Update basic settings
        self.symbols = env.get("TRADING_SYMBOLS", ",".join(self.symbols)).split(",")
        self.timeframe = env.get("TRADING_TIMEFRAME", self.timeframe)
        self.capital = Decimal(env.get("TRADING_CAPITAL", str(self.capital)))
        self.risk_pct = Decimal(env.get("RISK_PER_TRADE_PCT", str(self.risk_pct * 100))) / Decimal("100")
        self.max_positions = int(env.get("MAX_POSITIONS", str(self.max_positions)))
        self.mandatory_stop = env.get("MANDATORY_STOP", str(self.mandatory_stop).lower()).lower() == "true"
        self.mandatory_take_profit = env.get("MANDATORY_TAKE_PROFIT", str(self.mandatory_take_profit).lower()).lower() == "true"
        self.long_only = env.get("LONG_ONLY", str(self.long_only).lower()).lower() == "true"
        self.max_daily_loss_pct = Decimal(env.get("MAX_DAILY_LOSS_PCT", str(self.max_daily_loss_pct * 100))) / Decimal("100")
        self.max_position_size_pct = Decimal(env.get("MAX_POSITION_SIZE_PCT", str(self.max_position_size_pct * 100))) / Decimal("100")
        self.max_exposure_pct = Decimal(env.get("MAX_EXPOSURE_PCT", str(self.max_exposure_pct * 100))) / Decimal("100")
        self.min_confidence = Decimal(env.get("MIN_CONFIDENCE", str(self.min_confidence)))
        self.circuit_breaker_pct = Decimal(env.get("CIRCUIT_BREAKER_PCT", str(self.circuit_breaker_pct * 100))) / Decimal("100")

        # Update risk engine limits
        self.risk_engine.limits = RiskLimits(
            reference_capital=self.capital,
            max_risk_per_trade_pct=self.risk_pct,
            max_simultaneous_positions=self.max_positions,
            circuit_breaker_drawdown_pct=self.circuit_breaker_pct,
            max_daily_loss_pct=self.max_daily_loss_pct,
            max_position_size_pct=self.max_position_size_pct,
            max_exposure_pct=self.max_exposure_pct,
        )

        # Update state from Portfolio (single source of truth, not config)
        self._state["capital"] = str(self.portfolio.cash)
        self._state["pnl"] = str(self.portfolio.total_realized_pnl)
        self._state["risk_limits"] = {
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
        }

        # Rebuild LLM prompt from file or fallback to dynamic
        if _PROMPT_FILE.exists():
            template = _PROMPT_FILE.read_text(encoding="utf-8")
            logger.info("Loaded prompt from file (%d chars)", len(template))
        else:
            direction = "Apenas LONG (sem SHORT)" if self.long_only else "LONG e SHORT"
            sl_tp_rules = []
            if self.mandatory_stop:
                sl_tp_rules.append("- Stop loss obrigatório")
            else:
                sl_tp_rules.append("- Stop loss opcional")
            if self.mandatory_take_profit:
                sl_tp_rules.append("- Take profit obrigatório")
            else:
                sl_tp_rules.append("- Take profit opcional")

            _rules = "\n".join(sl_tp_rules)
            _max_pos = self.max_positions
            _risk = self.risk_pct * 100
            _cap = self.capital
            _conf = self.min_confidence * 100
            _daily = self.max_daily_loss_pct * 100
            _pos_sz = self.max_position_size_pct * 100

            template = (
                "Você é um trader de swing trade de criptomoedas. Analise os dados de mercado e tome uma decisão de trading.\n"
                "\n"
                "Dados de Mercado:\n"
                "{market_state}\n"
                "\n"
                "Portfólio Atual:\n"
                "{portfolio}\n"
                "\n"
                "Regras:\n"
                f"- {direction}\n"
                f"- Máximo {_max_pos} posição(ões) por vez\n"
                f"- Risco de {_risk}% por trade\n"
                f"- Capital de referência: R$ {_cap}\n"
                f"{_rules}\n"
                f"- Só opera se confiança >= {_conf}%\n"
                f"- Perda diária máxima: {_daily}% do capital\n"
                f"- Tamanho máximo de posição: {_pos_sz}% do capital\n"
                "\n"
                "Responda com JSON:\n"
                "{{\n"
                '    "action": "LONG" ou "HOLD" ou "CLOSE",\n'
                '    "confidence": 0.0 a 1.0,\n'
                '    "thesis": "raciocínio breve",\n'
                '    "entry_price": número ou null,\n'
                '    "stop_loss": número ou null,\n'
                '    "take_profit": número ou null,\n'
                '    "reasoning": "análise detalhada"\n'
                "}}"
            )

        self.prompt_manager.register(
            PromptVersion(
                version="trading_v1",
                template=template,
                description="Dynamic trading prompt from config",
            )
        )
        logger.info("Config reloaded: %d symbols, max_positions=%d, risk=%.1f%%",
                     len(self.symbols), self.max_positions, self.risk_pct * 100)

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
        # AC-FIN-12: Load persisted state and rebuild Risk Engine on startup
        self._load_state()
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

        # Reload config from .env.prod (settings may have changed via dashboard)
        self._reload_config()

        for symbol in self.symbols:
            try:
                await self._process_symbol(symbol.strip())
            except Exception as e:
                logger.error("Error processing %s: %s", symbol, e)

        # Update current_price and P&L for all open positions
        self._update_positions_pnl()

        # C6-02/C6-02B/C6-02C: Sync RiskEngine and state from Portfolio (source of truth)
        self.risk_engine.update_equity(self.portfolio.equity)
        self._state["capital"] = str(self.portfolio.cash)
        self._state["pnl"] = str(self.portfolio.total_realized_pnl)
        self._state["exposure"] = str(self.portfolio.exposure)
        self._state["peak_equity"] = str(self.portfolio._peak_equity)

        # AC-FIN-12: Persist state for restart recovery
        self._save_state()

        # Broadcast state update
        await self._broadcast({"type": "state_update", **self._state})

    def _update_positions_pnl(self) -> None:
        """C6-05: Sync P&L from Portfolio (source of truth) to state dict."""
        for pos in self._state["positions"]:
            if pos["status"] != "OPEN":
                continue
            symbol = pos["symbol"]
            entry = Decimal(pos["entry_price"])
            qty = Decimal(pos["quantity"])
            # C6-01: Read current_price from Portfolio if available
            portfolio_pos = self.portfolio._positions.get(symbol)
            if portfolio_pos and portfolio_pos.quantity > 0:
                current = portfolio_pos.current_price
                pos["current_price"] = str(current)
            else:
                current = Decimal(pos["current_price"])
            # C6-05: Read unrealized_pnl from Portfolio
            if portfolio_pos:
                unrealized = portfolio_pos.unrealized_pnl
            else:
                unrealized = (current - entry) * qty
            pos["pnl"] = str(unrealized)
            pos["pnl_pct"] = str((unrealized / (entry * qty) * 100) if entry > 0 and qty > 0 else 0)

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

        # C6-01: Update Portfolio with real market price (Portfolio is source of truth)
        if symbol in self.portfolio._positions and self.portfolio._positions[symbol].quantity > 0:
            self.portfolio.update_prices({symbol: current_price})

        # Sync current_price from Portfolio to state (UI/API representation)
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
                risk_decision=risk_result,
            )

            if order_result.fill_price:
                # Update portfolio — AC-FIN-08: fee propagated from broker
                from aegis.domain.enums import OrderSide
                self.portfolio.record_fill(
                    asset=symbol,
                    side=PositionSide.LONG,
                    quantity=risk_result.approved_quantity,
                    price=order_result.fill_price,
                    fee=order_result.fee,
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
                    "entry_fee": str(order_result.fee),
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

                # Update capital from Portfolio (single source of truth)
                self._state["capital"] = str(self.portfolio.cash)
                self._state["pnl"] = str(self.portfolio.total_realized_pnl)

                logger.info(
                    "Order filled: %s %s @ R$ %s",
                    risk_result.approved_quantity,
                    symbol,
                    order_result.fill_price,
                )

        elif decision.action == TradingAction.CLOSE:
            # AC-C3-01: CLOSE routes through Portfolio.close_position()
            # AC-C3-02: Fee comes from broker config, not hardcoded zero
            # C6-03: CLOSE must pass through RiskEngine.evaluate() first
            close_decision = DecisionContract(
                action=TradingAction.CLOSE,
                confidence=Decimal("1.0"),
                thesis=f"Autonomous CLOSE for {symbol}",
            )
            close_risk = self.risk_engine.evaluate(close_decision)
            if not close_risk.is_approved:
                logger.warning(
                    "Risk rejected CLOSE for %s: %s",
                    symbol,
                    [v.code for v in close_risk.violations],
                )
                return

            for pos in self._state["positions"]:
                if pos["symbol"] == symbol and pos["status"] == "OPEN":
                    entry = Decimal(pos["entry_price"])
                    qty = Decimal(pos["quantity"])

                    # Exit fee: same rate as SandboxBroker (0.50 per order)
                    exit_fee = Decimal("0.50")

                    # Route through Portfolio for correct net P&L accounting
                    realized = self.portfolio.close_position(
                        asset=symbol,
                        price=current_price,
                        fee=exit_fee,
                    )

                    pos["status"] = "CLOSED"

                    # Update state from Portfolio (single source of truth)
                    self._state["capital"] = str(self.portfolio.cash)
                    self._state["pnl"] = str(self.portfolio.total_realized_pnl)

                    # Add to history
                    trade = {
                        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "symbol": symbol,
                        "side": "LONG",
                        "quantity": pos["quantity"],
                        "entry_price": pos["entry_price"],
                        "exit_price": str(current_price),
                        "pnl": str(realized),
                        "fee": str(exit_fee),
                    }
                    self._state["history"].append(trade)

                    # Update risk engine
                    self.risk_engine.record_position_close()
                    self.risk_engine.record_daily_pnl(realized)

                    logger.info("Position closed: %s, P&L: R$ %s", symbol, realized)
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
