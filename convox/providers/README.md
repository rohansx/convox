# providers

Vendor clients behind the Protocols in `convox/interp/contracts.py`. Every client takes an
injectable `base_url` so unit tests can point it at a local mock (`httpx.MockTransport` or
a `websockets.serve` server on 127.0.0.1). All TTS output is PCM16 mono 16 kHz.

| Module | Class (`name`) | Protocol | Transport |
|---|---|---|---|
| `sarvam_stt.py` | `SarvamSTT` (`sarvam`) → `SarvamSTTStream` | STT / STTStream | websocket, warm pool |
| `sarvam_stt_translate.py` | `SarvamSTTTranslate` (`sarvam-translate`) | STT / STTStream | as `sarvam`, plus `mode=translate`: finals in English, partials in the source language |
| `deepgram_stt.py` | `DeepgramSTT` (`deepgram`) → `DeepgramSTTStream` | STT / STTStream | websocket, warm pool (mock-tested only) |
| `sarvam_mt.py` | `SarvamMT` (`sarvam`) | MT | REST, shared `httpx.AsyncClient` |
| `sarvam_tts.py` | `BulbulTTS` (`bulbul`) | TTS | websocket, warm pool |
| `eleven_tts.py` | `ElevenTTS` (`eleven`) | TTS | HTTP streaming |
| `sarvam_lid.py` | `SarvamLID` (`sarvam`) | LanguageID (`interp/langid.py`) | REST STT with `language_code=unknown` (+ optional text-lid), shared `httpx.AsyncClient` |
| `registry.py` | `build_stt/build_mt/build_tts/build_lid(name, settings)`, `ProviderError`, `ProviderUnavailable` | | |

`build_*` returns the fake (lazily imported from `providers/fakes.py`) when the name is
`fake`, the matching `*_IMPL` setting is `fake` (the default), or `CONVOX_ENV=test`.
`build_lid` follows `STT_IMPL` (language ID is a speech provider) and returns `FakeLID`.
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

## Pivot trial (L1)

Question: is Sarvam speech→English (realtime STT `mode=translate`) followed by en→kn MT
faster than hi STT followed by hi→kn MT? Measured **live** on 21 Oct with
`uv run python tools/pivot_probe.py` (and `--translate-model saaras:v4`).

**The API.** The realtime socket `wss://api.sarvam.ai/speech-to-text-realtime/ws` takes
`mode=transcribe|translate|verbatim|translit|codemix` (default `transcribe`);
`translate` = "Translation to English". Models: `saaras:v3-realtime` (default) or
`saaras:v4`. Per the guide, the task is applied to the **final only**: "Partials are always
plain transcription", so partials stream in Hindi and the one final per utterance is
English. Sources: [realtime guide](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming),
[WS reference](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe/realtime/ws).
`SarvamSTTTranslate` is `SarvamSTT` with `mode=translate` on the URL (same warm pool,
manual endpointing, reconnect-and-replay). Enable it per pair with `stt: sarvam-translate`
and `mt_src: en-IN` (pair `hi-kn-pivot` in `config/pairs.yaml`).

**Setup.** 10 short colloquial Hindi delivery/clinic sentences, synthesised by Bulbul
(`bulbul:hi-female-1`) with 300 ms of trailing silence, pushed in real time on warm
sockets. STT = `speech_end` → final. MT = Sarvam `mayura:v1` modern-colloquial on a warm
httpx client. The routes alternate which goes first. n = 10 per run, so p95 is the max.

| stage (ms) | v3-realtime p50 | p95 | v4 run p50 | p95 |
|---|---|---|---|---|
| A: STT hi (transcribe) | 287 | 318 | 283 | 317 |
| A: MT hi→kn | 1357 | 1585 | 1385 | 1606 |
| **A: STT + MT** | **1630** | **1872** | **1667** | **1882** |
| B: STT hi→en (translate) | 277 | 327 | 284 | 291 |
| B: MT en→kn | 716 | 780 | 705 | 762 |
| **B: STT + MT** | **962** | **1107** | **988** | **1031** |
| MT hi→en (on A's transcript) | 707 | 771 | 739 | 788 |
| first-audio est. A (+550 EOU +250 TTS +200 playout) | 2630 | 2872 | 2667 | 2882 |
| first-audio est. B | 1962 | 2107 | 1988 | 2031 |

(Route A's STT is `saaras:v3-realtime` in both runs; only route B's model changes.)

**Findings.**
- Translate mode costs nothing extra at the STT: its final arrives as fast as a plain
  transcript (277 vs 287 ms p50).
- Route B saves **about 670 ms at p50 and 770–850 ms at p95**, every sentence (B's slowest,
  1107 ms, is faster than A's fastest, 1478 ms).
- hi→kn ≈ hi→en + en→kn: the sums ran 1366–1529 ms against hi→kn at 1228–1606 ms. This
  supports the guess that Sarvam pivots through English inside hi→kn. Route B does the
  first hop inside the STT for free.
- B alone gets the estimated p95 to about 2.0–2.1 s and p50 to about 2.0 s: at the p95
  target, but not the 1.5 s p50 target. The EOU wait (550 ms) is now the largest fixed cost.

**Quality (no native Kannada rating yet; flags taken from the English intermediate).**
Each sentence lists names and numbers a faithful translation must keep (`keeps` in the
probe). Route B's English kept all of them in both runs: Sharma, Mehta, Priya, 302, 450,
6 PM, 10 AM, five minutes, OTP four digits. Things that are visible:
- Route B was **more** faithful than A on 3 of 10. #5: A's Hindi STT misheard "चार अंकों
  वाला" as "चार अंक आना" and A's Kannada says the OTP "should start with four digits";
  B (from audio) says "four digit OTP". #6: A's hi→kn says "tell Priya to share *her*
  name"; B keeps "tell Priya *your* name". #9: B keeps "tomorrow morning"; A's drops "morning".
- Route B makes the Kannada more code-mixed (English loanwords in Latin script, e.g.
  "please ಕೆಳಗೆ ಬನ್ನಿ", "five minutes", "arrive ಆಗ್ತೀನಿ"). Bulbul reads Latin loanwords
  fine (L1), but a Kannada speaker should judge whether it sounds natural.
- v4 English writes "₹450" and "6:00 PM", which MT copies into the Kannada as is. Check
  that Bulbul reads "₹450" correctly before choosing v4. v3-realtime wrote "four hundred
  fifty rupees".
- Honorifics come through as English ("Sharma Ji", "Brother" for भैया) in both routes.

**Trade-off with speculative MT on partials.** In translate mode the partials are Hindi
and only the final is English, so en→kn cannot start before the final arrives. Speculative
MT would have to run hi→kn on Hindi partials (about 1.36 s), and that result is thrown
away whenever B's English final is used. The two speed-ups do not stack unless both
sockets (transcribe and translate) are opened per utterance, which doubles STT cost.

## Language identification (L8)

For `lang: "auto"`: which of `SUPPORTED_LANGS` (hi, mr, gu, kn, ta, en) a caller speaks.
Contract in `convox/interp/langid.py` (`LangGuess`, `LanguageID`, `pick`); engine
`SarvamLID` (`sarvam_lid.py`); `FakeLID` in `fakes.py`; `build_lid(name, settings)`.

**What Sarvam offers** (docs read 21 Oct, then checked live):
- **Realtime STT socket**, [WS reference](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe/realtime/ws),
  [guide](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming):
  `language_code=auto` (not `unknown`) is accepted by `saaras:v3-realtime` and `saaras:v4`
  ("adaptive language detection"). Finals carry `language` (BCP-47) and
  `language_confidence` (0..1), "present only when `language_code=auto`". Partials carry
  `language` too. **Live:** works with `mode=translate` as well, where the final is
  English and `language` is the *source* language. With a fixed `language_code` the final
  carries **no** language at all (checked live), so `detected_lang` is always None today
  and the cross-talk `lang` gate (floor-controller.md) never fires on Sarvam.
  `SarvamSTT`/`SarvamSTTTranslate.open("auto")` now set `detected_lang` (or-IN → od-IN)
  and `detected_confidence` from the final. Partials' `language` is ignored: it said
  `en-IN` while Marathi was being spoken.
- **REST STT**, [reference](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe):
  `POST /speech-to-text` (multipart `file`, `model`, `language_code`) with
  `language_code=unknown` auto-detects and returns `language_code` +
  `language_probability`. The docs tie `unknown` to `saaras:v3`; live it also works on
  `saaras:v4` and `saarika:v2.5`. Limits ([rate limits](https://docs.sarvam.ai/api/getting-started/ratelimits),
  [STT FAQ](https://docs.sarvam.ai/api/speech-to-text/faq)): 30 s of audio per request,
  60 req/min on the starter tier (we hit 429s at about 1 req/s and retried). Live latency
  on a warm client: **p50 about 195 ms, p95 about 350 ms** for 1-3 s of audio.
- **Text LID**, [reference](https://docs.sarvam.ai/api-reference/text/identify-language),
  [guide](https://docs.sarvam.ai/api/api-guides-tutorials/text-processing/language-detection):
  `POST /text-lid` `{"input"}` (max 1000 chars) → `language_code`, `script_code`; 11
  languages; **no confidence**. Live: **p50 604 ms, p95 681 ms**.
- No dedicated speech-LID endpoint exists; the REST STT with `unknown` is it.

**The engine.** `SarvamLID.identify(pcm, candidates)` sends the first `window_ms` (2000)
of the utterance as a WAV to `saaras:v3` and `saaras:v4` **in parallel** and applies
`decide()`: if both name the same candidate, that language (an agreed `en-IN` also needs
v3 ≥ 0.9); otherwise v3 alone if it clears 0.7 (`en-IN`: 0.95); else `lang=None`. Errors
answer None (`lid.error` logged), never raise. `text_check=True` adds a text-LID veto
(off: it cost 600 ms and caught nothing the thresholds did not).

**Measurement** (`uv run python tools/langid_probe.py`, 21 Oct). Corpus: 10 short, hard,
parallel sentences per language (OTP, lift, flat, parcel, plumber, "cash on delivery";
hi/mr/gu near-identical: "लिफ्ट बंद है / लिफ्ट बंद आहे / લિફ્ટ બંધ છે"), Bulbul v3 shubh
(male) and ishita (female), leading silence trimmed to 100 ms: 120 clips, 0.76-2.8 s,
p50 1.6 s, 85 under 2 s. "Long": consecutive pairs joined (60 clips, 2.2-5.2 s, p50 3.4 s).
Channels: `clean` 16 kHz and `pstn` (16k → 8k → G.711 mu-law → 8k → 16k, `audio/codec.py`
+ `audio/resample.py`). Candidates = all six. Synthetic voices: real callers will differ.

Top-1 accuracy (raw answer, no threshold), short / long clips:

| engine | channel | first 1.0 s | first 2.0 s | whole utterance |
|---|---|---|---|---|
| REST `saaras:v3` | pstn | 78% / 80% | 92.5% / 95% | 91% / 100% |
| REST `saaras:v3` | clean | 78% / 80% | 90% / 93% | 89% / 100% |
| REST `saaras:v4` | pstn | 80% / 82% | 95% / 97% | 96% / 100% |
| REST `saaras:v4` | clean | 79% / 83% | 97% / 97% | 97% / 100% |
| realtime `auto` (transcribe) | pstn | | | **17%** / 70% |
| realtime `auto` (translate) | pstn | | | **17%** / 70% |
| text-lid on the v3 transcript | pstn | | | 92% / 95% |

Decisions (accepted = a language returned; wrong = accepted and not the truth):

| rule | pstn 2.0 s short / long | clean 2.0 s short / long | wrong |
|---|---|---|---|
| v3 ≥ 0.7 (en 0.95) | 64% overall | 79% overall | 0 / 360 |
| **v3+v4 vote (default)** | **87% / 88%** | **89% / 92%** | **0 / 360** |
| v3+v4 vote, whole utterance | 86% / 95% | 88% / 98% | 0 / 360 |
| v3+v4 vote, first 1.0 s | 82% / 75% | 91% / 88% | **11 + 24 wrong** |

Latency of the vote (the slower of two parallel calls): p50 **230 ms**, p95 **430-760 ms**.

Confusion, REST `saaras:v3`, pstn, short clips (rows = truth; "other" = a language
outside the six: te, pa, ml, bn):

| truth | 1.0 s: hi mr gu kn ta en other | 2.0 s: hi mr gu kn ta en other | whole: hi mr gu kn ta en other |
|---|---|---|---|
| hi | 14 0 0 0 0 4 2 | 20 0 0 0 0 0 0 | 20 0 0 0 0 0 0 |
| mr | 0 15 0 0 0 4 1 | 0 17 0 0 0 2 1 | 0 18 0 0 0 1 1 |
| gu | 1 0 17 0 0 2 0 | 0 0 20 0 0 0 0 | 0 0 18 0 0 2 0 |
| kn | 0 0 0 16 0 4 0 | 0 0 0 19 0 1 0 | 0 0 0 18 0 2 0 |
| ta | 0 1 1 1 13 3 1 | 0 0 0 0 16 4 0 | 0 0 0 0 16 4 0 |
| en | 1 0 0 0 0 19 0 | 1 0 0 0 0 19 0 | 1 0 0 0 0 19 0 |

**mr↔hi and gu↔hi.** REST: **0** mr→hi and **0** hi→mr at every window, both channels,
both models (30 clips each). gu→hi **1** at 1.0 s only ("હા, બરાબર છે" cut to "हाँ,
बराबर", v3 0.66 pstn / 0.94 clean); 0 at 2.0 s and whole. The realtime socket did
confuse them on long clips: mr→hi 3/10 (at confidence 1.0, and it then transcribed the
Marathi as Hindi: "लिफ्ट बंद है"), gu→mr 2/10.

**What goes wrong.** Almost every error is **en-IN** on an utterance that starts with, or
is only, English loanwords: "Flat number 302", "Cash on delivery ₹400", "OTP vandcha"
(transcripts come back in Latin script). On 8 kHz audio v3 scores these 0.38-0.65; on
wideband up to 0.93, hence the en-IN thresholds. At 1.0 s confident wrong answers
(0.99) are common, so no threshold makes 1.0 s safe. v4 is the better top-1 model but
says 0.99 when wrong (kn→mr 0.987, ta→en 0.993), so it only votes. The realtime `auto`
socket answers en-IN for every short clip (120/120, often *translating* to English in
transcribe mode: "The lift is closed."), and is wrong at confidence 1.0 on long ones.
The same audio on a fresh or a warm socket gave the same answers (it is not sticky).

**Recommendation.** Decide from the first **2.0 s** of speech with the default vote
(0.7 / en 0.95 / agreed-en 0.9). If it returns None (about 1 in 8 utterances), try again
on the next utterance (on PSTN the whole long utterance was accepted 95% of the time,
never wrong); after two inconclusive tries, use the keypad menu. Never decide on 1.0 s.
The STT final's `detected_lang` alone does **not** suffice: realtime `auto` is wrong on
short turns and confidently wrong on long ones, and it also degrades the transcript; a
fixed-language socket reports nothing. Open the side's STT in its decided language.
