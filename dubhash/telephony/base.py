"""The `Dialer` Protocol: place one outbound leg whose answered audio streams to our
`/ws/leg` websocket. One implementation per CPaaS (exotel_api, plivo_api) plus
`FakeDialer` for tests and dev.

Hanging up is not part of the Protocol: closing the leg's websocket ends the voicebot
applet (Exotel) or the Stream element (Plivo), and the flow then hangs up the call.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from dubhash.config.settings import Settings
from dubhash.types import Side

WIRE_SAMPLE_RATE = 16000


class DialError(Exception):
    """The provider rejected the dial request; the message is the provider's text."""


@runtime_checkable
class Dialer(Protocol):
    provider: str

    async def dial(self, call_id: str, side: Side, number: str, caller_id: str, ws_url: str) -> str:
        """Ring `number` showing `caller_id`; on answer stream to `ws_url`. Returns the
        provider's call sid (Exotel Call.Sid, Plivo request_uuid). Raises DialError."""
        ...


def leg_ws_url(settings: Settings, call_id: str, side: Side) -> str:
    base = settings.edge_wss_base.rstrip("/")
    return f"{base}/ws/leg?call={call_id}&side={side}&sample-rate={WIRE_SAMPLE_RATE}"


def status_callback_url(settings: Settings, provider: str, call_id: str, side: Side) -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/webhooks/{provider}?call={call_id}&side={side}"


@dataclass(slots=True, frozen=True)
class DialedCall:
    call_id: str
    side: Side
    number: str
    caller_id: str
    ws_url: str
    sid: str


class FakeDialer:
    """Records every dial; never touches the network. `fail_with` makes dials raise."""

    provider = "fake"

    def __init__(self, *, fail_with: str | None = None, fail_sides: set[str] | None = None):
        self.calls: list[DialedCall] = []
        self.fail_with = fail_with
        self.fail_sides = fail_sides
        self._n = itertools.count(1)

    async def dial(self, call_id: str, side: Side, number: str, caller_id: str, ws_url: str) -> str:
        if self.fail_with is not None and (self.fail_sides is None or side in self.fail_sides):
            raise DialError(self.fail_with)
        sid = f"fake-{call_id}-{side}-{next(self._n)}"
        self.calls.append(DialedCall(call_id, side, number, caller_id, ws_url, sid))
        return sid
