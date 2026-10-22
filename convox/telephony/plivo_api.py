"""PlivoDialer: place one leg with Plivo's Call API; the answer URL returns a Stream XML.

What Plivo documents (checked 2026-10-21):
  https://www.plivo.com/docs/voice/api/call/make-a-call
    POST https://api.plivo.com/v1/Account/{auth_id}/Call/  (HTTP Basic auth_id:auth_token)
    from, to (E.164 digits, no "+" in the examples), answer_url, answer_method (default
    POST); optional hangup_url, ring_url, ring_timeout, time_limit. Response
    {"message": "call fired", "request_uuid": ..., "api_id": ...}.
  https://www.plivo.com/docs/voice/audio-streaming/audio-streaming
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-l16;rate=16000"
     ...>wss://...</Stream>. contentType default audio/x-mulaw;rate=8000.

Notes:
  - request_uuid is not the CallUUID that later callbacks carry, so the answer and hangup
    URLs carry `call` and `side` as query params instead of relying on a sid lookup.
  - audioTrack="inbound" is from our telephony-integration doc; the current Plivo page
    does not list it. Harmless if ignored.
  - The answer route itself (GET/POST /telephony/plivo/answer) lives in api/webhooks.py.
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape, quoteattr

import httpx

from convox.telephony.base import DialError
from convox.types import Side

PLIVO_API = "https://api.plivo.com"
DEFAULT_TIMEOUT_S = 10.0


def stream_xml(ws_url: str, content_type: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>"
        f'<Stream bidirectional="true" keepCallAlive="true" audioTrack="inbound" '
        f"contentType={quoteattr(content_type)}>{escape(ws_url)}</Stream>"
        "</Response>"
    )


def _digits(number: str) -> str:
    return number.strip().lstrip("+")


class PlivoDialer:
    provider = "plivo"

    def __init__(
        self,
        *,
        auth_id: str,
        auth_token: str,
        public_base_url: str,
        client: httpx.AsyncClient | None = None,
        ring_timeout_s: int = 25,
        api_base: str = PLIVO_API,
    ) -> None:
        self.auth_id = auth_id
        self.public_base_url = public_base_url.rstrip("/")
        self.ring_timeout_s = ring_timeout_s
        self._auth = (auth_id, auth_token)
        self._url = f"{api_base.rstrip('/')}/v1/Account/{auth_id}/Call/"
        self._client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S)

    def answer_url(self, call_id: str, side: Side) -> str:
        return f"{self.public_base_url}/telephony/plivo/answer?call={call_id}&side={side}"

    def hangup_url(self, call_id: str, side: Side) -> str:
        return f"{self.public_base_url}/webhooks/plivo?call={call_id}&side={side}"

    async def dial(self, call_id: str, side: Side, number: str, caller_id: str, ws_url: str) -> str:
        body = {
            "from": _digits(caller_id),
            "to": _digits(number),
            "answer_url": self.answer_url(call_id, side),
            "answer_method": "POST",
            "hangup_url": self.hangup_url(call_id, side),
            "hangup_method": "POST",
            "ring_timeout": self.ring_timeout_s,
        }
        try:
            resp = await self._client.post(self._url, json=body, auth=self._auth)
        except httpx.HTTPError as e:
            raise DialError(f"plivo unreachable: {e!r}") from e
        data: Any
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code >= 400 or not isinstance(data, dict) or not data.get("request_uuid"):
            if isinstance(data, dict) and data.get("error"):
                raise DialError(str(data["error"]))
            raise DialError(f"plivo http {resp.status_code}: {resp.text[:200]}")
        uuid = data["request_uuid"]
        return str(uuid[0] if isinstance(uuid, list) else uuid)

    async def aclose(self) -> None:
        await self._client.aclose()
