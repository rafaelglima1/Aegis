"""Tests for AEGIS time and correlation contracts."""

from __future__ import annotations

from aegis.domain.time import utc_now, new_correlation_id, new_idempotency_key


def test_utc_now_returns_utc() -> None:
    """AC-02.10: Internal timestamps use UTC."""
    now = utc_now()
    assert now.tzinfo is not None
    assert now.tzinfo.utcoffset(now).total_seconds() == 0


def test_new_correlation_id_is_unique() -> None:
    """AC-02.08: correlation_id is propagated through critical operations."""
    cid1 = new_correlation_id()
    cid2 = new_correlation_id()
    assert cid1 != cid2


def test_new_idempotency_key_is_unique() -> None:
    """AC-02.09: Idempotency keys are supported."""
    key1 = new_idempotency_key()
    key2 = new_idempotency_key()
    assert key1 != key2
    assert len(key1) > 0
