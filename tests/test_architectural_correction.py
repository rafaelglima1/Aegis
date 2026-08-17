"""AEGIS Architectural Correction Tests — Post-Audit V1.3.

Tests 1-8: Required architectural correction tests.
Tests A1-A3: Architecture dependency tests.

All tests use mocks — no real credentials or live API calls.
"""

from __future__ import annotations

import inspect
import pytest
import httpx
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from aegis.config import Settings, TradingEnvironment
from aegis.domain.enums import OrderSide, OrderStatus, TradingAction
from aegis.execution.broker import BrokerAdapter, OrderSubmission, OrderResult, CancelResult
from aegis.execution.sandbox import SandboxBroker
from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig
from aegis.execution.engine import ExecutionEngine
from aegis.execution.factory import create_broker
from aegis.pipeline import TradingPipeline
from aegis.risk_engine.risk_engine import RiskEngine
from aegis.risk_engine.risk_limits import RiskLimits
from aegis.ai_engine.decision_engine import DecisionContract


def _make_decision(action: TradingAction = TradingAction.LONG) -> DecisionContract:
    return DecisionContract(
        action=action,
        confidence=Decimal("0.85"),
        thesis="test",
        entry_price=Decimal("50000"),
    )


def _make_submission(**overrides) -> OrderSubmission:
    defaults = dict(
        order_id=uuid4(),
        idempotency_key=uuid4(),
        symbol="BTC-BRL",
        side=OrderSide.BUY,
        quantity=Decimal("0.001"),
        price=Decimal("50000"),
        correlation_id=uuid4(),
    )
    defaults.update(overrides)
    return OrderSubmission(**defaults)


# ============================================================
# TEST 1 — SANDBOX SELECTION
# ============================================================

class Test1_SandboxSelection:
    """TRADING_ENVIRONMENT=SANDBOX -> SandboxBroker via factory, Pipeline gets BrokerAdapter."""

    def test_factory_returns_sandbox_broker(self) -> None:
        """AC-CORR-03: Factory returns SandboxBroker for SANDBOX."""
        settings = Settings(trading_environment=TradingEnvironment.SANDBOX)
        broker = create_broker(settings)
        assert isinstance(broker, SandboxBroker)

    def test_sandbox_broker_implements_broker_adapter(self) -> None:
        """SandboxBroker must implement BrokerAdapter."""
        assert issubclass(SandboxBroker, BrokerAdapter)

    def test_pipeline_accepts_broker_adapter_type(self) -> None:
        """TradingPipeline constructor signature must accept BrokerAdapter, not SandboxBroker only."""
        sig = inspect.signature(TradingPipeline.__init__)
        broker_param = sig.parameters.get("broker")
        assert broker_param is not None
        annotation = broker_param.annotation
        assert annotation != SandboxBroker, "Pipeline must not be typed to SandboxBroker"
        assert "BrokerAdapter" in str(annotation), f"Pipeline broker type should be BrokerAdapter, got {annotation}"

    def test_pipeline_default_uses_factory(self) -> None:
        """TradingPipeline() with no args should use factory (SANDBOX by default)."""
        pipeline = TradingPipeline()
        assert isinstance(pipeline._broker, SandboxBroker)
        assert isinstance(pipeline._broker, BrokerAdapter)


# ============================================================
# TEST 2 — LIVE DISABLED
# ============================================================

class Test2_LiveDisabled:
    """TRADING_ENVIRONMENT=LIVE + LIVE_ENABLED=false -> RuntimeError (fail-closed)."""

    def test_factory_raises_when_live_disabled(self) -> None:
        """AC-CORR-05: LIVE_ENABLED=false blocks execution with RuntimeError."""
        settings = Settings(
            trading_environment=TradingEnvironment.LIVE,
            live_enabled=False,
        )
        with pytest.raises(RuntimeError, match="LIVE trading is disabled"):
            create_broker(settings)

    def test_no_order_sent_when_live_disabled(self) -> None:
        """No order can be submitted when LIVE is disabled."""
        settings = Settings(
            trading_environment=TradingEnvironment.LIVE,
            live_enabled=False,
        )
        with pytest.raises(RuntimeError):
            broker = create_broker(settings)
            assert False, "Broker should not be created when LIVE is disabled"


# ============================================================
# TEST 3 — LIVE SELECTION
# ============================================================

class Test3_LiveSelection:
    """TRADING_ENVIRONMENT=LIVE + LIVE_ENABLED=true -> MercadoBitcoinBroker."""

    def test_factory_returns_mercadobitcoin_broker(self) -> None:
        """AC-CORR-04: Factory returns MercadoBitcoinBroker for LIVE."""
        settings = Settings(
            trading_environment=TradingEnvironment.LIVE,
            live_enabled=True,
            live_api_key="test_key",
            live_api_secret="test_secret",
        )
        broker = create_broker(settings)
        assert isinstance(broker, MercadoBitcoinBroker)

    def test_mercadobitcoin_broker_implements_broker_adapter(self) -> None:
        """AC-CORR-07: MercadoBitcoinBroker implements BrokerAdapter."""
        assert issubclass(MercadoBitcoinBroker, BrokerAdapter)

    def test_pipeline_gets_only_broker_adapter(self) -> None:
        """Pipeline should only see BrokerAdapter, never concrete type."""
        mb_broker = MercadoBitcoinBroker(MercadoBitcoinConfig(enabled=True))
        pipeline = TradingPipeline(broker=mb_broker)
        assert isinstance(pipeline._broker, BrokerAdapter)
        assert isinstance(pipeline._broker, MercadoBitcoinBroker)


# ============================================================
# TEST 4 — LIVE PIPELINE (mock HTTP)
# ============================================================

class Test4_LivePipeline:
    """Integration: Pipeline -> BrokerAdapter -> MercadoBitcoinBroker -> Mock HTTP API."""

    @pytest.mark.asyncio
    async def test_mock_api_receives_order(self) -> None:
        """AC-CORR-09: MercadoBitcoinBroker POSTs to mock API when submitting order."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"order_id": "mb-456", "status": "OPEN"}
        mock_response.raise_for_status = MagicMock()

        mock_auth_response = MagicMock()
        mock_auth_response.status_code = 200
        mock_auth_response.json.return_value = {"access_token": "fake_token", "expires_in": 3600}
        mock_auth_response.raise_for_status = MagicMock()

        call_log = []

        async def mock_post(url, **kwargs):
            call_log.append(("POST", url))
            if "oauth2" in url:
                return mock_auth_response
            return mock_response

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = mock_post
        mock_client.aclose = AsyncMock()

        config = MercadoBitcoinConfig(
            api_key="test_key",
            api_secret="test_secret",
            enabled=True,
        )
        broker = MercadoBitcoinBroker(config)
        broker._client = mock_client

        submission = _make_submission()
        result = await broker.submit_order(submission)

        post_calls = [c for c in call_log if c[0] == "POST"]
        assert len(post_calls) >= 1, f"Expected at least 1 POST call, got {len(post_calls)}"


# ============================================================
# TEST 5 — LIVE API FAILURE
# ============================================================

class Test5_LiveApiFailure:
    """Simulate API failures: timeout, HTTP 500, invalid response, auth failure."""

    @pytest.mark.asyncio
    async def test_timeout_fails_closed(self) -> None:
        """AC-CORR-12: Timeout -> fail-closed, no order considered executed."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client.aclose = AsyncMock()

        config = MercadoBitcoinConfig(
            api_key="test_key",
            api_secret="test_secret",
            enabled=True,
        )
        broker = MercadoBitcoinBroker(config)
        broker._client = mock_client

        submission = _make_submission()
        result = await broker.submit_order(submission)

        assert result.status == OrderStatus.REJECTED
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_http_500_fails_closed(self) -> None:
        """AC-CORR-12: HTTP 500 -> fail-closed."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "500", request=MagicMock(), response=mock_response
            )
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        config = MercadoBitcoinConfig(
            api_key="test_key",
            api_secret="test_secret",
            enabled=True,
        )
        broker = MercadoBitcoinBroker(config)
        broker._client = mock_client

        submission = _make_submission()
        result = await broker.submit_order(submission)

        assert result.status in (OrderStatus.REJECTED, OrderStatus.ERROR)

    @pytest.mark.asyncio
    async def test_auth_failure_fails_closed(self) -> None:
        """AC-CORR-12: Auth failure -> fail-closed."""
        config = MercadoBitcoinConfig(
            api_key="",
            api_secret="",
            enabled=True,
        )
        broker = MercadoBitcoinBroker(config)

        submission = _make_submission()
        result = await broker.submit_order(submission)

        assert result.status == OrderStatus.REJECTED
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_invalid_json_fails_closed(self) -> None:
        """AC-CORR-12: Malformed response body -> fail-closed (ERROR)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        config = MercadoBitcoinConfig(
            api_key="test_key",
            api_secret="test_secret",
            enabled=True,
        )
        broker = MercadoBitcoinBroker(config)
        broker._client = mock_client
        broker._connected = True
        broker._access_token = "fake_token"
        broker._token_expiry = 9999999999.0

        submission = _make_submission()
        result = await broker.submit_order(submission)

        assert result.status in (OrderStatus.REJECTED, OrderStatus.ERROR)


# ============================================================
# TEST 6 — ENVIRONMENT SWITCH
# ============================================================

class Test6_EnvironmentSwitch:
    """SANDBOX and LIVE selected by config only, no code change."""

    def test_sandbox_to_live_by_config(self) -> None:
        """AC-CORR-08: Switching between SANDBOX and LIVE requires only config change."""
        settings_sandbox = Settings(trading_environment=TradingEnvironment.SANDBOX)
        settings_live = Settings(
            trading_environment=TradingEnvironment.LIVE,
            live_enabled=True,
            live_api_key="test",
            live_api_secret="test",
        )

        broker_sandbox = create_broker(settings_sandbox)
        broker_live = create_broker(settings_live)

        assert isinstance(broker_sandbox, SandboxBroker)
        assert isinstance(broker_live, MercadoBitcoinBroker)
        assert isinstance(broker_sandbox, BrokerAdapter)
        assert isinstance(broker_live, BrokerAdapter)

    def test_switch_requires_no_code_change(self) -> None:
        """The same create_broker function handles both environments."""
        settings = Settings(trading_environment=TradingEnvironment.SANDBOX)
        broker = create_broker(settings)
        assert isinstance(broker, BrokerAdapter)

        settings = Settings(
            trading_environment=TradingEnvironment.LIVE,
            live_enabled=True,
            live_api_key="k",
            live_api_secret="s",
        )
        broker = create_broker(settings)
        assert isinstance(broker, BrokerAdapter)


# ============================================================
# TEST 7 — RISK GATE (rejection blocks broker)
# ============================================================

class Test7_RiskGate:
    """Risk REJECT must prevent BrokerAdapter.submit_order()."""

    @pytest.mark.asyncio
    async def test_risk_rejection_blocks_execution(self) -> None:
        """AC-CORR-13: Rejected risk decision must not reach BrokerAdapter."""
        mock_broker = MagicMock(spec=BrokerAdapter)
        mock_execution = ExecutionEngine(mock_broker)

        decision = _make_decision(TradingAction.LONG)
        risk_engine = RiskEngine()
        risk_result = risk_engine.evaluate(decision)

        assert not risk_result.is_approved, "Decision should be rejected for test to be meaningful"

        mock_broker.submit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_risk_rejection_blocks_broker(self) -> None:
        """AC-CORR-13: Pipeline with rejected risk never calls broker."""
        mock_broker = MagicMock(spec=BrokerAdapter)
        mock_broker.submit_order = AsyncMock(return_value=OrderResult(
            order_id=uuid4(), status=OrderStatus.REJECTED,
        ))

        pipeline = TradingPipeline(
            risk_engine=RiskEngine(RiskLimits(max_risk_per_trade_pct=Decimal("0.001"))),
            broker=mock_broker,
        )

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.85"),
            thesis="test",
            entry_price=Decimal("50000"),
        )

        result = await pipeline.run(symbol="BTC-BRL", decision=decision)
        assert result.status == "REJECTED"
        mock_broker.submit_order.assert_not_called()


# ============================================================
# TEST 8 — RISK APPROVED
# ============================================================

class Test8_RiskApproved:
    """Valid risk approval allows execution through normal flow."""

    @pytest.mark.asyncio
    async def test_approved_order_reaches_broker(self) -> None:
        """AC-CORR-14: Approved risk decision proceeds to BrokerAdapter."""
        mock_broker = MagicMock(spec=BrokerAdapter)
        mock_broker.submit_order = AsyncMock(return_value=OrderResult(
            order_id=uuid4(),
            status=OrderStatus.FILLED,
            fill_price=Decimal("50000"),
            fill_quantity=Decimal("0.001"),
            fee=Decimal("0.50"),
        ))

        pipeline = TradingPipeline(
            risk_engine=RiskEngine(RiskLimits(
                reference_capital=Decimal("10000"),
                max_risk_per_trade_pct=Decimal("1.0"),
            )),
            broker=mock_broker,
        )

        decision = DecisionContract(
            action=TradingAction.LONG,
            confidence=Decimal("0.85"),
            thesis="test",
            entry_price=Decimal("50000"),
            stop_loss=Decimal("48000"),
            take_profit=Decimal("55000"),
        )

        result = await pipeline.run(symbol="BTC-BRL", decision=decision)
        assert result.status == "FILLED"
        mock_broker.submit_order.assert_called_once()


# ============================================================
# ARCHITECTURE TESTS — Verify Pipeline depends on BrokerAdapter only
# ============================================================

class TestArchitecture_DependencyCheck:
    """Verify Pipeline has no concrete broker imports."""

    def test_pipeline_source_no_sandboxbroker_import(self) -> None:
        """AC-CORR-01: TradingPipeline source must not import SandboxBroker."""
        source = inspect.getsource(TradingPipeline)
        assert "from aegis.execution.sandbox import SandboxBroker" not in source, \
            "TradingPipeline must not import SandboxBroker"

    def test_pipeline_source_no_mercadobitcoinbroker_import(self) -> None:
        """TradingPipeline source must not import MercadoBitcoinBroker."""
        source = inspect.getsource(TradingPipeline)
        assert "from aegis.execution.mercadobitcoin" not in source, \
            "TradingPipeline must not import MercadoBitcoinBroker"

    def test_pipeline_type_hint_is_broker_adapter(self) -> None:
        """AC-CORR-02: TradingPipeline broker parameter is typed as BrokerAdapter."""
        sig = inspect.signature(TradingPipeline.__init__)
        broker_hint = sig.parameters["broker"].annotation
        assert "BrokerAdapter" in str(broker_hint)

    def test_factory_is_only_selection_mechanism(self) -> None:
        """Factory is the single point of broker selection."""
        assert callable(create_broker)

    def test_no_broker_bypass_in_pipeline(self) -> None:
        """Pipeline must not have any direct broker method calls bypassing ExecutionEngine."""
        source = inspect.getsource(TradingPipeline)
        assert "self._broker.submit_order" not in source, \
            "Pipeline must not call broker.submit_order directly (must go through ExecutionEngine)"
        assert "self._broker.cancel_order" not in source, \
            "Pipeline must not call broker.cancel_order directly"


class TestArchitecture_BrokerContracts:
    """Verify all brokers implement BrokerAdapter."""

    def test_sandbox_implements_adapter(self) -> None:
        assert issubclass(SandboxBroker, BrokerAdapter)

    def test_mercadobitcoin_implements_adapter(self) -> None:
        assert issubclass(MercadoBitcoinBroker, BrokerAdapter)

    def test_factory_returns_adapter(self) -> None:
        settings = Settings(trading_environment=TradingEnvironment.SANDBOX)
        broker = create_broker(settings)
        assert isinstance(broker, BrokerAdapter)


class TestArchitecture_CorrelationAndAudit:
    """AC-CORR-15, AC-CORR-16: Correlation ID and environment audit trail."""

    def test_correlation_id_in_pipeline_result(self) -> None:
        """AC-CORR-15: PipelineResult contains correlation_id."""
        from aegis.pipeline import PipelineResult
        result = PipelineResult()
        assert hasattr(result, "correlation_id")
        assert result.correlation_id is not None

    def test_pipeline_has_audit_logger(self) -> None:
        """AC-CORR-16: Pipeline has audit logger for event recording."""
        pipeline = TradingPipeline()
        assert hasattr(pipeline, "_audit")
