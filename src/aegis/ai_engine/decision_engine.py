"""AEGIS AI Decision Engine — structured decision generation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from aegis.ai_engine.provider import LLMProvider, LLMResponse
from aegis.ai_engine.prompt_manager import PromptManager
from aegis.domain.contracts import utc_now
from aegis.domain.enums import TradingAction
from aegis.domain.time import new_correlation_id


class InvalidDecisionOutput(Exception):
    """Raised when LLM output doesn't match Decision Contract."""


class LLMTimeoutError(Exception):
    """Raised when LLM times out."""


class LLMBrokerAccessError(Exception):
    """Raised when LLM tries to access broker directly."""


@dataclass
class DecisionContract:
    """AC-05.06: LLM output is validated against the Decision Contract."""

    decision_id: UUID = field(default_factory=uuid4)
    correlation_id: UUID = field(default_factory=new_correlation_id)
    action: TradingAction = TradingAction.HOLD
    confidence: Decimal = Decimal("0")
    thesis: str = ""
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    reasoning: str = ""
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    created_at: datetime = field(default_factory=utc_now)

    @classmethod
    def from_llm_response(
        cls,
        response: LLMResponse,
        provider: str,
        model: str,
        prompt_version: str,
        correlation_id: UUID | None = None,
    ) -> DecisionContract:
        """Create a DecisionContract from an LLM response."""
        return cls(
            decision_id=uuid4(),
            correlation_id=correlation_id or new_correlation_id(),
            action=response.action,
            confidence=response.confidence,
            thesis=response.thesis,
            entry_price=response.entry_price,
            stop_loss=response.stop_loss,
            take_profit=response.take_profit,
            reasoning=response.reasoning,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
        )

    @classmethod
    def validate(cls, contract: DecisionContract) -> None:
        """Validate a DecisionContract."""
        if contract.confidence < Decimal("0") or contract.confidence > Decimal("1"):
            raise InvalidDecisionOutput(
                f"Confidence must be between 0 and 1, got: {contract.confidence}"
            )

        if contract.action not in (TradingAction.LONG, TradingAction.HOLD, TradingAction.CLOSE):
            raise InvalidDecisionOutput(
                f"Invalid action: {contract.action}"
            )

        if not contract.thesis:
            raise InvalidDecisionOutput("Thesis is required")

    @classmethod
    def from_json(
        cls,
        json_str: str,
        provider: str,
        model: str,
        prompt_version: str,
        correlation_id: UUID | None = None,
    ) -> DecisionContract:
        """Parse LLM JSON output into a DecisionContract."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise InvalidDecisionOutput(f"Invalid JSON: {e}") from e

        try:
            action_str = data.get("action", "HOLD").upper()
            action = TradingAction(action_str)
        except ValueError:
            raise InvalidDecisionOutput(f"Invalid action: {data.get('action')}")

        try:
            confidence = Decimal(str(data.get("confidence", 0)))
        except (InvalidOperation, ValueError):
            raise InvalidDecisionOutput(f"Invalid confidence: {data.get('confidence')}")

        return cls(
            decision_id=uuid4(),
            correlation_id=correlation_id or new_correlation_id(),
            action=action,
            confidence=confidence,
            thesis=data.get("thesis", ""),
            entry_price=Decimal(str(data["entry_price"])) if "entry_price" in data else None,
            stop_loss=Decimal(str(data["stop_loss"])) if "stop_loss" in data else None,
            take_profit=Decimal(str(data["take_profit"])) if "take_profit" in data else None,
            reasoning=data.get("reasoning", ""),
            provider=provider,
            model=model,
            prompt_version=prompt_version,
        )


class DecisionEngine:
    """AI Decision Engine — generates structured decisions from market context."""

    def __init__(
        self,
        provider: LLMProvider,
        prompt_manager: PromptManager,
        provider_name: str,
        model: str,
        prompt_version: str,
    ) -> None:
        self._provider = provider
        self._prompt_manager = prompt_manager
        self._provider_name = provider_name
        self._model = model
        self._prompt_version = prompt_version

    async def decide(
        self,
        context: dict[str, Any],
        correlation_id: UUID | None = None,
    ) -> DecisionContract:
        """Generate a trading decision from market context."""
        prompt = self._prompt_manager.render(self._prompt_version, context)

        response = await self._provider.complete(
            prompt=prompt,
            model=self._model,
            timeout_seconds=30,
        )

        contract = DecisionContract.from_llm_response(
            response=response,
            provider=self._provider_name,
            model=self._model,
            prompt_version=self._prompt_version,
            correlation_id=correlation_id,
        )

        DecisionContract.validate(contract)

        return contract
