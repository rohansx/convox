"""Executes one trial: connect, converse, record, package.

The runner owns the only part of the system that touches real time. Everything it
produces is a self-describing artifact bundle that can be re-scored later without
placing the call again — evaluation is deliberately not part of this path.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from convox.adapters import registry
from convox.adapters.base import AdapterError, Clock, TargetSession, TrialContext
from convox.adapters.websocket_audio import AudioWebSocketSession
from convox.model.enums import EndedBy, TrialStatus
from convox.model.persona import DEFAULT_PERSONA, Persona
from convox.model.scenario import Scenario
from convox.model.target import Target
from convox.model.trial import TrialArtifacts
from convox.sim.channel import ChannelSimulator
from convox.sim.conversation import AgentStream
from convox.sim.mouth import AudioMouth, CallerMouth, TextMouth
from convox.sim.policy.scripted import ScriptedPolicy
from convox.sim.recorder import Recorder
from convox.spec.loader import scenario_hash
from convox.voice.registry import build_stt, build_tts


@dataclass
class RunOptions:
    """Knobs that apply to every trial in a run."""

    #: Hold the floor for the real duration of each utterance. Disabled in unit
    #: tests so suites run fast; enabled everywhere else so that timing
    #: measurements reflect a real conversation.
    realtime: bool = True
    seed: int = 0
    run_id: str | None = None
    #: Voice stack for the synthetic caller. Defaults to the offline reference
    #: codec, so the audio path runs with no credentials.
    tts: str = "tone"
    stt: str = "tone"
    #: Where per-trial recordings are written. None keeps audio in memory only.
    artifact_dir: Path | None = None


def stable_seed(scenario: Scenario, repeat_index: int, base_seed: int = 0) -> int:
    """Deterministic per-trial seed, so a rerun reproduces the same conditions."""
    return abs(hash((scenario.name, repeat_index, base_seed))) % (2**31)


def _build_mouth(
    session: TargetSession,
    persona: Persona,
    recorder: Recorder,
    options: RunOptions,
    seed: int,
) -> CallerMouth:
    if isinstance(session, AudioWebSocketSession):
        return AudioMouth(
            session,
            persona,
            recorder,
            build_tts(options.tts),
            ChannelSimulator(persona, seed=seed),
            realtime=options.realtime,
        )
    return TextMouth(session, persona, recorder, realtime=options.realtime)


async def run_trial(
    scenario: Scenario,
    target: Target,
    *,
    persona: Persona | None = None,
    repeat_index: int = 0,
    options: RunOptions | None = None,
) -> TrialArtifacts:
    options = options or RunOptions()
    persona = persona or DEFAULT_PERSONA
    clock = Clock()
    seed = stable_seed(scenario, repeat_index, options.seed)

    adapter = registry.build(target)
    recorder = Recorder(
        clock=clock,
        trial_id=str(uuid.uuid4()),
        run_id=options.run_id,
        scenario_name=scenario.name,
        scenario_hash=scenario_hash(scenario),
        repeat_index=repeat_index,
        seed=seed,
        persona_name=persona.name,
        language=persona.language,
        target_name=target.name,
        target_kind=target.kind,
        capabilities=adapter.capabilities.as_flags(),
        facts=dict(scenario.caller.facts),
        artifact_dir=options.artifact_dir,
    )

    ctx = TrialContext(
        trial_id=recorder.trial_id,
        scenario_name=scenario.name,
        clock=clock,
        facts=dict(scenario.caller.facts),
        timeout_s=scenario.timeout_s,
    )

    try:
        session = await adapter.connect(ctx)
    except AdapterError as exc:
        clock.start()
        recorder.event("call.connect_failed", message=str(exc))
        return recorder.finalize(
            status=TrialStatus.INFRASTRUCTURE_ERROR,
            ended_by=EndedBy.ERROR,
            error=str(exc),
        )

    recorder.event("call.connected", at_ms=0)

    audio_mode = isinstance(session, AudioWebSocketSession)
    sample_rate = session.audio_format.sample_rate if audio_mode else None
    recorder.sample_rate = sample_rate

    stream = AgentStream(
        session,
        recorder,
        stt=build_stt(options.stt) if audio_mode else None,
        sample_rate=sample_rate,
    )
    stream.start()

    mouth = _build_mouth(session, persona, recorder, options, seed)
    if isinstance(mouth, AudioMouth):
        # Open the line before the agent greets, so its first word is captured.
        mouth.start()
    policy = ScriptedPolicy(scenario, persona, recorder, stream, mouth, realtime=options.realtime)

    status = TrialStatus.COMPLETED
    ended_by: EndedBy | None = None
    error: str | None = None

    try:
        ended_by = await asyncio.wait_for(policy.run(), timeout=scenario.timeout_s)
    except TimeoutError:
        ended_by = EndedBy.TIMEOUT
        recorder.event("call.timeout", timeout_s=scenario.timeout_s)
    except AdapterError as exc:
        status = TrialStatus.INFRASTRUCTURE_ERROR
        ended_by = EndedBy.ERROR
        error = str(exc)
        recorder.event("call.adapter_error", message=error)
    except Exception as exc:  # noqa: BLE001 - never let one trial kill a run
        status = TrialStatus.INFRASTRUCTURE_ERROR
        ended_by = EndedBy.ERROR
        error = f"{type(exc).__name__}: {exc}"
        recorder.event("call.error", message=error)
    finally:
        if isinstance(mouth, AudioMouth):
            with contextlib.suppress(Exception):
                await mouth.stop()
        with contextlib.suppress(Exception):
            stream.finalize_barge_ins()
        with contextlib.suppress(Exception):
            await stream.stop()
        with contextlib.suppress(Exception):
            await session.close()

    # A transport error observed mid-call is infrastructure, not a bad agent.
    if stream.transport_error and status == TrialStatus.COMPLETED:
        status = TrialStatus.INFRASTRUCTURE_ERROR
        error = stream.transport_error
        ended_by = EndedBy.ERROR

    return recorder.finalize(status=status, ended_by=ended_by, error=error)
