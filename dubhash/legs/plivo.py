"""PlivoLeg: Plivo bidirectional audio stream (<Stream bidirectional="true">) as a LegTransport.

Protocol, as documented (checked 2026-10-21):
  https://www.plivo.com/docs/voice-agents/audio-streaming/concepts/stream-event-protocol
  https://www.plivo.com/docs/voice/audio-streaming/audio-streaming

Inbound (Plivo -> us):
  {"event": "start", "sequenceNumber": 1,
   "start": {"callId", "streamId", "accountId", "tracks": ["inbound"],
             "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}},
   "extra_headers": "k1=v1;k2=v2"}
  {"event": "media", "streamId", "media": {"track", "timestamp", "chunk", "payload": b64}}
  {"event": "dtmf", "streamId", "dtmf": {"track", "digit": "5", "timestamp"}}
  {"event": "playedStream", "streamId", "name": "<checkpoint name>"}
  {"event": "clearedAudio", "streamId"}

Outbound (us -> Plivo):
  {"event": "playAudio", "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000,
                                    "payload": b64}}
  {"event": "checkpoint", "streamId", "name": "..."}  -> answered by playedStream
  {"event": "clearAudio", "streamId"}                 -> answered by clearedAudio

Stream XML contentType values: "audio/x-mulaw;rate=8000" (default), "audio/x-l16;rate=8000",
"audio/x-l16;rate=16000". Recommended playAudio size <= 16 KB base64; max ws message 64 KB.
Plivo signs the ws upgrade with X-Plivo-Signature-V3 / -Nonce (validation belongs to the API).

Unconfirmed / flagged:
  - The exact start.mediaFormat.encoding string for l16 is not shown; we accept any
    encoding containing "l16" or "mulaw", with the rate from sampleRate or ";rate=".
  - playAudio contentType for l16 is sent as "audio/x-l16" plus sampleRate, mirroring the
    documented mu-law example; it may need the ";rate=16000" suffix instead.
  - No `stop` event is documented on these pages. We handle {"event": "stop"} if it
    arrives and otherwise end on websocket disconnect.
  - India-region support for l16@16k on bidirectional streams is open question 6.
"""

from __future__ import annotations

import re
from typing import Any

from dubhash.audio.codec import ulaw_decode, ulaw_encode
from dubhash.legs.ws import JsonStreamLeg, WSLike, b64decode, b64encode
from dubhash.types import Side

PLIVO_DEFAULT_RATE = 8000
_RATE_RE = re.compile(r"rate=(\d+)")


def parse_media_format(media_format: Any) -> tuple[str, int]:
    """("mulaw" | "l16", sample_rate) from start.mediaFormat."""
    if not isinstance(media_format, dict):
        return "mulaw", PLIVO_DEFAULT_RATE
    enc = str(media_format.get("encoding") or "audio/x-mulaw").lower()
    codec = "l16" if "l16" in enc else "mulaw"
    rate: int | None = None
    try:
        rate = int(media_format["sampleRate"])
    except (KeyError, TypeError, ValueError):
        if m := _RATE_RE.search(enc):
            rate = int(m.group(1))
    return codec, rate or PLIVO_DEFAULT_RATE


def _parse_extra_headers(raw: Any) -> dict[str, str]:
    if not isinstance(raw, str):
        return {}
    out: dict[str, str] = {}
    for part in raw.split(";"):
        key, sep, value = part.partition("=")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


class PlivoLeg(JsonStreamLeg):
    provider = "plivo"

    def __init__(self, leg_id: Side, ws: WSLike, *, outbound_chunk_ms: int = 100) -> None:
        super().__init__(
            leg_id, ws, outbound_chunk_ms=outbound_chunk_ms, default_rate=PLIVO_DEFAULT_RATE
        )
        self.stream_id: str | None = None
        self.call_id: str | None = None
        self.encoding = "mulaw"
        self.extra_headers: dict[str, str] = {}

    @property
    def stream_sid(self) -> str | None:
        return self.stream_id

    @property
    def call_sid(self) -> str | None:
        return self.call_id

    async def _handle(self, event: str, msg: dict[str, Any]) -> bool:
        if event == "media":
            media = msg.get("media") or {}
            payload = b64decode(media.get("payload"))
            pcm = ulaw_decode(payload) if self.encoding == "mulaw" else payload
            self._media(pcm, media.get("chunk"))
        elif event == "playedStream":
            if (name := msg.get("name")) is not None:
                self._mark_played(str(name))
        elif event == "dtmf":
            digit = (msg.get("dtmf") or {}).get("digit")
            if digit is not None:
                self._dtmf(str(digit))
        elif event == "start":
            start = msg.get("start") or {}
            self.stream_id = start.get("streamId") or msg.get("streamId")
            self.call_id = start.get("callId")
            self.extra_headers = _parse_extra_headers(msg.get("extra_headers"))
            self.encoding, rate = parse_media_format(start.get("mediaFormat"))
            await self._begin(rate, self.stream_id)
        elif event == "stop":
            self.close_reason = "stop"
            return True
        return False

    def _media_message(self, wire_pcm16: bytes) -> dict[str, Any]:
        mulaw = self.encoding == "mulaw"
        return {
            "event": "playAudio",
            "media": {
                "contentType": "audio/x-mulaw" if mulaw else "audio/x-l16",
                "sampleRate": self.sample_rate,
                "payload": b64encode(ulaw_encode(wire_pcm16) if mulaw else wire_pcm16),
            },
        }

    def _mark_message(self, tag: str) -> dict[str, Any]:
        return {"event": "checkpoint", "streamId": self.stream_id, "name": tag}

    def _clear_message(self) -> dict[str, Any]:
        return {"event": "clearAudio", "streamId": self.stream_id}
