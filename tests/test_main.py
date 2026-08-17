"""Tests for AEGIS FastAPI application."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_app_starts_successfully(client: TestClient) -> None:
    """AC-01.02: FastAPI starts successfully."""
    response = client.get("/health")
    assert response.status_code == 200


def test_app_title(settings: "Settings", client: TestClient) -> None:
    """AC-01.02: FastAPI has correct title."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert data["info"]["title"] == "AEGIS"
    assert data["info"]["version"] == "1.3.0"
