# dubhash

dubhash is a live interpreter inside a phone call between two people who speak different Indian languages.
The server joins two telephony legs itself, so A hears B translated and B hears A translated, about 1.5 s after each one stops speaking.
The listener hears a gender-matched natural voice with the original speaker ducked underneath; pressing 0 turns interpretation off.

## Quickstart

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/). Docker is optional (Postgres and Redis are off the hot path).

```bash
uv sync                     # or: make install
cp .env.example .env        # vendor keys are optional; without them everything runs on fakes
make test                   # offline tests, no network, no keys
make dev                    # API on http://127.0.0.1:8000 with reload (one worker)
```

Then open:

| URL | What |
|---|---|
| `http://127.0.0.1:8000/loopback/new` | creates a loopback call and gives two tab URLs: one browser tab per leg, talk into each mic (lands with the phase-2 session/API work) |
| `http://127.0.0.1:8000/ward` | ward mode: one phone, two people |
| `http://127.0.0.1:8000/console` | live console: turns, state badge, per-stage latency over SSE (HTTP Basic, any user, password `CONSOLE_PASSWORD`) |

With real keys in `.env`, `make smoke` checks Sarvam (translate, Bulbul, STT) and ElevenLabs end to end and writes wavs to `out/smoke/`. See [dev setup](docs/engineering/dev-setup.md) for everything else.

## Architecture

One `CallSession` per call owns two legs (Exotel, Plivo, WebRTC or Fake, behind one `LegTransport` interface), two interpreter pipelines (one per direction: VAD, end-of-utterance, streaming STT, Sarvam Mayura translate, Bulbul or ElevenLabs streaming TTS) and one `FloorController`, the state machine that decides who is speaking, queues translations, handles barge-in and DTMF, and is the only code that sends audio to a leg through the ducking mixer. Audio is PCM16 mono 16 kHz in 20 ms frames everywhere inside; resampling happens at the leg edge. Every component sits behind a Protocol with a deterministic fake, so the whole pipeline runs offline. Postgres (turn log) and Redis (registry) are never touched on the hot path. Start with the [docs index](docs/README.md), then the [system overview](docs/architecture/system-overview.md), the [floor controller](docs/tech-spec/floor-controller.md), the [interpreter pipeline](docs/tech-spec/interpreter-pipeline.md) and the [leg transports](docs/tech-spec/leg-transports.md).

## Status (Day 1, 21 Oct 2026)

| Area | With fakes | With real vendors | Telephony |
|---|---|---|---|
| Audio: frames, resample, mixer, earcon, wav | works, unit-tested | n/a | n/a |
| Floor controller (states, queue, barge-in, DTMF, bypass) | works, invariant tests | n/a | n/a |
| Interpreter pipeline | works end to end on fakes | wired to real providers; not yet run live | n/a |
| Sarvam STT, Mayura translate, Bulbul TTS | `FakeSTT` / `FakeMT` / `FakeTTS` | implemented, mock-tested; run `make smoke` to verify live | n/a |
| ElevenLabs Flash v2.5 TTS (hi, en) | `FakeTTS` | implemented, mock-tested; `make smoke` | n/a |
| Deepgram Nova STT (fallback) | `FakeSTT` | mock-tested only, no key yet | n/a |
| FakeLeg, WebRtcLeg + loopback page | works | browser mic, real providers | n/a |
| ExotelLeg | mock-tested against the documented protocol | n/a | pending: Exotel voicebot access expected in a few days (open question 1, 16 kHz, still unverified) |
| PlivoLeg | mock-tested (L16 and mu-law) | n/a | pending: Udyam-verified account (open question 6) |
| CallSession, registry, `/v1/calls`, `/ws/leg`, `/loopback/new` | phase 2, in progress | in progress | in progress |
| Console (`/console`) and ward page (`/ward`) | pages and SSE work | n/a | n/a |
| Turn writer (Postgres) | works with SQLite in tests; app runs without a DB | n/a | n/a |
| Tools: `smoke`, `label`, `gen_fixtures`, `record_fixtures` | tested | `smoke` is the live check | n/a |
| Tools: `bench`, `loopback` | phase 2, in progress | | |

Latency numbers we quote come from real PSTN calls only; loopback numbers are for correctness. See [evaluation and metrics](docs/engineering/evaluation-and-metrics.md).

## Repo layout

```text
dubhash/
  api/          FastAPI app factory, console routes, health, dependencies
  audio/        frames (640-byte 20 ms frames), resample (soxr), mixer + earcon, wav I/O
  config/       settings.py (env), pairs.py + pairs.yaml (per language pair)
  console/      server-rendered templates, static JS, SSE
  interp/       Protocols (contracts.py), VAD, endpointing, gender, MT cache, pipeline
  legs/         LegTransport base, FakeLeg, ExotelLeg, PlivoLeg, WebRtcLeg (+ loopback page)
  providers/    Sarvam STT/MT/Bulbul, ElevenLabs, Deepgram, registry, fakes
  session/      FloorController (CallSession and registry land in phase 2)
  store/        SQLAlchemy models, background TurnWriter
  clock.py, events.py, types.py   shared clock, event bus, value types
tools/          smoke.py, label.py, gen_fixtures.py, record_fixtures.py (bench.py, loopback.py: phase 2)
tests/          unit, contract and pipeline tests; fixtures/ holds small synthetic wavs
docs/           product, architecture, tech spec, plan, engineering
Makefile        install, test, test-live, lint, fmt, dev, up, down, bench, smoke, fixtures, label
```

Contributing: read [conventions](docs/engineering/conventions.md) first. `make lint` and `make test` must pass before every commit; CI runs both.
