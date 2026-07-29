# Convox v2 — Roadmap

> Last updated: July 2026
> Status: Pivot definition
> Related: [Features](features.md) · [Positioning](positioning.md) · [Migration from v1](migration-from-v1.md)

Phases are ordered by **what earns the next user**, not by what's architecturally tidy. The wedge is a developer running one command and finding a real bug.

---

## Phase 0 — Foundation (weeks 1–3)

Strip v1 down to the substrate and stand up the new spine.

- Remove the agent-serving runtime; keep providers, config, auth, database, cost, compliance ([details](migration-from-v1.md))
- New migration series: teams, targets, personas, scenarios, runs, trials, turns, metrics, assertion_results
- Redis Streams job queue with consumer groups and lease/watchdog semantics
- Object store (MinIO) artifact layer
- `TargetAdapter` protocol + capabilities model + contract test harness
- WebSocket adapter (the universal one) and a local echo agent for testing ourselves

**Exit criteria:** `convox run` places a scripted call against a local WebSocket agent and stores a complete artifact bundle.

## Phase 1 — The wedge: it finds a real bug (weeks 4–10)

Everything needed for the 10-minute first-value experience.

- Simulation worker: scripted + agentic + hybrid caller policies
- Ground-truth capture, per-leg recording, event timeline
- Channel simulator: codec, noise mixing, resampling (deterministic, seeded)
- Persona library (~40 shipped) with the noise profile set
- Adapters: WebRTC, Pipecat, **Retell**, **Vapi**
- Deterministic assertion catalog (transcript, slot, tool, timing, lifecycle, audio)
- Metrics: latency, turn-taking, WER/slot accuracy, audio quality
- Judges with evidence citation + self-consistency voting
- Layer attribution
- `pass^k` and flake classification
- CLI with rich output; JUnit + JSON reports
- Cost tracking and budget guards
- `convox generate --from-prompt`

**Exit criteria:** a developer with a Retell or Vapi agent goes from install to a failing test that reveals a genuine bug in under 10 minutes, without reading past the README.

## Phase 2 — It runs in CI (weeks 8–13, overlapping)

- Baselines and run diffing (new vs pre-existing failures, metric deltas with significance)
- `--fail-on regression|any-failure|threshold`
- **GitHub Action** with PR comments, artifacts, and links to failed-trial recordings
- GitLab / Jenkins recipes
- Scenario linting and cost projection
- Dashboard v1: run list, run detail, **trial detail** (waveform + synchronized transcript + turn timeline + assertion evidence)

**Exit criteria:** a team gates merges on Convox and a prompt regression is caught in a pull request.

## Phase 3 — Public launch (weeks 12–16)

- Docs site: quickstart per platform, scenario cookbook, metric definitions
- Example repo: deliberately buggy demo agent + the suite that catches its bugs
- Self-test suite (reference agent with injected bugs; determinism gate) — shipped and visible, because a testing tool must prove its own reliability
- Show HN + Product Hunt launch
- Platform integration guides ("Testing your Retell agent", "…your Vapi assistant", "…your Pipecat bot")

**Exit criteria:** 500+ GitHub stars, 10 teams running in CI, 3 external adapter PRs.

## Phase 4 — Production observability (months 4–6)

- Ingest: platform webhooks (Retell, Vapi, Bland, ElevenLabs), OTLP, SDK
- Uniform scoring of production calls with the same evaluators
- Monitors, cohorts, drift detection, outlier surfacing
- Alerts: Slack, webhook, email — every alert linking to a listenable call
- **Replay**: production call → scenario file; batch replay against a candidate version
- PII redaction on ingest; retention policies

**Exit criteria:** the observe→simulate loop closes — a real bad call becomes a permanent regression test in one click.

## Phase 5 — Depth and differentiation (months 5–9)

- **Load testing**: ramp profiles, distributed workers, degradation curves, ceiling discovery
- **Indic depth**: code-switching personas, Indic WER/CER normalizers, per-language breakdowns, Indian telephony profiles
- Adapters: PSTN (Twilio/Exotel/Plivo), LiveKit, ElevenLabs, Bland
- IVR/DTMF: send/detect, path assertions, transfer testing
- Red-team suite: prompt injection over voice, jailbreaks, PII extraction, hostile callers
- **Judge calibration**: labeling UI, F1/κ reporting, CI gate
- Scenario matrices, tool mocking/fixtures
- pytest plugin, MCP server
- Teams/projects, audit log

**Exit criteria:** 2,000+ stars (past every dedicated OSS competitor combined); first inbound enterprise self-host conversations.

## Phase 6 — The benchmark (months 7–10)

- Reference agent implemented identically across Retell, Vapi, Pipecat, LiveKit, ElevenLabs, Bland
- Standard suite: reliability, latency, barge-in, noise, multilingual, safety
- Public leaderboard with published methodology, raw recordings, and one-command reproduction
- Versioned methodology; results state harness and model versions

**Exit criteria:** the benchmark is cited by someone we didn't ask — ideally a platform vendor.

## Phase 7 — Enterprise (months 9–15)

- Air-gapped deployment as a supported, CI-tested configuration (local Whisper/Piper/vLLM)
- Helm chart, horizontal worker autoscaling
- RBAC, SSO (OIDC/SAML)
- Compliance profiles (DPDP, HIPAA, GDPR) and signed evidence packs
- Regression bisect, coverage analysis, IVR crawling, shadow comparison
- Plugin registry

**Exit criteria:** first paid enterprise contracts; 5,000+ stars.

---

## Sequencing principles

1. **CLI before dashboard.** The first users are developers who convert on a terminal command. A dashboard built first would be a dashboard with nothing to show.
2. **Deterministic before judged.** Ship the assertions that can't be wrong first; they build the trust that makes people tolerate judges later.
3. **Two adapters deep before five adapters wide.** Retell and Vapi cover most of the reachable market; breadth is a PR magnet once the contract tests exist.
4. **Testing before observing.** Testing is the acute pain and the CI habit. Observability is the retention feature, and it's worth more once the same evaluators are already trusted.
5. **The benchmark waits for credibility.** Publishing comparative numbers before the harness is provably stable would burn the one asset a small project can't rebuy.

## Explicit non-goals for the first year

- Building, hosting, or running anyone's voice agent (that was v1)
- Being a telephony provider
- General-purpose text LLM evals (promptfoo and deepeval own that)
- Workforce-management style call-center analytics
- A hosted cloud offering before the self-hosted product is genuinely complete

## Known risks on this plan

| Risk | Where it bites | Response |
|---|---|---|
| Adapter maintenance load grows with platform count | Phase 5 onward | Capability declarations + nightly contract tests; community-owned adapters with a clear ownership file |
| Judge cost makes production scoring expensive | Phase 4 | Deterministic pre-filters, sampling policies, content-hash caching, small-model defaults |
| Our own flakiness undermines trust | Everywhere | The self-test suite is a Phase 3 deliverable, not a nice-to-have |
| Platforms bundle deeper native QA | Phase 4+ | Neutrality and self-hosting are structural advantages they can't copy; lean harder on cross-platform and compliance |
| Scope drifts back toward building agents | Phase 5+ | The non-goals list above is a design constraint, reviewed at each phase boundary |
