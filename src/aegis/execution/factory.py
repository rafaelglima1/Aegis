"""AEGIS Broker Factory — environment-driven broker selection.

AC-CORR-03: SANDBOX -> SandboxBroker
AC-CORR-04: LIVE + LIVE_ENABLED=true -> MercadoBitcoinBroker
AC-CORR-05: LIVE + LIVE_ENABLED=false -> RuntimeError (fail-closed)
AC-CORR-08: Selection by configuration only, no code change required.
"""

from __future__ import annotations

from aegis.config import Settings
from aegis.execution.broker import BrokerAdapter
from aegis.execution.sandbox import SandboxBroker
from aegis.execution.mercadobitcoin import MercadoBitcoinBroker, MercadoBitcoinConfig


def create_broker(settings: Settings | None = None) -> BrokerAdapter:
    """Create the appropriate broker based on environment configuration.

    SANDBOX -> SandboxBroker (paper trading)
    LIVE + LIVE_ENABLED=true -> MercadoBitcoinBroker (real exchange)
    LIVE + LIVE_ENABLED=false -> RuntimeError (fail-closed)
    """
    if settings is None:
        from aegis.config import get_settings
        settings = get_settings()

    if settings.trading_environment.value == "SANDBOX":
        return SandboxBroker()

    if settings.trading_environment.value == "LIVE":
        if not settings.live_enabled:
            raise RuntimeError(
                "LIVE trading is disabled (LIVE_ENABLED=false). "
                "Set LIVE_ENABLED=true to enable live execution."
            )

        config = MercadoBitcoinConfig(
            api_key=settings.live_api_key,
            api_secret=settings.live_api_secret,
            enabled=settings.live_enabled,
            max_positions=settings.max_positions,
        )
        return MercadoBitcoinBroker(config)

    raise ValueError(f"Unknown trading environment: {settings.trading_environment}")
