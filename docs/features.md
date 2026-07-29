# Convox — Feature Specification

> Last updated: July 2026
> Status: Pre-implementation design
> Related: [Product Overview](product-overview.md) · [Scenario Spec](scenario-spec.md) · [Metrics Catalog](metrics.md) · [Tech Spec](tech-spec.md) · [Roadmap](roadmap.md)

This is the complete feature inventory, grouped by capability area. Each feature carries a phase tag: **P0** (MVP / first public release), **P1** (fast follow), **P2** (platform maturity), **P3** (enterprise / later).

---

## Table of contents

1. [Targets — connecting to the agent under test](#1-targets--connecting-to-the-agent-under-test)
2. [Personas — the synthetic caller](#2-personas--the-synthetic-caller)
3. [Scenarios and suites](#3-scenarios-and-suites)
4. [Scenario generation](#4-scenario-generation)
5. [Simulation engine](#5-simulation-engine)
6. [Channel and network realism](#6-channel-and-network-realism)
7. [Evaluation — deterministic assertions](#7-evaluation--deterministic-assertions)
8. [Evaluation — LLM judges and calibration](#8-evaluation--llm-judges-and-calibration)
9. [Metrics and audio-layer analysis](#9-metrics-and-audio-layer-analysis)
10. [Failure attribution](#10-failure-attribution)
11. [Reliability and flake control](#11-reliability-and-flake-control)
12. [Regression, baselines, and CI/CD](#12-regression-baselines-and-cicd)
13. [Load and stress testing](#13-load-and-stress-testing)
14. [Red teaming, safety, and compliance testing](#14-red-teaming-safety-and-compliance-testing)
15. [IVR, DTMF, and telephony flows](#15-ivr-dtmf-and-telephony-flows)
16. [Multilingual and Indic testing](#16-multilingual-and-indic-testing)
17. [Production observability](#17-production-observability)
18. [Replay](#18-replay)
19. [Alerts and reporting](#19-alerts-and-reporting)
20. [Dashboard](#20-dashboard)
21. [CLI, SDK, and developer experience](#21-cli-sdk-and-developer-experience)
22. [Open benchmark](#22-open-benchmark)
23. [Cost tracking](#23-cost-tracking)
24. [Security, tenancy, and governance](#24-security-tenancy-and-governance)
25. [Extensibility](#25-extensibility)

---

## 1. Targets — connecting to the agent under test

A **target** is a connection descriptor plus credentials. One adapter interface, many implementations.

| Feature | Phase | Notes |
|---|---|---|
| Raw **WebSocket** adapter | P0 | Bidirectional PCM16 / µ-law audio; configurable sample rate, framing, and handshake. The universal fallback — anything with a socket can be tested. |
| **WebRTC** adapter | P0 | SmallWebRTC (Pipecat), Daily rooms, generic SDP offer/answer. No telephony cost. |
| **Pipecat** adapter | P0 | Auto-creates a WebRTC room against a Pipecat bot; reads the Pipecat OTel trace if exposed. |
| **Retell** adapter | P0 | Creates a web call or triggers an outbound call via the Retell API; passes dynamic variables; auto-syncs agent config/prompt version into run metadata. |
| **Vapi** adapter | P0 | Web call or outbound assistant trigger; assistant overrides as dynamic variables. |
| **LiveKit** adapter | P1 | Joins an agent room as a participant; consumes LiveKit agent traces where available. |
| **ElevenLabs Agents** adapter | P1 | Direct WebSocket voice session with signed URL auth. |
| **Bland** adapter | P1 | Outbound trigger + pathway variables. |
| **PSTN / phone number** adapter | P1 | Dial a real number through Twilio / Exotel / Plivo / Telnyx using your credentials. |
| **SIP trunk** adapter | P2 | Direct SIP INVITE against a trunk; for contact-center and carrier-attached agents. |
| **Inbound (agent-calls-us)** testing | P1 | Convox provisions/holds a number, your agent dials it, caller-ID (ANI) routes the call to the right scenario. Required for testing outbound agents. |
| **Chat / text** adapter | P2 | Same scenario and assertion model over a text channel; reuses judges, drops audio metrics. |
| Target **config snapshotting** | P1 | Store the agent's prompt/flow/version at run time so a regression can be diffed against the config change that caused it. |
| **Health check** on target add | P0 | `convox target test` places a 5-second call and verifies audio flows both ways before you write 40 scenarios against a broken config. |

**Design note.** Adapters expose exactly one contract to the engine: *give me a bidirectional audio stream plus optional side-channel events (tool calls, agent transcript, trace spans)*. Everything above the adapter — personas, assertions, metrics — is platform-agnostic by construction. This is what keeps neutrality cheap.

---

## 2. Personas — the synthetic caller

A persona is a reusable bundle of voice, language, and behavior. Convox ships a library; teams extend it.

### 2.1 Voice and identity

| Dimension | Phase | Values |
|---|---|---|
| Voice provider/ID | P0 | Any configured TTS (Sarvam, ElevenLabs, Cartesia, OpenAI, Azure, Piper/local) |
| Language | P0 | Primary language + optional secondary for code-switching |
| Accent / locale | P0 | e.g. `en-IN-south`, `en-US-southern`, `en-GB`, `hi-IN-delhi` |
| Gender presentation | P0 | Voice selection hint |
| Age band | P1 | child / young-adult / adult / elderly — affects voice choice, speech rate, and disfluency defaults |
| Voice cloning | P2 | Clone a real caller's voice from a production recording for high-fidelity replay (consent-gated, off by default) |

### 2.2 Speech behavior

| Dimension | Phase | Description |
|---|---|---|
| Speech rate | P0 | 0.5×–2.0× multiplier |
| Volume / loudness | P0 | Affects SNR against background noise |
| Emotion | P0 | calm, cheerful, frustrated, angry, anxious, confused, rushed, sad, flat — rendered via TTS style controls where supported, otherwise via prosody markup + lexical choice in agentic mode |
| Disfluency level | P1 | Injects "um", "uh", false starts, self-repairs ("I need — sorry, I want to…"), filler pauses. The single most under-tested realism dimension; endpointing models break on it. |
| Pause behavior | P0 | Thinking pauses, mid-sentence gaps, long silences — directly probes VAD and silence-timeout handling |
| Backchanneling | P1 | "mm-hm", "yeah", "right" while the agent speaks — tests false barge-in detection |
| Comprehension level | P1 | Probability of mishearing, asking for repetition, or needing rephrasing |
| Verbosity | P1 | terse ("yeah") ↔ rambling (three sentences of context before the ask) |

### 2.3 Interaction behavior

| Dimension | Phase | Description |
|---|---|---|
| Interruption style | P0 | `never` / `polite` / `frequent` / `aggressive`; parameterized by barge-in probability and delay-after-agent-starts-speaking |
| Patience | P0 | Turns of no progress before the caller gets annoyed, escalates, or hangs up |
| Cooperativeness | P1 | How readily the caller volunteers required information |
| Goal persistence | P1 | Whether the caller accepts a deflection or pushes back |
| Off-topic drift | P1 | Probability of going off-script mid-call |
| Hang-up behavior | P0 | Abrupt disconnect at a chosen turn or condition — tests cleanup, webhooks, and partial-call handling |

### 2.4 Environment

| Dimension | Phase | Description |
|---|---|---|
| Background noise profile | P0 | Library of 25+ profiles: street, traffic, café, office, TV, crying baby, construction, call center, wind, train station, market. Mixed at a specified SNR. |
| Device profile | P1 | Handset, speakerphone, headset, car Bluetooth, landline — as impulse-response/EQ shaping |
| Room acoustics | P2 | Reverb via convolution IR |

### 2.5 Shipped persona library

P0 ships ~40 named personas so nobody starts from a blank file, including: `polite_cooperative`, `impatient_interrupter`, `elderly_slow_speech`, `noisy_street_mobile`, `heavy_accent_nonnative`, `terse_one_word_answers`, `rambling_oversharer`, `confused_needs_repetition`, `angry_escalating`, `backchannel_heavy`, `hinglish_code_switch`, `tamil_english_mix`, `low_snr_speakerphone`, `child_caller`, `poor_network_dropouts`, `adversarial_prompt_injector`.

---

## 3. Scenarios and suites

| Feature | Phase | Description |
|---|---|---|
| YAML scenario format | P0 | Human-writable, diffable, versioned next to your agent prompt. Full grammar in [scenario-spec.md](scenario-spec.md). |
| Scripted mode | P0 | Exact ordered utterances, waits, DTMF, interruption markers. Deterministic. |
| Agentic mode | P0 | LLM-driven caller with goal, private facts, and persona-constrained behavior |
| Hybrid mode | P0 | Scripted opening → agentic continuation after turn *n* |
| Private fact sheet | P0 | Facts the caller knows, with a disclosure policy (`never` / `on_request` / `eagerly`) — this is what makes slot-capture assertions possible |
| Success criteria | P0 | Assertions block, mixing deterministic and judged claims |
| Suites | P0 | Named collections with shared target, repeat count, and defaults |
| Tags and filters | P0 | `convox run --tag critical --tag hindi` |
| Scenario variables / matrices | P1 | Run one scenario across a matrix of personas, languages, or noise levels — combinatorial coverage from one file |
| Fixtures and mocks | P1 | Mock the agent's tool/webhook backend so tests don't hit real systems; assert on what the agent *tried* to call |
| Setup / teardown hooks | P1 | Seed a CRM record before the call, clean up after |
| Timeouts and budgets | P0 | Max call duration, max turns, max cost per trial |
| Scenario linting | P1 | `convox lint` catches unreachable assertions, undefined personas, and unsatisfiable goals before you burn call minutes |

---

## 4. Scenario generation

Writing 200 scenarios by hand is the reason teams don't test. Convox generates them.

| Feature | Phase | Description |
|---|---|---|
| **Generate from prompt** | P0 | Point at the agent's system prompt / flow definition; an LLM produces N diverse scenarios with goals, facts, and success criteria |
| **Generate from transcripts** | P1 | Feed a corpus of real production calls; cluster by intent and outcome; emit representative scenarios per cluster, weighted by real-world frequency |
| **Edge-case expansion** | P1 | Take a happy path and derive its failure modes: missing data, wrong data, mid-call change of mind, ambiguity, out-of-scope requests, hostile caller |
| **Coverage analysis** | P2 | Parse the agent's flow graph (Retell/Vapi flows, or a declared state machine) and report which nodes, branches, and tools no scenario has exercised. "You have never tested the transfer-to-human path." |
| **Persona sweep** | P1 | Auto-expand one scenario across the persona library to find which caller types break it |
| **Human review gate** | P0 | Generated scenarios land as files in your repo for review — never auto-committed, never run blind |

---

## 5. Simulation engine

The core runtime: a synthetic caller that holds a real-time voice conversation with your agent.

| Feature | Phase | Description |
|---|---|---|
| Pipecat-based caller pipeline | P0 | STT → caller-LLM → TTS, with VAD and turn-taking, running as the *caller* side. Reuses v1's provider abstraction. |
| **Ground-truth text capture** | P0 | Every caller utterance is recorded as the exact text sent to TTS, before synthesis — the foundation for exact WER and slot accuracy |
| Turn-taking control | P0 | Deliberate barge-in at configurable offsets, controlled pause lengths, backchannel injection |
| Full-duplex audio recording | P0 | Both legs recorded separately plus a mixed stereo track (caller left / agent right) — separate legs are what make interruption analysis possible |
| Event timeline | P0 | Microsecond-stamped events: speech start/stop per leg, first audio byte, tool calls, DTMF, disconnects |
| Agent-side transcript capture | P0 | Where the platform exposes it (Retell/Vapi/LiveKit/Pipecat), captured for STT-fault attribution |
| Convox-side re-transcription | P0 | Independent STT pass over the agent's audio, so agent speech is scorable even when the platform exposes nothing |
| Tool-call capture | P0 | Via platform side-channel or via the mock backend |
| Concurrency | P0 | Worker pool; N trials in flight; per-target concurrency caps to avoid tripping rate limits |
| Retry and quarantine | P1 | Distinguish infrastructure failures (adapter timeout, provider 503) from agent failures; retry the former, never mask the latter |
| Deterministic seeds | P0 | Seeded RNG for noise mixing, barge-in timing, and caller-LLM sampling so runs are reproducible |
| Artifact bundle | P0 | Per trial: audio legs, ground-truth transcript, agent transcript, event timeline, metrics JSON, assertion results, judge rationales |

---

## 6. Channel and network realism

Testing over a clean WebRTC link tells you how the agent behaves in a studio. Production is a phone.

| Feature | Phase | Description |
|---|---|---|
| Codec simulation | P0 | G.711 µ-law/a-law (8 kHz), G.722, GSM, Opus at chosen bitrates — applied to the caller's audio without needing real telephony |
| Sample-rate degradation | P0 | Downsample/upsample chain that reproduces narrowband loss |
| Packet loss | P1 | Random and bursty (Gilbert-Elliott) loss models |
| Jitter and latency injection | P1 | Fixed + variable network delay on each leg |
| Network profiles | P1 | Named presets: `wifi_good`, `4g_urban`, `4g_congested`, `3g_rural_india`, `satellite`, `pstn_landline` |
| Dropout / reconnect | P2 | Mid-call audio gaps and transport reconnects — tests session recovery |
| One-way audio fault | P2 | The classic telephony bug; verifies the agent detects and handles it |

---

## 7. Evaluation — deterministic assertions

Deterministic by default. No model in the loop means no variance, and a failure that is always the same failure.

### 7.1 Transcript and content

- `transcript.contains` / `not_contains` (scoped to `agent` / `caller` / `any`, with fuzzy and regex forms)
- `transcript.matches` — regex with named capture groups usable in later assertions
- `transcript.order` — phrases occurred in sequence
- `transcript.turn_count` / `turn_count.agent` / `turn_count.caller`
- `transcript.no_repetition` — no n-gram repeated beyond a threshold (catches loop bugs)

### 7.2 Slot and data capture (ground-truth powered)

- `slot.captured` — we spoke `+91 98765 43210`; the agent's captured value must normalize-equal it. Normalizers for `e164`, `digits`, `date`, `time`, `currency`, `email`, `name`, `indic_numeral`.
- `slot.readback_correct` — the agent's spoken confirmation matches what we said
- `slot.not_hallucinated` — the agent never asserts a fact the caller never provided

### 7.3 Tool and side effects

- `tool.called` / `tool.not_called` / `tool.call_count`
- `tool.called_with` — argument matcher (exact, subset, JSONPath, regex)
- `tool.call_order` — sequence constraint
- `tool.arg_equals` — single-field check with normalization
- `webhook.received` — the mock backend saw the expected request

### 7.4 Timing

- `latency.first_response_ms` — call answer → first agent audio
- `latency.response_ms` with `{p50, p90, p95, max}` — end of caller speech → first agent audio byte
- `latency.tool_response_ms` — latency of turns that involve a tool call, measured separately (they're always slower; a single budget hides both)
- `dead_air.max_ms` / `dead_air.count` — silences above a threshold
- `barge_in.stop_ms` — caller speech onset → agent audio stops
- `barge_in.handled` — the agent actually addressed the interrupting utterance rather than resuming its script
- `endpoint.false_interrupt_count` — agent started talking while the caller was mid-sentence

### 7.5 Call lifecycle

- `call.ended_by` — `agent` / `caller` / `timeout` / `error`
- `call.duration_s` bounds
- `call.transferred_to` — warm/cold transfer target
- `call.no_error_frames` — no adapter/provider errors during the call

### 7.6 Audio quality

- `audio.no_truncation` — the agent's final utterance wasn't cut off (a very common, transcript-invisible bug)
- `audio.no_clipping` — no sample saturation
- `audio.min_snr_db`
- `audio.no_artifacts` — spectral discontinuity detection for garbled TTS
- `audio.silence_ratio` bounds

### 7.7 Safety and compliance

- `pii.not_leaked` — named pattern sets (Aadhaar, PAN, SSN, card numbers, other-record identifiers)
- `compliance.disclosure_present` — required disclosure ("this call is recorded", "I'm an AI assistant") spoken, optionally within the first N seconds
- `compliance.consent_captured`
- `content.no_forbidden_claims` — configurable deny list (medical advice, guarantees, pricing commitments)

### 7.8 Composition

- Boolean composition: `all_of`, `any_of`, `not`
- `soft:` prefix — records a failure without failing the trial (for metrics you're tracking but not enforcing yet)
- Custom assertions via Python plugin (`@convox.assertion`)

---

## 8. Evaluation — LLM judges and calibration

Judges are for genuinely semantic claims only, and they are treated as instruments that need calibration, not oracles.

| Feature | Phase | Description |
|---|---|---|
| Rubric judge | P0 | `judge: "The agent confirmed the pickup date and time before ending the call."` → pass/fail + rationale |
| Goal-achievement judge | P0 | Did the caller's stated goal get met? |
| Instruction-following judge | P0 | Scores the agent against its own system prompt, supplied as context |
| Tone / empathy judge | P1 | Against an expected register |
| Hallucination judge | P1 | Claims checked against a supplied knowledge base; ungrounded assertions flagged |
| **Evidence requirement** | P0 | Judges must cite the turn IDs supporting the verdict; a verdict without valid citations is rejected and retried |
| **Self-consistency voting** | P0 | Run the judge *n* times (default 3) at temperature 0 with shuffled non-semantic ordering; require quorum. Disagreement is surfaced as a confidence score, not hidden. |
| Deterministic pre-filter | P0 | Judges only run when cheap deterministic checks pass, saving tokens and eliminating "the judge said pass but the tool was never called" contradictions |
| **Judge calibration** | P1 | Label a set of trials by hand once. Convox reports judge precision, recall, F1, and Cohen's κ against those labels — per judge, per rubric. Re-runs automatically when the judge model or prompt changes. |
| Calibration regression gate | P2 | CI fails if a judge prompt change drops F1 below a floor |
| Judge model choice | P0 | Any configured LLM, including local models for air-gapped installs; different judges per rubric |
| Audio-aware judging | P2 | Multimodal judge listens to the recording for prosody/tone claims that transcripts can't carry |
| Human review queue | P1 | Dashboard flow for labeling ambiguous trials; labels feed calibration |

---

## 9. Metrics and audio-layer analysis

Full definitions, formulas, and measurement methodology: [metrics.md](metrics.md). Summary of what's computed on every trial:

**Latency** — first-response, per-turn response distribution (p50/p90/p95/max), tool-turn vs non-tool-turn split, TTS time-to-first-byte, dead-air events.

**Turn-taking** — barge-in stop latency, TTS overrun after barge-in, false-endpoint count (agent cut the caller off), backchannel false positives, double-talk duration, turn-transition gaps.

**Speech recognition** — exact WER/CER against ground truth (the differentiator), per-language and per-noise-condition breakdowns, slot-level capture accuracy, entity error rate.

**Audio quality** — SNR, clipping, truncation, spectral artifact score, silence ratio, loudness (LUFS).

**Conversation quality** — talk ratio, average turn length, repetition score, interruption count by party, sentiment trajectory, politeness.

**Task** — goal completion rate, tool-call accuracy, script adherence, containment (resolved without transfer), turns-to-resolution.

**Reliability** — `pass^k`, per-scenario flake rate, variance across repeats.

**Cost** — test-side cost (our STT/LLM/TTS/telephony) and estimated target-side cost, per trial and per run.

---

## 10. Failure attribution

The feature that turns a red test into a fix. When a trial fails, Convox determines *which layer* is responsible by comparing four sources of truth:

```
  ground truth text  →  synthesized audio  →  agent's ASR transcript  →  agent's reply  →  agent's audio
        (known)            (we made it)          (captured/inferred)        (captured)       (recorded)
            │                     │                       │                     │                 │
            └──── TTS fault ──────┘                       │                     │                 │
                  (rare, ours)                            │                     │                 │
                                                          │                     │                 │
            └──────────── STT fault (truth ≠ agent transcript) ─────────────────┘                 │
                                                                                                  │
            └──────────── LLM fault (transcript correct, reply wrong) ──────────────┘             │
                                                                                                  │
            └──────────── TTS/audio fault (reply correct, audio garbled/truncated) ───────────────┘

            └──────────── Timing fault (content correct, arrived too late / talked over) ─────────
```

| Feature | Phase | Description |
|---|---|---|
| Layer attribution on failure | P0 | Each failed assertion carries a `suspected_layer` with the evidence that implicates it |
| Turn-level diff view | P0 | Side-by-side: what we said, what the agent heard, what it replied, when |
| Cross-condition isolation | P1 | Auto-rerun a failing scenario with noise off / wideband codec / slower speech to confirm the causal factor. "This fails at SNR 10dB, passes at 25dB → ASR robustness issue." |
| Regression bisect | P2 | Given a range of agent config versions, binary-search which change introduced the failure |

---

## 11. Reliability and flake control

Voice agents are stochastic. A single pass proves nothing.

| Feature | Phase | Description |
|---|---|---|
| `repeat: k` per scenario | P0 | k independent trials |
| **`pass^k` reporting** | P0 | Fraction of repeats that passed, reported as the headline number instead of a binary verdict |
| Flake detection | P0 | Scenarios whose outcome varies across repeats are labeled `flaky`, not `failed`, and reported separately |
| Flake source split | P1 | Attributes variance to the agent, to the caller LLM, or to the judge — three very different bugs |
| Deterministic mode | P0 | Scripted caller + seeded noise + temperature-0 judges = maximum reproducibility for CI |
| Variance budget | P1 | Fail a suite if its own measurement noise exceeds a threshold — the tool policing itself |

---

## 12. Regression, baselines, and CI/CD

| Feature | Phase | Description |
|---|---|---|
| Baseline runs | P0 | Mark a run as the baseline for a target (e.g. last green run on `main`) |
| Diff against baseline | P0 | Per-scenario verdict changes, metric deltas with significance, new vs pre-existing failures |
| `--fail-on` policy | P0 | `any-failure` / `regression` / `threshold:<expr>` — teams with a red suite can still gate on *new* breakage |
| **GitHub Action** | P0 | First-class action: run suite, upload artifacts, comment on PR with the results table and links to failed-trial recordings |
| GitLab CI / Jenkins / Azure recipes | P1 | Documented pipelines; everything is CLI + exit codes, so nothing is GitHub-specific |
| JUnit XML output | P0 | Native CI test reporting |
| SARIF / annotations | P2 | Inline PR annotations on the prompt file that caused a regression |
| Scheduled runs | P1 | Cron suites against production agents (canary calls) |
| Config-change correlation | P1 | Ties a regression to the agent config diff captured at run time |
| Run comparison view | P1 | Any two runs, side by side, in the dashboard |

---

## 13. Load and stress testing

No open-source tool does this at all today.

| Feature | Phase | Description |
|---|---|---|
| Concurrency ramp profiles | P1 | `ramp:10->500/10m`, `step:50,100,200`, `spike`, `soak` |
| Distributed workers | P1 | Horizontal worker fleet; a coordinator schedules trials across nodes |
| Degradation curves | P1 | Latency percentiles, task success, and error rate plotted against concurrency — the output is a chart, not a pass/fail |
| Concurrency ceiling discovery | P1 | Automatically finds the point where p95 latency or success rate breaks a threshold |
| Provider rate-limit detection | P1 | Classifies failures as agent-side, platform-rate-limit, or Convox-side |
| Mixed-scenario load | P2 | Realistic traffic mix (60% happy path, 20% edge, 20% adversarial) rather than one scenario cloned |
| Cost projection | P1 | Extrapolates what this traffic level would cost in production |
| Soak testing | P2 | Hours-long runs surfacing memory leaks, session-state corruption, and slow drift |

---

## 14. Red teaming, safety, and compliance testing

| Feature | Phase | Description |
|---|---|---|
| Prompt-injection over voice | P1 | Spoken attempts to override the system prompt, extract instructions, or change persona |
| Jailbreak suite | P1 | Curated, versioned attack library with pass/fail per attack class |
| PII extraction attempts | P1 | Attempts to make the agent reveal other users' data or its own configuration |
| Out-of-scope probing | P1 | Medical/legal/financial advice, competitor questions, pricing commitments |
| Hostile caller escalation | P1 | Abuse, threats, profanity — does the agent stay in policy and escalate correctly? |
| Social engineering | P2 | Authority/urgency pressure to bypass verification steps |
| Disclosure and consent checks | P0 | Required disclosures spoken, on time, in the caller's language |
| Regulatory profiles | P2 | Assertion bundles for DPDP, HIPAA, GDPR, TCPA-style calling rules |
| Compliance report export | P2 | Signed PDF/JSON evidence pack per run, for auditors |
| Attack library updates | P2 | Versioned, community-contributable, with a changelog so a passing run states which library version it passed against |

---

## 15. IVR, DTMF, and telephony flows

| Feature | Phase | Description |
|---|---|---|
| DTMF send | P1 | Inline in scripted steps: `dtmf: "1"`, with configurable tone duration/gap |
| DTMF detection | P1 | Verifies the agent's own tones and prompts |
| IVR path assertions | P1 | Assert the traversed menu path |
| IVR tree crawling | P2 | Automatically explores a menu tree and emits a map plus per-branch scenarios |
| Transfer testing | P1 | Warm/cold transfer to a Convox-controlled endpoint; verifies context handoff and that the transfer target actually received the call |
| Voicemail detection | P2 | Answering-machine simulation to test AMD behavior |
| Caller-ID / ANI routing | P1 | For inbound-to-Convox tests of outbound agents |
| Hold and music-on-hold | P2 | Long-hold behavior and timeout handling |

---

## 16. Multilingual and Indic testing

The differentiator. Generic elsewhere, deep here.

| Feature | Phase | Description |
|---|---|---|
| Multilingual personas | P0 | Ships with 12 Indic languages (Hindi, Tamil, Telugu, Bengali, Marathi, Kannada, Malayalam, Gujarati, Punjabi, Odia, Assamese, Urdu) plus major global languages |
| **Code-switching personas** | P0 | Sentence-level *and* intra-sentential mixing (Hinglish, Tanglish, Benglish) with controllable switch density — the case every vendor claims and nobody tests |
| **Indic-correct WER/CER** | P0 | Script-aware normalization: Unicode NFC, matra/nukta normalization, transliteration equivalence (Roman ↔ Devanagari), numeral forms ("do hazaar" / "2000" / "२०००"), honorific and word-boundary conventions. Naive WER is wrong on all of these. |
| Per-language metric breakdown | P0 | Every metric sliced by language and by code-switch density |
| Accent matrix | P1 | Regional English accents (South Indian, Bengali, Punjabi, Marathi-influenced) and regional variants within Indic languages |
| Language-detection testing | P1 | Does the agent notice the caller switched languages, and does it follow? |
| Script-mixing in slots | P1 | Names and addresses spoken in one language, expected in another |
| Indian telephony realism | P0 | 8 kHz narrowband + Indian mobile network jitter/loss profiles as first-class presets |
| Indic-language judges | P1 | Judge prompts and rubrics validated in-language, not translated-then-judged |
| Public Indic benchmark | P2 | Dialect-level accuracy leaderboard — nobody publishes this today |

---

## 17. Production observability

The same evaluators, pointed at real traffic.

| Feature | Phase | Description |
|---|---|---|
| Webhook ingestion | P1 | Retell / Vapi / Bland / ElevenLabs call-completion webhooks → normalized call records |
| OpenTelemetry ingestion | P1 | OTLP endpoint for Pipecat / LiveKit / custom agents; spans mapped to the turn timeline |
| SDK ingestion | P1 | `convox.ingest(call)` from your own code for fully custom stacks |
| Recording fetch | P1 | Pulls recordings where the platform provides them; otherwise scores from transcript + trace with audio metrics marked unavailable |
| Uniform scoring | P1 | Production calls scored with the same assertions, judges, and metrics as tests — so "89% task success in tests, 71% in production" is a valid comparison |
| Sampling policy | P1 | Score 100% or a sampled subset with cost controls; always score calls matching a filter (e.g. long calls, transfers, negative sentiment) |
| Live call view | P2 | In-flight calls with running latency and transcript |
| Cohort analysis | P1 | Slice by language, persona-inferred caller type, time of day, region, agent version |
| Drift detection | P1 | Statistical change detection on key metrics with configurable sensitivity |
| Outlier surfacing | P1 | "Here are the 20 worst calls this week" — ranked, one click to listen |
| PII redaction on ingest | P1 | Configurable redaction before storage; required for the regulated ICP |
| Retention policies | P1 | Per-tenant TTL on recordings/transcripts, with hard deletion |

---

## 18. Replay

| Feature | Phase | Description |
|---|---|---|
| Call → scenario | P1 | Extract caller turns from a production call and emit a scripted scenario file |
| Voice-preserving replay | P2 | Re-synthesize with a cloned caller voice for maximum fidelity (consent-gated) |
| Agentic replay | P2 | Infer the caller's goal and persona; rebuild as an agentic scenario that generalizes instead of parroting |
| Batch replay | P1 | Replay last week's 500 calls against a candidate agent version before deploying it |
| Shadow comparison | P2 | Same caller input, two agent versions, outputs diffed turn by turn |
| Failure-to-test workflow | P1 | One click from a bad production call to a permanent regression test in the repo |

---

## 19. Alerts and reporting

| Feature | Phase | Description |
|---|---|---|
| Threshold alerts | P1 | Any metric, any window, any target |
| Drift alerts | P1 | Statistical rather than fixed-threshold |
| Slack / email / webhook / PagerDuty | P1 | Slack and webhook at P1; PagerDuty P2 |
| Alert context | P1 | Every alert links to the specific failing calls and a listenable recording — not just a number |
| Digest reports | P1 | Daily/weekly summaries per target |
| Run report (HTML/PDF) | P1 | Self-contained shareable report with embedded audio |
| Public run links | P2 | Shareable read-only link for a run (self-hosted, so you control exposure) |

---

## 20. Dashboard

| View | Phase | Contents |
|---|---|---|
| Overview | P0 | Recent runs, pass rate trend, active monitors, alert feed |
| Run detail | P0 | Scenario × repeat grid, pass^k, filters, baseline diff |
| **Trial detail** | P0 | The core view: waveform + synchronized transcript, turn timeline with latency bars, barge-in markers, assertion results with evidence, judge rationale with cited turns, layer attribution, downloadable artifacts |
| Scenario editor | P1 | Edit YAML with schema validation and inline docs; run a single scenario from the UI |
| Persona library | P1 | Browse, audition (hear the voice), clone, edit |
| Metrics explorer | P1 | Arbitrary slicing over runs and production calls |
| Monitors | P1 | Production health per target, cohort breakdowns, outliers |
| Load test view | P1 | Degradation curves and ceiling |
| Benchmark view | P2 | Public leaderboard rendering |
| Settings | P0 | Targets, providers/keys, team, retention, alerting |

Audio playback with turn-synchronized transcript scrubbing is the single most-used feature in tools like this and gets first-class treatment, including keyboard navigation between failed turns.

---

## 21. CLI, SDK, and developer experience

```
convox init                       Scaffold convox.yaml, personas/, scenarios/
convox target add|list|test       Manage and verify targets
convox generate                   Generate scenarios from a prompt or transcripts
convox lint                       Validate scenarios and personas
convox run <path>                 Execute scenarios/suites
convox load                       Load and stress testing
convox replay <call-id>           Turn a production call into a scenario
convox report <run-id>            Render/export a run report
convox baseline set <run-id>      Mark a baseline
convox monitor add|list           Manage production monitors
convox label                      Human-label trials for judge calibration
convox calibrate                  Report judge agreement metrics
convox serve                      Run the API + dashboard locally
```

| Feature | Phase | Description |
|---|---|---|
| Rich terminal output | P0 | Live progress, per-scenario results, failure detail with the turn that broke |
| `pytest` plugin | P1 | `@convox.scenario` decorators, fixtures, and assertions in Python for teams that prefer code |
| Python SDK | P0 | Everything the CLI does, programmatically |
| TypeScript SDK | P2 | For JS-first teams |
| **MCP server** | P1 | Claude Code / Cursor can run suites, read failures, and propose prompt fixes without leaving the editor — a natural fit given this repo's own workflow |
| Local-first mode | P0 | Works with no dashboard and no Postgres for a single developer (file-based artifacts) |
| Offline/air-gapped mode | P2 | Local STT/TTS/LLM only; zero external calls |
| Docs site | P0 | Quickstart per platform, scenario cookbook, metric definitions |
| Example repo | P0 | A deliberately buggy demo agent plus the suite that catches its bugs — the fastest way to show value |

---

## 22. Open benchmark

| Feature | Phase | Description |
|---|---|---|
| Reference agent spec | P2 | One agent definition (appointment scheduling, 4 tools, fixed knowledge base) implemented identically across Retell, Vapi, Pipecat, LiveKit, ElevenLabs, Bland |
| Standard suite | P2 | Fixed scenario set spanning reliability, latency, barge-in, noise, multilingual, and safety |
| Public leaderboard | P2 | Published results with methodology, raw recordings, and a one-command reproduction |
| Versioned methodology | P2 | Every result states the harness version, model versions, and date |
| Community submissions | P3 | Vendors and users can submit runs; results marked self-reported vs independently reproduced |

The benchmark is simultaneously a product feature, the strongest marketing asset available to a small OSS project, and the artifact that makes the metric definitions credible.

---

## 23. Cost tracking

Carried over and repurposed from v1's cost engine.

| Feature | Phase | Description |
|---|---|---|
| Per-trial test cost | P0 | Caller STT + caller LLM + caller TTS + judge tokens + telephony |
| Per-run rollup | P0 | With a breakdown by component |
| Budget guards | P0 | Abort a run that exceeds a cost ceiling; per-trial cost cap |
| Target-side cost estimate | P1 | What the agent's own provider costs were for the call |
| Cost per bug found | P2 | The metric that actually justifies the tool in a budget conversation |
| Cheap-mode defaults | P0 | WebSocket over telephony, scripted over agentic, small judge models, dedupe of identical judge calls |

---

## 24. Security, tenancy, and governance

| Feature | Phase | Description |
|---|---|---|
| API keys + JWT | P0 | Carried over from v1 |
| Teams / projects | P1 | Multi-tenant scoping on every query |
| RBAC | P2 | Owner / maintainer / developer / viewer |
| SSO (OIDC/SAML) | P3 | Enterprise |
| Encrypted credential storage | P0 | Provider keys encrypted at rest |
| Audit log | P1 | Who ran what, who changed a baseline, who read a recording |
| PII redaction | P1 | On ingest and in exports |
| Retention policies | P1 | Per-tenant TTLs with hard deletion |
| Consent gating for voice cloning | P2 | Cloning a real caller's voice requires an explicit recorded consent flag on the source call |
| No-telemetry default | P0 | Zero phone-home. Any usage analytics are opt-in and documented. |

---

## 25. Extensibility

Everything the core does, a plugin can do — this is what makes the OSS bet compound.

| Extension point | Phase | Interface |
|---|---|---|
| Target adapters | P0 | `TargetAdapter` — connect, stream audio, expose side-channel events |
| Providers (STT/TTS/LLM) | P0 | Inherited from v1's provider base classes |
| Assertions | P0 | `@convox.assertion` — receives the trial artifact, returns pass/fail + evidence |
| Metrics | P1 | `@convox.metric` — receives audio + timeline, returns a number |
| Judges | P1 | Custom rubrics and judge backends |
| Noise/impulse profiles | P0 | Drop audio files into a directory |
| Personas | P0 | YAML files |
| Reporters | P1 | Custom output formats and destinations |
| Ingest sources | P1 | Custom production-call ingestion |
| Plugin distribution | P2 | `convox plugin install <pkg>`; a community registry |

---

## Phase summary

**P0 — MVP (what makes it real):** WebSocket/WebRTC/Pipecat/Retell/Vapi adapters, persona library with noise and codec simulation, scripted + agentic + hybrid scenarios, generation from prompt, ground-truth capture, deterministic assertion catalog, judges with evidence and voting, core metrics with exact WER, layer attribution, `pass^k`, baselines + GitHub Action, CLI, trial-detail dashboard, cost guards.

**P1 — the platform:** PSTN/SIP/LiveKit/ElevenLabs/Bland adapters, load testing, red-team suite, IVR/DTMF, production monitoring and alerts, replay, judge calibration, pytest plugin, MCP server, teams.

**P2 — depth and moat:** IVR crawling, coverage analysis, regression bisect, shadow comparison, multimodal judges, public benchmark, compliance report packs, air-gapped mode, plugin registry.

**P3 — enterprise:** SSO, advanced RBAC, community benchmark submissions, managed cloud.
