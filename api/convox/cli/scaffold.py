"""Project scaffolding for `convox init`."""

from __future__ import annotations

from pathlib import Path

PROJECT_YAML = """version: convox/v1
project: my-voice-agent

targets:
  # The bundled reference agent — start it with:
  #   python -m convox.testing.reference_agent
  demo:
    kind: websocket
    url: ws://127.0.0.1:8765

defaults:
  repeat: 3
  timeout_s: 120
  max_turns: 20
"""

PERSONA_YAML = """version: convox/v1
name: impatient_mobile
description: Urban caller on a mobile, speaks quickly, low patience.

voice:
  voice_id: default
  language: en-IN

language: en-IN
speech_rate: 1.25
emotion: rushed
disfluency: low

interruption:
  style: frequent
  probability: 0.5
  delay_ms_range: [400, 1100]

patience_turns: 6

environment:
  noise_profile: street_traffic_india
  snr_db: 12
  device: mobile_speakerphone

channel:
  codec: g711u
  sample_rate: 8000
"""

SCENARIO_YAML = """version: convox/v1
name: refill_happy_path
description: Standard prescription refill for a known patient.

persona: impatient_mobile
mode: scripted

caller:
  goal: Refill your Metformin prescription and confirm the pickup time.
  facts:
    phone: "+91 98765 43210"
    medication: "Metformin 500mg"
  opening: "Hi, I need to refill my prescription."
  script:
    - say: "My number is nine eight seven six five four three two one zero"
    - say: "It's Metformin, 500 milligram"
    - say: "No, that's all. Thanks."

assert:
  # Deterministic — no model in the loop, so no run-to-run variance.
  - tool.called: create_refill_order
  - slot.captured: { field: phone, value: "+919876543210", normalize: e164 }
  - latency.response_ms: { p95: { lt: 2000 } }
  - dead_air.max_ms: { lt: 3000 }
  - transcript.no_repetition: { max_repeats: 2 }
  - call.no_error_frames: true

  # Judged — reports `unsupported` until a judge backend is configured,
  # which is deliberately not the same as passing.
  - judge: "The agent confirmed the pickup time before ending the call."

repeat: 3
"""

GITIGNORE = """convox-out/
"""

FILES = {
    "convox.yaml": PROJECT_YAML,
    "personas/impatient_mobile.yaml": PERSONA_YAML,
    "scenarios/refill_happy_path.yaml": SCENARIO_YAML,
    ".gitignore": GITIGNORE,
}


def write_project(directory: Path, *, force: bool = False) -> list[Path]:
    """Write starter files. Returns the paths actually created."""
    created: list[Path] = []
    for relative, content in FILES.items():
        path = directory / relative
        if path.exists() and not force:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        created.append(path)
    return created
