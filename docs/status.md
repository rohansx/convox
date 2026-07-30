# Convox — Status and Plan

> Last updated: July 2026
> Branch: `main` (mirrored to `master`) · 97 tests passing · lint clean
> Related: [Roadmap](roadmap.md) · [Architecture](architecture.md) · [Tech Spec](tech-spec.md) · [Features](features.md)

This is the working engineering document: what exists today, what it is proven to
do, where it is weak, and what happens next. The other docs describe the target
product; this one describes reality.

---

## 1. Where things stand

**Convox is a working voice-agent testing tool.** You can point it at an agent,
place a real spoken call, and get back a scored verdict with evidence. The
engine, the scenario format, the evaluation layer, and the CLI are built. The
audio path — the thing that separates this from a transcript-diffing script — is
built and tested end to end.

What it cannot do yet: talk to Retell or Vapi, drive an LLM-powered caller, run
judges, load-test, or monitor production. Those are next, and every one of them
sits on foundations that now exist.

| | |
|---|---|
| Production code | ~7,600 lines across `api/convox/` |
| Tests | ~1,200 lines, 97 tests (86 fast + 11 real-time audio calls) |
| Documentation | ~3,200 lines across `docs/` |
| Assertions | 40 registered (17 capability-gated, 5 judged and awaiting a backend) |
| Adapters | 2 (`websocket`, `websocket_audio`) |
| Codecs | G.711 µ-law, G.711 a-law, G.722, GSM |
| Noise / network profiles | 14 / 10 |
| Voice providers | 1 (the offline reference codec) |

---

## 2. What is built

### 2.1 The shape of it

```
        CLI  ──▶  spec loader  ──▶  run planner  ──▶  simulation  ──▶  evaluation
                  (YAML)            (× repeats)       (one call)       (scoring)
                                                          │                │
                                                    target adapter    artifact bundle
                                                          │
                                                  the agent under test
```

Every stage is a separate module with one job, and the artifact bundle between
simulation and evaluation is the contract: a call can be re-scored with better
assertions without being placed again.

### 2.2 Simulation engine — `convox/sim/`

| Module | What it does |
|---|---|
| `runner.py` | Executes one trial: connect, converse, record, package. Owns all real-time behaviour. |
| `policy/scripted.py` | Walks an ordered script. Turn discipline, barge-in scheduling, patience, hang-up. |
| `mouth.py` | How the caller speaks — `TextMouth` (JSON frames) and `AudioMouth` (synthesis → channel → continuous PCM). |
| `conversation.py` | Assembles agent turns from the event stream; independent transcription; interruption bookkeeping. |
| `audio_tracker.py` | Streaming VAD on the agent leg: turn edges, barge-in stop time, TTS overrun. |
| `channel.py` | Persona → actual damage (noise, codec, loss, jitter). Deterministic per seed. |
| `recorder.py` | The single writer of ground truth. Timeline, turns, audio, costs, WAV output. |

**The design decision everything rests on:** the caller's text is recorded *before*
it is synthesised. That ordering is why recognition accuracy is a diff against
known truth rather than an estimate, and why a mis-heard digit can be attributed
to the agent's ASR rather than guessed at.

### 2.3 Audio path — `convox/audio/`, `convox/voice/`

- `pcm.py` — format conversion, resampling, filtering, framing, mixing
- `codec.py` — G.711 µ-law/a-law, G.722, GSM implemented directly (the standard
  library's `audioop` was removed in Python 3.13)
- `impair.py` — 14 synthesised noise environments, SNR mixing, Gilbert-Elliott
  bursty packet loss, jitter, 10 named network profiles
- `analysis.py` — VAD, SNR estimation, clipping, truncation detection, spectral
  artefact scoring
- `voice/tone.py` — **the reference codec**: a matched TTS/STT pair that encodes
  text as tones inside the telephony passband

The reference codec is the load-bearing decision for testability. An audio
pipeline that can only run with a paid API key is a pipeline whose own tests
cannot run. This one is deterministic, offline, and degrades the way real
recognition degrades — clean audio and G.711 decode perfectly, 2 dB street noise
produces genuine substitution errors. It is explicitly *not* speech and not a
substitute for testing against real ASR; it is a reference instrument. Real
providers implement the same two protocols and drop in.

### 2.4 Evaluation — `convox/eval/`

- `registry.py` — assertion registration, capability gating, error isolation
- `assertions/deterministic.py` — transcript, slots, tools, timing, lifecycle, PII, compliance
- `assertions/audio.py` — barge-in, audio quality, recognition accuracy
- `assertions/judges.py` — five judged assertions, contract fixed, backend pending
- `metrics.py` — latency, dead air, turn shape, repetition, talk ratio, WER family, audio quality, interruptions, cost
- `wer.py` — Levenshtein alignment with edit backtrace, CER, entity error rate
- `normalize.py` — phone/date/time/currency/email normalisers, spoken-digit handling in English and Hindi
- `compare.py` — the threshold grammar (`lt`/`lte`/`gt`/`gte`/`eq`/`between` × percentile selectors)
- `evaluator.py` — orchestration, layer attribution, unsupported-assertion reporting

### 2.5 Everything else

- `convox/adapters/` — `TargetAdapter` protocol, capability declarations, text and audio WebSocket adapters, registry
- `convox/spec/` — strict YAML loading with file-located errors; format parsing lives on the models so the loader, API, and SDK cannot drift apart
- `convox/model/` — the nine domain nouns
- `convox/service/run.py` — run planning, bounded concurrency, `pass^k` aggregation
- `convox/report/junit.py` — JUnit XML; flaky is reported as failure, not rounded up to pass
- `convox/cli/` — `init`, `lint`, `run`, `target test`, `assertions`, `adapters`, plus scaffolding
- `convox/testing/` — two reference agents with injectable faults

### 2.6 The rules enforced in code, not just documented

These are the things that make the numbers worth reading, and each has a test
pinning it:

1. **Unsupported is never pass.** An assertion whose inputs the target cannot
   supply reports `unsupported`, and every run prints how many and why.
2. **Judged assertions do not silently succeed.** With no judge backend
   configured they report `unsupported` — not green.
3. **Infrastructure failure is not agent failure.** A call that never connected
   produces `ERROR` with zero assertion results, and exit code 3 rather than 1.
4. **Metrics say when they are estimates.** Talk ratio on a text channel carries
   `estimated: true`; percentiles over fewer than 20 samples carry
   `low_confidence: true`.
5. **Flaky is not green.** A scenario passing 3 of 5 repeats reports
   `pass^5 = 0.60` and fails the run.
6. **Determinism is a requirement.** Seeded channel simulation, scripted callers,
   and a shared virtual clock; a test asserts verdicts do not vary across repeats.

---

## 3. What is proven

`api/tests/` — 97 tests, no warnings, no flakes observed.

| Suite | Covers |
|---|---|
| `test_simulation.py` | Bundle completeness, turn ordering, tool capture, capability recording, infrastructure errors |
| `test_evaluation.py` | Comparison grammar, normalisers, each assertion family, and all six honesty rules |
| `test_self_check.py` | **Convox against known bugs** — catches exactly the injected faults and no others |
| `test_spec.py` | YAML round-trip, all three assertion shapes, strict rejection, suites, tags, content hashing |
| `test_cli.py` | Scaffolding, linting, exit codes (0/1/3/4), JUnit and JSON output |
| `test_audio.py` | PCM, four codecs, noise→WER degradation, SNR accuracy, truncation, artefacts, channel determinism, WER/CER/entity rates |
| `test_audio_call.py` | Real spoken calls: digit capture through audio, exact WER, truncation, artefacts, barge-in handling and timing, noise degradation end to end |

**The self-check is the most important suite.** It runs the full pipeline against
the reference agent with specific faults switched on and asserts Convox reports
*exactly* those faults — catching false negatives and, just as importantly, false
positives. A testing tool that cries wolf gets ignored.

### Bugs found and fixed while building

Worth recording, because four of them were the same shape — **waiting for an event
that never arrives**:

1. The agent's greeting was consumed as the reply to the caller's first
   utterance, shifting every turn by one and silently corrupting all latency
   measurements.
2. In fast mode the caller's timeline advanced while agent events used the wall
   clock; the two diverged. Fixed with a virtual clock both legs share.
3. Turn-end detection waited for a frame proving silence, but a finished agent
   sends nothing. Needed a timer.
4. Same again, mirrored: the caller only transmitted while speaking, so the
   agent's VAD never saw end-of-turn. Real lines carry audio continuously.
5. `ToneVoice` implements both TTS and STT and both protocols declared
   `cost_usd`, so one silently shadowed the other.
6. Codec ring-out in trailing silence was decoded as a phantom extra character.

---

## 4. Known gaps and limitations

Stated plainly, because a status document that only lists wins is marketing.

**Capability gaps**
- Only WebSocket targets. No Retell, Vapi, LiveKit, Pipecat, PSTN, or SIP.
- Scripted callers only — no LLM-driven caller pursuing a goal.
- No judge backend, so five assertions and every semantic claim are unmeasurable.
- No baselines, regression diffing, or GitHub Action.
- No load testing, production monitoring, replay, or dashboard.

**Implementation limitations**
- The reference codec is not speech. Everything downstream is exercised
  faithfully, but real ASR failure modes (homophones, accents, coarticulation)
  are not reproduced. Testing against real providers will surface issues this
  cannot.
- Comfort frames between utterances are pure silence; background noise is mixed
  into speech only. A real open line carries continuous ambience, which affects
  how an agent's VAD behaves.
- Resampling is linear with an FFT anti-alias filter — fine for reproducing
  narrowband loss, not archival quality.
- Indic normalisation is stubbed. The interface exists; matra/nukta/ZWJ handling
  and transliteration equivalence do not.
- Audio analysis is computed at capture time and carried in the bundle. Re-scoring
  recomputes assertions but not DSP.
- `endpoint.false_interrupt_count` infers overlap from turn timestamps rather than
  measuring true double-talk.
- Concurrency is per-process; there is no worker fleet or queue yet.

**Repository debt**
- `Dockerfile`, `docker-compose.yml`, `docker-entrypoint.sh`, `web/`, and
  `api/migrations/` are all left over from the orchestration platform and assume
  a server that no longer exists. Harmless — the engine has no database
  dependency — but misleading to a cold reader.
- The FastAPI app serves only `/health`; the REST surface in the tech spec is
  unbuilt.

---

## 5. The plan

Ordered by what earns the next user. Each phase is independently useful.

### Phase A — Reach real agents (next)

The single biggest gap: Convox can only talk to agents speaking its own protocol.
Nobody's production agent does.

- **Retell adapter** — create web call via API, stream PCM, consume the tool-call
  and transcript side channel, snapshot agent config per run
- **Vapi adapter** — same shape, assistant overrides as dynamic variables
- **Adapter contract tests** — a shared suite every adapter must pass, run
  nightly against sandbox accounts, so capability declarations stay honest
- **Real voice providers** — Sarvam, ElevenLabs, Deepgram, and local
  Whisper/Piper behind the existing `TTS`/`STT` protocols

*Exit:* a developer with a Retell agent finds a real bug in under ten minutes.
This is the roadmap's Phase 1 exit criterion and the thing that makes Convox
usable by anyone but us.

### Phase B — Become part of the workflow

- Baselines, run diffing, new-vs-pre-existing failure classification
- `--fail-on regression|any-failure|threshold`
- GitHub Action: run on PR, comment results, upload artifacts
- Scenario matrices (persona × language × noise sweeps) and cost projection

*Exit:* a team gates merges on Convox and a prompt regression is caught in a PR.

### Phase C — Semantic evaluation

- Judge backend with temperature 0, self-consistency voting, and required turn-id
  citations; a verdict without valid evidence is rejected and retried
- Deterministic pre-filters so judges never contradict a failed tool assertion
- **Judge calibration**: label trials once, report precision/recall/F1/κ per
  rubric and model, and gate CI on it
- Agentic caller policy — LLM with goal, private fact sheet, and disclosure policy
- Scenario generation from a prompt, and from production transcripts

*Exit:* semantic claims are measurable, and their reliability is itself a number.

### Phase D — Production observability

- Ingest via platform webhooks, OTLP, and SDK; normalise to the same call shape
- Score production calls with the same evaluators (making test-vs-production a
  valid comparison)
- Monitors, cohorts, drift detection, outlier surfacing, Slack alerts
- **Replay**: production call → scenario file, in one click

*Exit:* the loop closes — a bad real call becomes a permanent regression test.

### Phase E — Depth and differentiation

- Load testing: ramp profiles, worker fleet, degradation curves, ceiling discovery
- **Indic depth**: code-switching personas, proper Indic WER normalisation,
  per-language breakdowns, Indian telephony profiles
- PSTN/SIP adapters, IVR/DTMF traversal, transfer testing
- Red-team suite: voice prompt injection, jailbreaks, PII extraction
- Control plane: REST API, Postgres persistence, worker queue, dashboard

### Phase F — Credibility

- Public reproducible benchmark across Retell/Vapi/Pipecat/LiveKit/ElevenLabs
- Air-gapped deployment as a CI-tested configuration
- Enterprise: RBAC, SSO, compliance evidence packs

---

## 6. Immediate next actions

If work resumes tomorrow, in this order:

1. **Repo cleanup** — delete or quarantine the orchestration leftovers
   (`Dockerfile`, `docker-compose.yml`, `web/`, `api/migrations/`) so the tree
   matches the product. Small, and it stops misleading readers.
2. **Retell adapter** plus contract tests — the highest-leverage single change.
3. **One real voice provider** (Sarvam or Deepgram) to validate that the
   `TTS`/`STT` protocols hold against a real API, and to find out what the
   reference codec has been hiding.
4. **Baselines and the GitHub Action** — cheap once runs are stored, and it turns
   a one-off command into a habit.

---

## 7. Open decisions

- **Does the benchmark live in this repo?** Publishing comparative vendor numbers
  from the same repo invites bias accusations; a separate repo costs discoverability.
- **Cloud offering, and when.** The open-core split is described in the
  positioning doc but nothing is committed.
- **Where per-scenario target routing goes.** Audio scenarios and text scenarios
  currently need separate `convox run` invocations; a `target:` field on a
  scenario would fix it but adds a resolution rule.
- **Whether the reference codec ships in the published package** or stays a test
  fixture. It is genuinely useful for offline development, but it needs to stay
  clearly labelled so nobody mistakes it for speech testing.

---

## 8. Running it

```bash
cd api && uv sync --all-extras

# Text channel
uv run python -m convox.testing.reference_agent &
uv run convox init my-tests && cd my-tests
uv run convox run scenarios/

# Audio channel — recognition, barge-in, truncation
uv run python -m convox.testing.audio_agent --bug truncate_audio &
uv run convox run scenarios/audio/ --target demo-audio

# Tests
uv run pytest                                        # everything (~8 min, real-time calls)
uv run pytest --ignore=tests/test_audio_call.py      # fast suite (~11 s)
uv run ruff check
```

Exit codes: `0` pass · `1` assertion failure · `2` regression · `3` infrastructure
error · `4` usage. CI can tell a broken agent from a broken harness.

## 9. Branches

| Branch | Purpose |
|---|---|
| `main` | Canonical. |
| `master` | Mirror of `main`, kept in sync on request. |
| `archive/orchestration-platform` | The original voice-orchestration codebase, preserved unchanged. |
