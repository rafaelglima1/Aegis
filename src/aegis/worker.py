"""AEGIS Autonomous Worker — real trading loop."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
from aegis.risk_engine.setup_scorer import SetupScorer, SetupWeights
from aegis.risk_engine.position_manager import PositionManager, PositionManagerConfig
from aegis.risk_engine.trade_journal import TradeJournal, TradeRecord
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

                # For reasoning models, JSON is often in reasoning field
                # Try reasoning first if content doesn't contain JSON
                result = self._parse_response(content)

                if result.action == TradingAction.HOLD and result.confidence == Decimal("0"):
                    # Content parse failed, try reasoning
                    if reasoning.strip():
                        result = self._parse_response(reasoning)

                    # If still failed, try combined content
                    if result.action == TradingAction.HOLD and result.confidence == Decimal("0"):
                        combined = (content + "\n" + reasoning).strip()
                        if combined:
                            result = self._parse_response(combined)

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
        """Parse LLM JSON response with robust extraction."""
        if not content or not content.strip():
            logger.warning("Empty LLM content, defaulting to HOLD")
            return LLMResponse(
                action=TradingAction.HOLD,
                confidence=Decimal("0"),
                thesis="Empty LLM response",
            )

        try:
            # Step 1: Strip markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                parts = content.split("```")
                if len(parts) >= 3:
                    content = parts[1]

            content = content.strip()

            # Step 2: Try direct JSON parse first
            try:
                data = json.loads(content)
                return self._build_response(data)
            except json.JSONDecodeError:
                pass

            # Step 3: Find JSON object using brace matching (search from end)
            json_str = self._extract_json(content)
            if json_str:
                try:
                    data = json.loads(json_str)
                    return self._build_response(data)
                except json.JSONDecodeError:
                    pass

            # Step 4: Try regex to find JSON pattern
            import re
            match = re.search(r'\{[^{}]*"action"[^{}]*\}', content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                    return self._build_response(data)
                except json.JSONDecodeError:
                    pass

            # Step 5: Try to find action/confidence/thesis with regex
            action_match = re.search(r'"action"\s*:\s*"(LONG|HOLD|CLOSE)"', content)
            conf_match = re.search(r'"confidence"\s*:\s*([\d.]+)', content)
            thesis_match = re.search(r'"thesis"\s*:\s*"([^"]*)"', content)

            if action_match:
                action_str = action_match.group(1).upper()
                try:
                    action = TradingAction(action_str)
                except ValueError:
                    action = TradingAction.HOLD

                confidence = Decimal(conf_match.group(1)) if conf_match else Decimal("0.5")
                thesis = thesis_match.group(1) if thesis_match else "LLM response parsed via regex"

                # Try to extract entry/stop/take_profit
                entry_match = re.search(r'"entry_price"\s*:\s*([\d.]+)', content)
                stop_match = re.search(r'"stop_loss"\s*:\s*([\d.]+)', content)
                tp_match = re.search(r'"take_profit"\s*:\s*([\d.]+)', content)

                return LLMResponse(
                    action=action,
                    confidence=confidence,
                    thesis=thesis,
                    entry_price=Decimal(entry_match.group(1)) if entry_match else None,
                    stop_loss=Decimal(stop_match.group(1)) if stop_match else None,
                    take_profit=Decimal(tp_match.group(1)) if tp_match else None,
                )

            logger.warning("Could not extract any trading decision from LLM response")
            return LLMResponse(
                action=TradingAction.HOLD,
                confidence=Decimal("0"),
                thesis="Could not parse LLM response",
            )

        except Exception as e:
            logger.error("Failed to parse LLM response: %s", e)
            return LLMResponse(
                action=TradingAction.HOLD,
                confidence=Decimal("0"),
                thesis=f"Parse error: {e}",
            )

    def _extract_json(self, text: str) -> str | None:
        """Extract JSON object from text using brace matching."""
        # Find all opening braces
        for i in range(len(text) - 1, -1, -1):
            if text[i] != "{":
                continue

            depth = 0
            in_string = False
            escape = False

            for j in range(i, len(text)):
                c = text[j]
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
                            candidate = text[i : j + 1]
                            if '"action"' in candidate:
                                return candidate
                            break
        return None

    def _build_response(self, data: dict) -> LLMResponse:
        """Build LLMResponse from parsed JSON dict."""
        action_str = data.get("action", "HOLD").upper()
        try:
            action = TradingAction(action_str)
        except ValueError:
            action = TradingAction.HOLD

        confidence = Decimal(str(data.get("confidence", 0)))
        # Clamp confidence to 0-1 range
        if confidence > Decimal("1"):
            confidence = confidence / Decimal("100")

        return LLMResponse(
            action=action,
            confidence=confidence,
            thesis=data.get("thesis", ""),
            entry_price=Decimal(str(data["entry_price"])) if data.get("entry_price") else None,
            stop_loss=Decimal(str(data["stop_loss"])) if data.get("stop_loss") else None,
            take_profit=Decimal(str(data["take_profit"])) if data.get("take_profit") else None,
            reasoning=data.get("reasoning", ""),
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

    def __init__(self, settings: Any | None = None) -> None:
        # AC1: Settings is the single source of truth for all configuration
        if settings is None:
            from aegis.config import get_settings as _get_settings
            settings = _get_settings()

        self._settings = settings

        # AC3: All config from Settings — no parallel env access
        self.llm_base_url = settings.llm_base_url
        self.llm_api_key = settings.llm_api_key
        self.llm_model = settings.llm_model
        self.symbols = settings.trading_symbols_list
        self.timeframe = settings.trading_timeframe
        self.capital = settings.initial_capital
        self.risk_pct = settings.risk_per_trade_decimal
        self.max_positions = settings.max_positions
        self.mandatory_stop = settings.mandatory_stop
        self.mandatory_take_profit = settings.mandatory_take_profit
        self.long_only = settings.long_only
        self.max_daily_loss_pct = settings.max_daily_loss_decimal
        self.max_position_size_pct = settings.max_position_size_decimal
        self.max_exposure_pct = settings.max_exposure_decimal
        self.min_confidence = settings.min_confidence
        self.circuit_breaker_pct = settings.circuit_breaker_decimal

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

        # New: Setup scorer, position manager, trade journal
        self.setup_scorer = SetupScorer()
        self.position_manager = PositionManager()
        self.trade_journal = TradeJournal()

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
        self._state_valid = True  # False if persisted state is corrupted/incompatible
        self._reconciled = False  # False until exchange reconciliation confirms
        self._reconciliation_attempted = False  # True once reconciliation was attempted
        self._reconciliation_status = "SKIPPED"  # Until reconciliation runs
        self._reconciliation_interval = self._settings.reconciliation_interval_seconds
        self._last_reconciliation_time: float = 0.0  # time.monotonic() timestamp
        self._state: dict[str, Any] = {
            "capital": str(self.capital),
            "pnl": "0.00",
            "positions": [],
            "orders": [],
            "history": [],
            "decisions": [],
            "exposure": "0.00",
            "peak_equity": str(self.capital),
            "equity": str(self.capital),
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
        s = self._state.copy()
        s["state_valid"] = self._state_valid
        s["reconciled"] = self._reconciled
        s["reconciliation_status"] = self._reconciliation_status
        return s

    async def close_position_manual(self, position_id: str) -> dict[str, Any]:
        """Close a position manually from the dashboard API.

        C7-03: Routes through RiskEngine → ExecutionEngine → Broker → Portfolio.
        Broker fill_price and fee are used for Portfolio.close_position().
        If broker rejects SELL (e.g. MercadoBitcoinBroker LIVE), position stays open (fail-closed).

        Returns dict with status, realized P&L, and errors.

        Pre-existing positions must have current_price stored from the last tick.
        """
        from aegis.ai_engine.decision_engine import DecisionContract
        from aegis.domain.enums import TradingAction, OrderSide, OrderStatus
        from uuid import uuid4

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

        current_price = Decimal(target.get("current_price", target["entry_price"]))
        qty = Decimal(target["quantity"])

        # C7-03: Execute SELL through ExecutionEngine → Broker
        order_result = await self.execution.execute_order(
            order_id=uuid4(),
            idempotency_key=uuid4(),
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=qty,
            price=current_price,
            correlation_id=close_decision.correlation_id,
            risk_decision=close_risk,
        )

        # C7-03: If broker rejected SELL, fail-closed — do not update Portfolio
        if order_result.status != OrderStatus.FILLED:
            logger.warning(
                "Broker rejected SELL for %s: %s (status=%s). Position stays open.",
                symbol,
                order_result.error,
                order_result.status,
            )
            return {
                "status": "BROKER_REJECTED",
                "error": f"Broker rejected SELL: {order_result.error}",
                "violations": [v.code for v in close_risk.violations],
            }

        # C7-03: Only after broker fill, update Portfolio with broker's fill_price and fee
        fill_price = order_result.fill_price or current_price
        fill_fee = order_result.fee

        realized = self.portfolio.close_position(
            asset=symbol,
            price=fill_price,
            fee=fill_fee,
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
            "exit_price": str(fill_price),
            "pnl": str(realized),
            "fee": str(fill_fee),
        }
        self._state["history"].append(trade)

        # Update risk engine
        self.risk_engine.record_position_close()
        self.risk_engine.record_daily_pnl(realized)

        # Persist
        self._save_state()

        logger.info(
            "Manual close: %s, fill: R$ %s, realized: R$ %s, capital: R$ %s",
            symbol,
            fill_price,
            realized,
            self.portfolio.cash,
        )

        return {
            "status": "CLOSED",
            "pnl": str(realized),
            "capital": str(self.portfolio.cash),
        }

    def _create_broker(self) -> Any:
        """Create broker via factory based on Settings (AC1)."""
        from aegis.execution.factory import create_broker
        return create_broker(self._settings, initial_balance=self.capital)

    def _save_state(self) -> None:
        """Persist worker state to JSON file for restart recovery.

        Uses atomic write (temp file + rename) to prevent corruption on crash.
        Persists: capital, pnl, fees, peak_equity, risk state, positions,
                  orders, history, decisions.
        """
        try:
            risk_state = {}
            if hasattr(self.risk_engine, '_daily_pnl'):
                risk_state["daily_pnl"] = str(self.risk_engine._daily_pnl)
            if hasattr(self.risk_engine, '_daily_trade_count'):
                risk_state["daily_trade_count"] = self.risk_engine._daily_trade_count
            if hasattr(self.risk_engine, '_last_trade_time'):
                # _last_trade_time is dict[symbol, timestamp] — serialize properly
                ltt = self.risk_engine._last_trade_time
                if isinstance(ltt, dict):
                    risk_state["last_trade_time"] = {
                        k: v.isoformat() if hasattr(v, 'isoformat') else str(v)
                        for k, v in ltt.items()
                    }
                else:
                    risk_state["last_trade_time"] = None
            if hasattr(self.risk_engine, '_kill_switch_active'):
                risk_state["kill_switch_active"] = self.risk_engine._kill_switch_active
            if hasattr(self.risk_engine, '_circuit_breaker_active'):
                risk_state["circuit_breaker_active"] = self.risk_engine._circuit_breaker_active

            state_to_save = {
                "capital": str(self.portfolio.cash),
                "pnl": str(self.portfolio.total_realized_pnl),
                "total_fees": str(self.portfolio.total_fees),
                "peak_equity": str(self.portfolio._peak_equity),
                "risk_peak_equity": str(self.risk_engine._peak_equity),
                "risk_state": risk_state,
                "positions": self._state["positions"],
                "orders": self._state["orders"],
                "history": self._state["history"],
                "decisions": self._state["decisions"][-50:],
                "idempotency_keys": [str(k) for k in getattr(self.broker, '_idempotency_keys', set())],
            }
            # Atomic write: write to temp file, then rename
            tmp_file = _STATE_FILE.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(state_to_save, default=str), encoding="utf-8")
            tmp_file.replace(_STATE_FILE)
        except Exception as e:
            logger.error("Failed to save state: %s", e)

    def _load_state(self) -> None:
        """Load persisted state and reconstruct Risk Engine + Portfolio.

        STATE UNKNOWN ≠ STATE EMPTY.
        - File not found → first boot, start fresh (state_valid=True)
        - File valid → restore state (state_valid=True)
        - File corrupted / incompatible / read error → FAIL-SAFE (state_valid=False)
          Process stays alive for observability but does NOT trade.
        """
        if not _STATE_FILE.exists():
            logger.info("No persisted state found, first boot — starting fresh")
            self._state_valid = True
            return

        # Try to read the file
        try:
            raw = _STATE_FILE.read_text(encoding="utf-8")
        except OSError as e:
            logger.critical("Cannot read state file: %s — FAIL-SAFE", e)
            self._state_valid = False
            return

        # Try to parse JSON
        try:
            saved = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.critical(
                "Corrupted state file — FAIL-SAFE. "
                "File preserved at %s for investigation. Error: %s",
                _STATE_FILE, e,
            )
            self._state_valid = False
            return

        # Validate required schema
        if "positions" not in saved:
            logger.critical(
                "Incompatible state schema (missing 'positions') — FAIL-SAFE"
            )
            self._state_valid = False
            return

        # Validate capital if present (backward compat: old files may lack it)
        if "capital" in saved:
            try:
                Decimal(saved["capital"])
            except (InvalidOperation, ValueError, TypeError) as e:
                logger.critical(
                    "Invalid capital value in state file: %s — FAIL-SAFE", e
                )
                self._state_valid = False
                return

        # State is valid — restore it
        try:
            self._state["positions"] = saved.get("positions", [])
            self._state["orders"] = saved.get("orders", [])
            self._state["history"] = saved.get("history", [])
            self._state["decisions"] = saved.get("decisions", [])

            # Reconstruct Portfolio from persisted financial state
            # Backward compat: old state files may lack "capital" field
            saved_capital = Decimal(saved.get("capital", str(self.capital)))
            saved_pnl = Decimal(saved.get("pnl", "0"))
            saved_fees = Decimal(saved.get("total_fees", "0"))

            self.portfolio._cash = saved_capital
            self.portfolio._total_realized_pnl = saved_pnl
            self.portfolio._total_fees = saved_fees

            # Restore Portfolio peak_equity
            saved_peak_equity = saved.get("peak_equity")
            if saved_peak_equity is not None:
                self.portfolio._peak_equity = Decimal(saved_peak_equity)
            else:
                self.portfolio._peak_equity = saved_capital

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
            self._state["equity"] = str(self.portfolio.equity)

            # Reconstruct Risk Engine state from persisted OPEN positions
            open_count = sum(1 for p in self._state["positions"] if p.get("status") == "OPEN")
            total_exposure = sum(
                Decimal(p.get("quantity", "0")) * Decimal(p.get("current_price", p.get("entry_price", "0")))
                for p in self._state["positions"]
                if p.get("status") == "OPEN"
            )
            self.risk_engine.rebuild_from_open_positions(open_count, total_exposure)

            # Restore RiskEngine peak_equity
            saved_risk_peak = saved.get("risk_peak_equity")
            if saved_risk_peak is not None:
                self.risk_engine._peak_equity = Decimal(saved_risk_peak)
            else:
                self.risk_engine._peak_equity = self.risk_engine._limits.reference_capital

            # Restore persisted risk state
            risk_saved = saved.get("risk_state", {})
            if "daily_pnl" in risk_saved:
                self.risk_engine._daily_pnl = Decimal(risk_saved["daily_pnl"])
            if "daily_trade_count" in risk_saved:
                self.risk_engine._daily_trade_count = risk_saved["daily_trade_count"]
            if "last_trade_time" in risk_saved and risk_saved["last_trade_time"]:
                from datetime import datetime as _dt
                ltt = risk_saved["last_trade_time"]
                if isinstance(ltt, dict):
                    self.risk_engine._last_trade_time = {
                        k: _dt.fromisoformat(v) if isinstance(v, str) else v
                        for k, v in ltt.items()
                    }
                elif isinstance(ltt, str):
                    # Backward compat: old single-value format
                    self.risk_engine._last_trade_time = {"_default": _dt.fromisoformat(ltt)}
            if "kill_switch_active" in risk_saved:
                self.risk_engine._kill_switch_active = risk_saved["kill_switch_active"]
            if "circuit_breaker_active" in risk_saved:
                self.risk_engine._circuit_breaker_active = risk_saved["circuit_breaker_active"]

            # Sync broker balance with Portfolio
            if hasattr(self.broker, "_balance"):
                self.broker._balance = self.portfolio.cash

            # Restore idempotency keys to prevent duplicate orders after restart
            saved_keys = saved.get("idempotency_keys", [])
            if saved_keys and hasattr(self.broker, "_idempotency_keys"):
                from uuid import UUID as _UUID
                self.broker._idempotency_keys.update(
                    _UUID(k) for k in saved_keys
                )

            self._state_valid = True
            logger.info(
                "State restored: %d open positions, capital R$ %s, P&L R$ %s",
                open_count, self.portfolio.cash, self.portfolio.total_realized_pnl,
            )
        except Exception as e:
            logger.critical("Failed to restore state — FAIL-SAFE: %s", e)
            self._state_valid = False

    async def _reconcile_exchange(self) -> None:
        """Reconcile local state against exchange state.

        Must be called after _load_state() and before trading begins.
        Blocks trading if reconciliation fails or diverges.
        """
        from aegis.reconciliation import (
            ReconciliationEngine, ExchangeSnapshot,
            LocalSnapshot, LocalPosition, LocalOrder,
            ReconciliationStatus,
        )

        self._reconciliation_status = "RECONCILING"
        self._reconciliation_attempted = True

        # Build local snapshot
        local_positions = []
        for pos in self._state.get("positions", []):
            if pos.get("status") == "OPEN":
                local_positions.append(LocalPosition(
                    symbol=pos["symbol"],
                    quantity=Decimal(pos.get("quantity", "0")),
                    entry_price=Decimal(pos.get("entry_price", "0")),
                    status="OPEN",
                ))

        local_orders = []
        for order in self._state.get("orders", []):
            if order.get("status") in ("SUBMITTED", "PENDING"):
                local_orders.append(LocalOrder(
                    local_order_id=order.get("id", ""),
                    symbol=order.get("symbol", ""),
                    side=order.get("side", ""),
                    quantity=Decimal(order.get("quantity", "0")),
                    status=order.get("status", ""),
                ))

        local_snapshot = LocalSnapshot(
            capital=self.portfolio.cash,
            positions=local_positions,
            orders=local_orders,
            state_valid=self._state_valid,
        )

        # Get exchange snapshot
        try:
            exchange_snapshot = await self.broker.get_exchange_snapshot()
        except Exception as e:
            logger.critical("Exchange snapshot failed: %s — reconciliation ERROR", e)
            self._reconciliation_status = "ERROR"
            self._reconciled = False
            return

        if exchange_snapshot is None:
            # Broker doesn't support snapshots (e.g., old adapter)
            logger.warning(
                "Broker does not support exchange snapshot — "
                "skipping reconciliation (SANDBOX mode)"
            )
            self._reconciliation_status = "SKIPPED"
            self._reconciled = True
            return

        # Run reconciliation
        engine = ReconciliationEngine()
        result = engine.reconcile(local_snapshot, exchange_snapshot)

        self._reconciliation_status = result.status.value
        self._reconciled = result.is_reconciled

        if result.is_reconciled:
            logger.info("Exchange reconciliation: RECONCILED")
            if result.divergences:
                for d in result.divergences:
                    logger.warning(
                        "Reconciliation warning: %s — %s",
                        d.field, d.message,
                    )
        else:
            logger.critical(
                "Exchange reconciliation: %s — %s. Trading BLOCKED.",
                result.status.value,
                result.error or f"{len(result.divergences)} divergence(s)",
            )
            for d in result.divergences:
                logger.critical(
                    "  Divergence [%s]: local=%s exchange=%s — %s",
                    d.severity.value, d.local_value, d.exchange_value, d.message,
                )

    async def _periodic_reconcile(self) -> None:
        """Periodic reconciliation during trading session.

        Non-blocking: reuses the same engine and flow as startup reconciliation.
        If reconciliation fails, trading is blocked until next successful reconciliation.
        """
        logger.info("Periodic reconciliation started...")
        try:
            await self._reconcile_exchange()
        except Exception as e:
            logger.critical("Periodic reconciliation failed: %s — trading BLOCKED", e)
            self._reconciled = False
            self._reconciliation_status = "ERROR"

        if self._reconciled:
            logger.info("Periodic reconciliation: RECONCILED — trading continues")
        else:
            logger.warning(
                "Periodic reconciliation: %s — trading BLOCKED until next successful reconciliation",
                self._reconciliation_status,
            )

    def _reload_config(self) -> None:
        """Re-read environment and rebuild configuration consistently.

        AC1: All config flows through Settings. No direct file reads for operational params.
        C9.2-02: TRADING_ENVIRONMENT and LIVE_ENABLED are stored in self._settings
            but do NOT trigger broker recreation. Changing TRADING_ENVIRONMENT
            requires a full process restart for the broker to be swapped.
            Hot-reload only propagates max_positions and operational settings.
        """
        env = _read_env_file()
        if not env:
            return

        # Build a fresh Settings from ALL env values
        from aegis.config import Settings, TradingEnvironment

        settings_kwargs: dict[str, Any] = {}
        if "TRADING_ENVIRONMENT" in env:
            settings_kwargs["trading_environment"] = TradingEnvironment(env["TRADING_ENVIRONMENT"])
        if "LIVE_ENABLED" in env:
            settings_kwargs["live_enabled"] = env["LIVE_ENABLED"].lower() == "true"
        if "MAX_POSITIONS" in env:
            settings_kwargs["max_positions"] = int(env["MAX_POSITIONS"])

        try:
            new_settings = Settings(**settings_kwargs)
            self._settings = new_settings
            self.max_positions = new_settings.max_positions
        except Exception:
            # If Settings validation fails (e.g. MAX_POSITIONS > 1), skip Settings update
            # but continue propagating other operational settings from env
            pass

        # Propagate operational settings from env (using Settings as intermediate)
        self.symbols = env.get("TRADING_SYMBOLS", ",".join(self.symbols)).split(",")
        self.timeframe = env.get("TRADING_TIMEFRAME", self.timeframe)
        self.risk_pct = Decimal(env.get("RISK_PER_TRADE_PCT", str(self.risk_pct * 100))) / Decimal("100")
        self.mandatory_stop = env.get("MANDATORY_STOP", str(self.mandatory_stop).lower()).lower() == "true"
        self.mandatory_take_profit = env.get("MANDATORY_TAKE_PROFIT", str(self.mandatory_take_profit).lower()).lower() == "true"
        self.long_only = env.get("LONG_ONLY", str(self.long_only).lower()).lower() == "true"
        self.max_daily_loss_pct = Decimal(env.get("MAX_DAILY_LOSS_PCT", str(self.max_daily_loss_pct * 100))) / Decimal("100")
        self.max_position_size_pct = Decimal(env.get("MAX_POSITION_SIZE_PCT", str(self.max_position_size_pct * 100))) / Decimal("100")
        self.max_exposure_pct = Decimal(env.get("MAX_EXPOSURE_PCT", str(self.max_exposure_pct * 100))) / Decimal("100")
        self.min_confidence = Decimal(env.get("MIN_CONFIDENCE", str(self.min_confidence)))
        self.circuit_breaker_pct = Decimal(env.get("CIRCUIT_BREAKER_PCT", str(self.circuit_breaker_pct * 100))) / Decimal("100")

        # C9.1: Propagate from Worker → RiskEngine (reference_capital stays unchanged)
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
        self._state["equity"] = str(self.portfolio.equity)
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
        logger.info("Config reloaded: %d symbols, max_positions=%d, risk=%.1f%%",
                     len(self.symbols), self.max_positions, self.risk_pct * 100)

    async def _poll_order_status(
        self, order_id: Any, max_retries: int = 3, delay_seconds: float = 2.0,
    ) -> OrderResult:
        """Poll broker for order status after SUBMITTED.

        Returns the confirmed status after polling.
        Returns UNKNOWN if polling fails or times out.
        """
        from aegis.domain.enums import OrderStatus as _OS

        for attempt in range(max_retries):
            try:
                result = await self.execution.get_order_status(order_id)
                if result.status in (
                    _OS.FILLED, _OS.REJECTED, _OS.CANCELLED,
                    _OS.EXPIRED, _OS.ERROR,
                ):
                    return result
                # Still SUBMITTED/PARTIALLY_FILLED — wait and retry
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay_seconds)
            except Exception as e:
                logger.warning(
                    "Order status poll failed (attempt %d/%d): %s",
                    attempt + 1, max_retries, e,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay_seconds)

        # All retries exhausted — return UNKNOWN
        logger.warning(
            "Order status unknown after %d retries: %s", max_retries, order_id,
        )
        return OrderResult(
            order_id=order_id,
            status=_OS.ERROR,
            error=f"Status unknown after {max_retries} polling attempts",
        )
        env = _read_env_file()
        if not env:
            return

        # Build a fresh Settings from ALL env values
        from aegis.config import Settings, TradingEnvironment

        settings_kwargs: dict[str, Any] = {}
        if "TRADING_ENVIRONMENT" in env:
            settings_kwargs["trading_environment"] = TradingEnvironment(env["TRADING_ENVIRONMENT"])
        if "LIVE_ENABLED" in env:
            settings_kwargs["live_enabled"] = env["LIVE_ENABLED"].lower() == "true"
        if "MAX_POSITIONS" in env:
            settings_kwargs["max_positions"] = int(env["MAX_POSITIONS"])

        try:
            new_settings = Settings(**settings_kwargs)
            self._settings = new_settings
            self.max_positions = new_settings.max_positions
        except Exception:
            # If Settings validation fails (e.g. MAX_POSITIONS > 1), skip Settings update
            # but continue propagating other operational settings from env
            pass

        # Propagate operational settings from env (using Settings as intermediate)
        self.symbols = env.get("TRADING_SYMBOLS", ",".join(self.symbols)).split(",")
        self.timeframe = env.get("TRADING_TIMEFRAME", self.timeframe)
        self.risk_pct = Decimal(env.get("RISK_PER_TRADE_PCT", str(self.risk_pct * 100))) / Decimal("100")
        self.mandatory_stop = env.get("MANDATORY_STOP", str(self.mandatory_stop).lower()).lower() == "true"
        self.mandatory_take_profit = env.get("MANDATORY_TAKE_PROFIT", str(self.mandatory_take_profit).lower()).lower() == "true"
        self.long_only = env.get("LONG_ONLY", str(self.long_only).lower()).lower() == "true"
        self.max_daily_loss_pct = Decimal(env.get("MAX_DAILY_LOSS_PCT", str(self.max_daily_loss_pct * 100))) / Decimal("100")
        self.max_position_size_pct = Decimal(env.get("MAX_POSITION_SIZE_PCT", str(self.max_position_size_pct * 100))) / Decimal("100")
        self.max_exposure_pct = Decimal(env.get("MAX_EXPOSURE_PCT", str(self.max_exposure_pct * 100))) / Decimal("100")
        self.min_confidence = Decimal(env.get("MIN_CONFIDENCE", str(self.min_confidence)))
        self.circuit_breaker_pct = Decimal(env.get("CIRCUIT_BREAKER_PCT", str(self.circuit_breaker_pct * 100))) / Decimal("100")

        # C9.1: Propagate from Worker → RiskEngine (reference_capital stays unchanged)
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
        self._state["equity"] = str(self.portfolio.equity)
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
            _rr = self.risk_engine.limits.min_risk_reward

            template = (
                "Voce e um trader de swing trade de criptomoedas profissional.\n"
                "Analise os dados de mercado e tome uma decisao de trading.\n"
                "\n"
                "DADOS DE MERCADO:\n"
                "{market_state}\n"
                "\n"
                "PORTFOLIO ATUAL:\n"
                "{portfolio}\n"
                "\n"
                "REGRAS OBRIGATORIAS (validadas pelo codigo):\n"
                f"- Apenas LONG (sem SHORT)\n"
                f"- Maximo {_max_pos} posicao(oes) aberta(s)\n"
                f"- Stop loss OBRIGATORIO e VALIDO (abaixo do entry)\n"
                f"- Take profit OBRIGATORIO e VALIDO (acima do entry)\n"
                f"- R/R minimo de {_rr} (reward/risk >= {_rr})\n"
                f"- Risk por trade: {_risk}% do capital (R$ {_cap})\n"
                f"- Tamanho maximo: {_pos_sz}% do capital\n"
                f"- Confidence minima: {_conf}%\n"
                f"- Perda diaria maxima: {_daily}%\n"
                "\n"
                "FILTROS OBRIGATORIOS:\n"
                "- NAO entre LONG contra tendencia BEARISH (preco < SMA20 < SMA50)\n"
                "- NAO entre LONG se R/R < 1.5\n"
                "- NAO entre LONG se confidence < 50%\n"
                "- NAO entre LONG sem stop loss VALIDO\n"
                "- NAO entre LONG sem take profit VALIDO\n"
                "- NAO feche posicao por ruido de curto prazo\n"
                "- NAO faca flip-flop (LONG -> CLOSE -> LONG imediato)\n"
                "- Considere a tendencia, momentum, volume e RSI\n"
                "- Prefira HOLD a operacao ruim\n"
                "- PRESERVE CAPITAL: e melhor nao operar do que operar mal\n"
                "\n"
                "ANALISE TECNICA:\n"
                "- Verifique SMA20 vs SMA50 (tendencia)\n"
                "- Verifique RSI (sobrecomprado >70, sobrevendido <30)\n"
                "- Verifique momentum (ultimos candles)\n"
                "- Verifique volume (confirmacao)\n"
                "- Calcule R/R REAL antes de sugerir entry/stop/take_profit\n"
                "- Considere o setup_score e market_regime nos dados de mercado\n"
                "- Para LONG, prefira regime BULL/STRONG_BULL com score >= 65\n"
                "- NAO entre LONG em regime BEAR/STRONG_BEAR\n"
                "\n"
                "Responda com JSON:\n"
                "{{\n"
                '    "action": "LONG" ou "HOLD" ou "CLOSE",\n'
                '    "confidence": 0.0 a 1.0,\n'
                '    "thesis": "raciocinio breve e justificativa",\n'
                '    "entry_price": numero ou null,\n'
                '    "stop_loss": numero ou null,\n'
                '    "take_profit": numero ou null,\n'
                '    "reasoning": "analise tecnica detalhada com R/R"\n'
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
        self._load_state()

        if not self._state_valid:
            logger.critical(
                "Worker NOT READY — state is invalid. "
                "Trading is BLOCKED until state is recovered. "
                "Process alive for observability only."
            )
            while self._running:
                await asyncio.sleep(60)
            return

        # Exchange reconciliation before trading
        await self._reconcile_exchange()

        if not self._reconciled:
            logger.critical(
                "Worker NOT READY — exchange reconciliation failed. "
                "Status: %s. Trading is BLOCKED.",
                self._reconciliation_status,
            )
            while self._running:
                await asyncio.sleep(60)
            return

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

        # Periodic reconciliation (time-based, not tick-based)
        now = time.monotonic()
        elapsed = now - self._last_reconciliation_time
        if (self._reconciliation_interval > 0 and
                elapsed >= self._reconciliation_interval):
            self._last_reconciliation_time = now
            await self._periodic_reconcile()

        # Block trading if not reconciled
        if not self._reconciled:
            logger.warning(
                "Tick skipped: not reconciled (status=%s). Trading BLOCKED.",
                self._reconciliation_status,
            )
            return

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
        # C7-05: Expose equity for dashboard drawdown calculation
        self._state["equity"] = str(self.portfolio.equity)

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
        """Process a single trading symbol with deterministic risk validation."""
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

        # Position monitoring: check SL/TP for open positions
        await self._monitor_position(symbol, current_price)

        # Sync current_price from Portfolio to state (UI/API representation)
        for pos in self._state["positions"]:
            if pos["symbol"] == symbol and pos["status"] == "OPEN":
                pos["current_price"] = str(current_price)

        # 3. Build market state with enhanced indicators
        market_state = self._build_market_state(symbol, candles, current_price)

        # 3b. Calculate setup score (deterministic)
        pre_setup = self.setup_scorer.score(market_state)

        # 4. Send to LLM with setup data
        portfolio_state = {
            "capital": str(self._state["capital"]),
            "positions": self._state["positions"],
            "pnl": str(self._state["pnl"]),
        }

        # Enrich market state with setup data for LLM
        enriched_state = {
            **market_state,
            "setup_score": pre_setup.score,
            "market_regime": pre_setup.market_regime,
        }

        prompt = self.prompt_manager.render(
            "trading_v1",
            {
                "market_state": json.dumps(enriched_state, indent=2, default=str),
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

        # 5b. Calculate setup score (deterministic, before risk evaluation)
        decision_rr = None
        if decision.entry_price and decision.stop_loss and decision.take_profit:
            if decision.stop_loss < decision.entry_price and decision.take_profit > decision.entry_price:
                risk_amount = decision.entry_price - decision.stop_loss
                reward_amount = decision.take_profit - decision.entry_price
                if risk_amount > 0:
                    decision_rr = reward_amount / risk_amount

        setup_result = self.setup_scorer.score(market_state, decision_rr)
        setup_score = setup_result.score

        # Record decision with setup data
        decision_record = {
            "symbol": symbol,
            "action": decision.action.value,
            "confidence": float(decision.confidence),
            "thesis": decision.thesis,
            "setup_score": setup_score,
            "market_regime": setup_result.market_regime,
            "rsi": market_state.get("rsi", "0"),
            "momentum": market_state.get("momentum", "0"),
            "trend": market_state.get("trend", "NEUTRAL"),
            "provider": decision.provider,
            "model": decision.model,
            "reasoning": decision.reasoning,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._state["decisions"].append(decision_record)

        logger.info(
            "Decision for %s: %s (confidence: %s, setup_score: %d, regime: %s)",
            symbol,
            decision.action.value,
            decision.confidence,
            setup_score,
            setup_result.market_regime,
        )

        # 6. Risk evaluation with setup score
        risk_result = self.risk_engine.evaluate(
            decision,
            current_price=current_price,
            market_state=market_state,
            symbol=symbol,
            setup_score=setup_score,
        )

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

            from aegis.domain.enums import OrderSide, OrderStatus as _OS

            if order_result.status == _OS.FILLED:
                # Fully filled — apply fill
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
                    "thesis": decision.thesis,
                    "status": "OPEN",
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                }
                self._state["positions"].append(position)
                self._save_state()

                if decision.stop_loss and decision.take_profit:
                    self.risk_engine.record_position_state(
                        symbol=symbol, entry_price=entry,
                        stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                        thesis=decision.thesis,
                    )
                    self.position_manager.register_position(
                        symbol=symbol, entry_price=entry,
                        stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                        quantity=risk_result.approved_quantity,
                    )

                self.risk_engine.record_trade(symbol)
                self._state["orders"].append({
                    "id": str(order_result.order_id), "symbol": symbol,
                    "side": "BUY", "quantity": str(risk_result.approved_quantity),
                    "price": str(order_result.fill_price), "status": "FILLED",
                    "fee": str(order_result.fee), "error": None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                self.risk_engine.record_position_open()
                self._state["capital"] = str(self.portfolio.cash)
                self._state["pnl"] = str(self.portfolio.total_realized_pnl)
                logger.info("Order filled: %s %s @ R$ %s", risk_result.approved_quantity, symbol, order_result.fill_price)

            elif order_result.status in (_OS.SUBMITTED, _OS.PARTIALLY_FILLED):
                # Not yet confirmed — poll for status
                confirmed = await self._poll_order_status(order_result.order_id)
                if confirmed.status == _OS.FILLED:
                    # Re-check: if fill_price is None but status is FILLED, use approved price
                    fill_price = confirmed.fill_price or risk_result.approved_price
                    fill_qty = confirmed.fill_quantity or risk_result.approved_quantity
                    self.portfolio.record_fill(
                        asset=symbol, side=PositionSide.LONG,
                        quantity=fill_qty, price=fill_price, fee=confirmed.fee,
                    )
                    entry = fill_price
                    pnl = (current_price - entry) * fill_qty
                    position = {
                        "id": str(uuid4()), "symbol": symbol, "side": "LONG",
                        "quantity": str(fill_qty), "entry_price": str(entry),
                        "current_price": str(current_price), "entry_fee": str(confirmed.fee),
                        "pnl": str(pnl),
                        "pnl_pct": str((pnl / (entry * fill_qty) * 100) if entry > 0 else 0),
                        "stop_loss": str(decision.stop_loss) if decision.stop_loss else None,
                        "take_profit": str(decision.take_profit) if decision.take_profit else None,
                        "thesis": decision.thesis, "status": "OPEN",
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                    }
                    self._state["positions"].append(position)
                    self._save_state()
                    self.risk_engine.record_trade(symbol)
                    self._state["orders"].append({
                        "id": str(confirmed.order_id), "symbol": symbol,
                        "side": "BUY", "quantity": str(fill_qty),
                        "price": str(fill_price), "status": "FILLED",
                        "fee": str(confirmed.fee), "error": None,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    self.risk_engine.record_position_open()
                    self._state["capital"] = str(self.portfolio.cash)
                    self._state["pnl"] = str(self.portfolio.total_realized_pnl)
                    logger.info("Order confirmed filled after poll: %s %s @ R$ %s", fill_qty, symbol, fill_price)

                elif confirmed.status == _OS.PARTIALLY_FILLED:
                    # Partial fill — record what was filled, leave remainder as UNKNOWN
                    fill_price = confirmed.fill_price or risk_result.approved_price
                    fill_qty = confirmed.fill_quantity or Decimal("0")
                    if fill_qty > 0:
                        self.portfolio.record_fill(
                            asset=symbol, side=PositionSide.LONG,
                            quantity=fill_qty, price=fill_price, fee=confirmed.fee,
                        )
                    self._state["orders"].append({
                        "id": str(confirmed.order_id), "symbol": symbol,
                        "side": "BUY", "quantity": str(risk_result.approved_quantity),
                        "filled_quantity": str(fill_qty),
                        "price": str(risk_result.approved_price),
                        "status": "PARTIALLY_FILLED",
                        "fee": str(confirmed.fee), "error": None,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    self._save_state()
                    logger.warning(
                        "Order partially filled: %s %s — filled %s of %s",
                        symbol, confirmed.order_id, fill_qty, risk_result.approved_quantity,
                    )

                else:
                    # REJECTED, ERROR, or UNKNOWN after polling
                    self._state["orders"].append({
                        "id": str(confirmed.order_id), "symbol": symbol,
                        "side": "BUY", "quantity": str(risk_result.approved_quantity),
                        "price": str(risk_result.approved_price),
                        "status": confirmed.status.value,
                        "error": confirmed.error,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    self._save_state()
                    logger.warning(
                        "Order not filled after poll: %s — status=%s error=%s",
                        symbol, confirmed.status.value, confirmed.error,
                    )

            else:
                # REJECTED, ERROR, or other terminal state — no position created
                self._state["orders"].append({
                    "id": str(order_result.order_id), "symbol": symbol,
                    "side": "BUY", "quantity": str(risk_result.approved_quantity),
                    "price": str(risk_result.approved_price),
                    "status": order_result.status.value,
                    "error": order_result.error,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                self._save_state()
                logger.warning(
                    "Order not filled: %s — status=%s error=%s",
                    symbol, order_result.status.value, order_result.error,
                )

        elif decision.action == TradingAction.CLOSE:
            # C7-03: CLOSE routes through ExecutionEngine → Broker → Portfolio.
            # MercadoBitcoinBroker blocks SELL (V1.0 long-only) → fail-closed in LIVE.
            # SandboxBroker supports SELL → works correctly in SANDBOX.
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
                    qty = Decimal(pos["quantity"])

                    # C7-03: Execute SELL through ExecutionEngine → Broker
                    from aegis.domain.enums import OrderSide
                    close_order_result = await self.execution.execute_order(
                        order_id=uuid4(),
                        idempotency_key=uuid4(),
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=qty,
                        price=current_price,
                        correlation_id=close_decision.correlation_id,
                        risk_decision=close_risk,
                    )

                    # C7-03: If broker rejected SELL, fail-closed — do not update Portfolio
                    if close_order_result.status != OrderStatus.FILLED:
                        logger.warning(
                            "Broker rejected SELL for %s: %s (status=%s). Position stays open.",
                            symbol,
                            close_order_result.error,
                            close_order_result.status,
                        )
                        return

                    # C7-03: Only after broker fill, update Portfolio with broker's fill_price and fee
                    fill_price = close_order_result.fill_price or current_price
                    fill_fee = close_order_result.fee

                    realized = self.portfolio.close_position(
                        asset=symbol,
                        price=fill_price,
                        fee=fill_fee,
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
                        "exit_price": str(fill_price),
                        "pnl": str(realized),
                        "fee": str(fill_fee),
                    }
                    self._state["history"].append(trade)

                    # Update risk engine
                    self.risk_engine.record_position_close()
                    self.risk_engine.record_daily_pnl(realized)

                    # Persist immediately after autonomous close
                    self._save_state()

                    logger.info(
                        "Position closed: %s, fill: R$ %s, P&L: R$ %s",
                        symbol,
                        fill_price,
                        realized,
                    )
                    break

    async def _monitor_position(self, symbol: str, current_price: Decimal) -> None:
        """Monitor open position for SL/TP hit and adaptive SL adjustments.

        Checks:
        1. PositionManager for BE/trailing/profit protection
        2. Stop loss hit
        3. Take profit hit
        """
        for pos in self._state["positions"]:
            if pos["symbol"] == symbol and pos["status"] == "OPEN":
                entry_price = Decimal(pos.get("entry_price", "0"))
                stop_loss = Decimal(pos["stop_loss"]) if pos.get("stop_loss") else None
                take_profit = Decimal(pos["take_profit"]) if pos.get("take_profit") else None

                # PositionManager evaluation (BE/trailing/profit protection)
                pm_result = self.position_manager.evaluate(symbol, current_price)
                if pm_result["action"] == "MOVE_STOP":
                    new_stop = pm_result["new_stop"]
                    pos["stop_loss"] = str(new_stop)
                    logger.info(
                        "Adaptive SL for %s: moved to %s (reason: %s)",
                        symbol, new_stop, pm_result["reason"],
                    )
                    # Update stop_loss for SL/TP check below
                    stop_loss = new_stop

                # Check stop loss hit (price dropped below stop)
                if stop_loss and current_price <= stop_loss:
                    logger.info(
                        "STOP LOSS hit for %s: price=%s <= stop=%s",
                        symbol, current_price, stop_loss,
                    )
                    # Record trade before closing
                    self._record_trade_close(symbol, pos, "STOP_LOSS", current_price)
                    self.position_manager.unregister_position(symbol)
                    await self._close_position_by_id(pos["id"], "STOP_LOSS")
                    return

                # Check take profit hit (price rose above target)
                if take_profit and current_price >= take_profit:
                    logger.info(
                        "TAKE PROFIT hit for %s: price=%s >= target=%s",
                        symbol, current_price, take_profit,
                    )
                    self._record_trade_close(symbol, pos, "TAKE_PROFIT", current_price)
                    self.position_manager.unregister_position(symbol)
                    await self._close_position_by_id(pos["id"], "TAKE_PROFIT")
                    return

    def _record_trade_close(self, symbol: str, pos: dict, reason: str, current_price: Decimal) -> None:
        """Record trade close in journal."""
        entry_price = Decimal(pos.get("entry_price", "0"))
        quantity = Decimal(pos.get("quantity", "0"))
        stop_loss = Decimal(pos["stop_loss"]) if pos.get("stop_loss") else entry_price

        # Calculate R
        risk = entry_price - stop_loss
        realized_r = (current_price - entry_price) / risk if risk > 0 else Decimal("0")

        # Calculate P&L
        realized_pnl = (current_price - entry_price) * quantity

        trade = TradeRecord(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc).isoformat(),
            action="CLOSE",
            entry_price=entry_price,
            exit_price=current_price,
            stop_loss=stop_loss,
            take_profit=Decimal(pos["take_profit"]) if pos.get("take_profit") else None,
            setup_score=0,  # Will be set from decision record if available
            realized_pnl=realized_pnl,
            realized_r=realized_r,
            exit_reason=reason,
        )
        self.trade_journal.record(trade)

    async def _close_position_by_id(self, position_id: str, reason: str) -> None:
        """Close a position by ID with reason logging."""
        result = await self.close_position_manual(position_id)
        if result.get("status") == "CLOSED":
            logger.info("Auto-closed position %s: reason=%s, PnL=%s",
                       position_id, reason, result.get("pnl", "0"))
        else:
            logger.warning("Failed to auto-close position %s: %s", position_id, result)

    def _build_market_state(
        self, symbol: str, candles: list[dict], current_price: Decimal
    ) -> dict[str, Any]:
        """Build enhanced market state with technical indicators."""
        if not candles:
            return {"symbol": symbol, "current_price": str(current_price)}

        closes = [Decimal(str(c.get("close", "0"))) for c in candles if c.get("close")]
        volumes = [Decimal(str(c.get("volume", "0"))) for c in candles if c.get("volume")]
        highs = [Decimal(str(c.get("high", "0"))) for c in candles if c.get("high")]
        lows = [Decimal(str(c.get("low", "0"))) for c in candles if c.get("low")]

        # Moving averages
        sma_20 = sum(closes[-20:]) / min(20, len(closes)) if closes else Decimal("0")
        sma_50 = sum(closes[-50:]) / min(50, len(closes)) if closes else Decimal("0")

        # Volatility (standard deviation of returns)
        if len(closes) > 1:
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
            volatility = (sum(r**2 for r in returns) / len(returns)) ** Decimal("0.5")
        else:
            volatility = Decimal("0")

        # Trend classification
        trend = "BULLISH" if current_price > sma_20 > sma_50 else "BEARISH" if current_price < sma_20 < sma_50 else "NEUTRAL"

        # RSI (14-period)
        rsi = self._calculate_rsi(closes, 14)

        # Momentum (rate of change over last 10 candles)
        momentum = Decimal("0")
        if len(closes) >= 10:
            momentum = (closes[-1] - closes[-10]) / closes[-10] * Decimal("100")

        # Volume trend (compare recent vs average)
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

        # Price position relative to range
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
            "symbol": symbol,
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
        """Calculate RSI (Relative Strength Index)."""
        if len(closes) < period + 1:
            return Decimal("50")  # Default neutral

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
        rsi = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
        return rsi


# Singleton
_worker: AutonomousWorker | None = None


def get_worker() -> AutonomousWorker:
    global _worker
    if _worker is None:
        _worker = AutonomousWorker()
    return _worker
