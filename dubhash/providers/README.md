# providers

Vendor clients behind the Protocols in `dubhash/interp/contracts.py`. Every client takes an
injectable `base_url` so unit tests can point it at a local mock (`httpx.MockTransport` or
a `websockets.serve` server on 127.0.0.1). All TTS output is PCM16 mono 16 kHz.

| Module | Class (`name`) | Protocol | Transport |
|---|---|---|---|
| `sarvam_stt.py` | `SarvamSTT` (`sarvam`) → `SarvamSTTStream` | STT / STTStream | websocket, warm pool |
| `deepgram_stt.py` | `DeepgramSTT` (`deepgram`) → `DeepgramSTTStream` | STT / STTStream | websocket, warm pool (mock-tested only) |
| `sarvam_mt.py` | `SarvamMT` (`sarvam`) | MT | REST, shared `httpx.AsyncClient` |
| `sarvam_tts.py` | `BulbulTTS` (`bulbul`) | TTS | websocket, warm pool |
| `eleven_tts.py` | `ElevenTTS` (`eleven`) | TTS | HTTP streaming |
| `registry.py` | `build_stt/build_mt/build_tts(name, settings)`, `ProviderError`, `ProviderUnavailable` | | |

`build_*` returns the fake (lazily imported from `providers/fakes.py`) when the name is
`fake`, the matching `*_IMPL` setting is `fake` (the default), or `DUBHASH_ENV=test`.
A missing key raises `ProviderUnavailable` naming the env var.

Live tests: `uv run pytest -m live -s` (needs `.env` with keys; each test skips without
its key). They were **not run** for this branch because no `.env` with keys existed.

Researched 2026-10-21 from the vendors' own docs (links per section).

---

## Sarvam STT: realtime streaming (`saaras:v3-realtime`)

Source: [realtime streaming guide](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming),
[API reference](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe/realtime/ws).

- URL: `wss://api.sarvam.ai/speech-to-text-realtime/ws?language_code=hi-IN&model=saaras:v3-realtime&stream_type=fast&endpointing=manual&encoding=linear16&sample_rate=16000`
- Auth header: `api-subscription-key: <key>` (case-insensitive; reference writes `API-SUBSCRIPTION-KEY`).
- Input: `{"event": "audio_input", "audio": "<base64 linear16>"}`, mono, 16000 or 8000 Hz
  only (anything else closes with 4000). We send our 640-byte frames as they come.
- Server events: `session.begin`, `vad.speech_start` / `vad.speech_end` (vad mode only),
  `transcript.partial` (`text` = transcript so far), `transcript.final` (`text`, once per
  utterance), `config.updated`, `pong`, `session.end` (`audio_duration_s` = billed audio),
  `error` (`code`, `is_fatal`, `message`).
- Turn control: `endpointing=vad` (default; server VAD, tune `threshold`,
  `silence_duration_ms` default 500, `min_speech_duration_ms` default 250) or
  `endpointing=manual` (client sends `{"event":"speech_start"}` / `{"event":"speech_end"}`;
  `{"event":"flush"}` force-finalises, manual only).
- One socket supports many utterances. `config.update` can change `language_code`, mode,
  etc. at the next utterance boundary without reconnecting.
- Keepalive: `{"event":"ping"}` → `pong`. Close code 1008 = inactivity timeout (duration
  not documented). Other closes: 1003 quota/bad key, 1011 server error, 4000 bad params.
- `stream_type`: `fast` (lowest-latency partials), `balanced` (default), `simulated` (no partials).
- Odia is `or-IN` here but `od-IN` on REST (translate, TTS). The clients map both ways.
- The older `wss://api.sarvam.ai/speech-to-text/ws` (saaras:v3, `vad_signals`,
  `flush_signal`, `{"audio":{"data","sample_rate","encoding"}}`) gives no partials; not used.

**Our choices.** Default `endpointing="manual"`: `open()` sends `speech_start`, `end()`
sends `speech_end` and awaits the one `transcript.final` (timeout 3 s → `""` plus a
`stt.final_timeout` warning). This keeps finals 1:1 with our Silero utterances.
`endpointing="vad"` is available: finals inside the stream are joined, and
`vad.speech_end` sets `stream.eou_signal` (an `asyncio.Event`) for the Endpointer's
`force_eou`. In manual mode `eou_signal` is never set.
Warm sockets: after a settled final the socket returns to a per-language idle pool (size 2)
and is pinged every `keepalive_s` (10 s). `prewarm(lang)` opens one ahead of speech.
If the socket dies mid-utterance, the stream reconnects (up to 2 times) and replays the
utterance's buffered audio (and `speech_end` if already sent). A socket is only reused
when its utterance settled, so a late final can never leak into the next utterance.

## Sarvam translate (Mayura)

Source: [translate API](https://docs.sarvam.ai/api-reference/text/translate-text),
[Mayura model](https://docs.sarvam.ai/api/getting-started/models/mayura),
[Sarvam Translate model](https://docs.sarvam.ai/api/getting-started/models/sarvam-translate).

- `POST https://api.sarvam.ai/translate`, header `api-subscription-key`.
- Body we send: `input`, `source_language_code`, `target_language_code`,
  `mode: "modern-colloquial"`, `model: "mayura:v1"`, `numerals_format: "international"`
  (= keep 0-9 digits; `native` is the opt-in). Response: `translated_text`.
- Modes: `formal` (default), `modern-colloquial`, `classic-colloquial`, `code-mixed`.
  The spec's "colloquial" is mapped to `modern-colloquial`.
- Input limit 1000 chars for mayura:v1.
- **Flag: Indic→Indic.** The Mayura page describes it as English ↔ Indian languages;
  `sarvam-translate:v1` is "bidirectional for all listed languages" but formal only. It is
  not documented whether `hi-IN → kn-IN` on mayura is accepted. `SarvamMT` tries the direct
  pair; on HTTP 400 for a non-English pair it falls back to an English pivot (two calls) and
  remembers that pair. The live test `test_live_sarvam_translate_hi_to_kn` settles it;
  if the pivot is needed, consider `model="sarvam-translate:v1"` (direct, formal) instead.

## Sarvam Bulbul TTS (websocket)

Source: [streaming TTS reference](https://docs.sarvam.ai/api-reference/text-to-speech/stream),
[Bulbul model](https://docs.sarvam.ai/api/getting-started/models/bulbul),
[voices](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/voices).

- URL: `wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3&send_completion_event=true`,
  header `api-subscription-key`.
- Per synth: `{"type":"config","data":{language_code, speaker, speech_sample_rate:"16000",
  output_audio_codec:"linear16", min_buffer_size}}`, then `{"type":"text","data":{"text"}}`,
  then `{"type":"flush"}`. Server: `{"type":"audio","data":{"content_type","audio":<b64>}}`
  …, then `{"type":"event","data":{"event_type":"final"}}`; errors as `{"type":"error",...}`.
  `{"type":"ping"}` keeps the socket alive.
- Streaming sample rates 8000/16000/22050/24000 (string enum); default 22050.
- Codecs listed: linear16, mulaw, alaw, opus, flac, aac, wav, mp3. **Flag:** the same
  schema says "currently supports MP3 only". We request linear16@16k; `decode_audio()`
  accepts raw PCM or wav-wrapped chunks (header stripped, rate read and resampled with
  `Resampler`), and raises `ProviderError` on mp3 (would need a decoder dependency).
- Models: `bulbul:v3` (API default; 30+ speakers, all 11 languages incl. ml/od),
  `bulbul:v4-flash` (222 persona ids `name_lang_style`; no Malayalam/Odia yet, and Tamil has
  only male personas). The model is a query param, so the warm pool is keyed by model.
- Speakers are lowercase. Sarvam's voices page names **shubh (male)** and **ishita
  (female)** as top-rated across every language.
- Voice mapping (`vendor_voice`): `bulbul:<lang>-male-1` → v3 `shubh`,
  `bulbul:<lang>-female-1` → v3 `ishita`, `bulbul:<name>` → v3 speaker,
  `bulbul:<name_lang_style>` → v4-flash persona (e.g. `bulbul:chetan_kn_conversation`,
  `bulbul:chaitra_kn_conversation`, `bulbul:nilesh_mr_conversation`, `bulbul:ishita_mr_conversational`).
- Abandoning a synth mid-stream (barge-in) closes that socket rather than reusing it.

## ElevenLabs Flash v2.5

Source: [stream speech (HTTP)](https://elevenlabs.io/docs/api-reference/text-to-speech/stream),
[stream-input websocket](https://elevenlabs.io/docs/api-reference/text-to-speech/v-1-text-to-speech-voice-id-stream-input).

- `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream?output_format=pcm_16000`,
  header `xi-api-key`, body `{"text", "model_id": "eleven_flash_v2_5", "language_code": "hi"}`.
  Response body is raw little-endian PCM16 mono at 16 kHz, streamed. We re-align odd-length
  network chunks to whole samples.
- Why HTTP, not the websocket: the docs say the websocket buffers text
  (`chunk_length_schedule` default [120,160,250,290] chars) and can be slower when the full
  text is known up front, which is our case (one sentence per utterance).
- **India residency hosts exist**: `https://api.in.residency.elevenlabs.io` (and `wss://`).
  Pass `base_url=` to try it from Mumbai; likely needs an enterprise/residency plan. Relevant
  to PQ6 (data residency) and the 150-250 ms RTT concern.
- `enable_logging=false` (zero retention) is enterprise-only.
- **Flag: voice ids.** Hindi voice-library ids were taken from a third-party catalogue of
  ElevenLabs voices ([json2video Hindi list](https://json2video.com/ai-voices/languages/hindi/india/?page=4)),
  not the official site: `jjlrvsvJ1M0ZMRNgZuLO` "Karan" (male, friendly/polite) and
  `KYiVPerWcenyBTIvWbfY` "Sia" (female, friendly/helpful). Library voices may need adding to
  the account ("My Voices") before API use. Verify with `GET /v1/voices` and the listening test.

## Deepgram Nova (mock-tested only)

Source: [listen streaming reference](https://developers.deepgram.com/reference/speech-to-text/listen-streaming).

- URL: `wss://api.deepgram.com/v1/listen?model=nova-3&language=hi&encoding=linear16&sample_rate=16000&channels=1&interim_results=true&punctuate=true&vad_events=true&utterance_end_ms=1000`,
  header `Authorization: Token <key>`.
- Audio as binary frames. Control: `{"type":"Finalize"}`, `{"type":"KeepAlive"}`,
  `{"type":"CloseStream"}`. Results: `{"type":"Results","is_final","speech_final",
  "from_finalize","channel":{"alternatives":[{"transcript"}]}}`; also `UtteranceEnd`,
  `SpeechStarted`, `Metadata`.
- `end()` sends Finalize and returns the joined `is_final` transcripts once a result with
  `from_finalize=true` arrives. Interim results feed `partials()`. `speech_final` or
  `UtteranceEnd` sets `eou_signal`.
- **Flag: language codes.** The schema types `language` as free-form. We send `hi` for
  hi-IN and `en-IN` for en-IN; pass `language="multi"` for Hinglish code-switching.
  Verify when a key exists.

---

## Open questions (partial answers from the docs)

- **Q2, Sarvam EOU event.** Yes, on the realtime endpoint in `endpointing=vad` mode:
  `vad.speech_end` fires after `silence_duration_ms` (default 500 ms, tunable per
  connection or live via `config.update`), and `transcript.final` follows per utterance.
  Still to measure on the bench: how often it fires before Silero's 550 ms and how often it
  splits sentences. Our client exposes it as `eou_signal` (vad mode); default stays manual.
- **Q3, TTS per language.** Not answerable from docs. Bulbul covers kn/ta/mr natively
  (v3 all; v4-flash has kn/mr personas, ta male only). ElevenLabs offers an India residency
  host, which could change the RTT side of the comparison. Run the listening test.
- **Q4, STT socket lifetime.** Docs: one realtime socket carries many utterances; idle
  sockets close with 1008 unless pinged (`{"event":"ping"}`); timeout length not stated.
  Our default: warm pool + ping every 10 s + reconnect-and-replay on failure. Still to
  test: the 5 s / 30 s / 70 s silence experiment from the plan, with and without ping.
