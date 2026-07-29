# Convox — Technical Specification

> Last updated: July 2026
> Status: Pre-implementation design
> Related: [Architecture](architecture.md) · [Scenario Spec](scenario-spec.md) · [Metrics](metrics.md) · [Features](features.md)

---

## 1. Stack

| Layer | Technology | Notes |
|---|---|---|
| Caller pipeline | **Pipecat** | Drives the synthetic caller; carried over from v1 |
| API / control plane | **Python 3.12+ / FastAPI** | Async throughout; no ORM |
| Workers | **Python asyncio** | Simulation + evaluation fleets |
| Database | **PostgreSQL 17** via asyncpg | Raw SQL, repository pattern |
| Migrations | **dbmate** | Plain SQL, unchanged from v1 |
| Queue / state | **Redis 7** | Streams + consumer groups; live state; rate-limit buckets |
| Object store | **MinIO / S3** | Recordings and artifacts |
| DSP | **numpy / scipy / soxr / av** | Noise, codecs, resampling, analysis |
| ASR scoring | **jiwer** + custom Indic normalizers | WER/CER |
| Frontend | **Vite / React 19 / TS / Tailwind v4 / shadcn** | SPA, served by FastAPI |
| Audio UI | **wavesurfer.js** | Waveform + synchronized transcript |
| CLI | **Typer + Rich** | `convox` |
| Packaging | **uv** (Python), **Bun** (web) | Unchanged from v1 |
| Containers | **Docker Compose** + Helm chart | Monolith and scaled topologies |
| Tracing | **OpenTelemetry** | Both emitted and ingested |

---

## 2. Repository layout

```
convox/
├── api/
│   ├── convox/
│   │   ├── app.py                  # FastAPI factory
│   │   ├── config.py               # Pydantic settings
│   │   │
│   │   ├── database/               # asyncpg pool, Redis client        [v1]
│   │   ├── crypto/                 # JWT, credential encryption        [v1]
│   │   ├── middleware/             # auth, CORS, logging, team scope   [v1]
│   │   ├── repository/             # raw SQL data access               [v1 pattern]
│   │   │
│   │   ├── model/                  # Pydantic domain models
│   │   │   ├── target.py  persona.py  scenario.py  suite.py
│   │   │   ├── run.py     trial.py    turn.py      event.py
│   │   │   ├── assertion.py  metric.py  judgment.py
│   │   │   └── monitor.py  alert.py   call.py
│   │   │
│   │   ├── handler/                # HTTP routes
│   │   │   ├── targets.py  personas.py  scenarios.py  suites.py
│   │   │   ├── runs.py     trials.py    monitors.py   alerts.py
│   │   │   ├── ingest.py   benchmark.py analytics.py  health.py
│   │   │
│   │   ├── adapters/               # ── target adapters ──
│   │   │   ├── base.py             # TargetAdapter protocol, capabilities
│   │   │   ├── websocket.py  webrtc.py  sip.py  pstn.py
│   │   │   ├── retell.py  vapi.py  livekit.py  pipecat.py
│   │   │   ├── elevenlabs.py  bland.py  chat.py
│   │   │   └── registry.py
│   │   │
│   │   ├── providers/              # STT / LLM / TTS plugins           [v1]
│   │   │
│   │   ├── sim/                    # ── simulation engine ──
│   │   │   ├── worker.py           # lease loop, watchdog
│   │   │   ├── caller.py           # Pipecat caller pipeline
│   │   │   ├── policy/             # scripted.py, agentic.py, hybrid.py
│   │   │   ├── persona.py          # persona → pipeline config
│   │   │   ├── turn_control.py     # barge-in scheduling, pauses, backchannel
│   │   │   ├── recorder.py         # per-leg recording + timeline
│   │   │   └── channel/            # ── channel simulator ──
│   │   │       ├── noise.py  codec.py  network.py  device.py
│   │   │
│   │   ├── eval/                   # ── evaluation engine ──
│   │   │   ├── worker.py
│   │   │   ├── timeline.py         # normalize events → turns
│   │   │   ├── audio/              # snr.py, clipping.py, artifacts.py, truncation.py
│   │   │   ├── asr/                # wer.py, normalizers/{en,hi,ta,...}.py, slots.py
│   │   │   ├── metrics/            # latency.py, turntaking.py, quality.py, task.py
│   │   │   ├── assertions/         # registry + built-in assertion implementations
│   │   │   ├── judges/             # rubric.py, voting.py, evidence.py, calibration.py
│   │   │   └── attribution.py      # which layer failed
│   │   │
│   │   ├── generate/               # scenario generation from prompts / transcripts
│   │   ├── load/                   # ramp profiles, coordinator
│   │   ├── observe/                # ingest normalizers, monitors, drift, alerts
│   │   ├── replay/                 # production call → scenario
│   │   ├── report/                 # JUnit, HTML, JSON, PR comment
│   │   ├── service/                # run planning, cost, baselines, aggregation
│   │   ├── compliance/             # retention, redaction, audit             [v1]
│   │   └── mcp/                    # MCP server
│   │
│   ├── cli/                        # Typer app → `convox`
│   ├── migrations/                 # dbmate SQL
│   └── tests/
│
├── web/                            # React dashboard                         [v1 shell]
├── personas/                       # shipped persona library (YAML)
├── scenarios/examples/             # example suites
├── assets/noise/                   # background noise profiles
├── benchmark/                      # reference agent + standard suite
├── action/                         # GitHub Action
└── docs/
```

---

## 3. Core domain models

```python
# model/target.py
class Target(BaseModel):
    id: UUID
    team_id: UUID
    name: str
    kind: Literal["websocket","webrtc","sip","pstn","retell","vapi",
                  "livekit","pipecat","elevenlabs","bland","chat"]
    config: dict          # adapter-specific, credentials referenced by key id
    credential_id: UUID | None
    capabilities: AdapterCapabilities
    config_snapshot: dict | None   # agent prompt/flow captured at run time
    created_at: datetime

class AdapterCapabilities(BaseModel):
    agent_transcript: bool      # platform exposes what it heard
    tool_calls: bool
    dtmf: bool
    recording: bool
    traces: bool
    inbound: bool               # can receive a call from the agent
    max_concurrency: int | None
```

```python
# model/persona.py
class Persona(BaseModel):
    id: UUID; name: str
    voice: VoiceConfig                 # provider, voice_id, language, accent
    language: str; secondary_language: str | None
    code_switch: CodeSwitchConfig | None   # density, granularity
    speech_rate: float = 1.0           # 0.5–2.0
    volume_db: float = 0.0
    emotion: Emotion = "calm"
    disfluency: Level = "none"         # none|low|medium|high
    pause: PauseConfig                 # thinking pauses, max gap
    backchannel: BackchannelConfig
    comprehension: Level = "normal"
    verbosity: Level = "normal"
    interruption: InterruptionConfig   # style, probability, delay_ms
    patience_turns: int = 12
    hangup: HangupPolicy | None
    environment: EnvironmentConfig     # noise profile, snr_db, device, room
    channel: ChannelConfig             # codec, sample_rate, network profile
```

```python
# model/scenario.py
class Scenario(BaseModel):
    id: UUID; name: str; description: str | None
    persona: str | Persona
    mode: Literal["scripted","agentic","hybrid"] = "hybrid"
    caller: CallerSpec                 # goal, facts, opening, script, behavior
    assert_: list[AssertionSpec] = Field(alias="assert")
    repeat: int = 1
    timeout_s: int = 300
    max_turns: int = 40
    max_cost_usd: float | None
    tags: list[str] = []
    matrix: dict[str, list] | None     # persona/language/noise sweeps
    fixtures: FixtureSpec | None       # tool mocks, setup/teardown
```

```python
# model/trial.py
class Trial(BaseModel):
    id: UUID; run_id: UUID; scenario_id: UUID; repeat_index: int
    status: Literal["queued","running","completed","failed","infrastructure_error","skipped"]
    verdict: Literal["pass","fail","flaky","unsupported"] | None
    seed: int
    started_at: datetime | None; ended_at: datetime | None
    duration_ms: int | None
    ended_by: Literal["agent","caller","timeout","error"] | None
    artifacts: ArtifactRefs            # object-store keys
    cost_usd: float
    suspected_layer: Literal["stt","llm","tts","tool","timing","none"] | None
```

```python
# model/turn.py — the unit everything is measured against
class Turn(BaseModel):
    trial_id: UUID; index: int
    speaker: Literal["caller","agent"]
    ground_truth_text: str | None      # caller turns only — known exactly
    heard_text: str | None             # what the *other* side transcribed
    convox_transcript: str | None      # our independent STT pass
    speech_start_ms: int; speech_end_ms: int   # relative to call start
    first_audio_ms: int | None         # agent turns: TTFB
    interrupted: bool
    interrupted_at_ms: int | None
    tool_calls: list[ToolCall] = []
```

---

## 4. Database schema

24 tables. Key ones in full; the rest summarized.

```sql
-- migrations/20260801000001_targets.sql
CREATE TABLE targets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id         UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,
    config          JSONB NOT NULL DEFAULT '{}',
    credential_id   UUID REFERENCES credentials(id),
    capabilities    JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, name)
);

CREATE TABLE runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id         UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    target_id       UUID NOT NULL REFERENCES targets(id),
    suite_name      TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',
    git_sha         TEXT,
    git_ref         TEXT,
    is_baseline     BOOLEAN NOT NULL DEFAULT false,
    baseline_run_id UUID REFERENCES runs(id),
    target_snapshot JSONB,               -- agent prompt/config at run time
    trial_count     INT NOT NULL DEFAULT 0,
    passed_count    INT NOT NULL DEFAULT 0,
    budget_usd      NUMERIC(10,4),
    cost_usd        NUMERIC(10,4) NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_runs_target_created ON runs(target_id, created_at DESC);
CREATE INDEX idx_runs_baseline ON runs(target_id) WHERE is_baseline;

CREATE TABLE trials (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    scenario_name     TEXT NOT NULL,
    scenario_hash     TEXT NOT NULL,     -- content hash → cache + change detection
    repeat_index      INT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'queued',
    verdict           TEXT,
    seed              BIGINT NOT NULL,
    persona_name      TEXT,
    language          TEXT,
    started_at        TIMESTAMPTZ,
    ended_at          TIMESTAMPTZ,
    duration_ms       INT,
    ended_by          TEXT,
    turn_count        INT,
    suspected_layer   TEXT,
    cost_usd          NUMERIC(10,6) NOT NULL DEFAULT 0,
    artifacts         JSONB NOT NULL DEFAULT '{}',
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_trials_run ON trials(run_id);
CREATE INDEX idx_trials_scenario ON trials(scenario_name, created_at DESC);

CREATE TABLE turns (
    id                 BIGSERIAL PRIMARY KEY,
    trial_id           UUID NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    idx                INT NOT NULL,
    speaker            TEXT NOT NULL,
    ground_truth_text  TEXT,
    heard_text         TEXT,
    convox_transcript  TEXT,
    speech_start_ms    INT NOT NULL,
    speech_end_ms      INT NOT NULL,
    first_audio_ms     INT,
    interrupted        BOOLEAN NOT NULL DEFAULT false,
    interrupted_at_ms  INT,
    tool_calls         JSONB NOT NULL DEFAULT '[]',
    UNIQUE (trial_id, idx)
);

CREATE TABLE metrics (
    trial_id   UUID NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    value      DOUBLE PRECISION,
    unit       TEXT,
    available  BOOLEAN NOT NULL DEFAULT true,   -- false = platform can't provide
    detail     JSONB,
    PRIMARY KEY (trial_id, name)
);

CREATE TABLE assertion_results (
    id           BIGSERIAL PRIMARY KEY,
    trial_id     UUID NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    spec         JSONB NOT NULL,
    kind         TEXT NOT NULL,          -- deterministic | judge
    status       TEXT NOT NULL,          -- pass | fail | unsupported | error
    soft         BOOLEAN NOT NULL DEFAULT false,
    actual       JSONB,
    expected     JSONB,
    evidence     JSONB,                  -- turn ids, timestamps, diffs
    message      TEXT
);

CREATE TABLE judgments (
    id            BIGSERIAL PRIMARY KEY,
    trial_id      UUID NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    rubric        TEXT NOT NULL,
    rubric_hash   TEXT NOT NULL,
    model         TEXT NOT NULL,
    votes         JSONB NOT NULL,        -- [{verdict, rationale, cited_turns}]
    verdict       TEXT NOT NULL,
    confidence    DOUBLE PRECISION,      -- vote agreement
    human_label   TEXT,                  -- for calibration
    cost_usd      NUMERIC(10,6)
);
```

Remaining tables: `teams`, `users`, `api_keys`, `credentials`, `personas`, `scenarios`, `suites`, `suite_scenarios`, `events` (fine-grained timeline), `recordings`, `monitors`, `monitor_calls`, `production_calls`, `alerts`, `alert_deliveries`, `baselines`, `calibration_labels`, `benchmark_runs`, `audit_log`, `cost_entries`.

**Partitioning.** `turns`, `events`, `metrics`, and `production_calls` are partitioned monthly by `created_at` — production monitoring generates far more rows than testing, and retention deletion becomes a partition drop instead of a mass `DELETE`.

---

## 5. Target adapter interface

```python
# adapters/base.py
class AudioChannel(Protocol):
    async def send(self, pcm: bytes) -> None: ...
    def receive(self) -> AsyncIterator[bytes]: ...
    sample_rate: int
    encoding: Literal["pcm16","mulaw","alaw"]

@dataclass
class SideChannelEvent:
    kind: Literal["agent_transcript","tool_call","tool_result",
                  "agent_speech_start","agent_speech_end","transfer","error","span"]
    at_ms: int
    payload: dict

class TargetAdapter(ABC):
    capabilities: AdapterCapabilities

    @abstractmethod
    async def connect(self, ctx: TrialContext) -> AudioChannel: ...
    @abstractmethod
    async def disconnect(self) -> None: ...
    def events(self) -> AsyncIterator[SideChannelEvent]:
        return empty_iterator()
    async def send_dtmf(self, digits: str) -> None:
        raise UnsupportedCapability("dtmf")
    async def snapshot_config(self) -> dict | None:
        return None                      # agent prompt/version, if the API exposes it
```

Reference implementation sketch:

```python
# adapters/retell.py
class RetellAdapter(TargetAdapter):
    capabilities = AdapterCapabilities(
        agent_transcript=True, tool_calls=True, dtmf=True,
        recording=True, traces=False, inbound=True, max_concurrency=None,
    )

    async def connect(self, ctx):
        resp = await self.http.post("/v2/create-web-call", json={
            "agent_id": self.agent_id,
            "retell_llm_dynamic_variables": ctx.dynamic_variables,
            "metadata": {"convox_trial_id": str(ctx.trial_id)},
        })
        self._call_id = resp["call_id"]
        self._ws = await websockets.connect(resp["web_call_url"])
        return RetellChannel(self._ws, sample_rate=24000, encoding="pcm16")

    async def snapshot_config(self):
        agent = await self.http.get(f"/get-agent/{self.agent_id}")
        return {"version": agent["version"], "prompt": agent.get("general_prompt"),
                "llm": agent.get("llm_websocket_url"), "voice": agent.get("voice_id")}
```

**Contract tests.** Every adapter must pass a shared suite: connect/disconnect cleanly, stream bidirectional audio for 10s, report capabilities truthfully (declaring `tool_calls=True` and never emitting one is a test failure), and surface errors as typed exceptions rather than hangs.

---

## 6. Simulation worker

```python
async def run_trial(trial: Trial) -> TrialResult:
    rng = random.Random(trial.seed)
    persona = resolve_persona(trial.scenario.persona)
    adapter = adapter_registry.build(trial.target)

    recorder = Recorder(trial.id)          # per-leg WAV + timeline.jsonl
    channel_sim = ChannelSimulator(persona.channel, persona.environment, rng)
    policy = build_policy(trial.scenario, persona, rng)   # scripted | agentic | hybrid
    caller = CallerPipeline(persona, policy, channel_sim, recorder)

    async with adapter_connection(adapter, trial, timeout=trial.scenario.timeout_s) as ch:
        events = asyncio.create_task(consume_events(adapter, recorder))
        try:
            await caller.converse(
                channel=ch,
                max_turns=trial.scenario.max_turns,
                deadline=monotonic() + trial.scenario.timeout_s,
                cost_cap=trial.scenario.max_cost_usd,
            )
        finally:
            events.cancel()

    return await recorder.finalize()       # upload artifacts, return refs + cost
```

### 6.1 Caller policies

**Scripted** — walks an ordered step list:

```python
steps = [
    Say("Hi, I need to refill my prescription"),
    WaitForAgent(),
    Say("Metformin, 500 milligrams"),
    BargeIn(after_ms=800, text="No no, 850 milligrams"),
    Dtmf("1"),
    WaitSilence(ms=3000),          # probe silence-timeout handling
    Say("Yes that's correct"),
    Hangup(),
]
```

**Agentic** — an LLM with a constrained system prompt containing the goal, the fact sheet with its disclosure policy, persona behavioral rules, and a hard instruction to emit exactly one caller utterance per turn plus a control token (`<continue>`, `<goal_met>`, `<give_up>`). Turn count, cost, and wall-clock are all capped.

**Hybrid** — scripted through step *n*, then hands the accumulated context to the agentic policy.

### 6.2 Turn control and barge-in

Barge-in must be *scheduled*, not reactive, or the measurement is meaningless:

```python
async def maybe_barge_in(agent_speech_start_ms: int, persona, rng) -> int | None:
    if rng.random() > persona.interruption.probability:
        return None
    delay = rng.randint(*persona.interruption.delay_ms_range)   # e.g. 400–1200
    return agent_speech_start_ms + delay
```

The recorder stamps `caller_speech_start_ms`; the agent's audio stream is watched for the moment output stops. `barge_in.stop_ms = agent_audio_stop_ms − caller_speech_start_ms`, and any agent audio emitted in that window is the **TTS overrun**.

### 6.3 Channel simulator

```python
def process(pcm: np.ndarray, cfg, rng) -> np.ndarray:
    x = resample(pcm, cfg.target_sample_rate)          # soxr
    if cfg.noise_profile:
        x = mix_noise(x, load_noise(cfg.noise_profile, rng), snr_db=cfg.snr_db)
    if cfg.device_ir:
        x = convolve(x, load_ir(cfg.device_ir))
    x = codec_roundtrip(x, cfg.codec)                  # g711u/g711a/g722/gsm/opus
    if cfg.network:
        x = apply_network(x, cfg.network, rng)         # loss (Gilbert-Elliott), jitter
    return x
```

Deterministic for a fixed seed — a hard requirement for reproducible CI.

---

## 7. Evaluation engine

### 7.1 Assertion registry

```python
@assertion("latency.response_ms")
def latency_response_ms(trial: TrialArtifacts, spec: dict) -> AssertionResult:
    samples = [t.first_audio_ms - prev.speech_end_ms
               for prev, t in agent_turn_pairs(trial)]
    if not samples:
        return AssertionResult.unsupported("no measurable agent turns")
    actual = {"p50": pct(samples,50), "p90": pct(samples,90),
              "p95": pct(samples,95), "max": max(samples)}
    return compare_numeric(actual, spec, evidence={"samples": samples})
```

Every assertion returns one of `pass` / `fail` / `unsupported` / `error`, always with `evidence` — the turn IDs, values, and diffs that justify the verdict. `unsupported` never counts as a pass; a run reports the count of unmeasurable assertions explicitly.

### 7.2 Slot capture (ground truth in action)

```python
@assertion("slot.captured")
def slot_captured(trial, spec):
    field, expected, norm = spec["field"], spec["value"], spec.get("normalize","default")
    spoken = find_ground_truth_containing(trial, field)      # what WE said
    heard  = extract_from_agent(trial, field)                # tool arg or readback
    n = normalizer(norm, language=trial.language)
    ok = n(heard) == n(expected)
    layer = None if ok else attribute_slot_failure(trial, spoken, heard, expected)
    return AssertionResult(
        status="pass" if ok else "fail",
        actual=heard, expected=expected,
        evidence={"spoken_ground_truth": spoken,
                  "agent_heard": agent_transcript_for(trial, spoken),
                  "suspected_layer": layer},
    )
```

### 7.3 Judges

```python
async def judge(trial, rubric: str, cfg: JudgeConfig) -> Judgment:
    if cached := judge_cache.get(hash(trial.transcript_digest, rubric, cfg.model)):
        return cached
    votes = await asyncio.gather(*[
        call_judge(trial, rubric, cfg.model, temperature=0, shuffle_seed=i)
        for i in range(cfg.votes)          # default 3
    ])
    votes = [v for v in votes if valid_citations(v, trial)]   # must cite real turns
    if len(votes) < cfg.min_valid:
        return Judgment(verdict="error", reason="judge failed to cite evidence")
    verdict = majority(v.verdict for v in votes)
    return Judgment(verdict=verdict,
                    confidence=agreement_ratio(votes),
                    votes=votes)
```

Judge prompts are versioned by hash; changing a rubric invalidates its cache and flags affected calibration data as stale.

### 7.4 Calibration

```
convox label --run 7f3a1c --sample 50      # human labels in the dashboard/TUI
convox calibrate --rubric goal_achieved
```

```
Rubric: goal_achieved   model: claude-sonnet-5   votes: 3
  n=50 human-labelled trials
  precision 0.91   recall 0.86   F1 0.88   Cohen κ 0.79
  disagreements: 6  (4 false-fail, 2 false-pass)  → convox calibrate --show-disagreements
```

Calibration results are stored per `(rubric_hash, model)`, surfaced next to every judged assertion in the UI, and optionally gated in CI (`--min-f1 0.8`).

### 7.5 Layer attribution

```python
def attribute(trial, failed: AssertionResult) -> Layer:
    for turn in caller_turns(trial):
        heard = agent_heard_text(trial, turn)                 # platform or our STT
        if heard and wer(turn.ground_truth_text, heard, lang=trial.language) > 0.15:
            return "stt"
    if timing_violation(trial):
        return "timing"
    if tool_expected_but_absent(trial, failed):
        return "tool"
    if agent_audio_degraded(trial):                           # truncation/artifacts
        return "tts"
    return "llm"
```

---

## 8. Run planning and aggregation

```python
def plan_run(suite, target, options) -> list[Trial]:
    trials = []
    for scenario in suite.scenarios:
        for variant in expand_matrix(scenario):               # persona/lang/noise sweeps
            for i in range(options.repeat or variant.repeat):
                trials.append(Trial(scenario=variant, repeat_index=i,
                                    seed=stable_seed(variant, i, options.seed)))
    enforce_budget(trials, options.budget_usd)
    return trials
```

Aggregation:

```
pass^k(scenario)      = passed_repeats / total_repeats
scenario verdict      = pass   if pass^k == 1
                        flaky  if 0 < pass^k < 1
                        fail   if pass^k == 0
run verdict           = per --fail-on policy against the baseline diff
```

Baseline diff classifies each scenario as `new_failure`, `fixed`, `still_failing`, `still_passing`, `newly_flaky`, plus per-metric deltas with a two-sample significance test so a 3ms latency wobble isn't reported as a regression.

---

## 9. REST API

```
Auth:  Authorization: Bearer <api_key|jwt>       Scope: X-Convox-Team
```

**Targets**
```
GET    /v1/targets                     POST   /v1/targets
GET    /v1/targets/{id}                PATCH  /v1/targets/{id}
DELETE /v1/targets/{id}                POST   /v1/targets/{id}/test
POST   /v1/targets/{id}/snapshot
```

**Personas / scenarios / suites**
```
GET|POST      /v1/personas             GET|PATCH|DELETE /v1/personas/{id}
POST          /v1/personas/{id}/audition          → sample audio
GET|POST      /v1/scenarios            GET|PATCH|DELETE /v1/scenarios/{id}
POST          /v1/scenarios/validate
POST          /v1/scenarios/generate   { from_prompt | from_transcripts, count }
GET|POST      /v1/suites               GET|PATCH|DELETE /v1/suites/{id}
```

**Runs and trials**
```
POST   /v1/runs                { target_id, suite|scenarios, repeat, budget_usd, git_sha }
GET    /v1/runs?target_id=&limit=
GET    /v1/runs/{id}                       DELETE /v1/runs/{id}
POST   /v1/runs/{id}/cancel
GET    /v1/runs/{id}/trials
GET    /v1/runs/{id}/diff?baseline={run_id}
POST   /v1/runs/{id}/baseline
GET    /v1/runs/{id}/report?format=json|junit|html
POST   /v1/runs/{id}/rescore           # re-evaluate without re-calling
WS     /v1/runs/{id}/stream            # live progress
```

**Trial detail**
```
GET    /v1/trials/{id}                 # verdict, metrics, assertions, attribution
GET    /v1/trials/{id}/turns
GET    /v1/trials/{id}/events
GET    /v1/trials/{id}/audio/{leg}     # caller | agent | mixed  (signed URL)
GET    /v1/trials/{id}/artifacts
POST   /v1/trials/{id}/label           # human label for calibration
POST   /v1/trials/{id}/replay          # → scenario file
```

**Load**
```
POST   /v1/load        { target_id, scenario, profile: "ramp:10->500/10m", hold }
GET    /v1/load/{id}   # degradation curves
```

**Observability**
```
POST   /v1/ingest/webhook/{provider}   # retell | vapi | bland | elevenlabs
POST   /v1/ingest/otlp                 # OTLP/HTTP
POST   /v1/ingest/call                 # SDK
GET|POST /v1/monitors                  GET|PATCH|DELETE /v1/monitors/{id}
GET    /v1/monitors/{id}/calls?cohort=&from=&to=
GET    /v1/monitors/{id}/metrics
GET|POST /v1/alerts                    GET /v1/alerts/{id}/deliveries
```

**Calibration / analytics / benchmark**
```
GET    /v1/calibration?rubric=         POST /v1/calibration/run
GET    /v1/analytics/overview          GET  /v1/analytics/metrics?group_by=
GET    /v1/benchmark/results
```

---

## 10. CLI

```bash
convox init
convox target add retell --agent-id agent_abc --name prod-scheduler
convox target test prod-scheduler

convox generate --from-prompt ./prompt.md --count 25 --out scenarios/
convox generate --from-transcripts ./calls/ --cluster --count 40
convox lint scenarios/

convox run scenarios/ --target prod-scheduler --repeat 3 --budget 5.00
convox run scenarios/critical.yaml --target prod-scheduler \
        --baseline main --fail-on regression --junit out.xml

convox load --target prod-scheduler --profile ramp:10->500/10m --hold 5m
convox replay call_9f2a --out scenarios/regressions/
convox report 7f3a1c --format html --open
convox baseline set 7f3a1c

convox monitor add --target prod-scheduler --source retell-webhook --sample 1.0
convox label --run 7f3a1c --sample 50
convox calibrate --rubric goal_achieved --min-f1 0.8

convox serve --port 8000
```

Exit codes: `0` pass · `1` assertion failures · `2` regression vs baseline · `3` infrastructure error · `4` budget exceeded. CI can distinguish "the agent is broken" from "the harness broke."

---

## 11. Scenario execution guarantees

| Guarantee | Mechanism |
|---|---|
| Reproducible | Seeded RNG for noise, barge-in timing, and caller LLM sampling; scripted mode deterministic end to end |
| Isolated | Each trial gets a fresh adapter connection and fresh agent session; no shared state |
| Bounded | Per-trial timeout, max turns, max cost; watchdog kills and marks `infrastructure_error` |
| Idempotent evaluation | Re-scoring is always safe; judge results cached by content hash |
| Honest | Unmeasurable assertions report `unsupported`, never `pass` |

---

## 12. Environment variables

```bash
# ─── Core ────────────────────────────────────────────────
CONVOX_DATABASE_URL=postgres://convox:convox@localhost:5432/convox
CONVOX_REDIS_URL=redis://localhost:6379/0
CONVOX_SECRET_KEY=change-me
CONVOX_ENV=development
CONVOX_LOG_LEVEL=info

# ─── Object storage ──────────────────────────────────────
CONVOX_S3_ENDPOINT=http://localhost:9000
CONVOX_S3_BUCKET=convox-artifacts
CONVOX_S3_ACCESS_KEY=...
CONVOX_S3_SECRET_KEY=...

# ─── Workers ─────────────────────────────────────────────
CONVOX_SIM_CONCURRENCY=8
CONVOX_EVAL_CONCURRENCY=4
CONVOX_TRIAL_TIMEOUT_S=300
CONVOX_WATCHDOG_GRACE_S=30

# ─── Caller stack (bring your own keys) ──────────────────
CONVOX_CALLER_TTS=sarvam            # sarvam|elevenlabs|cartesia|openai|azure|piper
CONVOX_CALLER_STT=whisper           # whisper|deepgram|sarvam|azure|local
CONVOX_CALLER_LLM=claude-sonnet-5
CONVOX_JUDGE_LLM=claude-sonnet-5
CONVOX_JUDGE_VOTES=3

SARVAM_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
ELEVENLABS_API_KEY=...
DEEPGRAM_API_KEY=...

# ─── Telephony (only for PSTN targets) ───────────────────
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
EXOTEL_API_KEY=...

# ─── Target platform credentials ─────────────────────────
RETELL_API_KEY=...
VAPI_API_KEY=...
LIVEKIT_URL=...   LIVEKIT_API_KEY=...   LIVEKIT_API_SECRET=...

# ─── Governance ──────────────────────────────────────────
CONVOX_RETENTION_DAYS=90
CONVOX_REDACT_PII=true
CONVOX_TELEMETRY=off                # off by default, always
CONVOX_MAX_RUN_BUDGET_USD=25
```

---

## 13. Performance targets

| Metric | Target |
|---|---|
| Concurrent simulated calls per worker node (8 vCPU) | 150+ |
| CPU per concurrent call (DSP + pipeline) | < 5% of a core |
| Added latency from channel simulator | < 15 ms |
| Barge-in scheduling accuracy | ± 20 ms |
| Timeline timestamp resolution | 1 ms |
| Evaluation time per 3-min trial (no judges) | < 5 s |
| Evaluation time per 3-min trial (3-vote judges) | < 25 s |
| Trial detail page load (5-min call) | < 500 ms |
| Run planning for 1,000 trials | < 2 s |

---

## 14. Testing Convox itself

The credibility problem is unavoidable: a testing tool that is itself unreliable is worthless. So:

- **Reference agent.** A deliberately imperfect agent with *known, injected* bugs (300ms extra latency on tool turns, TTS truncation at 5% of turns, ignores barge-in, mishears digits under noise). Convox's own CI asserts that it catches exactly those bugs and reports no others.
- **Determinism suite.** The same scripted scenario run 20× against the reference agent must produce identical verdicts and metrics within tolerance. Variance above threshold fails CI.
- **Adapter contract tests.** Nightly against sandbox accounts on each platform.
- **DSP golden files.** Channel simulator output byte-compared against checked-in references per seed.
- **Judge regression.** The calibration set is versioned; judge prompt changes must not drop F1.
- **Load self-test.** 500 concurrent trials against a local echo agent, asserting no dropped trials and stable timestamps.

---

## 15. Compatibility commitments

- **Scenario format** (`version: convox/v1`) is the public contract. Additive fields are minor changes; removals or semantic changes require a format version bump plus `convox migrate-scenarios`.
- **Assertion and metric names** are public API. Renaming one is a breaking change; metric *definitions* are versioned, and every run records the definition version it was scored against.
- **Adapter interface** is stable within a major version, so third-party adapters keep working.
- **Artifact bundle layout** is documented and versioned — external tooling can read trial output without going through our API.
