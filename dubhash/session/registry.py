"""In-process table of live CallSessions, keyed by call id.

In memory only (ADR 0009): both legs of a call land in this process, so the `/ws/leg`
handler finds the session here. A Redis registry (call id -> owning node) comes later
for multi-node lookup; it will sit beside this table, written at setup and teardown
only, never per frame.
"""

from __future__ import annotations

import base64
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dubhash.session.call_session import CallSession


def new_call_id() -> str:
    """A new call id: "c_" + 10 lowercase base32 chars (50 random bits) from `secrets`."""
    return "c_" + base64.b32encode(secrets.token_bytes(10)).decode("ascii").lower()[:10]


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, CallSession] = {}

    def create(self, call_id: str, session: CallSession) -> None:
        if call_id in self._sessions:
            raise KeyError(f"call {call_id} already registered")
        self._sessions[call_id] = session

    def get(self, call_id: str) -> CallSession | None:
        return self._sessions.get(call_id)

    def remove(self, call_id: str) -> None:
        self._sessions.pop(call_id, None)

    def list(self) -> list[CallSession]:
        return list(self._sessions.values())

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, call_id: object) -> bool:
        return call_id in self._sessions
