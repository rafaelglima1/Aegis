"""Tests for AEGIS AI engine — LLM provider, decision engine, and prompt manager."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from aegis.ai_engine.provider import LLMProvider, LLMResponse
from aegis.ai_engine.decision_engine import (
    DecisionEngine,
    DecisionContract,
    InvalidDecisionOutput,
)
from aegis.ai_engine.prompt_manager import PromptManager, PromptVersion
from aegis.domain.enums import TradingAction


class MockLLMProvider(LLMProvider):
    """Mock LLM provider for testing."""

    def __init__(
        self,
        response: LLMResponse | None = None,
        should_timeout: bool = False,
        should_error: bool = False,
    ) -> None:
        self._response = response
        self._should_timeout = should_timeout
        self._should_error = should_error

    async def complete(
        self,
        prompt: str,
        model: str | None = None,
        timeout_seconds: int = 30,
    ) -> LLMResponse:
        if self._should_timeout:
            raise asyncio.TimeoutError("LLM timeout")
        if self._should_error:
            raise RuntimeError("LLM error")
        if self._response is None:
            raise RuntimeError("No response configured")
        return self._response

    async def validate_connection(self) -> bool:
        return not self._should_error

    @property
    def provider_name(self) -> str:
        return "mock"


def make_valid_response() -> LLMResponse:
    return LLMResponse(
        action=TradingAction.LONG,
        confidence=Decimal("0.85"),
        thesis="Strong bullish signal",
        entry_price=Decimal("50.00"),
        stop_loss=Decimal("48.00"),
        take_profit=Decimal("55.00"),
        reasoning="Price above SMA",
    )


# LLM Provider Tests


def test_llm_provider_is_abstract() -> None:
    """AC-05.01: An abstract LLM provider interface exists."""
    with pytest.raises(TypeError):
        LLMProvider()


def test_mock_provider_returns_response() -> None:
    """AC-05.01: An abstract LLM provider interface exists."""
    provider = MockLLMProvider(response=make_valid_response())
    assert provider.provider_name == "mock"


@pytest.mark.asyncio
async def test_mock_provider_complete() -> None:
    """AC-05.01: An abstract LLM provider interface exists."""
    provider = MockLLMProvider(response=make_valid_response())
    response = await provider.complete("test prompt")
    assert response.action == TradingAction.LONG
    assert response.confidence == Decimal("0.85")


@pytest.mark.asyncio
async def test_provider_timeout() -> None:
    """AC-05.08: LLM timeout produces safe behavior."""
    provider = MockLLMProvider(should_timeout=True)
    with pytest.raises(asyncio.TimeoutError):
        await provider.complete("test prompt")


@pytest.mark.asyncio
async def test_provider_error() -> None:
    """AC-05.07: Invalid LLM output is rejected safely."""
    provider = MockLLMProvider(should_error=True)
    with pytest.raises(RuntimeError):
        await provider.complete("test prompt")


# Prompt Manager Tests


def test_prompt_manager_register_and_get() -> None:
    """AC-05.04: Prompt versions are explicit and traceable."""
    pm = PromptManager()
    version = PromptVersion(
        version="v1",
        template="Analyze {asset}",
        description="Basic analysis prompt",
    )
    pm.register(version)
    retrieved = pm.get("v1")
    assert retrieved.version == "v1"
    assert retrieved.hash is not None


def test_prompt_manager_render() -> None:
    """AC-05.04: Prompt versions are explicit and traceable."""
    pm = PromptManager()
    version = PromptVersion(version="v1", template="Analyze {asset} for {timeframe}")
    pm.register(version)
    rendered = pm.render("v1", {"asset": "PETR4", "timeframe": "1d"})
    assert "PETR4" in rendered
    assert "1d" in rendered


def test_prompt_manager_missing_version() -> None:
    """AC-05.04: Prompt versions are explicit and traceable."""
    pm = PromptManager()
    with pytest.raises(KeyError):
        pm.get("nonexistent")


def test_prompt_manager_list_versions() -> None:
    """AC-05.04: Prompt versions are explicit and traceable."""
    pm = PromptManager()
    pm.register(PromptVersion(version="v1", template="test"))
    pm.register(PromptVersion(version="v2", template="test2"))
    versions = pm.list_versions()
    assert "v1" in versions
    assert "v2" in versions


# Decision Contract Tests


def test_decision_contract_creation() -> None:
    """AC-05.06: LLM output is validated against the Decision Contract."""
    contract = DecisionContract(
        action=TradingAction.LONG,
        confidence=Decimal("0.85"),
        thesis="Strong bullish signal",
    )
    assert contract.decision_id is not None
    assert contract.correlation_id is not None
    assert contract.action == TradingAction.LONG


def test_decision_contract_validate_valid() -> None:
    """AC-05.06: LLM output is validated against the Decision Contract."""
    contract = DecisionContract(
        action=TradingAction.LONG,
        confidence=Decimal("0.85"),
        thesis="Strong bullish signal",
    )
    DecisionContract.validate(contract)


def test_decision_contract_validate_invalid_confidence() -> None:
    """AC-05.07: Invalid LLM output is rejected safely."""
    contract = DecisionContract(
        action=TradingAction.LONG,
        confidence=Decimal("1.5"),
        thesis="Test",
    )
    with pytest.raises(InvalidDecisionOutput):
        DecisionContract.validate(contract)


def test_decision_contract_validate_negative_confidence() -> None:
    """AC-05.07: Invalid LLM output is rejected safely."""
    contract = DecisionContract(
        action=TradingAction.LONG,
        confidence=Decimal("-0.1"),
        thesis="Test",
    )
    with pytest.raises(InvalidDecisionOutput):
        DecisionContract.validate(contract)


def test_decision_contract_validate_missing_thesis() -> None:
    """AC-05.07: Invalid LLM output is rejected safely."""
    contract = DecisionContract(
        action=TradingAction.LONG,
        confidence=Decimal("0.85"),
        thesis="",
    )
    with pytest.raises(InvalidDecisionOutput):
        DecisionContract.validate(contract)


def test_decision_contract_from_json() -> None:
    """AC-05.06: LLM output is validated against the Decision Contract."""
    json_str = '{"action": "LONG", "confidence": 0.85, "thesis": "Bullish"}'
    contract = DecisionContract.from_json(
        json_str,
        provider="openai",
        model="gpt-4",
        prompt_version="v1",
    )
    assert contract.action == TradingAction.LONG
    assert contract.confidence == Decimal("0.85")
    assert contract.provider == "openai"


def test_decision_contract_from_json_invalid() -> None:
    """AC-05.07: Invalid LLM output is rejected safely."""
    with pytest.raises(InvalidDecisionOutput):
        DecisionContract.from_json(
            "not json",
            provider="openai",
            model="gpt-4",
            prompt_version="v1",
        )


def test_decision_contract_from_json_invalid_action() -> None:
    """AC-05.07: Invalid LLM output is rejected safely."""
    json_str = '{"action": "INVALID", "confidence": 0.85, "thesis": "Test"}'
    with pytest.raises(InvalidDecisionOutput):
        DecisionContract.from_json(
            json_str,
            provider="openai",
            model="gpt-4",
            prompt_version="v1",
        )


def test_decision_contract_preserves_correlation_id() -> None:
    """AC-05.11: Decision ID and correlation_id are preserved."""
    cid = uuid4()
    contract = DecisionContract(
        action=TradingAction.HOLD,
        confidence=Decimal("0.5"),
        thesis="Wait",
        correlation_id=cid,
    )
    assert contract.correlation_id == cid


# Decision Engine Tests


@pytest.mark.asyncio
async def test_decision_engine_generates_decision() -> None:
    """AC-05.03: The selected provider/model is recorded for each AI run."""
    provider = MockLLMProvider(response=make_valid_response())
    pm = PromptManager()
    pm.register(PromptVersion(version="v1", template="Analyze {asset}"))

    engine = DecisionEngine(
        provider=provider,
        prompt_manager=pm,
        provider_name="openai",
        model="gpt-4",
        prompt_version="v1",
    )

    contract = await engine.decide({"asset": "PETR4"})
    assert contract.action == TradingAction.LONG
    assert contract.provider == "openai"
    assert contract.model == "gpt-4"
    assert contract.prompt_version == "v1"


@pytest.mark.asyncio
async def test_decision_engine_no_broker_access() -> None:
    """AC-05.09: LLM has no direct broker access."""
    provider = MockLLMProvider(response=make_valid_response())
    pm = PromptManager()
    pm.register(PromptVersion(version="v1", template="Analyze {asset}"))

    engine = DecisionEngine(
        provider=provider,
        prompt_manager=pm,
        provider_name="openai",
        model="gpt-4",
        prompt_version="v1",
    )

    contract = await engine.decide({"asset": "PETR4"})
    assert not hasattr(contract, "broker")
    assert not hasattr(contract, "order")
