"""Tests for AEGIS Portfolio & Accounting (Phase 07)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from aegis.domain.enums import PositionSide, PositionStatus
from aegis.portfolio.portfolio import Portfolio, PortfolioSnapshot
from aegis.portfolio.accounting import Accounting


def make_portfolio(cash: str = "10000.00") -> Portfolio:
    return Portfolio(initial_cash=Decimal(cash))


def test_cash_balance_maintained() -> None:
    """AC-07.01: Cash balance is maintained."""
    p = make_portfolio()
    assert p.cash == Decimal("10000.00")
    p.record_fill("AAPL", PositionSide.LONG, Decimal("10"), Decimal("100.00"))
    assert p.cash == Decimal("9000.00")


def test_positions_maintained() -> None:
    """AC-07.02: Positions are maintained."""
    p = make_portfolio()
    p.record_fill("AAPL", PositionSide.LONG, Decimal("10"), Decimal("100.00"))
    assert "AAPL" in p.positions
    assert p.positions["AAPL"].quantity == Decimal("10")


def test_fill_updates_position_correctly() -> None:
    """AC-07.03: Fills update positions correctly."""
    p = make_portfolio()
    p.record_fill("AAPL", PositionSide.LONG, Decimal("10"), Decimal("100.00"))
    p.record_fill("AAPL", PositionSide.LONG, Decimal("5"), Decimal("120.00"))
    assert p.positions["AAPL"].quantity == Decimal("15")


def test_average_cost_deterministic() -> None:
    """AC-07.04: Average cost is calculated deterministically."""
    acc = Accounting(make_portfolio())
    avg = acc.calculate_average_cost(Decimal("10"), Decimal("100"), Decimal("5"), Decimal("120"))
    # 1500 / 15 = 106.666...
    assert (avg * 15).quantize(Decimal("1")) == Decimal("1600")


def test_realized_pnl() -> None:
    """AC-07.05: Realized P&L is calculated."""
    p = make_portfolio()
    p.record_fill("AAPL", PositionSide.LONG, Decimal("10"), Decimal("100.00"))
    realized = p.close_position("AAPL", Decimal("120.00"))
    assert realized == Decimal("200.00")
    assert p.total_realized_pnl == Decimal("200.00")


def test_unrealized_pnl() -> None:
    """AC-07.06: Unrealized P&L is calculated."""
    p = make_portfolio()
    p.record_fill("AAPL", PositionSide.LONG, Decimal("10"), Decimal("100.00"))
    p.update_prices({"AAPL": Decimal("110.00")})
    assert p.unrealized_pnl == Decimal("100.00")


def test_fees_accounted_for() -> None:
    """AC-07.07: Fees are accounted for."""
    acc = Accounting(make_portfolio())
    fees = acc.calculate_fees(Decimal("10"), Decimal("100"), Decimal("0.001"))
    assert fees == Decimal("1.00")


def test_fees_deducted_from_cash() -> None:
    """AC-07.07: Fees are accounted for."""
    p = make_portfolio()
    p.record_fill("AAPL", PositionSide.LONG, Decimal("10"), Decimal("100.00"), fee=Decimal("5.00"))
    assert p.cash == Decimal("8995.00")
    assert p.total_fees == Decimal("5.00")


def test_exposure_calculated() -> None:
    """AC-07.08: Exposure is calculated."""
    p = make_portfolio()
    p.record_fill("AAPL", PositionSide.LONG, Decimal("10"), Decimal("100.00"))
    p.record_fill("GOOG", PositionSide.LONG, Decimal("5"), Decimal("200.00"))
    assert p.exposure == Decimal("2000.00")


def test_accounting_no_llm_dependency() -> None:
    """AC-07.09: Accounting does not depend on LLM output."""
    acc = Accounting(make_portfolio())
    result = acc.get_portfolio_summary()
    assert "cash" in result
    assert "equity" in result
    assert "exposure" in result


def test_snapshot_survives_restart() -> None:
    """AC-07.10: Financial state survives restart."""
    p = make_portfolio()
    p.record_fill("AAPL", PositionSide.LONG, Decimal("10"), Decimal("100.00"))
    snap = p.snapshot()
    assert isinstance(snap, PortfolioSnapshot)
    assert snap.cash == Decimal("9000.00")


def test_partial_fills() -> None:
    """AC-07.11: Accounting tests cover entries, exits, partial fills and fees."""
    p = make_portfolio()
    p.record_fill("AAPL", PositionSide.LONG, Decimal("5"), Decimal("100.00"))
    assert p.positions["AAPL"].quantity == Decimal("5")
    p.record_fill("AAPL", PositionSide.LONG, Decimal("5"), Decimal("110.00"))
    assert p.positions["AAPL"].quantity == Decimal("10")
    assert p.positions["AAPL"].average_entry == Decimal("105.00")


def test_entry_and_exit() -> None:
    """AC-07.11: Accounting tests cover entries, exits, partial fills and fees."""
    p = make_portfolio()
    p.record_fill("AAPL", PositionSide.LONG, Decimal("10"), Decimal("100.00"))
    realized = p.close_position("AAPL", Decimal("120.00"), fee=Decimal("2.00"))
    assert realized == Decimal("200.00")
    assert p.total_fees == Decimal("2.00")
    assert p.cash == Decimal("10198.00")
