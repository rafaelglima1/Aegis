"""Pytest fixtures for AEGIS tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aegis.config import Settings, TradingEnvironment
from aegis.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Create test settings with SANDBOX defaults."""
    return Settings(
        TRADING_ENVIRONMENT=TradingEnvironment.SANDBOX,
        LIVE_ENABLED=False,
        DATABASE_URL="postgresql+asyncpg://aegis:aegis@localhost:5432/aegis_test",
        REDIS_URL="redis://localhost:6379/1",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    """Create test client."""
    app = create_app(settings)
    return TestClient(app)
