"""AC-C10-24: Initial schema — all domain entities.

Revision ID: 001_initial
Revises:
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # market_states
    op.create_table(
        "market_states",
        sa.Column("market_state_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("ohlcv", sa.Text, nullable=False),
        sa.Column("indicators", sa.Text, server_default="{}"),
        sa.Column("market_context", sa.Text, server_default="{}"),
        sa.Column("data_quality", sa.String(20), server_default="GOOD"),
        sa.Column("source", sa.String(50), server_default="UNKNOWN"),
        sa.Column("hash", sa.String(64), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_market_states_asset", "market_states", ["asset"])
    op.create_index("ix_market_states_asset_timestamp", "market_states", ["asset", "timestamp"])

    # ai_runs
    op.create_table(
        "ai_runs",
        sa.Column("ai_run_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent", sa.String(50), server_default=""),
        sa.Column("provider", sa.String(50), server_default=""),
        sa.Column("model", sa.String(100), server_default=""),
        sa.Column("prompt_version", sa.String(50), server_default=""),
        sa.Column("input_hash", sa.String(64), server_default=""),
        sa.Column("output_hash", sa.String(64), server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), server_default="CREATED", nullable=False),
        sa.Column("latency_ms", sa.Integer, server_default="0"),
        sa.Column("token_usage", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    # trade_intents
    op.create_table(
        "trade_intents",
        sa.Column("trade_intent_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("stop_loss", sa.Numeric(20, 8), nullable=False),
        sa.Column("take_profit", sa.Numeric(20, 8), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("thesis", sa.Text, server_default=""),
        sa.Column("invalidation", sa.Text, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("market_state_id", UUID(as_uuid=True), sa.ForeignKey("market_states.market_state_id"), nullable=False),
        sa.Column("ai_run_id", UUID(as_uuid=True), sa.ForeignKey("ai_runs.ai_run_id"), nullable=False),
    )
    op.create_index("ix_trade_intents_asset", "trade_intents", ["asset"])

    # risk_decisions
    op.create_table(
        "risk_decisions",
        sa.Column("risk_decision_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("trade_intent_id", UUID(as_uuid=True), sa.ForeignKey("trade_intents.trade_intent_id"), nullable=False),
        sa.Column("status", sa.String(20), server_default="PENDING", nullable=False),
        sa.Column("approved_quantity", sa.Numeric(20, 8), server_default="0", nullable=False),
        sa.Column("approved_price", sa.Numeric(20, 8), server_default="0", nullable=False),
        sa.Column("risk_amount", sa.Numeric(20, 8), server_default="0", nullable=False),
        sa.Column("exposure", sa.Numeric(20, 8), server_default="0", nullable=False),
        sa.Column("reasons", sa.Text, server_default="[]", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    # orders
    op.create_table(
        "orders",
        sa.Column("order_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("client_order_id", sa.String(50), nullable=False, unique=True),
        sa.Column("status", sa.String(20), server_default="CREATED", nullable=False),
        sa.Column("filled_quantity", sa.Numeric(20, 8), server_default="0"),
        sa.Column("remaining_quantity", sa.Numeric(20, 8), server_default="0"),
        sa.Column("average_price", sa.Numeric(20, 8), server_default="0"),
        sa.Column("fees", sa.Numeric(20, 8), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_orders_status", "orders", ["status"])

    # fills
    op.create_table(
        "fills",
        sa.Column("fill_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", UUID(as_uuid=True), sa.ForeignKey("orders.order_id"), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("price", sa.Numeric(20, 8), nullable=False),
        sa.Column("fee", sa.Numeric(20, 8), server_default="0"),
        sa.Column("timestamp", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_fills_order_id", "fills", ["order_id"])

    # positions
    op.create_table(
        "positions",
        sa.Column("position_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), server_default="NONE", nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), server_default="0"),
        sa.Column("average_entry", sa.Numeric(20, 8), server_default="0"),
        sa.Column("current_price", sa.Numeric(20, 8), server_default="0"),
        sa.Column("stop_loss", sa.Numeric(20, 8), server_default="0"),
        sa.Column("take_profit", sa.Numeric(20, 8), server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(20, 8), server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(20, 8), server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_positions_asset", "positions", ["asset"])
    op.create_index("ix_positions_status", "positions", ["status"])

    # portfolio_snapshots
    op.create_table(
        "portfolio_snapshots",
        sa.Column("snapshot_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True)),
        sa.Column("cash", sa.Numeric(20, 8), server_default="0"),
        sa.Column("equity", sa.Numeric(20, 8), server_default="0"),
        sa.Column("exposure", sa.Numeric(20, 8), server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(20, 8), server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(20, 8), server_default="0"),
        sa.Column("drawdown", sa.Numeric(20, 8), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    # audit_events
    op.create_table(
        "audit_events",
        sa.Column("audit_event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("correlation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True)),
        sa.Column("actor", sa.String(50), server_default="system", nullable=False),
        sa.Column("payload_hash", sa.String(64), server_default=""),
    )
    op.create_index("ix_audit_events_correlation", "audit_events", ["correlation_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    op.create_index("ix_audit_events_correlation_event", "audit_events", ["correlation_id", "event_type"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("portfolio_snapshots")
    op.drop_table("positions")
    op.drop_table("fills")
    op.drop_table("orders")
    op.drop_table("risk_decisions")
    op.drop_table("trade_intents")
    op.drop_table("ai_runs")
    op.drop_table("market_states")
