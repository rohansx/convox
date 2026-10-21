"""ExotelDialer: place one leg with Exotel's Calls/connect into the voicebot flow.

What Exotel documents (checked 2026-10-21):
  https://developer.exotel.com/api/make-a-call-api
    POST https://<key>:<token>@<subdomain>/v1/Accounts/<sid>/Calls/connect
    mandatory From (E.164), CallerId (ExoPhone), Url = http://my.exotel.com/{sid}/exoml/
    start_voice/{app_id}; optional CustomField ("any application specific value that will
    be passed back as a parameter"), StatusCallback, StatusCallbackEvents[] (terminal,
    answered), TimeLimit, TimeOut (ring seconds), CallType. Response Call.Sid. We append
    `.json` to the path to get JSON instead of XML.
  https://developer.exotel.com/docs/agentstream/stream-voicebot-applet
  https://support.exotel.com/support/solutions/articles/3000108630
    The Voicebot applet URL is either a static wss URL with up to 3 query params (256
    chars, echoed in start.custom_parameters) or "an HTTPS endpoint that dynamically
    returns the WSS URL" as JSON {"url": "wss://..."}.

How the per-leg ws URL reaches the applet (open question material):
  Calls/connect has no parameter that sets the applet's URL, and a static URL cannot carry
  a per-call `call` id. So the flow's Voicebot applet is configured with the dynamic
  HTTPS endpoint GET/POST {public_base_url}/telephony/exotel/stream (api/webhooks.py), which
  returns {"url": leg_ws_url(call, side)}. To resolve call and side it reads, in order:
    1. `call` and `side` query params (if the flow is configured with them),
    2. CustomField = "<call_id>:<side>", which we send on every Calls/connect,
    3. CallSid, looked up in the CallBook (we record Call.Sid as soon as dial returns).
  Exotel does not document which parameters it sends to the dynamic endpoint. Passthru
  sends CallSid/From/To/CustomField as query params; we assume the same and keep (3) as
  the fallback. Confirm with the first sponsor minute and with hello@exotel.com.
  The `ws_url` argument to dial() is therefore not sent to Exotel; it is recomputed by
  the stream endpoint from settings.edge_wss_base.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

from dubhash.telephony.base import DialError
from dubhash.types import Side

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0


def parse_custom_field(value: Any) -> tuple[str, Side] | None:
    if not isinstance(value, str):
        return None
    call_id, sep, side = value.strip().rpartition(":")
    if not sep or not call_id or side not in ("a", "b"):
        return None
    return call_id, side  # type: ignore[return-value]


def _host(host: str) -> str:
    host = host.strip().rstrip("/")
    for scheme in ("https://", "http://"):
        if host.startswith(scheme):
            host = host[len(scheme) :]
    return host


class ExotelDialer:
    provider = "exotel"

    def __init__(
        self,
        *,
        account_sid: str,
        api_key: str,
        api_token: str,
        host: str,
        flow_url: str,
        status_callback: Callable[[str, Side], str] | None = None,
        client: httpx.AsyncClient | None = None,
        ring_timeout_s: int = 25,
    ) -> None:
        self.account_sid = account_sid
        self.flow_url = flow_url
        self.status_callback = status_callback
        self.ring_timeout_s = ring_timeout_s
        self._auth = (api_key, api_token)
        self._url = f"https://{_host(host)}/v1/Accounts/{account_sid}/Calls/connect.json"
        self._client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S)

    async def dial(self, call_id: str, side: Side, number: str, caller_id: str, ws_url: str) -> str:
        form: dict[str, str] = {
            "From": number,
            "CallerId": caller_id,
            "Url": self.flow_url,
            "CustomField": f"{call_id}:{side}",
            "TimeOut": str(self.ring_timeout_s),
        }
        if self.status_callback is not None:
            form["StatusCallback"] = self.status_callback(call_id, side)
            form["StatusCallbackEvents[0]"] = "terminal"
            form["StatusCallbackEvents[1]"] = "answered"
        try:
            resp = await self._client.post(self._url, data=form, auth=self._auth)
        except httpx.HTTPError as e:
            raise DialError(f"exotel unreachable: {e!r}") from e
        body: Any
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code >= 400 or not isinstance(body, dict):
            raise DialError(_error_text(body, resp))
        sid = (body.get("Call") or {}).get("Sid")
        if not sid:
            raise DialError(f"exotel response without Call.Sid: {resp.text[:200]}")
        return str(sid)

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_text(body: Any, resp: httpx.Response) -> str:
    if isinstance(body, dict):
        exc = body.get("RestException") or {}
        if isinstance(exc, dict) and exc.get("Message"):
            return str(exc["Message"])
    return f"exotel http {resp.status_code}: {resp.text[:200]}"
