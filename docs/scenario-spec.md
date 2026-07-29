# Convox — Scenario & Persona Specification

> Last updated: July 2026
> Status: Pre-implementation design · Schema version `convox/v1`
> Related: [Features](features.md) · [Metrics](metrics.md) · [Tech Spec](tech-spec.md)

The scenario format is the product's real interface. It lives in your repo, next to the agent prompt it tests, and drives the CLI, the API, CI, and the dashboard identically.

---

## 1. Project layout

```
your-agent-repo/
├── convox.yaml               # project config: targets, defaults, providers
├── personas/
│   ├── hinglish_impatient.yaml
│   └── elderly_noisy_street.yaml
└── scenarios/
    ├── critical.suite.yaml
    ├── refill_happy_path.yaml
    ├── refill_wrong_dob.yaml
    └── redteam/
        └── prompt_injection.yaml
```

## 2. `convox.yaml` — project configuration

```yaml
version: convox/v1

project: pharmacy-voice-agent

targets:
  prod-scheduler:
    kind: retell
    agent_id: ${RETELL_AGENT_ID}
    credential: retell_main
  staging-ws:
    kind: websocket
    url: ws://localhost:8765/agent
    encoding: pcm16
    sample_rate: 16000

defaults:
  repeat: 3
  timeout_s: 300
  max_turns: 40
  max_cost_usd: 0.25
  language: en-IN
  channel:
    codec: g711u              # test at telephony fidelity by default
    sample_rate: 8000

caller:                        # the synthetic caller's own stack (BYO keys)
  tts: { provider: sarvam, model: bulbul-v2 }
  stt: { provider: whisper, model: large-v3 }
  llm: { provider: anthropic, model: claude-sonnet-5 }

judge:
  llm: { provider: anthropic, model: claude-sonnet-5 }
  votes: 3
  require_evidence: true
  min_f1: 0.80                 # CI fails if calibration drops below this

reporting:
  junit: ./convox-out/junit.xml
  html: ./convox-out/report.html
  artifacts: ./convox-out/
```

## 3. Scenario schema

### 3.1 Top level

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | ✔ | Unique identifier; used in reports and baselines |
| `description` | string | | Human context |
| `persona` | string \| object | ✔ | Named persona reference or inline definition |
| `mode` | `scripted`\|`agentic`\|`hybrid` | | Default `hybrid` |
| `caller` | object | ✔ | Goal, facts, script, behavior |
| `assert` | list | ✔ | Assertions |
| `repeat` | int | | Trials per scenario (default from project) |
| `timeout_s` | int | | Max call duration |
| `max_turns` | int | | Hard turn cap |
| `max_cost_usd` | float | | Per-trial budget |
| `tags` | list[string] | | For `--tag` filtering |
| `matrix` | object | | Sweep over personas/languages/noise |
| `fixtures` | object | | Tool mocks, setup/teardown |
| `skip` / `only` | bool | | Focus and exclusion during development |

### 3.2 `caller`

```yaml
caller:
  goal: |
    Refill your Metformin prescription and pick it up tomorrow evening.
    Confirm the pickup time before ending the call.

  facts:                        # what the caller knows; enables slot assertions
    patient_name: "Rohan Sharma"
    date_of_birth: "1961-03-14"
    phone: "+91 98765 43210"
    medication: "Metformin 500mg"

  opening: "Haan hello, mujhe apni dawai refill karwani hai."

  script:                       # scripted / hybrid modes only
    - say: "Metformin, 500 milligram"
    - wait_for_agent: {}
    - barge_in: { after_ms: 800, say: "Nahi nahi, 850 milligram" }
    - dtmf: "1"
    - wait_silence: { ms: 3000 }        # probe silence-timeout handling
    - say: "Haan, that's correct"
    - hangup: {}

  handoff_after_turn: 4         # hybrid: script until turn 4, then agentic

  behavior:
    volunteer_facts: on_request # never | on_request | eagerly
    patience_turns: 8           # give up / hang up after N unproductive turns
    persistence: high           # accepts deflection? low | normal | high
    off_topic_probability: 0.1
```

### 3.3 Script step types

| Step | Fields | Purpose |
|---|---|---|
| `say` | text | Speak an exact utterance (ground truth recorded) |
| `say_facts` | field | Speak a fact from the sheet, formatted for speech ("nine eight seven six five…") |
| `wait_for_agent` | `timeout_ms` | Block until the agent finishes speaking |
| `wait_silence` | `ms` | Stay silent for a period — probes VAD/timeout behavior |
| `barge_in` | `after_ms`, `say` | Start speaking N ms into the agent's turn |
| `backchannel` | `text`, `after_ms` | "mm-hm" during agent speech — tests false barge-in |
| `dtmf` | digits, `tone_ms`, `gap_ms` | Send keypad tones |
| `repeat_last` | | Ask the agent to repeat |
| `hangup` | | Disconnect abruptly |
| `noise_burst` | `profile`, `ms` | Inject a transient (door slam, horn) |
| `expect` | assertion | Inline mid-call assertion (fails fast) |

### 3.4 `matrix` — combinatorial coverage

```yaml
matrix:
  persona: [polite_cooperative, impatient_interrupter, elderly_slow_speech]
  language: [hi-IN, ta-IN, en-IN]
  environment.snr_db: [30, 15, 8]
# → 27 variants; each with `repeat` trials
```

Matrix expansion happens at run planning time; each variant is a separate scenario in reports, so you can see *exactly* which persona × language × noise combination breaks.

### 3.5 `fixtures` — mocking the agent's backend

```yaml
fixtures:
  tools:
    lookup_patient:
      match: { phone: "+919876543210" }
      respond: { name: "Rohan Sharma", dob: "1961-03-14", active_rx: ["Metformin 500mg"] }
      latency_ms: 250                    # simulate a slow backend
    create_refill_order:
      respond: { order_id: "RX-88213", ready_at: "2026-07-30T18:00:00+05:30" }
    check_inventory:
      respond_error: { code: 503 }       # force the failure path
  setup:
    - http: { method: POST, url: "${CRM_URL}/test/seed", body: { patient: "..." } }
  teardown:
    - http: { method: DELETE, url: "${CRM_URL}/test/seed/RX-88213" }
```

Tool mocking matters twice: tests stop hitting real systems, and you can force error paths (`503`, timeout, empty result) that are nearly impossible to trigger on demand in production.

---

## 4. Assertions reference

Two kinds. **Deterministic** assertions never involve a model. **Judged** assertions do, and are marked `judge*`.

### 4.1 Comparison grammar

Numeric assertions accept: `lt`, `lte`, `gt`, `gte`, `eq`, `between: [a,b]`, and percentile selectors `p50`, `p90`, `p95`, `p99`, `max`, `mean`.

```yaml
- latency.response_ms: { p95: { lt: 1200 }, max: { lt: 2500 } }
```

Any assertion may be prefixed `soft:` to record without failing:

```yaml
- soft: { talk_ratio: { between: [0.3, 0.7] } }
```

### 4.2 Transcript

```yaml
- transcript.contains: { text: "prescription is ready", speaker: agent }
- transcript.not_contains: { text: "I cannot help", speaker: agent }
- transcript.matches: { pattern: "order (?P<id>RX-\\d+)", speaker: agent }
- transcript.order: ["verify identity", "confirm medication", "confirm pickup"]
- transcript.no_repetition: { max_ngram: 8, max_repeats: 2 }
- turn_count: { lte: 12 }
```

### 4.3 Slot capture — ground-truth powered

```yaml
- slot.captured: { field: phone, value: "+919876543210", normalize: e164 }
- slot.captured: { field: date_of_birth, value: "1961-03-14", normalize: date }
- slot.readback_correct: { field: medication }
- slot.not_hallucinated: [insurance_id, address]
```

Normalizers: `default`, `digits`, `e164`, `date`, `time`, `currency`, `email`, `name`, `indic_numeral`, `transliteration`.

### 4.4 Tools

```yaml
- tool.called: create_refill_order
- tool.not_called: transfer_to_human
- tool.call_count: { tool: lookup_patient, eq: 1 }
- tool.called_with:
    tool: create_refill_order
    args: { medication: "Metformin 500mg", quantity: 30 }
    match: subset                 # exact | subset | jsonpath
- tool.call_order: [lookup_patient, check_inventory, create_refill_order]
```

### 4.5 Timing

```yaml
- latency.first_response_ms: { lt: 1500 }
- latency.response_ms: { p50: { lt: 800 }, p95: { lt: 1200 } }
- latency.tool_response_ms: { p95: { lt: 2500 } }    # tool turns budgeted separately
- dead_air.max_ms: { lt: 2000 }
- dead_air.count: { eq: 0 }
- barge_in.stop_ms: { max: { lt: 300 } }
- barge_in.handled: true
- endpoint.false_interrupt_count: { eq: 0 }
```

### 4.6 Call lifecycle

```yaml
- call.ended_by: agent
- call.duration_s: { lt: 180 }
- call.transferred_to: null
- call.no_error_frames: true
```

### 4.7 Audio quality

```yaml
- audio.no_truncation: true          # agent's last utterance not cut off
- audio.no_clipping: true
- audio.min_snr_db: { gte: 15 }
- audio.no_artifacts: true
- audio.silence_ratio: { lt: 0.4 }
```

### 4.8 ASR quality

```yaml
- asr.wer: { lt: 0.15 }              # agent's understanding vs our ground truth
- asr.entity_error_rate: { lt: 0.05 }
```

### 4.9 Safety and compliance

```yaml
- pii.not_leaked: [aadhaar, pan, card_number, other_patient_records]
- compliance.disclosure_present: { text: "recorded", within_s: 10 }
- compliance.consent_captured: true
- content.no_forbidden_claims: [medical_advice, price_guarantee]
```

### 4.10 Judged

```yaml
- judge: "The agent confirmed both the pickup date and time before ending the call."
- judge.goal_achieved: true
- judge.instruction_following: { prompt_file: ../prompt.md, min_score: 0.8 }
- judge.tone: { expected: "warm and professional" }
- judge.no_hallucination: { knowledge_base: ./kb/ }
```

Judge options per assertion:

```yaml
- judge:
    rubric: "The agent never guessed the medication name; it asked when unsure."
    votes: 5
    model: claude-sonnet-5
    require_evidence: true
```

### 4.11 Composition

```yaml
- any_of:
    - tool.called: create_refill_order
    - tool.called: schedule_callback
- all_of:
    - transcript.contains: { text: "confirmed", speaker: agent }
    - call.ended_by: agent
- not:
    - transcript.contains: { text: "I don't know", speaker: agent }
```

---

## 5. Persona schema

```yaml
# personas/hinglish_impatient.yaml
version: convox/v1
name: hinglish_impatient
description: Urban Indian caller, mixes Hindi and English mid-sentence, interrupts often.

voice:
  provider: sarvam
  voice_id: meera
  language: hi-IN
  accent: hi-IN-delhi

language: hi-IN
code_switch:
  secondary: en-IN
  density: 0.35              # fraction of tokens in the secondary language
  granularity: intra_sentence # sentence | intra_sentence

speech_rate: 1.25
volume_db: 2.0
emotion: rushed
disfluency: medium           # none | low | medium | high

pause:
  thinking_ms: [200, 600]
  max_gap_ms: 1500

backchannel:
  enabled: true
  probability: 0.4
  phrases: ["haan", "hmm", "ok ok"]

comprehension: normal
verbosity: terse

interruption:
  style: frequent            # never | polite | frequent | aggressive
  probability: 0.5
  delay_ms_range: [400, 1100]

patience_turns: 6
hangup:
  on_unproductive_turns: 6

environment:
  noise_profile: street_traffic_india
  snr_db: 12
  device: mobile_speakerphone

channel:
  codec: g711u
  sample_rate: 8000
  network: 4g_congested
```

### 5.1 Shipped noise profiles

`quiet_room`, `office_open_plan`, `cafe`, `street_traffic`, `street_traffic_india`, `market_crowd`, `construction`, `car_interior`, `car_window_open`, `train_station`, `airport`, `tv_background`, `crying_baby`, `dog_barking`, `call_center_floor`, `wind`, `rain`, `keyboard_typing`, `restaurant`, `mall`, `school_yard`, `factory`, `hospital_ward`, `siren_passing`, `bad_line_hum`.

### 5.2 Network profiles

`wifi_good`, `wifi_congested`, `4g_urban`, `4g_congested`, `3g_rural`, `3g_rural_india`, `pstn_landline`, `satellite`, `lossy_5pct`, `lossy_bursty`.

---

## 6. Suite schema

```yaml
# scenarios/critical.suite.yaml
version: convox/v1
name: critical
description: Must-pass before any deploy.

target: prod-scheduler
repeat: 5

defaults:
  timeout_s: 240
  max_cost_usd: 0.30

scenarios:
  - refill_happy_path.yaml
  - refill_wrong_dob.yaml
  - refill_out_of_stock.yaml
  - transfer_to_pharmacist.yaml
  - redteam/prompt_injection.yaml

fail_on: regression          # any-failure | regression | threshold
thresholds:
  pass_rate: { gte: 0.9 }
  latency.response_ms.p95: { lt: 1200 }
```

---

## 7. Python SDK / pytest plugin

For teams that prefer code:

```python
import convox
from convox import assertions as a

@convox.scenario(
    persona="hinglish_impatient",
    target="prod-scheduler",
    repeat=3,
)
async def test_refill_happy_path(call: convox.Call):
    call.facts(phone="+91 98765 43210", medication="Metformin 500mg")

    await call.say("Haan hello, mujhe apni dawai refill karwani hai")
    await call.wait_for_agent()
    await call.say_facts("medication")
    await call.barge_in(after_ms=800, say="Nahi, 850 milligram")

    await call.until_goal("Refill scheduled and pickup time confirmed", max_turns=12)

    call.expect(a.tool_called("create_refill_order"))
    call.expect(a.slot_captured("phone", normalize="e164"))
    call.expect(a.latency_response_ms(p95=dict(lt=1200)))
    call.expect(a.judge("Agent confirmed pickup date and time"))
```

Run with `pytest` (results also land in the Convox dashboard) or `convox run tests/`.

---

## 8. Validation and errors

`convox lint` catches, before any call is placed:

- Unknown persona, target, or noise profile references
- Assertions unsupported by the target's declared capabilities (e.g. `tool.called` against a raw WebSocket target with no side channel) — reported as a warning with the note that they will evaluate as `unsupported`
- Facts referenced by `slot.captured` that don't exist in `caller.facts`
- Script steps after `hangup`
- `handoff_after_turn` beyond the script length
- Matrix expansions exceeding a configured trial-count ceiling (a 4-dimension matrix with `repeat: 5` is 405 calls — worth being told before, not after)
- Cost projection for the run, with a confirmation prompt above a threshold

## 9. Versioning and stability

The `version: convox/v1` header is required. The format follows semver-ish rules: additive fields are minor, removals or semantic changes require `convox/v2` with a migration command (`convox migrate-scenarios`). Assertion names are part of the public contract — renaming one is a breaking change.
