# Dev setup

This document gets a new engineer from a clean laptop to a running dubhash: the offline tests on fakes, the API with the console, the live vendor smoke test, and the eval tools, without spending any telephony minutes. Every command here exists in the repo today. If you change a command, change this doc in the same PR; `tests/tools/test_repo_files.py` checks that its links resolve and that the main commands are still mentioned.

## Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.12 (uv installs it if missing) | asyncio app, every vendor ships Python first |
| [uv](https://docs.astral.sh/uv/) | recent | dependency install and running everything (`uv run ...`) |
| make | any | the shortcuts below; each target is a one-line `uv run` you can type yourself |
| Docker + Docker Compose | recent, optional | Postgres and Redis; the app and tests run without them |
| Chrome or Firefox | recent | WebRTC loopback page (mic access on `localhost`) |
| sox (optional) | any | inspecting wavs; recording corpus prompts without `sounddevice` |

## 1. Install

```bash
git clone <repo-url> dubhash && cd dubhash
uv sync          # or: make install. Creates .venv from pyproject.toml + uv.lock, dev group included
cp .env.example .env
```

`make help` lists every target.

| Target | Runs |
|---|---|
| `make install` | `uv sync` |
| `make test` | `uv run pytest -q` (live vendor tests deselected via `addopts = "-m 'not live'"`) |
| `make test-live` | `uv run pytest -m live -s` (each test skips when its key is missing) |
| `make lint` | `uv run ruff check .` and `uv run ruff format --check .` (what CI runs) |
| `make fmt` | `ruff check --fix` and `ruff format` |
| `make dev` | `uv run uvicorn dubhash.api.app:app --host 127.0.0.1 --port 8000 --reload --ws-per-message-deflate false` |
| `make up` / `make down` | `docker compose up -d postgres redis` / `docker compose down` |
| `make bench` | `uv run python -m tools.bench $(BENCH_ARGS)` |
| `make smoke` | `uv run python tools/smoke.py $(SMOKE_ARGS)` |
| `make fixtures` | `uv run python tools/gen_fixtures.py` |
| `make label` | `uv run python tools/label.py $(TURNS) --rater "$(RATER)"` |

Override variables on the command line: `make dev PORT=8080 HOST=0.0.0.0`, `make smoke SMOKE_ARGS="--only mt,tts"`.

## 2. Environment variables

`dubhash/config/settings.py` reads `.env` (pydantic-settings). The full list with examples is in `.env.example` and in [configuration](../tech-spec/configuration.md#configsettingspy-environment-variables). Missing vendor keys never crash startup: the provider just can't be selected and the fakes keep working.

| Variable | Needed for | Notes |
|---|---|---|
| `DUBHASH_ENV` | all | `dev` \| `test` \| `prod`. `test` forces fakes and turns console auth off (CI sets it) |
| `STT_IMPL`, `MT_IMPL`, `TTS_IMPL` | provider registry | `fake` (default) \| `real`. Set all three to `real` to use the vendors in the app |
| `SARVAM_API_KEY` | Sarvam STT, Mayura translate, Bulbul TTS | |
| `ELEVENLABS_API_KEY` | ElevenLabs Flash v2.5 (hi, en targets) | |
| `DEEPGRAM_API_KEY` | Deepgram Nova fallback | optional; expected to be empty for now |
| `CONSOLE_PASSWORD` | `/console`, `/ward` | HTTP Basic, any username |
| `DATABASE_URL`, `REDIS_URL` | turn writer, registry | the app logs `store.unavailable` and carries on if the DB does not answer within 3 s |
| `PAIRS_FILE` | interpreters | leave unset to use the packaged `dubhash/config/pairs.yaml` (see troubleshooting) |
| `EXOTEL_*`, `PLIVO_*` | telephony legs | fill in when access lands |

Rules: `.env` is gitignored, only `.env.example` is committed. Share keys through a password manager, never chat or git.

## 3. Vendor keys

| Vendor | Used for | How we get access | Owner |
|---|---|---|---|
| Sarvam | STT, translate, Bulbul TTS | buildathon sponsor credits | P2 |
| ElevenLabs | Flash v2.5 TTS for hi/en | buildathon sponsor | P3 |
| Deepgram | Nova streaming STT fallback | buildathon sponsor (not yet) | P2 |
| Exotel | voicebot applet, two-leg calls | sponsor ask via the Bolna Slack; access expected in a few days. Ask Exotel to IP-allowlist the VM | Rohan / P3 |
| Plivo | audio streaming fallback | self-serve, needs Udyam registration | P3 |

## 4. Tests

```bash
make test                                  # all offline tests: unit, contract, pipeline on fakes
uv run pytest tests/test_floor.py -q       # one file
make lint                                  # ruff check + format check
make test-live                             # real vendors, needs keys in .env
```

Offline tests need no network, keys, Postgres or Redis. Live tests are marked `@pytest.mark.live`; they never run in CI. CI (`.github/workflows/ci.yml`) runs `uv sync --locked`, `ruff check`, `ruff format --check`, `tools/gen_fixtures.py --check` and `pytest` on every push to `main` and every PR.

Test fixtures: `tests/fixtures/synth_*.wav` are synthetic speech-like bursts (16 kHz, 8 kHz, 8 kHz with a noise floor) made by `make fixtures`; `synth_manifest.json` lists each speech/silence segment's start and end ms for VAD and endpointing tests. The generator is deterministic, so regenerating is a no-op unless you change `SPECS`.

## 5. Live vendor smoke test

With `SARVAM_API_KEY` and/or `ELEVENLABS_API_KEY` in `.env`:

```bash
make smoke                                 # or: uv run python tools/smoke.py
uv run python tools/smoke.py --only mt     # just translate
uv run python tools/smoke.py --eou         # + open question 2: when does Sarvam's vad.speech_end fire
uv run python tools/smoke.py --idle 5,30,70   # + open question 4: does an idle STT socket survive, with and without ping
uv run python tools/smoke.py --json out/smoke/results.json
```

It runs Mayura hi→kn on three sentences (and notes if the direct pair was rejected and an English pivot was used), Bulbul kn and ElevenLabs hi TTS twice each (cold, then warm) with time to first byte and total, then Sarvam STT on every TTS wav (final latency after `speech_end`, transcript, similarity to the spoken text). Wavs go to `out/smoke/`; listen to them for open question 3. Vendors without a key are skipped with a message (Deepgram is expected to be). The exit code is 0 when nothing failed, 1 on any failure, 2 when nothing ran.

## 6. Run the app

```bash
make dev                                   # http://127.0.0.1:8000, one worker, reload
```

Exactly one worker, always: both legs of a call must land in the same process. `make dev` never passes `--workers`.

| URL | What |
|---|---|
| `/healthz` | liveness |
| `/console` | live console: active calls, turns, state badge, per-stage latency over SSE |
| `/ward` | ward mode page (one phone, two people) |
| `/loopback/new` | two browser tabs as two legs (`WebRtcLeg`): open each tab URL, allow the mic, talk. |

Loopback latency numbers are for correctness only. Deck numbers come from real PSTN calls. See [latency engineering](latency-engineering.md).

### Postgres and Redis (optional)

```bash
make up            # docker compose up -d postgres redis
make down
```

The compose file is the VM's: it does not publish the Postgres and Redis ports to the host. To reach them from `make dev` on your laptop, add a local, uncommitted `docker-compose.override.yml`:

```yaml
services:
  postgres: { ports: ["5432:5432"] }
  redis: { ports: ["6379:6379"] }
```

Without it the app starts anyway, logs `store.unavailable`, and runs without a turn log.

### Live loopback

`tools/live_loopback.py` places one scripted call through the real app with no telephony and no browser. It starts the app in-process (uvicorn on a free port), creates a loopback call (`POST /loopback/calls` with `record: true`, so the turn log keeps text), and connects two websocket clients that behave like `static/loopback.html`: they send 20 ms PCM16 16 kHz frames in real time, play what they receive in real time into a recording, and ack each mark when playout reaches it.

```bash
STT_IMPL=real MT_IMPL=real TTS_IMPL=real uv run python tools/live_loopback.py             # hi-kn (pivot)
STT_IMPL=real MT_IMPL=real TTS_IMPL=real uv run python tools/live_loopback.py --pair hi-kn-direct
uv run python tools/live_loopback.py --fake            # fake providers, synthetic audio, no network
```

Set the three `*_IMPL` switches in the environment: the tool does not trust the `.env` values for them. If `.env` has no valid `DUBHASH_ENV`, prefix `DUBHASH_ENV=dev` too. Flags: `--n` (script lines, default 6), `--pause-ms`, `--log INFO`, `--out`.

The script is example call 1 from [problem and users](../product/problem-and-users.md): a rider speaking Hindi on leg A and a customer speaking Kannada on leg B, 6 lines. The utterances are synthesised once with Bulbul (`shubh` for the rider, `ishita` for the customer) and cached in `out/live/utt_*.wav`. The Kannada lines are our own and still need a native speaker's review. The tool waits for both opening lines to finish playing, then plays one line at a time, and waits for the translation to finish before the next line.

For each turn it prints:

- **earcon** and **heard**: ms from the end of the speaker's last voiced frame to the earcon onset, and to the first non-silent frame of the translation after the earcon. This is what the listener experiences, on the client's 20 ms tick clock. No browser jitter buffer or device latency is modelled, so these are lower bounds for a real tab.
- **eou, stt, mt, tts, queue, srv_fa**: the server turn log's stages (`GET /v1/calls/{id}/turns`), as differences of its `t_*` fields. `srv_fa` is `t_play_start - t_speech_end`.
- **src/tgt**: the turn log's transcripts.

It also prints p50/p95 for every column and when each caller first heard the opening line. It writes `out/live/<call>_a.wav` and `<call>_b.wav` (what each leg heard) and `out/live/<call>.json` (rows, raw turns, `play_to_ear_ms` and `vad_end_lag_ms` per turn, mark acks, clears). Keep live runs small: each call is about 6 STT, 6 MT and 8 TTS requests. `tests/tools/test_live_loopback.py` runs the same core loop offline with fakes. `tests/tools/test_live_loopback_live.py` runs two live lines under `-m live`.

### Diagnosing a test call (recording and timeline)

In `DUBHASH_ENV=dev` every call is recorded (`CALL_RECORDING`, [configuration](../tech-spec/configuration.md#fakes-and-dev-switches)): the QR stage demo (`/join`), loopback tabs, live_loopback runs and real legs alike. Each call leaves `out/calls/<YYYY-MM-DD>/<call_id>/` with `a_in/b_in.wav` (each mic), `a_out/b_out.wav` (what each phone was sent, where it played), `events.jsonl` (every event, with wall-clock and call-relative ms), `client.jsonl` (the phone page's telemetry and ping/pong clock offset) and `summary.json`. The app log is also written as JSON lines to `logs/dubhash.log` (rotating, kept across restarts), each line with its `call_id`.

```bash
uv run python tools/call_report.py c_ab12cd34ef          # per-turn timeline, warnings, where the time went
uv run python tools/call_report.py c_ab12cd34ef --json
grep c_ab12cd34ef logs/dubhash.log                         # the server log for that call
```

The call id is on the host page (`/join/<call_id>`) and in the log's `recorder.start` line. Import the four wavs into Audacity together: they share one timeline from call start. The report flags the patterns behind the usual complaints: `cross_talk` (one phone's mic made a turn while the other person was talking: the phones are close enough to hear each other, so the listener gets the original voice and a second translation), `echo` (a mic picked up the translation playing on its own speaker), `queue_wait` / `opening_hold` (translation ready but held), `long_utterance` (EOU never fired: > 8 s), `early_final_restart` and `wrong_language`. Recordings hold both callers' voices: dev and test only, with the callers' consent, never committed.

## 7. Offline pipeline bench

`tools/bench.py` (owned by the session work) plays wavs through two FakeLegs with the real controller and mixer and writes per-stage p50/p95 and a turn log. Run it as `make bench BENCH_ARGS="..."`; its flags are documented in the tool itself. See [testing strategy](testing-strategy.md).

## 8. Eval tools

```bash
# meaning rating: local page on :8765, writes ratings.csv, resumes where you stopped
uv run python tools/label.py out/turns.json --rater asha --ratings out/ratings-hi-kn.csv
uv run python tools/label.py out/turns.json --ratings out/ratings-hi-kn.csv --summary

# record the bench corpus (30 utterances per language) at 8 kHz from the mic
uv run --with sounddevice python tools/record_fixtures.py prompts/hi.txt --lang hi --out out/corpus
```

`tools/label.py` shows one src/tgt pair at a time with kept / partly / lost buttons (keys `1`/`2`/`3`), an entities flag (`e`) and a note (`n`). Its summary gives % kept per pair and says "preliminary, n=…" until a pair has 300 rated turns. The turn-log JSON schema it reads is documented at the top of the file. See [evaluation and metrics](evaluation-and-metrics.md).

`tools/record_fixtures.py` takes a `.txt` (one prompt per line) or `.csv` (`id,lang,text`) prompt list and writes `<out>/<lang>/<id>.wav` plus `manifest.csv` (`id, lang, reference_text, path`). `sounddevice` is optional and not a project dependency: `uv run --with sounddevice` pulls it for one run (it needs PortAudio: `brew install portaudio` or `apt install libportaudio2`). Without it the tool prints a `sox rec` command per prompt; record those, then run with `--manifest-only`. The corpus goes to `out/` (gitignored) until we decide git vs a bucket.

## 9. Real phones (once Exotel access lands)

1. Get a public TLS endpoint. Use the VM (see [deployment](deployment.md)). A local tunnel works for a first smoke test only, because its RTT pollutes latency.
2. Create a call with `POST /v1/calls` (see [api](../tech-spec/api.md)) with both numbers, languages and `"provider": "exotel"`.
3. Both phones ring. Interpretation starts once both websockets have sent `start`.
4. Check the `start` message's `media_format` in the logs (`leg.start`, `leg.resample`). Confirm Exotel actually sends 16 kHz. This is open question 1 and the first thing to test.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| One leg connects, the other never does | more than one uvicorn worker, or the two legs hit different processes |
| Robotic or chipmunk audio | sample-rate mismatch (8k vs 16k); check `media_format` and the `leg.resample` log |
| STT stops after a quiet stretch | Sarvam idle timeout (close code 1008); the client pings every 10 s. Measure with `tools/smoke.py --idle` |
| `pairs.yaml` not found | `.env.example` sets `PAIRS_FILE=config/pairs.yaml`, which is relative to the working directory; the file lives at `dubhash/config/pairs.yaml`. Delete the line from `.env` or point it there |
| `/console` asks for a password | HTTP Basic: any username, password `CONSOLE_PASSWORD` from `.env` |
| `make smoke` exits 2 | no `SARVAM_API_KEY` or `ELEVENLABS_API_KEY` in `.env`, so nothing ran |

## Related

- [Repo structure](repo-structure.md)
- [Testing strategy](testing-strategy.md)
- [Evaluation and metrics](evaluation-and-metrics.md)
- [Deployment](deployment.md)
- [Configuration](../tech-spec/configuration.md)
- [Leg transports](../tech-spec/leg-transports.md)
- [Open questions](../plan/open-questions.md)
