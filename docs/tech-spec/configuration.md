# Configuration

This document defines how dubhash is configured. `config/pairs.yaml` holds the per-language-pair tuning (STT, TTS engine, voices, EOU and barge-in thresholds, duck level). `config/settings.py` holds environment variables (vendor credentials, database URLs, hostnames, global timing constants). The `pairs.yaml` shape and the hi-kn values come from tech spec §4 and are normative. The env var names and the global-constant knobs are **proposed**, and P3 and Rohan should confirm them on Day 1. The rule from the spec applies: TTS engine choice per language "is a config table, not an architecture decision", so changing a vendor for a language must never need a code change.

## `config/pairs.yaml`

### The hi-kn example (from the spec)

```yaml
# config/pairs.yaml
hi-kn:
  a: { lang: hi-IN, stt: sarvam, tts: eleven,  voice_m: "eleven:hi-male-1",  voice_f: "eleven:hi-female-1" }
  b: { lang: kn-IN, stt: sarvam, tts: bulbul,  voice_m: "bulbul:kn-male-1",  voice_f: "bulbul:kn-female-1" }
  eou_ms: 550
  backchannel_max_ms: 600
  barge_in_min_ms: 1200
  duck_db: -14
hi-ta: { ... }
mr-hi: { ... }
```

### Field reference

Per pair (top-level key `{x}-{y}`, two-letter codes):

| Field | Type | hi-kn value | Meaning | Used by |
|---|---|---|---|---|
| `a`, `b` | side object | — | one block per side. `a` is the side whose language is the first code in the key | session |
| `eou_ms` | int, ms | `550` | silence after the last speech frame before end-of-utterance fires (with utterance ≥ 300 ms). The single biggest latency lever. 550 is a starting guess, tuned per language on the bench and on PSTN (false-EOU rate vs latency) | `interp/endpoint.py` |
| `backchannel_max_ms` | int, ms | `600` | utterances shorter than this with ≤ 2 words are tagged backchannel: not translated, never change floor state, never clear audio | endpoint, floor controller |
| `barge_in_min_ms` | int, ms | `1200` | listener speech during playback longer than this is a real interruption: `clear()` the listener leg, mark the playing translation clipped, switch floor | floor controller |
| `duck_db` | int, dB | `-14` | level of the speaker's original audio mixed under the TTS in the listener's stream | `audio/mixer.py` |

Per side (`a` / `b`):

| Field | Type | Values | Meaning |
|---|---|---|---|
| `lang` | BCP-47 | `hi-IN`, `kn-IN`, `ta-IN`, `mr-IN`, … | the language **this side speaks and hears**. Used as the STT language code for this side's speech and as the MT source/target code (e.g. Sarvam translate `source=hi-IN target=kn-IN`) |
| `stt` | enum | `sarvam` \| `deepgram` \| `fake` | STT engine for **this side's speech**. Sarvam streaming (Saarika/Saaras) for Indic. Deepgram Nova for English and Hinglish fallback. Selectable per leg |
| `tts` | enum | `bulbul` \| `eleven` \| `fake` | TTS engine used to synthesize speech **in this side's language**, i.e. what this side hears |
| `voice_m` | string `engine:voice-id` | `"bulbul:kn-male-1"` | voice used when the **other side's** speaker is detected male |
| `voice_f` | string | `"bulbul:kn-female-1"` | voice used when the other side's speaker is detected female |

How to read the hi-kn example: A (Hindi) speaks. Sarvam STT transcribes `hi-IN`, Sarvam Mayura translates to `kn-IN`, and B's TTS block applies (Bulbul, `kn-male-1` or `kn-female-1` chosen by A's detected gender). B hears it. The reverse direction uses ElevenLabs Flash v2.5 with a Hindi voice.

The voice IDs above (`hi-male-1` etc.) are placeholders from the spec. The real vendor voice IDs are TBD after the listening test. See [open questions](../plan/open-questions.md).

### TTS engine by target language (resolved)

| Target language | `tts` | Why |
|---|---|---|
| `hi`, `en` | `eleven` (ElevenLabs Flash v2.5) | covered natively. ElevenLabs is also the v2 voice-cloning engine |
| `kn`, `ta`, `mr`, `te`, `ml`, `bn`, `gu`, `pa`, `od` | `bulbul` (Sarvam Bulbul) | ElevenLabs Flash does not cover every scheduled language natively. Bulbul does, with similar first-byte, and it is hosted in India (ElevenLabs RTT from Mumbai is 150 to 250 ms) |

This is the default. Open question 3 (Bulbul vs ElevenLabs Flash for kn, ta, mr on naturalness and first-byte from Mumbai, a 30-minute listening test) can change a row, and the result goes in `pairs.yaml`.

### Voice selection (v1)

v1 uses a **fixed natural voice per target language and detected speaker gender**. Gender comes from a pitch heuristic on the first 2 s of the speaker's audio. The listener hears a gender-matched natural voice, **not** the speaker's own voice. Voice cloning is v2 and needs explicit consent. See [security and privacy](security-privacy.md#voice-and-cloning). Whether the platform API should pass gender instead is open question 5. Until gender is detected (first utterance under 2 s), the default is TBD, see [open questions](../plan/open-questions.md).

### Pairs to ship

| Pair | Status | Notes |
|---|---|---|
| `hi-kn` | spec example above | primary demo pair (CP1 pair) |
| `hi-ta` | `{ ... }` in spec, values TBD | `ta-IN`, Bulbul for ta. Tamil speakers pause differently, so tune `eou_ms` separately |
| `mr-hi` | `{ ... }` in spec, values TBD | `mr-IN`, Bulbul for mr, ElevenLabs for hi. Used for the ward-mode story |

Proposed skeletons, with values to fill from the bench:

```yaml
hi-ta:
  a: { lang: hi-IN, stt: sarvam, tts: eleven, voice_m: "eleven:hi-male-1", voice_f: "eleven:hi-female-1" }
  b: { lang: ta-IN, stt: sarvam, tts: bulbul, voice_m: "bulbul:ta-male-1", voice_f: "bulbul:ta-female-1" }
  eou_ms: 550            # TBD after per-language bench
  backchannel_max_ms: 600
  barge_in_min_ms: 1200
  duck_db: -14
mr-hi:
  a: { lang: mr-IN, stt: sarvam, tts: bulbul, voice_m: "bulbul:mr-male-1", voice_f: "bulbul:mr-female-1" }
  b: { lang: hi-IN, stt: sarvam, tts: eleven, voice_m: "eleven:hi-male-1", voice_f: "eleven:hi-female-1" }
  eou_ms: 550            # TBD
  backchannel_max_ms: 600
  barge_in_min_ms: 1200
  duck_db: -14
```

### Loading and validation (proposed)

- Load once at startup into frozen pydantic models. Reject unknown keys and unknown engines, and fail fast.
- Pair lookup for `POST /v1/calls` with `a.lang=kn-IN, b.lang=hi-IN`: try `kn-hi`, then `hi-kn` with sides swapped. Unknown pair → `422 unsupported_pair` ([API](api.md#errors-proposed)). The v1 non-goal is any language whose STT we have not measured on PSTN, so only measured pairs go in the file.
- Every side's `voice_m`/`voice_f` prefix must match its `tts` engine (`bulbul:` with `bulbul`).
- Hot reload is not needed in v1. Restart the container.

## `config/settings.py` (environment variables)

Read with pydantic-settings from the environment (`.env` in dev, Compose `env_file` on the VM). All names are **proposed**. Secrets never go in git. See [deployment](../engineering/deployment.md) and [dev setup](../engineering/dev-setup.md).

### Core

| Var | Example | Required | Notes |
|---|---|---|---|
| `DUBHASH_ENV` | `dev` \| `prod` \| `test` | yes | `test` forces fakes |
| `PUBLIC_BASE_URL` | `https://api.dubhash.in` | yes | platform API, provider webhook callback base, Passthru URL |
| `EDGE_WSS_BASE` | `wss://edge.dubhash.in` | yes | base for `/ws/leg?call=…&side=…` given to Exotel and Plivo |
| `PAIRS_FILE` | `config/pairs.yaml` | no | |
| `DEFAULT_PROVIDER` | `exotel` | no | used when `POST /v1/calls` omits `provider` |
| `LOG_LEVEL` | `INFO` | no | |
| `MAX_CONCURRENT_CALLS` | TBD | no | per node, returns `429 capacity` above it. Set from CPU profiling |

### Storage

| Var | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://dubhash:…@postgres:5432/dubhash` | Compose service `postgres` |
| `REDIS_URL` | `redis://redis:6379/0` | call registry only |
| `S3_BUCKET` | `dubhash-recordings` | opt-in recordings only, ap-south-1 |
| `AWS_REGION` | `ap-south-1` | India-region hosting |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | — | or an instance role (preferred) |

### Telephony

| Var | Notes |
|---|---|
| `EXOTEL_ACCOUNT_SID` | `{sid}` in `POST /v1/Accounts/{sid}/Calls/connect` |
| `EXOTEL_API_KEY` / `EXOTEL_API_TOKEN` | REST credentials. The exact auth scheme is per Exotel docs |
| `EXOTEL_API_HOST` | Exotel REST host/subdomain (region-specific). TBD |
| `EXOTEL_EXOPHONE` | `CallerId` for outbound legs |
| `EXOTEL_FLOW_URL` | applet flow containing the Voicebot applet (`Url=<applet-flow-url>`) |
| `EXOTEL_DIALIN_NUMBER` | public consumer number (Gather → Passthru flow) |
| `EXOTEL_WS_BASIC_USER` / `EXOTEL_WS_BASIC_PASS` | Basic auth expected on inbound `/ws/leg` from Exotel |
| `EXOTEL_ALLOWED_IPS` | comma-separated CIDRs if using an IP allowlist instead of, or in addition to, Basic auth |
| `PLIVO_AUTH_ID` / `PLIVO_AUTH_TOKEN` | `{id}` in `POST /v1/Account/{id}/Call/` |
| `PLIVO_NUMBER` | `from` for Plivo legs |
| `PLIVO_STREAM_CONTENT_TYPE` | `audio/x-l16;rate=16000` (try first) or `audio/x-mulaw;rate=8000`. Depends on open question 6 |

### Vendors

| Var | Used by |
|---|---|
| `SARVAM_API_KEY` | Sarvam STT (Saarika/Saaras streaming), Mayura translate, Bulbul TTS, language id (dial-in) |
| `ELEVENLABS_API_KEY` | ElevenLabs Flash v2.5 TTS (hi, en) |
| `DEEPGRAM_API_KEY` | Deepgram Nova streaming STT (English/Hinglish fallback) |

### Platform API and console

| Var | Notes |
|---|---|
| `PLATFORM_API_KEYS` | dev only: `platform_id:key` pairs. Prod stores hashed keys in Postgres |
| `WEBHOOK_SIGNING_SECRET` | default HMAC secret for outbound platform webhooks (per-platform override later) |
| `CONSOLE_PASSWORD` | console basic auth (or set in Caddy) |

### Fakes and dev switches

Every component is behind an interface with a fake, and the pipeline must run end to end with `FakeLeg` (wav in, wav out) before any telephony credit exists.

| Var | Values | Effect |
|---|---|---|
| `STT_IMPL` | `real` \| `fake` | swap to `FakeSTT` |
| `MT_IMPL` | `real` \| `fake` | `FakeMT` |
| `TTS_IMPL` | `real` \| `fake` | `FakeTTS` |
| `ENABLE_WEBRTC_LEG` | `true` in dev | exposes the loopback signalling page |
| `BENCH_REALTIME` | `true` \| `false` | FakeLeg plays wav at real time or faster for tests |

### Global constants (defaults from the spec)

These rarely change. They live in `settings.py` with env overrides (proposed names) and must not be scattered as literals.

| Constant | Default | Source rule |
|---|---|---|
| `FRAME_MS` / frame bytes | 20 ms / 640 bytes | 16 kHz PCM16 mono internally |
| `INTERNAL_SAMPLE_RATE` | 16000 | resample 8k ⇄ 16k with soxr if the leg is 8k |
| `VAD_THRESHOLD` | 0.5 | Silero v5 speech prob > 0.5 = speech (512-sample windows ≈ 32 ms, hop 20 ms) |
| `MIN_UTTERANCE_MS` | 300 | EOU needs utterance ≥ 300 ms |
| `BACKCHANNEL_MAX_WORDS` | 2 | with `backchannel_max_ms` |
| `OUTBOUND_CHUNK_MS` | 100 | 3,200 bytes at 16 kHz, multiple of 320. Stay at 100 ms |
| `EARCON_MS` | 120 | earcon before TTS at 0 dB |
| `NO_ANSWER_TIMEOUT_S` | 25 | other leg hears "doosri taraf se jawab nahi aaya" and is hung up |
| `FORCE_PLAY_SILENCE_S` | 4 | both silent with queue non-empty → force play |
| `ARE_YOU_THERE_SILENCE_S` | 10 | "aap line par hain?" in both languages |
| `GENDER_DETECT_WINDOW_S` | 2 | pitch heuristic on first 2 s |
| `DTMF_REPLAY` / default `escape_dtmf` | `1` / `0` | replay last translation / bypass |
| `MT_CACHE` | on | exact-text cache per pair (saves about 200 ms of MT) |

## Example `.env` (dev)

```dotenv
DUBHASH_ENV=dev
PUBLIC_BASE_URL=https://localhost:8443
EDGE_WSS_BASE=wss://localhost:8443
DATABASE_URL=postgresql+asyncpg://dubhash:dubhash@localhost:5432/dubhash
REDIS_URL=redis://localhost:6379/0
SARVAM_API_KEY=
ELEVENLABS_API_KEY=
DEEPGRAM_API_KEY=
STT_IMPL=fake
MT_IMPL=fake
TTS_IMPL=fake
ENABLE_WEBRTC_LEG=true
```

## Related

- [Interpreter pipeline](interpreter-pipeline.md)
- [Floor controller](floor-controller.md)
- [Audio](audio.md)
- [Leg transports](leg-transports.md)
- [API](api.md)
- [Security and privacy](security-privacy.md)
- [Dev setup](../engineering/dev-setup.md)
- [Deployment](../engineering/deployment.md)
- [Open questions](../plan/open-questions.md)
