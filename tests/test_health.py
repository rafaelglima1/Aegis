"""Tests for AEGIS health endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_check(client: TestClient) -> None:
    """AC-01.10: Application health check responds successfully."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "environment" in data


def test_readiness_check(client: TestClient) -> None:
    """AC-01.10: Readiness check responds successfully."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
