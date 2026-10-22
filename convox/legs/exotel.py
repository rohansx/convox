"""ExotelLeg: Exotel Voicebot applet (bidirectional stream) as a LegTransport.

Protocol, as documented (checked 2026-10-21):
  https://developer.exotel.com/docs/agentstream/stream-voicebot-applet
  https://support.exotel.com/support/solutions/articles/3000108630

Inbound (Exotel -> us), JSON text frames keyed by "event":
  {"event": "connected"}
  {"event": "start", "sequence_number": 1, "stream_sid": "...",
   "start": {"stream_sid", "call_sid", "account_sid", "from", "to",
             "custom_parameters": {...},
             "media_format": {"encoding": "raw", "sample_rate": "8000", "bit_rate": "128"}}}
  {"event": "media", "sequence_number": 3, "stream_sid": "...",
   "media": {"chunk": 2, "timestamp": "10", "payload": "<base64 PCM16 LE mono>"}}
  {"event": "dtmf", ..., "dtmf": {"duration": "200", "digit": "5"}}
  {"event": "mark", ..., "mark": {"name": "<label>"}}  # audio before our mark has played
  {"event": "stop", ..., "stop": {"call_sid", "account_sid", "reason": "stopped"}}

Outbound (us -> Exotel, voicebot only):
  {"event": "media", "stream_sid": "...", "media": {"payload": "<base64>"}}
  {"event": "mark", "stream_sid": "...", "mark": {"name": "..."}}
  {"event": "clear", "stream_sid": "..."}

Audio: raw/slin PCM16 LE mono. 8000 Hz by default, 16000 or 24000 when the URL carries
`?sample-rate=16000`. Outbound chunks: min 3.2 KB (100 ms), max 100 KB, multiple of 320 B.
Up to 3 custom URL params (256 chars total) come back in start.custom_parameters.

Unconfirmed / flagged:
  - Whether the voicebot applet honours sample-rate=16000 (open question 1). We trust
    start.media_format.sample_rate (a string in the docs; ints accepted) and resample
    both ways when it is not 16000. If media_format is missing we assume 8000, the
    documented default, and log leg.resample.
  - The 3.2 KB minimum applies to every outbound media message per the docs. Our final
    chunk before a mark is padded only to a multiple of 320 B (audio.md section 7), so it
    can be shorter than 100 ms. If Exotel rejects or drops short chunks, pad to a full
    chunk in OutboundAudio.flush_final.
  - Whether Exotel echoes marks that were queued before a `clear` (Twilio does). We drop
    pending marks on clear, so stale echoes are ignored either way.
  - The docs do not say whether `timestamp` is ms or what `chunk` starts at; only the
    chunk counter is used, for gap detection.
"""

from __future__ import annotations

from typing import Any

from convox.legs.ws import JsonStreamLeg, WSLike, b64decode, b64encode
from convox.types import Side

EXOTEL_DEFAULT_RATE = 8000


def parse_rate(media_format: Any, default: int = EXOTEL_DEFAULT_RATE) -> int:
    if not isinstance(media_format, dict):
        return default
    try:
        return int(media_format.get("sample_rate") or default)
    except (TypeError, ValueError):
        return default


class ExotelLeg(JsonStreamLeg):
    provider = "exotel"

    def __init__(self, leg_id: Side, ws: WSLike, *, outbound_chunk_ms: int = 100) -> None:
        super().__init__(
            leg_id, ws, outbound_chunk_ms=outbound_chunk_ms, default_rate=EXOTEL_DEFAULT_RATE
        )
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        self.from_number: str | None = None
        self.to_number: str | None = None
        self.custom_parameters: dict[str, Any] = {}

    async def _handle(self, event: str, msg: dict[str, Any]) -> bool:
        if event == "media":
            media = msg.get("media") or {}
            self._media(b64decode(media.get("payload")), media.get("chunk"))
        elif event == "mark":
            name = (msg.get("mark") or {}).get("name")
            if name is not None:
                self._mark_played(str(name))
        elif event == "dtmf":
            digit = (msg.get("dtmf") or {}).get("digit")
            if digit is not None:
                self._dtmf(str(digit))
        elif event == "start":
            start = msg.get("start") or {}
            self.stream_sid = start.get("stream_sid") or msg.get("stream_sid")
            self.call_sid = start.get("call_sid")
            self.from_number = start.get("from")
            self.to_number = start.get("to")
            self.custom_parameters = dict(start.get("custom_parameters") or {})
            await self._begin(parse_rate(start.get("media_format")), self.stream_sid)
        elif event == "stop":
            self.close_reason = (msg.get("stop") or {}).get("reason") or "stop"
            return True
        return False

    def _media_message(self, wire_pcm16: bytes) -> dict[str, Any]:
        return {
            "event": "media",
            "stream_sid": self.stream_sid,
            "media": {"payload": b64encode(wire_pcm16)},
        }

    def _mark_message(self, tag: str) -> dict[str, Any]:
        return {"event": "mark", "stream_sid": self.stream_sid, "mark": {"name": tag}}

    def _clear_message(self) -> dict[str, Any]:
        return {"event": "clear", "stream_sid": self.stream_sid}
