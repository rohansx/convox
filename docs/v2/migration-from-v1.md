# Convox v1 → v2 — Migration Plan

> Last updated: July 2026
> Status: Pivot definition
> Related: [Architecture](architecture.md) · [Tech Spec](tech-spec.md) · [Roadmap](roadmap.md)

v1 was a self-hosted voice-agent **orchestration** platform. v2 is a voice-agent **testing and observability** platform. This document is the concrete accounting of what survives, what gets rebuilt, and what gets deleted.

The short version: **most of the substrate survives, because a tool that simulates callers needs almost exactly the same plumbing as a tool that serves them.** What gets deleted is the agent-serving runtime and everything that existed only to make Convox the thing on the other end of a customer's phone call.

---

## 1. The conceptual inversion

| | v1 | v2 |
|---|---|---|
| Convox's role | The agent | The caller |
| Audio direction | Serves a human | Drives a machine |
| Pipecat's job | Run the production pipeline | Run the synthetic caller |
| Success means | The conversation happened | The conversation was measured |
| The customer's agent | Is Convox | Is external, and under test |
| Providers (STT/TTS/LLM) | Serve end users | Voice the simulated caller and judge results |

Everything in v1 that was pointed *outward at humans* now points *inward at another machine*. The pipeline code, the provider abstraction, and the cost accounting don't care which direction they're facing.

---

## 2. Component-by-component disposition

### Keep as-is (or near-as-is)

| Component | v1 purpose | v2 purpose |
|---|---|---|
| `database/postgres.py`, `database/redis.py` | Connection pools | Unchanged |
| `crypto/` | JWT, credential encryption | Unchanged — now encrypts target platform keys too |
| `middleware/` (auth, CORS, logging, team scope) | HTTP middleware | Unchanged |
| `config.py` | Pydantic settings | Extended with worker/judge/artifact settings |
| `repository/` pattern | Raw SQL data access | Same pattern, new tables |
| `app.py` | FastAPI factory | Extended with new routers |
| dbmate migrations tooling | Schema management | Unchanged tooling, new migration series |
| Docker Compose / Dockerfile | Deployment | Extended with worker services + MinIO |
| React/Vite/Tailwind shell | Dashboard chrome | Reused; new views |

### Refactor and repurpose

| Component | v1 purpose | v2 purpose |
|---|---|---|
| `providers/stt/*` | Transcribe the caller | Transcribe the **agent**, and score audio independently |
| `providers/tts/*` | Speak to the caller | **Voice the synthetic caller** — now the core of persona rendering |
| `providers/llm/*` | Drive the agent's responses | Drive the **caller's** responses (agentic mode) + back the judges |
| `providers/base.py` | Plugin contract | Contract survives; cost hooks become per-trial attribution |
| `providers/telephony/*` | Receive/place production calls | PSTN target adapter + inbound number handling for outbound-agent tests |
| Pipecat integration | Production voice pipeline | Synthetic caller pipeline (inverted, plus deliberate turn control) |
| `service/cost.py` | Per-session cost tracking | Per-trial/per-run cost attribution + budget guards |
| `compliance/dpdp/` | Consent capture, retention, audit | Retention/redaction/audit for recordings **and** becomes a *testable surface*: compliance assertions (`compliance.disclosure_present`, `pii.not_leaked`) |
| `handler/analytics.py` | Call cost/latency analytics | Run and monitor analytics |
| `handler/sessions.py` | Call session lifecycle | Becomes `runs` + `trials` lifecycle |
| `ws/` | Real-time call audio to browsers | Live run progress + live call view |

The Sarvam STT/TTS work is worth calling out specifically: it was v1's India differentiator and it becomes v2's, more sharply. In v1 it was one of several ways to serve Indian users; in v2 it powers Indic personas and Indic-correct scoring — a capability no competitor has bothered to build.

### Build new

| Component | Why it's new |
|---|---|
| `adapters/` | Nothing in v1 connected *to* someone else's agent |
| `sim/` (worker, caller policies, turn control, recorder) | The simulation engine is the product |
| `sim/channel/` (codec, noise, network, device) | Channel realism didn't exist in v1 |
| `eval/` (assertions, judges, metrics, attribution, ASR scoring) | The entire evaluation layer |
| `generate/` | Scenario generation |
| `load/` | Load testing coordinator |
| `observe/` | Ingest, monitors, drift, alerts |
| `replay/` | Production call → scenario |
| `report/` | JUnit, HTML, PR comments |
| `cli/` | v1 had no CLI; in v2 the CLI *is* the primary interface |
| `mcp/` | Editor integration |
| Scenario/persona schema + loader | The product's public contract |

### Delete

| Component | Why |
|---|---|
| Agent-serving runtime (spawn/manage agents that answer real calls) | v2 never serves an agent |
| `handler/agents.py` (agent CRUD as *deployable agents*) | Replaced by `targets` — descriptors of someone else's agent |
| Inbound call routing / production telephony webhooks for serving | Only inbound-for-testing survives |
| Visual agent/pipeline builder (planned, unbuilt) | That's the commoditized layer we're exiting |
| Conversation/prompt management for production agents | Not our product anymore |
| v1 database schema | Superseded (see below) |
| v1 README positioning and docs | Replaced by the v2 doc set |

---

## 3. Database

**No data migration.** v1 is pre-alpha with no production users, so v2 starts a clean migration series rather than carrying compatibility weight. The v1 migrations directory is archived under `migrations/_v1_archive/` for reference and removed from the active series.

Table mapping for orientation:

| v1 table | v2 equivalent |
|---|---|
| `agents` | `targets` (semantically inverted: theirs, not ours) |
| `sessions` | `trials` (plus `runs` above them) |
| `transcripts` | `turns` (with four transcript columns instead of one) |
| `costs` | `cost_entries` (per trial) |
| `consents`, `audit_log` | Retained; audit extended to run/baseline/recording access |
| `users`, `teams`, `api_keys` | Retained largely unchanged |
| — | New: `personas`, `scenarios`, `suites`, `metrics`, `assertion_results`, `judgments`, `events`, `recordings`, `monitors`, `production_calls`, `alerts`, `baselines`, `calibration_labels`, `benchmark_runs` |

---

## 4. Execution order

The migration is sequenced so the repo is never in a state where nothing runs.

1. **Branch and archive.** Tag v1 as `v1-archive`; move v1 docs to `docs/v1/`. Nothing is lost; the orchestration work stays readable.
2. **Strip.** Delete the agent-serving runtime and its handlers. Keep the app booting — health check green, auth working, providers importable.
3. **New schema.** Fresh migration series for the v2 tables.
4. **Adapter layer.** `TargetAdapter` protocol, capabilities, registry, WebSocket adapter, local echo agent, contract tests.
5. **Invert Pipecat.** Rebuild the pipeline as the caller: scripted policy first (no LLM in the loop), ground-truth logging, per-leg recording.
6. **First green test.** Scripted scenario → local echo agent → artifact bundle → one deterministic assertion. This is the moment v2 exists.
7. **Layer upward.** Channel simulator → personas → agentic policy → assertion catalog → metrics → judges → attribution → CLI polish.
8. **Reconnect the survivors.** Cost service, compliance retention/redaction, analytics, dashboard views.

Step 6 is the milestone that matters. Everything before it is demolition; everything after it is compounding.

---

## 5. What we keep from v1 that isn't code

- **Pipecat fluency.** Non-trivial and directly transferable — the caller pipeline is a Pipecat pipeline.
- **Provider integration knowledge.** Sarvam, OpenAI, telephony quirks, streaming behaviors, and the real-world latency characteristics of each. This is exactly the knowledge that makes good metrics and realistic personas.
- **India/DPDP context.** Becomes the compliance wedge for the enterprise ICP and the Indic testing differentiator.
- **The name and the repo.** Convox still reads correctly for a conversation-testing product; the sub-line changes, the identity doesn't.

## 6. What the pivot costs

Honest accounting:

- Roughly **60–70% of v1's application-layer code is deleted** — the agent runtime, its handlers, and the planned builder. The provider layer, database layer, auth, cost, compliance, and frontend shell survive.
- The public positioning resets: README, website, and any existing external descriptions of Convox all change meaning.
- The v1 architecture and tech-spec docs are superseded (archived, not deleted — they remain the record of why the orchestration bet was reconsidered).

Against that: the surviving substrate is the part that takes longest to build from scratch, and the deleted part is the part with twelve funded competitors.
