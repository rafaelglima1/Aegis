"""AEGIS Mercado Bitcoin Broker — BRL crypto exchange adapter (API v4).

Auth: OAuth2 client_credentials → Bearer token.
Docs: https://api.mercadobitcoin.net/api/v4/docs
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

from aegis.domain.enums import OrderSide, OrderStatus
from aegis.execution.broker import (
    BrokerAdapter,
    CancelResult,
    OrderResult,
    OrderSubmission,
)


@dataclass
class MercadoBitcoinConfig:
    """Mercado Bitcoin configuration."""

    api_key: str = ""
    api_secret: str = ""
    enabled: bool = False
    base_url: str = "https://api.mercadobitcoin.net"
    max_order_size: Decimal = Decimal("10000.00")
    max_daily_loss: Decimal = Decimal("10.00")
    max_positions: int = 1


class MercadoBitcoinBroker(BrokerAdapter):
    """Mercado Bitcoin broker adapter for BRL crypto trading (API v4).

    Auth flow:
      1. POST /oauth2/token with Basic(client_id:client_secret)
      2. Receive access_token
      3. Send Authorization: Bearer <token> on every request

    Supports: BTC-BRL, ETH-BRL, SOL-BRL, etc.
    Instrument: Spot only (no leverage)
    Direction: Long only
    """

    def __init__(self, config: MercadoBitcoinConfig) -> None:
        self._config = config
        self._orders: dict[UUID, dict[str, Any]] = {}
        self._idempotency_keys: set[UUID] = set()
        self._connected = False
        self._audit_log: list[dict[str, Any]] = []
        self._client: httpx.AsyncClient | None = None
        self._access_token: str = ""
        self._token_expiry: float = 0.0

    @property
    def is_enabled(self) -> bool:
        return self._config.enabled

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _authenticate(self) -> bool:
        """OAuth2 client_credentials → Bearer token."""
        if not self._config.api_key or not self._config.api_secret:
            return False

        # Reuse token if still valid (with 5min buffer)
        if self._access_token and time.time() < self._token_expiry - 300:
            return True

        try:
            import base64
            creds = base64.b64encode(
                f"{self._config.api_key}:{self._config.api_secret}".encode()
            ).decode()

            response = await self._client.post(
                "/oauth2/token",
                content="grant_type=client_credentials",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

            if response.status_code == 200:
                data = response.json()
                self._access_token = data["access_token"]
                self._token_expiry = time.time() + data.get("expires_in", 3600)
                self._audit("authenticated", {"exchange": "mercado_bitcoin"})
                return True
            else:
                self._audit("auth_failed", {"status": response.status_code, "error": response.text[:200]})
                return False
        except Exception as e:
            self._audit("auth_error", {"error": str(e)})
            return False

    def _get_auth_headers(self) -> dict[str, str]:
        """Get Bearer auth headers."""
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    async def connect(self) -> bool:
        """Establish connection to Mercado Bitcoin via OAuth2."""
        if not self._config.enabled:
            return False

        self._client = httpx.AsyncClient(base_url=self._config.base_url, timeout=30.0)

        if await self._authenticate():
            # Verify by fetching account balances
            try:
                response = await self._client.get(
                    "/api/v4/accounts/balances",
                    headers=self._get_auth_headers(),
                )
                if response.status_code == 200:
                    self._connected = True
                    self._audit("connected", {"exchange": "mercado_bitcoin"})
                    return True
            except Exception as e:
                self._audit("connection_check_failed", {"error": str(e)})

        return False

    async def disconnect(self) -> None:
        """Disconnect from broker."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        self._access_token = ""

    async def health_check(self) -> bool:
        """Check broker health."""
        if not self._client:
            return False
        # Refresh token if needed
        if not await self._authenticate():
            return False
        try:
            response = await self._client.get(
                "/api/v4/accounts/balances",
                headers=self._get_auth_headers(),
            )
            return response.status_code == 200
        except Exception:
            return False

    def _audit(self, event: str, data: dict[str, Any]) -> None:
        """Every execution attempt is auditable."""
        self._audit_log.append({"event": event, **data})

    async def submit_order(self, submission: OrderSubmission) -> OrderResult:
        """Submit order to Mercado Bitcoin (API v4).

        V1.0 Rules:
        - Spot only (no leverage)
        - Long only (BUY only)
        - BRL pairs (BTC-BRL, ETH-BRL, etc.)
        """
        if not self._config.enabled:
            self._audit("order_blocked", {"reason": "LIVE_DISABLED", "order_id": str(submission.order_id)})
            return OrderResult(
                order_id=submission.order_id,
                status=OrderStatus.REJECTED,
                error="LIVE trading is disabled",
            )

        if not self._config.api_key or not self._config.api_secret:
            self._audit("order_blocked", {"reason": "INVALID_CREDENTIALS", "order_id": str(submission.order_id)})
            return OrderResult(
                order_id=submission.order_id,
                status=OrderStatus.REJECTED,
                error="Invalid or missing credentials",
            )

        if not await self._authenticate():
            self._audit("order_blocked", {"reason": "AUTH_FAILED", "order_id": str(submission.order_id)})
            return OrderResult(
                order_id=submission.order_id,
                status=OrderStatus.REJECTED,
                error="Authentication failed",
            )

        if submission.side == OrderSide.SELL:
            self._audit("order_blocked", {"reason": "SHORT_NOT_ALLOWED", "order_id": str(submission.order_id)})
            return OrderResult(
                order_id=submission.order_id,
                status=OrderStatus.REJECTED,
                error="V1.0: Long only, SELL not allowed",
            )

        if submission.idempotency_key in self._idempotency_keys:
            existing = self._orders.get(submission.order_id)
            if existing:
                return OrderResult(order_id=submission.order_id, status=existing.get("status", OrderStatus.REJECTED))
            return OrderResult(order_id=submission.order_id, status=OrderStatus.REJECTED, error="Duplicate order")

        self._idempotency_keys.add(submission.idempotency_key)

        cost = submission.price * submission.quantity
        if cost > self._config.max_order_size:
            self._audit("order_blocked", {"reason": "ORDER_SIZE_EXCEEDED", "order_id": str(submission.order_id)})
            return OrderResult(
                order_id=submission.order_id,
                status=OrderStatus.REJECTED,
                error=f"Order size {cost} exceeds max {self._config.max_order_size}",
            )

        # MB API v4: symbol format is BTC-BRL (with dash)
        # Create order: POST /api/v4/orders
        try:
            order_data = {
                "symbol": submission.symbol,
                "side": "buy",
                "type": "limit",
                "quantity": str(submission.quantity),
                "limit_price": str(submission.price),
            }

            response = await self._client.post(
                "/api/v4/orders",
                json=order_data,
                headers=self._get_auth_headers(),
            )

            if response.status_code in (200, 201):
                result = response.json()
                mb_order_id = result.get("id") or result.get("order_id")
                self._audit("order_placed", {
                    "order_id": str(submission.order_id),
                    "mb_order_id": mb_order_id,
                    "symbol": submission.symbol,
                })

                self._orders[submission.order_id] = {
                    "order_id": submission.order_id,
                    "status": OrderStatus.SUBMITTED,
                    "symbol": submission.symbol,
                    "mb_order_id": mb_order_id,
                }

                return OrderResult(
                    order_id=submission.order_id,
                    status=OrderStatus.SUBMITTED,
                )
            else:
                error_msg = response.text[:500]
                self._audit("order_failed", {"order_id": str(submission.order_id), "error": error_msg, "status": response.status_code})
                return OrderResult(
                    order_id=submission.order_id,
                    status=OrderStatus.REJECTED,
                    error=f"MB API {response.status_code}: {error_msg}",
                )

        except Exception as e:
            self._audit("order_error", {"order_id": str(submission.order_id), "error": str(e)})
            return OrderResult(order_id=submission.order_id, status=OrderStatus.ERROR, error=str(e))

    async def cancel_order(self, order_id: UUID, idempotency_key: UUID) -> CancelResult:
        """Cancel order on Mercado Bitcoin."""
        if idempotency_key in self._idempotency_keys:
            return CancelResult(order_id=order_id, success=False, error="Duplicate request")
        self._idempotency_keys.add(idempotency_key)

        order = self._orders.get(order_id)
        if not order:
            return CancelResult(order_id=order_id, success=False, error="Order not found")

        mb_order_id = order.get("mb_order_id")
        if not mb_order_id:
            return CancelResult(order_id=order_id, success=False, error="No MB order ID")

        if not await self._authenticate():
            return CancelResult(order_id=order_id, success=False, error="Auth failed")

        try:
            response = await self._client.delete(
                f"/api/v4/orders/{mb_order_id}",
                headers=self._get_auth_headers(),
            )
            if response.status_code in (200, 204):
                self._audit("order_cancelled", {"order_id": str(order_id)})
                order["status"] = OrderStatus.CANCELLED
                return CancelResult(order_id=order_id, success=True)
            else:
                return CancelResult(order_id=order_id, success=False, error=response.text[:200])
        except Exception as e:
            return CancelResult(order_id=order_id, success=False, error=str(e))

    async def get_order_status(self, order_id: UUID) -> OrderResult:
        """Get order status from Mercado Bitcoin."""
        order = self._orders.get(order_id)
        if not order:
            return OrderResult(order_id=order_id, status=OrderStatus.REJECTED, error="Order not found")

        mb_order_id = order.get("mb_order_id")
        if not mb_order_id:
            return OrderResult(order_id=order_id, status=order.get("status", OrderStatus.REJECTED))

        if not await self._authenticate():
            return OrderResult(order_id=order_id, status=OrderStatus.ERROR, error="Auth failed")

        try:
            response = await self._client.get(
                f"/api/v4/orders/{mb_order_id}",
                headers=self._get_auth_headers(),
            )
            if response.status_code == 200:
                result = response.json()
                status_map = {
                    "placed": OrderStatus.SUBMITTED,
                    "partially_filled": OrderStatus.PARTIALLY_FILLED,
                    "filled": OrderStatus.FILLED,
                    "cancelled": OrderStatus.CANCELLED,
                    "expired": OrderStatus.EXPIRED,
                }
                mb_status = result.get("status", "").lower()
                status = status_map.get(mb_status, OrderStatus.ERROR)

                return OrderResult(
                    order_id=order_id,
                    status=status,
                    fill_price=Decimal(str(result.get("executed_price", "0"))) if result.get("executed_price") else None,
                    fill_quantity=Decimal(str(result.get("executed_quantity", "0"))) if result.get("executed_quantity") else None,
                    fee=Decimal(str(result.get("fee", "0"))),
                )
        except Exception as e:
            return OrderResult(order_id=order_id, status=OrderStatus.ERROR, error=str(e))

        return OrderResult(order_id=order_id, status=order.get("status", OrderStatus.REJECTED))

    async def get_position(self, symbol: str) -> dict[str, Any]:
        """Get current position for a symbol."""
        if not await self._authenticate():
            return {"symbol": symbol, "quantity": Decimal("0"), "orders": 0}

        try:
            response = await self._client.get(
                "/api/v4/accounts/balances",
                headers=self._get_auth_headers(),
            )
            if response.status_code == 200:
                balances = response.json()
                coin = symbol.split("-")[0]
                for balance in balances:
                    if balance.get("symbol") == coin or balance.get("coin") == coin:
                        return {
                            "symbol": symbol,
                            "quantity": Decimal(str(balance.get("available", "0"))),
                            "locked": Decimal(str(balance.get("locked", "0"))),
                            "orders": 0,
                        }
        except Exception:
            pass

        return {"symbol": symbol, "quantity": Decimal("0"), "orders": 0}

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        """Get current ticker for a symbol."""
        try:
            response = await self._client.get(f"/api/v4/{symbol}/ticker/")
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return {}

    async def get_candles(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[dict[str, Any]]:
        """Get candle data for a symbol."""
        tf_map = {"1m": "1m", "15m": "15m", "1h": "1h", "3h": "3h", "1d": "1d", "1w": "1w", "1M": "1M"}
        mb_tf = tf_map.get(timeframe, "1h")

        import time as _time
        now = int(_time.time())
        start = now - (limit * 3600)  # approximate

        try:
            response = await self._client.get(
                f"/api/v4/{symbol}/candles/?resolution={mb_tf}&from={start}&to={now}",
                headers=self._get_auth_headers() if self._connected else {},
            )
            if response.status_code == 200:
                data = response.json()
                return data if isinstance(data, list) else data.get("candles", [])[:limit]
        except Exception:
            pass
        return []

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        """Every execution attempt is auditable."""
        return self._audit_log.copy()

    def __repr__(self) -> str:
        """Secrets never appear in logs."""
        return f"MercadoBitcoinBroker(enabled={self._config.enabled}, connected={self._connected})"
