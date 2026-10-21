"""Outbound dialing through a CPaaS. `build_dialer(provider, settings)` returns a Dialer or
raises ProviderUnavailable when the provider is unknown or its credentials are missing."""

from __future__ import annotations

import httpx

from dubhash.config.settings import Settings
from dubhash.telephony.base import (
    Dialer,
    DialError,
    FakeDialer,
    leg_ws_url,
    status_callback_url,
)

__all__ = [
    "Dialer",
    "DialError",
    "FakeDialer",
    "ProviderUnavailable",
    "build_dialer",
    "leg_ws_url",
    "status_callback_url",
]

RING_TIMEOUT_S = 25


class ProviderUnavailable(Exception):
    """The provider is unknown, disabled, or its credentials are not configured."""


def build_dialer(
    provider: str, settings: Settings, *, client: httpx.AsyncClient | None = None
) -> Dialer:
    if provider == "fake":
        return FakeDialer()
    if provider == "exotel":
        from dubhash.telephony.exotel_api import ExotelDialer

        needed = (
            settings.exotel_account_sid,
            settings.exotel_api_key,
            settings.exotel_api_token,
            settings.exotel_api_host,
            settings.exotel_flow_url,
        )
        if not all(needed):
            raise ProviderUnavailable("exotel credentials or flow url not configured")
        return ExotelDialer(
            account_sid=settings.exotel_account_sid,
            api_key=settings.exotel_api_key,
            api_token=settings.exotel_api_token,
            host=settings.exotel_api_host,
            flow_url=settings.exotel_flow_url,
            status_callback=lambda call_id, side: status_callback_url(
                settings, "exotel", call_id, side
            ),
            client=client,
            ring_timeout_s=RING_TIMEOUT_S,
        )
    if provider == "plivo":
        from dubhash.telephony.plivo_api import PlivoDialer

        if not (settings.plivo_auth_id and settings.plivo_auth_token):
            raise ProviderUnavailable("plivo credentials not configured")
        return PlivoDialer(
            auth_id=settings.plivo_auth_id,
            auth_token=settings.plivo_auth_token,
            public_base_url=settings.public_base_url,
            client=client,
            ring_timeout_s=RING_TIMEOUT_S,
        )
    raise ProviderUnavailable(f"unknown provider {provider!r}")
