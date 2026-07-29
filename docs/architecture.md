# Convox — System Architecture

> Last updated: July 2026
> Status: Pre-implementation design
> Related: [Tech Spec](tech-spec.md) · [Features](features.md) · [Roadmap](roadmap.md)

---

## 1. Overview

Convox is a **self-hosted testing and observability platform for voice AI agents**. Architecturally it is a control plane plus two worker fleets, connected by a queue and a shared artifact store.

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                              CONVOX PLATFORM                                  │
│                                                                               │
│  ┌────────────┐   ┌──────────────────────────┐   ┌─────────────────────────┐  │
│  │ Dashboard  │   │      Control Plane       │   │   Ingest / Observe      │  │
│  │  (React)   │──▶│  (FastAPI: REST + WS)    │◀──│  webhooks · OTLP · SDK  │  │
│  └────────────┘   └──────────────────────────┘   └─────────────────────────┘  │
│        ▲                      │       ▲                                       │
│        │                      ▼       │                                       │
│  ┌────────────┐   ┌──────────────────────────┐                                │
│  │ CLI / SDK  │──▶│    Job Queue (Redis)     │                                │
│  │ CI · MCP   │   └──────────────────────────┘                                │
│  └────────────┘        │                  │                                   │
│                        ▼                  ▼                                   │
│  ┌──────────────────────────────┐  ┌──────────────────────────────────────┐   │
│  │     SIMULATION WORKERS       │  │        EVALUATION WORKERS            │   │
│  │  ┌────────────────────────┐  │  │  ┌────────────────────────────────┐  │   │
│  │  │  Synthetic Caller      │  │  │  │ Deterministic assertion engine │  │   │
│  │  │  (Pipecat pipeline)    │  │  │  │ Audio analysis (DSP)           │  │   │
│  │  │  STT · LLM · TTS · VAD │  │  │  │ WER / slot scoring             │  │   │
│  │  └────────────────────────┘  │  │  │ LLM judges (voting, evidence)  │  │   │
│  │  ┌────────────────────────┐  │  │  │ Layer attribution              │  │   │
│  │  │  Target Adapters       │  │  │  └────────────────────────────────┘  │   │
│  │  │  WS·WebRTC·SIP·Retell· │  │  └──────────────────────────────────────┘   │
│  │  │  Vapi·LiveKit·Pipecat  │  │                                             │
│  │  └────────────────────────┘  │                                             │
│  │  ┌────────────────────────┐  │                                             │
│  │  │  Channel Simulator     │  │                                             │
│  │  │  codec·noise·loss·jitter│ │                                             │
│  │  └────────────────────────┘  │                                             │
│  └──────────────────────────────┘                                             │
│                        │                  │                                   │
│                        ▼                  ▼                                   │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────────────────┐   │
│  │  PostgreSQL    │  │     Redis      │  │  Object Store (MinIO / S3)     │   │
│  │  metadata,     │  │  queue, live   │  │  audio legs, mixed recordings, │   │
│  │  results, ts   │  │  state, cache  │  │  artifacts, reports            │   │
│  └────────────────┘  └────────────────┘  └────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
                   ┌──────────────────────────────────────┐
                   │       AGENT UNDER TEST (external)    │
                   │  Retell · Vapi · LiveKit · Pipecat · │
                   │  Bland · ElevenLabs · SIP · your own │
                   └──────────────────────────────────────┘
```

**The central inversion.** Most voice-AI infrastructure runs a pipeline that *serves* a human caller. Convox runs a pipeline that *plays* one — the same STT/LLM/TTS machinery, pointed at someone else's agent, with deliberate control over timing and turn-taking so that every interaction is measurable rather than merely plausible.

---

## 2. Design principles

1. **The adapter boundary is sacred.** Everything above it (personas, scenarios, assertions, metrics) is platform-agnostic. Adding Bland must never require touching the evaluation engine.
2. **Ground truth is generated, never inferred.** The caller's text exists before its audio does. Every downstream measurement that can be exact, is exact.
3. **Deterministic first.** Models enter the loop only where semantics genuinely require them, and never for anything a string comparison can settle.
4. **Simulation and evaluation are separate concerns.** Calls are expensive and real-time; evaluation is cheap, retryable, and improvable after the fact. Re-scoring a run with a better judge must never require re-calling the agent.
5. **Artifacts are the contract.** A trial produces a self-describing bundle. The dashboard, the CLI, the reports, and third-party tools all read the same bundle.
6. **Nothing phones home.** Self-hosted means self-hosted.
7. **Local-first must work.** A single developer with `pip install convox` and no Docker gets a working tool; Postgres, Redis, and MinIO are for teams.

---

## 3. Component breakdown

### 3.1 Control plane (FastAPI)

The system of record and the only writer to Postgres. Responsibilities:

- REST API for targets, personas, scenarios, suites, runs, trials, monitors, baselines, alerts
- Run planning: expand a suite × repeat count × matrix into a concrete trial list, enforce cost budgets, enqueue
- Job dispatch and lease management via Redis
- WebSocket streams for live run progress and live call views
- Ingest endpoints (platform webhooks, OTLP, SDK) for production calls
- Auth (API keys, JWT), team scoping, audit logging
- Serves the built React SPA in the monolith deployment

It does **no** audio work. It must stay responsive while 500 calls are in flight.

### 3.2 Simulation workers

Long-running async processes that lease trials from the queue and execute calls. Each worker runs N concurrent trials (default 4–8, bounded by CPU for DSP and by provider rate limits).

Per trial, in order:

1. **Resolve** — fetch scenario, persona, target credentials; seed the RNG.
2. **Connect** — target adapter opens the audio channel; wait for answer; start the clock.
3. **Converse** — the caller pipeline runs: agent audio → (optional Convox STT) → caller policy (scripted or LLM) → TTS → channel simulator → target.
4. **Record** — both legs written separately and continuously; the event timeline appended with monotonic timestamps.
5. **Terminate** — on goal completion, max turns, max duration, caller hang-up policy, or adapter disconnect.
6. **Package** — upload artifacts, write the trial row, enqueue an evaluation job.

The worker is the only component that touches real time. Everything about it is designed to avoid stalls: no synchronous DB writes in the audio path, bounded queues, and a hard watchdog that terminates and marks a trial `infrastructure_error` rather than hanging a run.

### 3.3 Synthetic caller pipeline

A Pipecat pipeline configured *as a caller*:

```
   agent audio in ──▶ [VAD] ──▶ [STT (optional)] ──▶ ┐
                                                     │
                                            [Caller Policy]
                                    scripted list | LLM w/ goal+facts
                                                     │
   channel out ◀── [Channel Sim] ◀── [TTS] ◀── [Prosody/Persona] ◀┘
                                       │
                                       └──▶ ground-truth text log
```

Key behaviors:

- **Turn control.** The caller decides when to speak based on persona: after the agent finishes (polite), at a configured offset into the agent's speech (barge-in), or on a timer (impatient). Barge-in is scheduled precisely, because measuring the agent's reaction requires knowing exactly when we started talking.
- **STT is optional on the caller side.** In scripted mode with no content-dependent branching, we can skip caller-side STT entirely and save cost; we still need it for agentic mode and for scoring the agent's speech when the platform exposes no transcript.
- **Ground-truth logging** happens at the TTS boundary — the exact string, the voice, the timestamp, and the synthesized duration.

### 3.4 Target adapters

```python
class TargetAdapter(Protocol):
    async def connect(self, trial: TrialContext) -> AudioChannel: ...
    async def disconnect(self) -> None: ...
    def events(self) -> AsyncIterator[SideChannelEvent]: ...   # tool calls, transcripts, spans
    async def send_dtmf(self, digits: str) -> None: ...
    @property
    def capabilities(self) -> AdapterCapabilities: ...
```

`AdapterCapabilities` declares what this platform can actually provide — agent transcript, tool-call visibility, DTMF, recording URL, trace export. The evaluation engine reads capabilities and degrades gracefully: if the platform exposes no tool calls, tool assertions report `unsupported`, not `failed`. Being honest about what can't be measured on a given platform is a correctness requirement, not a nicety.

### 3.5 Channel simulator

A pure-DSP stage between caller TTS and the target: resample → noise mix at target SNR → device EQ/IR → codec encode/decode → packet loss / jitter. Deterministic given a seed. Runs in-process (numpy/scipy + audioop-style codecs) so no external media server is required for the common case.

### 3.6 Evaluation workers

Consume completed trials and produce scores. Deliberately decoupled and idempotent — a trial can be re-evaluated any number of times, and re-evaluation is a first-class operation (`convox report --rescore`).

Pipeline:

```
artifacts ──▶ [normalize timeline] ──▶ [audio analysis] ──▶ [ASR scoring vs ground truth]
                                            │                        │
                                            ▼                        ▼
                                     [metric computation] ──▶ [deterministic assertions]
                                                                     │
                                                          (only if pre-filter passes)
                                                                     ▼
                                                              [LLM judges: vote, cite]
                                                                     │
                                                                     ▼
                                                           [layer attribution] ──▶ results
```

Judges run last and conditionally, which keeps token spend proportional to how far a call got.

### 3.7 Ingest pipeline (production observability)

Three entry points — platform webhooks, OTLP spans, and the SDK — normalize into the same `Call` shape the simulator produces. Once normalized, a production call is scored by the *same* evaluation workers with the same assertions. That shared shape is why "test success rate vs production success rate" is an apples-to-apples comparison rather than two unrelated dashboards.

Where audio isn't available (some platforms don't expose recordings), the call is scored on transcript-only metrics and every audio metric is marked `unavailable` rather than defaulted to a passing value.

### 3.8 Dashboard

React 19 + Vite SPA, built to static files and served by FastAPI in the monolith deployment. The one genuinely demanding view is the trial detail page: a waveform renderer with turn-synchronized transcript scrubbing, latency bars per turn, and barge-in markers. That view is built against the artifact bundle format, so it works identically for simulated and production calls.

---

## 4. Data flow: one simulated call, end to end

```
 CLI: convox run scenarios/ --target retell:agent_abc
   │
   ▼
 POST /v1/runs  ────────────────────────────────────────────────┐
   │  control plane expands suite × repeats × matrix            │
   │  → 60 trial rows (status=queued), cost budget attached     │
   ▼                                                            │
 Redis Stream "trials"  ──lease──▶ Simulation Worker            │
                                     │                          │
                                     │ 1. resolve + seed        │
                                     │ 2. adapter.connect()     │
                                     │    (Retell API creates   │
                                     │     a web call → WS URL) │
                                     │ 3. conversation loop:    │
                                     │      agent audio ──▶ VAD │
                                     │      caller policy       │
                                     │      TTS → channel sim   │
                                     │      ──▶ target          │
                                     │    (ground truth logged  │
                                     │     at every utterance)  │
                                     │ 4. terminate + upload    │
                                     ▼                          │
                        MinIO: caller.wav, agent.wav,           │
                               mixed.wav, timeline.jsonl        │
                        Postgres: trial, turns, events          │
                                     │                          │
                                     ▼                          │
                        Redis Stream "evaluations"              │
                                     │                          │
                                     ▼                          │
                          Evaluation Worker                     │
                            │ audio DSP metrics                 │
                            │ WER vs ground truth               │
                            │ deterministic assertions          │
                            │ judges (3 votes, cite turns)      │
                            │ layer attribution                 │
                            ▼                                   │
                        Postgres: metrics, assertion_results,   │
                                  judgments                     │
                                     │                          │
                                     ▼                          │
                   Run aggregation: pass^k, baseline diff  ◀────┘
                                     │
                        ┌────────────┴────────────┐
                        ▼                         ▼
                 CLI exit code +           Dashboard / PR comment /
                 JUnit XML                 report artifact
```

---

## 5. Deployment topologies

### 5.1 Local developer (no infra)

`pip install convox` → SQLite-free file mode: artifacts on disk under `./convox-out/`, no queue (in-process executor), no dashboard unless `convox serve` is run. Target: a first failing test inside ten minutes.

### 5.2 Self-hosted monolith (default)

One `docker compose up`: API + embedded workers + Postgres + Redis + MinIO + Caddy. Handles small-team CI and modest monitoring volume on a single box.

### 5.3 Self-hosted scaled

API and workers as separate deployments; workers scale horizontally for load testing (hundreds of concurrent calls). Helm chart with an HPA keyed on queue depth. Postgres and object storage are whatever the org already runs.

### 5.4 Air-gapped

Same as scaled, with local providers only: Whisper (STT), Piper/Kokoro (TTS), vLLM-served open weights (caller LLM and judges). Zero egress. This is the deployment the regulated ICP actually buys, so it's a supported configuration with its own CI job, not a "should work" claim.

---

## 6. Scaling and performance

| Concern | Approach |
|---|---|
| Concurrency | Trials are independent and embarrassingly parallel; workers lease from a Redis Stream with consumer groups |
| Audio path latency | No blocking I/O in the audio loop; DB writes batched and off-path; artifacts uploaded after termination (streamed to disk during the call) |
| CPU | DSP (noise mixing, codecs, resampling) is the main cost — numpy vectorized, ~2–5% of a core per concurrent call |
| Provider rate limits | Per-provider token buckets shared across workers via Redis; backpressure at lease time, not mid-call |
| Load testing | Coordinator schedules a ramp across the fleet; workers report health so the ramp degrades gracefully rather than lying about achieved concurrency |
| Evaluation cost | Judges gated behind deterministic pre-filters; judge results cached by content hash so re-runs of unchanged trials are free |
| Storage | Recordings dominate; per-tenant retention TTLs, opus compression for archives, raw PCM only during the call |

**Targets:** 500 concurrent simulated calls on a 3-node worker fleet; median trial evaluation under 15s after call end; dashboard trial-detail load under 500ms for a 5-minute call.

---

## 7. Component reuse

Several subsystems are shared rather than purpose-built, which keeps the surface area small:

| Subsystem | Used by |
|---|---|
| Provider plugins (STT / TTS / LLM) | Synthetic caller voice stack, independent transcription, judge backends |
| Pipecat pipeline | The caller runtime |
| Cost accounting | Per-trial and per-run attribution, budget guards |
| Retention / redaction / audit | Recording lifecycle, and simultaneously a *testable* surface via compliance assertions |
| Telephony provider clients | PSTN target adapter and inbound-number handling for outbound-agent tests |
| Evaluation engine | Simulated trials **and** ingested production calls — identical scoring, which is what makes test-vs-production comparison valid |

## 8. Key architectural risks

| Risk | Mitigation |
|---|---|
| **Our own tool is flaky** — variance in the harness masquerading as agent bugs | Deterministic mode (scripted caller, seeded DSP, temperature-0 voting judges); a self-test suite that runs Convox against a *known-good reference agent* and fails CI if verdicts vary |
| **Adapter rot** — platforms change APIs | Capability declarations + per-adapter contract tests run nightly against real sandbox accounts; adapters degrade rather than crash |
| **Real-time audio in Python** | The audio loop does no blocking work; Pipecat already handles this class of problem in production; DSP is vectorized; a Rust/C extension is the escape hatch if profiling demands it |
| **Judge cost at scale** | Deterministic pre-filters, content-hash caching, sampling policies for production scoring, small-model defaults |
| **Storage growth from recordings** | Retention TTLs on by default, opus archival, per-tenant quotas |
| **Scope sprawl back toward v1** | The "what Convox is not" list in the product overview is a design constraint, not marketing copy: we never build or host the agent |
