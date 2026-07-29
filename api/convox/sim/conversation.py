"""Consumes agent-side events and turns them into a recorded conversation.

Runs as a background task for the life of a call so that agent output is captured
with accurate timestamps regardless of what the caller policy is doing. The caller
policy interacts with it only through :meth:`wait_for_turn`.

On a text channel a turn is a message. On an audio channel the boundaries have to
be found in the sound itself, which is what makes response latency, barge-in stop
time, and TTS truncation measurable at all.
"""

from __future__ import annotations

import asyncio
import contextlib

import numpy as np

from convox.adapters.base import AgentEvent, TargetSession
from convox.audio import pcm
from convox.model.trial import BargeInEvent, Turn
from convox.sim.audio_tracker import AgentAudioTracker
from convox.sim.recorder import Recorder
from convox.voice.base import STT


class AgentStream:
    """Assembles agent turns from a session's event stream."""

    def __init__(
        self,
        session: TargetSession,
        recorder: Recorder,
        *,
        stt: STT | None = None,
        sample_rate: int | None = None,
    ) -> None:
        self._session = session
        self._recorder = recorder
        self._completed: asyncio.Queue[Turn] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.agent_hung_up = False
        self.transport_error: str | None = None
        self.stream_closed = asyncio.Event()

        self._stt = stt
        self._tracker = AgentAudioTracker(sample_rate=sample_rate) if sample_rate else None
        self._watchdog: asyncio.Task[None] | None = None

    @property
    def audio_mode(self) -> bool:
        return self._tracker is not None

    @property
    def agent_speaking(self) -> bool:
        """True while the agent is audibly mid-utterance."""
        return bool(self._tracker and self._tracker.speaking)

    def start(self) -> None:
        self._task = asyncio.create_task(self._consume())
        if self._tracker is not None:
            self._watchdog = asyncio.create_task(self._watch_for_silence())

    async def stop(self) -> None:
        for task in (self._watchdog, self._task):
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None
        self._watchdog = None

    async def _watch_for_silence(self) -> None:
        """Close an agent turn once the audio stops arriving.

        Without this the stream waits forever for a frame that signals the end of
        speech, but a finished agent sends nothing at all.
        """
        while True:
            await asyncio.sleep(0.05)
            if self._tracker is None:
                return
            now = self._recorder.clock.now_ms()
            if self._tracker.silence_elapsed(now):
                turn = self._close_agent_turn(now)
                if turn is not None:
                    self._completed.put_nowait(turn)
                self._flush_barge_ins()

    async def _consume(self) -> None:
        try:
            async for event in self._session.events():
                self._handle(event)
        finally:
            if self._recorder.agent_turn_open:
                turn = self._close_agent_turn(self._recorder.clock.now_ms())
                if turn is not None:
                    self._completed.put_nowait(turn)
            self.stream_closed.set()

    # ── turn assembly ──

    def _close_agent_turn(self, at_ms: int, *, explicit_end: int | None = None) -> Turn | None:
        if self._tracker is None:
            return self._recorder.agent_speech_end(explicit_end)

        audio, end_ms = self._tracker.close_turn(at_ms)
        turn = self._recorder.agent_speech_end(end_ms)
        if turn is None:
            return None
        if self._stt is not None and len(audio):
            # Convox transcribes the agent independently, so a platform that
            # exposes no transcript of its own is still fully scorable.
            turn.convox_transcript = self._stt.transcribe(audio, sample_rate=self._tracker.sample_rate)
            self._recorder.cost(
                "caller_stt", self._stt.transcription_cost_usd(audio, self._tracker.sample_rate)
            )
        return turn

    def _handle(self, event: AgentEvent) -> None:
        match event.kind:
            case "agent.audio":
                self._handle_audio(event)

            case "agent.speech_start":
                self._recorder.agent_speech_start(event.at_ms)

            case "agent.transcript" if event.payload.get("of") == "caller":
                # What the agent believes the caller said. Diffing this against
                # the ground truth is the exact-WER measurement.
                self._recorder.attach_agent_hearing(str(event.payload.get("text", "")))

            case "agent.text" | "agent.transcript":
                text = str(event.payload.get("text", ""))
                if text:
                    self._recorder.agent_text(text, event.at_ms)
                if event.kind == "agent.text" and not self.audio_mode and event.payload.get("final", True):
                    turn = self._close_agent_turn(event.at_ms)
                    if turn is not None:
                        self._completed.put_nowait(turn)

            case "agent.speech_end":
                turn = self._close_agent_turn(event.at_ms, explicit_end=event.at_ms)
                if turn is not None:
                    self._completed.put_nowait(turn)

            case "agent.tool_call":
                self._recorder.agent_tool_call(
                    str(event.payload.get("name", "")),
                    dict(event.payload.get("args") or {}),
                    event.at_ms,
                )

            case "agent.hangup":
                self.agent_hung_up = True
                turn = self._close_agent_turn(event.at_ms)
                if turn is not None:
                    self._completed.put_nowait(turn)
                self.stream_closed.set()

            case "error":
                self.transport_error = str(event.payload.get("message", "unknown transport error"))
                self._recorder.event("error", at_ms=event.at_ms, message=self.transport_error)

            case _:
                self._recorder.event(event.kind, at_ms=event.at_ms, **event.payload)

    def _handle_audio(self, event: AgentEvent) -> None:
        if self._tracker is None:
            return
        payload = event.payload.get("pcm")
        if not payload:
            return
        samples = pcm.from_pcm16(payload)
        self._recorder.append_agent_audio(samples)

        transition = self._tracker.feed(samples, event.at_ms)
        if transition == "start":
            self._recorder.agent_speech_start(self._tracker.speech_start_ms or event.at_ms)
        elif transition == "end":
            turn = self._close_agent_turn(event.at_ms)
            if turn is not None:
                self._completed.put_nowait(turn)
            self._flush_barge_ins()

    # ── interruptions ──

    def note_caller_started(self, at_ms: int) -> None:
        """Tell the tracker the caller began speaking, so barge-in can be timed."""
        if self._tracker is not None:
            self._tracker.note_caller_started(at_ms)

    def _flush_barge_ins(self) -> None:
        if self._tracker is None:
            return
        for event in self._tracker.barge_ins:
            if event.agent_stopped_ms is None:
                continue
            if any(b.caller_start_ms == event.caller_start_ms for b in self._recorder._barge_ins):
                continue
            self._recorder.record_barge_in(
                BargeInEvent(
                    caller_start_ms=event.caller_start_ms,
                    agent_stopped_ms=event.agent_stopped_ms,
                    stop_ms=event.stop_ms,
                    overrun_ms=event.overrun_ms,
                    overrun_bytes=event.overrun_bytes,
                )
            )

    def finalize_barge_ins(self) -> None:
        """Record any interruption the agent never stopped for."""
        if self._tracker is None:
            return
        self._flush_barge_ins()
        for event in self._tracker.barge_ins:
            if event.agent_stopped_ms is not None:
                continue
            if any(b.caller_start_ms == event.caller_start_ms for b in self._recorder._barge_ins):
                continue
            # The agent talked straight through. Reported with no stop time, which
            # is worse than a slow stop, not better.
            self._recorder.record_barge_in(
                BargeInEvent(
                    caller_start_ms=event.caller_start_ms,
                    agent_stopped_ms=None,
                    stop_ms=None,
                    overrun_ms=event.overrun_ms,
                    overrun_bytes=event.overrun_bytes,
                )
            )

    @property
    def agent_audio(self) -> np.ndarray:
        return self._tracker.all_audio if self._tracker else np.zeros(0, dtype=np.float32)

    # ── waiting ──

    async def wait_for_turn(self, timeout_ms: int, *, after_ms: int = 0) -> Turn | None:
        """Wait for an agent turn that began at or after ``after_ms``.

        The filter matters: an agent that greets on connect produces a turn before
        the caller has said anything. Without it, that greeting would be consumed
        as the reply to the caller's first utterance and every subsequent turn
        would be attributed one step late — silently corrupting every latency
        measurement on the call.

        Returns ``None`` on timeout or once the agent has hung up.
        """
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while True:
            if self.agent_hung_up and self._completed.empty():
                return None
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                turn = await asyncio.wait_for(self._completed.get(), timeout=remaining)
            except TimeoutError:
                return None
            if turn.speech_start_ms >= after_ms:
                return turn

    def drain(self) -> list[Turn]:
        turns = []
        while not self._completed.empty():
            turns.append(self._completed.get_nowait())
        return turns
