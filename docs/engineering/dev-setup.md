# Dev setup

This document gets a new engineer from a clean laptop to running the dubhash pipeline. It covers wav files through FakeLegs first, then two browser tabs through the loopback WebRTC harness, without spending any telephony minutes. The repo is empty right now, so **every command here is proposed**. It describes what the Day 1 scaffolding should make true. Whoever lands the scaffolding PR updates this doc so the commands match what actually works.

## Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.12 | asyncio app, every vendor ships Python first |
| uv (preferred) or pip + venv | recent | dependency install; `uv` is faster, pip works |
| Docker + Docker Compose | recent | Postgres and Redis locally; same compose file as the VM |
| Chrome or Firefox | recent | WebRtcLeg loopback harness (mic access on `localhost`) |
| sox / ffmpeg (optional) | any | converting and inspecting wav fixtures |

Hot-path dependencies: numpy, onnxruntime (Silero VAD v5), soxr (resampling). Web: FastAPI, websockets, uvicorn. Storage: SQLAlchemy, a Postgres async driver, a Redis client. Pipecat may be installed so we can borrow its service classes (Silero VAD, Exotel serializer, Sarvam/ElevenLabs clients) as libraries. Do not run its pipeline. See [conventions](conventions.md).

## 1. Clone and install (proposed)

```bash
git clone <repo-url> dubhash && cd dubhash
git switch main

# option A: uv
uv venv --python 3.12
uv sync                      # installs from pyproject.toml / uv.lock

# option B: pip
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## 2. Start Postgres and Redis (proposed)

Locally you only need the two stores. The `app` and `caddy` services are for the VM (see [deployment](deployment.md)).

```bash
docker compose up -d postgres redis
docker compose ps
```

The pipeline itself does not need either store. Nothing in the hot path touches Postgres or Redis. FakeLeg runs and unit tests should pass with both stopped. Only the turn writer, `GET /v1/calls/{id}/turns` and the call registry need them.

## 3. Environment variables (proposed)

Copy `.env.example` to `.env`. `config/settings.py` reads these. The names are a proposal. Vendor-specific credential shapes are whatever each vendor's console issues. This doc does not specify them beyond what the spec says.

The canonical list of variable names, with examples, is in [configuration](../tech-spec/configuration.md#configsettingspy-environment-variables). The ones you need locally:

| Variable | Needed for | Notes |
|---|---|---|
| `DUBHASH_ENV` | all | `dev` \| `test` \| `prod` (`test` forces fakes) |
| `DATABASE_URL` | turn writer, API reads | e.g. `postgresql+asyncpg://dubhash:dubhash@localhost:5432/dubhash` |
| `REDIS_URL` | call registry | e.g. `redis://localhost:6379/0` |
| `PUBLIC_BASE_URL` | platform API, webhook and Passthru callbacks | `https://api.dubhash.in` in prod |
| `EDGE_WSS_BASE` | outbound call setup | `wss://edge.dubhash.in` in prod; the leg URL is `{base}/ws/leg?call=…&side=…&sample-rate=16000` |
| `PAIRS_FILE` | interpreters | defaults to `config/pairs.yaml` (inside the `dubhash/` package) |
| `SARVAM_API_KEY` | Sarvam STT (Saarika/Saaras), Mayura translate, Bulbul TTS | |
| `ELEVENLABS_API_KEY` | ElevenLabs Flash v2.5 (hi, en targets) | |
| `DEEPGRAM_API_KEY` | Deepgram Nova (English/Hinglish fallback) | |
| `EXOTEL_ACCOUNT_SID`, `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN` | ExotelLeg call setup | used for `POST /v1/Accounts/{sid}/Calls/connect` |
| `EXOTEL_EXOPHONE` | ExotelLeg | the ExoPhone used as `CallerId` |
| `EXOTEL_FLOW_URL` | ExotelLeg | applet-flow URL whose Voicebot applet points at `/ws/leg` |
| `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_NUMBER` | PlivoLeg | used for `POST /v1/Account/{id}/Call/` |
| `DEFAULT_PROVIDER` | default provider | `exotel` \| `plivo`; `POST /v1/calls` can override per call |
| `STT_IMPL`, `MT_IMPL`, `TTS_IMPL` | fakes | `real` \| `fake` |

Rules:

- `.env` is gitignored. Only `.env.example` is committed, with placeholder values.
- Missing vendor keys must not crash startup. The corresponding provider just can't be selected, and `FakeSTT`/`FakeMT`/`FakeTTS` keep working.

## 4. Getting vendor keys

| Vendor | Used for | How we get access | Owner |
|---|---|---|---|
| Sarvam | STT, translate, Bulbul TTS | buildathon sponsor credits | P2 |
| ElevenLabs | Flash v2.5 TTS for hi/en | buildathon sponsor | P3 |
| Deepgram | Nova streaming STT fallback | buildathon sponsor | P2 |
| Exotel | voicebot applet, two-leg calls | sponsor ask (sent Day 1, via the Bolna Slack). Voicebot applet must be enabled on the account; ask Exotel to IP-allowlist the VM. | Rohan / P3 |
| Plivo | audio streaming fallback | self-serve, needs Udyam registration (filed Day 1) | P3 |

Share keys through a password manager, never through chat or git. Each person uses their own key where the vendor allows it, so usage is attributable. Credit amounts and quotas are TBD, see [open questions](../plan/open-questions.md).

## 5. Run the pipeline on FakeLegs (proposed)

This is the Day 2 milestone: wav in, translated wav out, latency printed per stage. See [testing strategy](testing-strategy.md).

```bash
# all fakes: no network, no keys. checks wiring, the controller and the mixer.
python -m tools.bench --pair hi-kn --stt fake --mt fake --tts fake \
  --a tests/fixtures/hi/gate_bahar.wav --out out/

# real providers, still no telephony
python -m tools.bench --pair hi-kn --a tests/fixtures/hi/ --b tests/fixtures/kn/ --out out/
```

Expected output: `out/leg_a.wav` and `out/leg_b.wav` (what each listener heard), a per-stage p50/p95 table (`t_eou`, `t_stt_final`, `t_mt`, `t_tts_ttfb`, `t_tts_done`), and a turn log for `tools/label.py`.

## 6. Run the app and the loopback WebRTC harness (proposed)

With two browser tabs as two legs (WebRtcLeg), the team can develop without a single telephony minute. This is also the fallback demo if credits fail.

```bash
# run the API with exactly ONE worker: both legs of a call must land in the same process
uvicorn dubhash.api.app:app --host 0.0.0.0 --port 8000 --workers 1 --reload

# in another shell: create a loopback call and print two tab URLs
python -m tools.loopback --pair hi-kn
#   leg a: http://localhost:8000/dev/leg?call=c_dev1&side=a
#   leg b: http://localhost:8000/dev/leg?call=c_dev1&side=b
#   console: http://localhost:8000/console
```

Open each leg URL in its own tab (or on two laptops on the same network), allow the mic, and talk. Open `/console` to watch the turns, the state badge and the stage latencies over SSE.

Loopback latency numbers are for correctness only. Deck numbers come from real PSTN calls. See [latency engineering](latency-engineering.md).

## 7. Tests (proposed)

```bash
pytest -q                          # unit + offline pipeline on fakes
pytest tests/test_floor.py -q      # floor controller invariants
ruff check . && ruff format --check .
```

## 8. Real phones (once credits exist)

1. Get a public TLS endpoint. Use the VM (see [deployment](deployment.md)). A local tunnel works for a first smoke test only, because its RTT pollutes latency.
2. `curl -X POST https://api.dubhash.in/v1/calls -H 'Authorization: Bearer <key>' -H 'Content-Type: application/json' -d '{"a":{"number":"+91…","lang":"hi-IN"},"b":{"number":"+91…","lang":"kn-IN"},"provider":"exotel","caller_id":"+91…","mode":"interpret","escape_dtmf":"0","record":false}'`
3. Both phones ring. Interpretation starts once both websockets have sent `start`.
4. Check the `start` message's `media_format` in the logs. Confirm Exotel actually sends 16 kHz. This is open question 1 and the first thing to test.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| One leg connects, the other never does | more than one uvicorn worker, or the two legs hit different processes |
| Robotic or chipmunk audio | sample-rate mismatch (8k vs 16k); check `media_format` and the resampler path |
| STT stops after a quiet stretch | Sarvam stream idle timeout; keepalive needed (open question 4) |
| Silero errors on frame size | feed 512-sample (≈32 ms) windows with a 20 ms hop, not raw 20 ms frames |

## Related

- [Repo structure](repo-structure.md)
- [Testing strategy](testing-strategy.md)
- [Deployment](deployment.md)
- [Configuration](../tech-spec/configuration.md)
- [Leg transports](../tech-spec/leg-transports.md)
- [Open questions](../plan/open-questions.md)
