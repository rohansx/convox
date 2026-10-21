"""Sarvam realtime speech -> English text (Saaras `mode=translate`) over websocket.

Same socket, events and warm-pool behaviour as SarvamSTT (sarvam_stt.py); the only wire
difference is `mode=translate` on the URL. Per Sarvam's docs the task is applied to the
final transcript only: "Partials are always plain transcription", so partials stay in the
source language while end() returns English. Pair it with `mt_src: en-IN` in pairs.yaml so
MT translates en -> target (the pivot route, open question L1).

https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming
https://docs.sarvam.ai/api-reference/speech-to-text/transcribe/realtime/ws
"""

from __future__ import annotations

from urllib.parse import urlencode

from dubhash.providers.sarvam_stt import SarvamSTT, SarvamSTTStream


class SarvamSTTTranslateStream(SarvamSTTStream):
    provider = "sarvam-translate"


class SarvamSTTTranslate(SarvamSTT):
    name = "sarvam-translate"

    def _new_stream(self, lang: str) -> SarvamSTTStream:
        return SarvamSTTTranslateStream(self, lang, self._endpointing)

    def _url(self, lang: str) -> str:
        return f"{super()._url(lang)}&{urlencode({'mode': 'translate'})}"
