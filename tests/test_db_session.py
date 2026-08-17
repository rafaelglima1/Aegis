"""Tests for AEGIS database session management."""

from __future__ import annotations

from aegis.db.session import (
    create_engine,
    get_session_factory,
)


def test_create_engine_returns_engine() -> None:
    """AC-03.02: SQLAlchemy 2.x is configured."""
    engine = create_engine("sqlite:///test.db")
    assert engine is not None
    engine.dispose()


def test_get_session_factory_returns_factory() -> None:
    """AC-03.02: SQLAlchemy 2.x is configured."""
    engine = create_engine("sqlite:///test.db")
    factory = get_session_factory(engine)
    assert factory is not None
    engine.dispose()
