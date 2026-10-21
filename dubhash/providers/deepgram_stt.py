"""Deepgram Nova streaming STT (English / Hinglish fallback). Mock-tested only: no key yet.

https://developers.deepgram.com/reference/speech-to-text/listen-streaming
Audio goes up as binary linear16 frames. On end() we send {"type": "Finalize"}; Deepgram
flushes and marks the last Results message with from_finalize=true. Idle warm sockets get
{"type": "KeepAlive"} (Deepgram closes after ~10 s without audio or KeepAlive).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from dubhash.audio.frames import SAMPLE_RATE
from dubhash.providers.sarvam_stt import WarmSTTPool, WarmSTTStream, WireMsg

DEFAULT_BASE_URL = "wss://api.deepgram.com"
DEFAULT_MODEL = "nova-3"
# BCP-47 -> Deepgram language param. Unverified for nova-3 beyond "hi", "en-IN", "multi";
# override with language= ("multi" is Deepgram's code-switching mode for Hinglish).
_LANGS = {"hi-IN": "hi", "en-IN": "en-IN"}


class DeepgramSTTStream(WarmSTTStream):
    provider = "deepgram"

    def _audio_msg(self, frame: bytes) -> WireMsg:
        return frame

    def _end_msgs(self) -> list[WireMsg]:
        return [{"type": "Finalize"}]

    def _on_message(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "Results":
            alts = (msg.get("channel") or {}).get("alternatives") or [{}]
            text = str(alts[0].get("transcript", ""))
            if not msg.get("is_final"):
                if text:
                    self._partials.put_nowait(text)
                return
            self._add_final(text, settles=bool(msg.get("from_finalize")))
            if msg.get("speech_final"):
                self.eou_signal.set()
        elif kind == "UtteranceEnd":
            self.eou_signal.set()


class DeepgramSTT(WarmSTTPool):
    name = "deepgram"
    _ping = {"type": "KeepAlive"}

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        language: str | None = None,
        final_timeout_s: float = 3.0,
        connect_timeout_s: float = 5.0,
        keepalive_s: float = 5.0,
        pool_size: int = 2,
    ) -> None:
        super().__init__(
            headers={"Authorization": f"Token {api_key}"},
            final_timeout_s=final_timeout_s,
            connect_timeout_s=connect_timeout_s,
            keepalive_s=keepalive_s,
            pool_size=pool_size,
        )
        self._base = base_url.rstrip("/")
        self._model = model
        self._language = language

    async def open(self, lang: str) -> DeepgramSTTStream:
        stream = await self._open(lang)
        assert isinstance(stream, DeepgramSTTStream)
        return stream

    def _new_stream(self, lang: str) -> DeepgramSTTStream:
        return DeepgramSTTStream(self, lang)

    def _url(self, lang: str) -> str:
        q = {
            "model": self._model,
            "language": self._language or _LANGS.get(lang, lang),
            "encoding": "linear16",
            "sample_rate": SAMPLE_RATE,
            "channels": 1,
            "interim_results": "true",
            "punctuate": "true",
            "vad_events": "true",
            "utterance_end_ms": 1000,
        }
        return f"{self._base}/v1/listen?{urlencode(q)}"
