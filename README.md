<p align="center">
  <img src="https://img.shields.io/badge/status-pre--alpha-orange" alt="Status" />
  <img src="https://img.shields.io/badge/license-Apache%202.0-blue" alt="License" />
  <img src="https://img.shields.io/badge/python-3.12+-yellow" alt="Python" />
  <img src="https://img.shields.io/badge/react-19-61dafb" alt="React" />
</p>

<h1 align="center">convox</h1>
<p align="center"><strong>Open-source testing &amp; observability for voice AI agents</strong></p>
<p align="center">Simulate thousands of real callers against your agent. Score every call on transcript <em>and</em> audio. Run all of it in your own infrastructure.</p>

<p align="center">
  <a href="docs/README.md">Documentation</a> ·
  <a href="docs/product-overview.md">Product Overview</a> ·
  <a href="docs/scenario-spec.md">Scenario Spec</a> ·
  <a href="docs/architecture.md">Architecture</a> ·
  <a href="docs/roadmap.md">Roadmap</a>
</p>

---

> **Status: pre-alpha, under active development.** The [documentation](docs/README.md) describes the full product; [what works today](#what-works-today) covers the current state, and [docs/status.md](docs/status.md) has the detailed engineering status, known gaps, and the phased plan.

## What is Convox?

Convox answers one question, continuously and automatically:

> **Does my voice agent actually work — for every kind of caller, on every channel, on every deploy, and right now in production?**

It's **pytest for voice agents**. Convox places real calls to your agent using synthetic callers with real voices, accents, interruptions, and background noise — then measures what happened at the audio layer, not just the transcript.

```
    SIMULATE                EVALUATE                 OBSERVE
  ─────────────           ─────────────            ─────────────
  Synthetic callers  ──▶  Deterministic       ──▶  Production calls
  over real audio         assertions +             scored on the
  channels                calibrated judges        same metrics
                          + audio metrics          + replay as tests
         ▲                                                │
         └───────────────  replay closes the loop  ◀──────┘
```

Works with **Retell · Vapi · LiveKit · Pipecat · Bland · ElevenLabs · raw SIP/WebSocket** — one adapter interface, any platform.

## Why another testing tool?

Every credible voice-agent testing product — Cekura, Coval, Hamming, Bluejay, Roark — is **closed SaaS**. You ship them your call recordings and pay per test minute.

Convox is Apache 2.0, self-hosted by default, and uses your own model keys. For healthcare, financial services, and anyone under DPDP/HIPAA/GDPR, that's the difference between adoptable and not.

## What makes it different

**We know exactly what the caller said.** Convox generates the caller's speech from text, so the ground-truth transcript exists *before* the audio does. That makes measurements possible that transcript-scoring tools literally cannot compute:

- **Exact WER** of the agent's speech recognition — a diff against known truth, not an estimate
- **Slot accuracy** — we spoke `98765 43210`; did the agent capture it? A string comparison, not a judgment call
- **Failure attribution by layer** — if we said X, the agent heard Y, and replied about Y, the fault is STT, not the LLM

**Deterministic first, judges second.** LLM-judge flakiness is the loudest complaint about every tool in this category. Convox's assertions are deterministic by default. Judges run only for genuinely semantic claims, with temperature 0, self-consistency voting, and a requirement to cite the turn IDs that justify the verdict — and **judge calibration is a first-class feature**: label some calls once and Convox reports your judge's precision, recall, F1, and κ against those labels.

**`pass^k`, not pass/fail.** A voice test that passes once is noise. Convox runs each scenario *k* times and reports the fraction that passed. A scenario at 3/5 isn't a pass; it's a 60% agent.

**Honest about what it can't measure.** If a platform doesn't expose tool calls, those assertions report `unsupported` — never a silent pass.

## Quick look

```yaml
# scenarios/refill_happy_path.yaml
name: refill_prescription_happy_path
persona: hinglish_impatient        # interrupts, code-switches, street noise, 8kHz
mode: hybrid

caller:
  goal: Refill your Metformin prescription and confirm the pickup time.
  facts:
    phone: "+91 98765 43210"
    medication: "Metformin 500mg"
  opening: "Haan hello, mujhe apni dawai refill karwani hai."

assert:
  - tool.called: create_refill_order
  - slot.captured: { field: phone, value: "+919876543210", normalize: e164 }
  - latency.response_ms: { p95: { lt: 1200 } }
  - barge_in.stop_ms: { max: { lt: 300 } }
  - call.ended_by: agent
  - judge: "The agent confirmed the pickup date AND time before ending the call."

repeat: 5
```

```bash
convox run scenarios/ --target retell:agent_abc123
```

```
Run 7f3a1c · target retell:agent_abc123 · 20 scenarios × 3 repeats = 60 trials

  ✓ refill_happy_path              3/3  pass^3   p95 840ms
  ✗ refill_interrupted             1/3  pass^3=0.33
      ✗ barge_in.stop_ms  max=880ms (budget 300ms)
      → agent kept speaking 880ms after caller barge-in at turn 4
  ✗ hinglish_code_switch           0/3
      ✗ slot.captured phone: expected +919876543210, agent read back +919876543219
      → attributed to STT (caller ground truth correct, agent transcript wrong)

  48/60 trials passed · 16/20 scenarios pass^3
  Report: http://localhost:8000/runs/7f3a1c
```

## What works today

You can run a real suite against a real agent right now:

```bash
cd api && uv sync --all-extras

# a demo agent to test against (deliberately buggy variants available)
uv run python -m convox.testing.reference_agent --bug mangle_digits &

uv run convox init my-tests && cd my-tests
uv run convox run scenarios/ --target websocket:ws://127.0.0.1:8765
```

```
  ✗ refill_happy_path                  0/3  pass^3=0.00       p95 1806ms
      ✗ slot.captured  agent never captured 'phone' (expected '+919876543210')
        → suspected layer: llm

  0/3 trials passed · 0/1 scenarios green

  1 assertion(s) could not be measured:
    judge: no judge backend configured (set `judge.llm` in convox.yaml)
```

### The audio path

Convox holds real spoken conversations, not just text exchanges — which is what
makes the interesting failures visible:

```bash
uv run python -m convox.testing.audio_agent --bug truncate_audio &
```

```yaml
persona: noisy_street          # café babble at 10 dB SNR, G.711 narrowband, 4G loss
assert:
  - asr.wer: { lt: 0.05 }             # exact, against what the caller actually said
  - asr.entity_error_rate: { lt: 0.0 }  # did it get the phone number right?
  - barge_in.handled: true            # does it stop when interrupted?
  - barge_in.stop_ms: { max: { lt: 300 } }
  - audio.no_truncation: true         # was the last word cut off?
  - audio.no_artifacts: true          # did the TTS glitch?
```

Because Convox synthesises the caller's speech, the reference transcript exists
before the audio does — so word error rate is a diff against known truth, and a
mis-heard digit is provable rather than inferred.

**It runs with no API keys.** The audio path ships with a deterministic reference
voice codec: text in, tones inside the telephony band out, and a matched decoder.
Add noise, drop packets, or squeeze it through G.711 and recognition degrades the
way real ASR degrades — so the whole pipeline is exercisable in CI, offline, with
identical results every run. Real providers (Sarvam, ElevenLabs, Deepgram, local
Whisper/Piper) implement the same two protocols and drop straight in.

Shipping now: the scripted simulation engine, text and audio WebSocket adapters,
ground-truth capture, the channel simulator (G.711/G.722/GSM codecs, 14 noise
profiles, packet loss and jitter), independent transcription, ~35 deterministic
assertions, latency / turn-taking / recognition / audio-quality metrics, layer
attribution, `pass^k` reliability scoring, JUnit/JSON reports, exit codes CI can
branch on, and `init` / `lint` / `run` / `target test`.

Not yet: agentic callers, judge backends, platform adapters beyond WebSocket,
load testing, production monitoring, and the dashboard. Assertions that need
those report `unsupported` — never a silent pass.

**Convox tests itself.** The bundled reference agent has injectable faults, and
the self-check suite asserts that Convox reports *exactly* those faults and no
others. A testing tool whose own reliability is unproven is worthless:

```bash
cd api && uv run pytest                          # everything
cd api && uv run pytest --ignore=tests/test_audio_call.py   # fast suite only
```

## Features

| Area | Highlights |
|---|---|
| **Simulation** | Scripted, agentic, and hybrid callers · 40+ shipped personas · barge-in scheduling · backchannels · disfluencies · hang-ups |
| **Channel realism** | G.711/G.722/GSM/Opus codecs · 25+ background noise profiles · packet loss, jitter, network presets · device EQ |
| **Assertions** | Transcript, slot capture, tool calls, latency, barge-in, lifecycle, audio quality, ASR, PII, compliance — all deterministic |
| **Judges** | Rubric, goal, instruction-following, hallucination · evidence citation · vote quorum · calibration reporting |
| **Metrics** | Per-turn latency percentiles · barge-in stop time · false interrupts · exact WER/CER · entity error rate · truncation, clipping, artifacts · talk ratio · repetition · cost |
| **CI/CD** | Baselines, regression diffing, `--fail-on regression`, GitHub Action with PR comments, JUnit output |
| **Load testing** | Ramp profiles, distributed workers, degradation curves, concurrency-ceiling discovery |
| **Red teaming** | Prompt injection over voice, jailbreaks, PII extraction, hostile callers, disclosure/consent checks |
| **Observability** | Webhook/OTLP/SDK ingest · same evaluators on production calls · cohorts, drift detection, outliers, Slack alerts |
| **Replay** | Turn a bad production call into a permanent regression test in one click |
| **Multilingual** | 12 Indic languages + globals · **intra-sentential code-switching** · Indic-correct WER normalization · Indian telephony profiles |

Full inventory with phase tags: [docs/features.md](docs/features.md)

## Tech stack

| Layer | Technology |
|---|---|
| Caller pipeline | [Pipecat](https://github.com/pipecat-ai/pipecat) |
| API / workers | Python 3.12+ · FastAPI · asyncio (no ORM) |
| Database | PostgreSQL 17 · Redis 7 (Streams) · MinIO/S3 |
| DSP | numpy (codecs, noise, and analysis are implemented in-repo) |
| Frontend | Vite · React 19 · TypeScript · Tailwind v4 · wavesurfer.js |
| CLI | Typer · Rich |
| Infra | Docker Compose · Helm · OpenTelemetry |

## Project structure

```
convox/
├── api/convox/
│   ├── adapters/       # target adapters: Retell, Vapi, LiveKit, Pipecat, WS, SIP…
│   ├── sim/            # simulation engine: caller policies, turn control, channel sim
│   ├── eval/           # assertions, judges, metrics, ASR scoring, attribution
│   ├── observe/        # production ingest, monitors, drift, alerts
│   ├── generate/       # scenario generation
│   ├── load/           # load testing coordinator
│   ├── replay/         # production call → scenario
│   ├── providers/      # STT/LLM/TTS plugins (caller voice + judges)
│   └── ...             # handlers, models, repository, service, compliance
├── cli/                # `convox` CLI
├── web/                # React dashboard
├── personas/           # shipped persona library
├── scenarios/examples/ # example suites
├── benchmark/          # reference agent + standard suite
└── docs/            # documentation
```

Detailed layout: [docs/tech-spec.md](docs/tech-spec.md)

## Deployment

| Shape | For |
|---|---|
| **Local** | `pip install convox` — file-based artifacts, no infra |
| **Self-hosted** | `docker compose up` — API + workers + Postgres + Redis + MinIO |
| **Scaled** | Helm chart, horizontally scaled workers for load testing |
| **Air-gapped** | Local Whisper/Piper/vLLM — zero egress, a supported and CI-tested configuration |

Convox never phones home. Telemetry is off by default and always opt-in.

## Roadmap

- **Phase 0–1** — Simulation engine, deterministic assertions, metrics, Retell/Vapi/Pipecat/WebSocket adapters, CLI
- **Phase 2** — Baselines, regression diffing, GitHub Action, dashboard
- **Phase 3** — Public launch, docs, self-test suite
- **Phase 4** — Production observability, replay, alerts
- **Phase 5** — Load testing, Indic depth, PSTN/SIP, red teaming, judge calibration
- **Phase 6** — Open public benchmark across agent platforms
- **Phase 7** — Enterprise: air-gapped, RBAC/SSO, compliance packs

Full plan: [docs/roadmap.md](docs/roadmap.md)

## Development

```bash
git clone https://github.com/rohansx/convox.git
cd convox/api
uv sync --all-extras

uv run pytest                 # tests
uv run ruff check             # lint
uv run convox --help          # the CLI

# the control plane (health endpoint today; full REST surface with the API phase)
uv run uvicorn convox.app:create_app --factory --reload --port 8000
```

The engine has no database dependency — `convox run` works with nothing but
Python. Postgres, Redis, and object storage come in when you want run history,
the dashboard, and production monitoring.

## Contributing

Apache 2.0. The highest-leverage contributions are **target adapters** (one interface, any platform), **personas and noise profiles**, and **assertions** — all three are designed as plugin points precisely so they don't have to go through us.

1. Fork, branch (`git checkout -b feature/amazing-thing`)
2. Make your change; adapters must pass the shared contract tests
3. Open a Pull Request

## Background

An earlier iteration of this repo explored voice-agent *orchestration* — building and serving the agents themselves. That layer turned out to be commoditized: twelve-plus funded platforms with converging features and deflating prices. The testing and observability layer above it was funded, growing fast, and completely unclaimed by open source. The [market research](docs/market-research.md) behind that conclusion is in the repo, and the orchestration work is preserved on the `archive/orchestration-platform` branch.

## License

[Apache License 2.0](LICENSE)

---

<p align="center"><strong>Test your voice agents like you test your code.</strong></p>
