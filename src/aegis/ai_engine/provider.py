"""AEGIS LLM provider abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from aegis.domain.contracts import utc_now
from aegis.domain.enums import TradingAction


@dataclass
class LLMResponse:
    """LLM response structure."""

    action: TradingAction
    confidence: Decimal
    thesis: str
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    reasoning: str = ""
    raw_output: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    token_usage: int = 0


class LLMProvider(ABC):
    """AC-05.01: An abstract LLM provider interface exists."""

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        model: str | None = None,
        timeout_seconds: int = 30,
    ) -> LLMResponse:
        """Send a prompt to the LLM and get a response."""
        ...

    @abstractmethod
    async def validate_connection(self) -> bool:
        """Validate the provider connection."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Get the provider name."""
        ...
