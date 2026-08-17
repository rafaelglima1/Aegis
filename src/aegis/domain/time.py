"""AEGIS time and correlation contracts — UTC, correlation_id, idempotency."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4


def utc_now() -> datetime:
    """AC-02.10: All internal timestamps use UTC."""
    return datetime.now(timezone.utc)


def new_correlation_id() -> UUID:
    """AC-02.08: Generate a new correlation_id for operation tracing."""
    return uuid4()


def new_idempotency_key() -> str:
    """AC-02.09: Generate a new idempotency key."""
    return str(uuid4())
