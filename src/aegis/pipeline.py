"""AEGIS Trading Pipeline — orchestrates the full trading flow."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from aegis.ai_engine.decision_engine import DecisionContract
from aegis.domain.enums import OrderSide, TradingAction
from aegis.domain.contracts import utc_now
from aegis.risk_engine.risk_engine import RiskEngine, RiskDecision
from aegis.execution.sandbox import SandboxBroker
from aegis.execution.engine import ExecutionEngine
from aegis.portfolio.portfolio import Portfolio
from aegis.audit import AuditLogger, AuditEventType

logger = logging.getLogger("aegis.pipeline")


@dataclass
class PipelineResult:
    """Result of a complete trading pipeline run."""

    pipeline_id: UUID = field(default_factory=uuid4)
    correlation_id: UUID = field(default_factory=uuid4)
    symbol: str = ""
    decision: DecisionContract | None = None
    risk_result: RiskDecision | None = None
    order_result: Any = None
    status: str = "PENDING"
    errors: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)


class TradingPipeline:
    """Complete trading pipeline: Market→AI→Risk→Execution→Portfolio→Audit.

    This orchestrates the full flow:
    1. Market Data → Market State
    2. Market State → AI Decision
    3. AI Decision → Risk Evaluation
    4. Risk Evaluation → Order Execution
    5. Order Execution → Portfolio Update
    6. Portfolio Update → Audit Log
    """

    def __init__(
        self,
        risk_engine: RiskEngine | None = None,
        broker: SandboxBroker | None = None,
        portfolio: Portfolio | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        self._risk = risk_engine or RiskEngine()
        self._broker = broker or SandboxBroker()
        self._execution = ExecutionEngine(self._broker)
        self._portfolio = portfolio or Portfolio()
        self._audit = audit or AuditLogger()
        self._state: dict[str, Any] = {
            "capital": str(Decimal("100.00")),
            "positions": [],
            "orders": [],
            "history": [],
            "decisions": [],
        }

    @property
    def state(self) -> dict[str, Any]:
        """Get current pipeline state."""
        return self._state.copy()

    async def run(
        self,
        symbol: str,
        decision: DecisionContract,
    ) -> PipelineResult:
        """Run the complete trading pipeline for a decision.

        Args:
            symbol: Trading pair (e.g., "BTC-BRL")
            decision: AI-generated decision contract

        Returns:
            PipelineResult with all steps documented
        """
        result = PipelineResult(
            symbol=symbol,
            decision=decision,
            correlation_id=decision.correlation_id,
        )

        try:
            # Step 1: Record decision in audit
            self._audit.record_event(
                event_type=AuditEventType.DECISION,
                correlation_id=decision.correlation_id,
                data={
                    "symbol": symbol,
                    "action": decision.action.value,
                    "confidence": str(decision.confidence),
                    "thesis": decision.thesis,
                },
            )

            # Step 2: Risk evaluation
            risk_result = self._risk.evaluate(decision)
            result.risk_result = risk_result

            self._audit.record_event(
                event_type=AuditEventType.RISK,
                correlation_id=decision.correlation_id,
                data={
                    "approved": risk_result.is_approved,
                    "violations": [v.code for v in risk_result.violations],
                },
            )

            if not risk_result.is_approved:
                result.status = "REJECTED"
                result.errors = risk_result.reasons
                logger.info(
                    "Risk rejected: %s, violations: %s",
                    symbol,
                    [v.code for v in risk_result.violations],
                )
                return result

            # Step 3: Execute order
            if decision.action == TradingAction.LONG:
                order_result = await self._execution.execute_order(
                    order_id=uuid4(),
                    idempotency_key=uuid4(),
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=risk_result.approved_quantity,
                    price=risk_result.approved_price,
                    correlation_id=decision.correlation_id,
                    risk_approved=True,
                )
                result.order_result = order_result

                self._audit.record_event(
                    event_type=AuditEventType.ORDER,
                    correlation_id=decision.correlation_id,
                    data={
                        "order_id": str(order_result.order_id),
                        "status": order_result.status.value,
                        "fill_price": str(order_result.fill_price) if order_result.fill_price else None,
                    },
                )

                # Step 4: Update portfolio
                if order_result.fill_price:
                    self._portfolio.record_fill(
                        symbol=symbol,
                        side=OrderSide.BUY,
                        quantity=risk_result.approved_quantity,
                        price=order_result.fill_price,
                    )

                    # Record position
                    position = {
                        "id": str(uuid4()),
                        "symbol": symbol,
                        "side": "LONG",
                        "quantity": str(risk_result.approved_quantity),
                        "entry_price": str(order_result.fill_price),
                        "stop_loss": str(decision.stop_loss) if decision.stop_loss else None,
                        "take_profit": str(decision.take_profit) if decision.take_profit else None,
                        "status": "OPEN",
                        "opened_at": utc_now().isoformat(),
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
                        "timestamp": utc_now().isoformat(),
                    }
                    self._state["orders"].append(order_record)

                    # Record decision
                    decision_record = {
                        "symbol": symbol,
                        "action": decision.action.value,
                        "confidence": float(decision.confidence),
                        "thesis": decision.thesis,
                        "provider": decision.provider,
                        "model": decision.model,
                        "reasoning": decision.reasoning,
                        "timestamp": utc_now().isoformat(),
                    }
                    self._state["decisions"].append(decision_record)

                    # Update risk engine state
                    self._risk.record_position_open()

                    # Step 5: Audit portfolio update
                    self._audit.record_event(
                        event_type=AuditEventType.PORTFOLIO,
                        correlation_id=decision.correlation_id,
                        data={
                            "symbol": symbol,
                            "action": "OPEN_POSITION",
                            "quantity": str(risk_result.approved_quantity),
                            "price": str(order_result.fill_price),
                        },
                    )

                result.status = "FILLED"
            else:
                result.status = "NO_ACTION"

            logger.info(
                "Pipeline completed: %s, status: %s",
                symbol,
                result.status,
            )

        except Exception as e:
            result.status = "ERROR"
            result.errors.append(str(e))
            logger.error("Pipeline error: %s, error: %s", symbol, e)

            self._audit.record_event(
                event_type=AuditEventType.ERROR,
                correlation_id=decision.correlation_id,
                data={"error": str(e)},
            )

        return result

    async def close_position(self, position_id: str) -> PipelineResult:
        """Close an existing position."""
        result = PipelineResult(symbol="", status="PENDING")

        # Find position
        position = None
        for pos in self._state["positions"]:
            if pos.get("id") == position_id:
                position = pos
                break

        if not position:
            result.status = "NOT_FOUND"
            result.errors.append(f"Position {position_id} not found")
            return result

        try:
            # Create close decision
            decision = DecisionContract(
                action=TradingAction.CLOSE,
                confidence=Decimal("1.0"),
                thesis=f"Closing position {position_id}",
            )

            # Execute close
            order_result = await self._execution.execute_order(
                order_id=uuid4(),
                idempotency_key=uuid4(),
                symbol=position["symbol"],
                side=OrderSide.SELL,
                quantity=Decimal(position["quantity"]),
                price=Decimal(position["entry_price"]),
                correlation_id=decision.correlation_id,
                risk_approved=True,
            )

            # Update portfolio
            if order_result.fill_price:
                self._portfolio.record_fill(
                    symbol=position["symbol"],
                    side=OrderSide.SELL,
                    quantity=Decimal(position["quantity"]),
                    price=order_result.fill_price,
                )

                # Calculate P&L
                entry = Decimal(position["entry_price"])
                exit_price = order_result.fill_price
                qty = Decimal(position["quantity"])
                pnl = (exit_price - entry) * qty

                # Add to history
                trade = {
                    "date": utc_now().strftime("%Y-%m-%d %H:%M"),
                    "symbol": position["symbol"],
                    "side": "LONG",
                    "quantity": str(qty),
                    "entry_price": str(entry),
                    "exit_price": str(exit_price),
                    "pnl": str(pnl),
                    "fee": "0",
                }
                self._state["history"].append(trade)

                # Update capital — keep as Decimal strings
                self._state["capital"] = str(Decimal(self._state["capital"]) + pnl)
                self._state["pnl"] = str(Decimal(self._state.get("pnl", "0")) + pnl)

                # Update risk engine
                self._risk.record_position_close()
                self._risk.record_daily_pnl(pnl)

            # Mark position as closed
            position["status"] = "CLOSED"

            result.status = "CLOSED"
            logger.info("Position closed: %s, P&L: %s", position_id, pnl)

        except Exception as e:
            result.status = "ERROR"
            result.errors.append(str(e))
            logger.error("Close position error: %s", e)

        return result
