"""AEGIS Broker Factory — environment-driven broker selection."""

from __future__ import annotations

from typing import Union

from aegis.config import Settings
from aegis.execution.broker import BrokerAdapter
from aegis.execution.sandbox import SandboxBroker
from aegis.execution.live import LiveBroker, LiveBrokerConfig


def create_broker(settings: Settings) -> BrokerAdapter:
    """AC-09.04: TRADING_ENVIRONMENT=SANDBOX selects SandboxBroker.
    AC-09.05: TRADING_ENVIRONMENT=LIVE selects LiveBroker.
    AC-09.06: Changing environment requires no application-code modification."""

    if settings.trading_environment.value == "SANDBOX":
        return SandboxBroker()

    if settings.trading_environment.value == "LIVE":
        if not settings.live_enabled:
            raise RuntimeError("LIVE trading is disabled (LIVE_ENABLED=false)")

        config = LiveBrokerConfig(
            enabled=settings.live_enabled,
        )
        return LiveBroker(config)

    raise ValueError(f"Unknown trading environment: {settings.trading_environment}")
