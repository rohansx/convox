"""How the caller actually speaks.

The policy decides *what* to say; a mouth decides *how* it reaches the agent —
as a text frame, or as synthesised audio pushed through a degraded channel one
20 ms packet at a time. Keeping that behind one small interface means the
scripted and agentic policies work unchanged over either channel, and adding a
new transport never touches conversation logic.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Protocol

import numpy as np

from convox.adapters.base import TargetSession
from convox.adapters.websocket_audio import AudioWebSocketSession
from convox.audio import pcm
from convox.model.persona import Persona
from convox.sim.channel import ChannelSimulator
from convox.sim.recorder import Recorder, estimate_speech_ms
from convox.voice.base import TTS


class CallerMouth(Protocol):
    async def speak(self, text: str, *, interrupting: bool = False) -> tuple[int, int]:
        """Utter one caller turn. Returns (start_ms, end_ms) on the call clock."""
        ...

    async def dtmf(self, digits: str, *, tone_ms: int = 120, gap_ms: int = 80) -> None: ...

    async def hangup(self) -> None: ...


class TextMouth:
    """Delivers utterances as text frames.

    Holds the floor for the utterance's estimated duration so that turn-taking and
    latency measurements stay meaningful even without audio.
    """

    def __init__(
        self,
        session: TargetSession,
        persona: Persona,
        recorder: Recorder,
        *,
        realtime: bool = True,
    ) -> None:
        self.session = session
        self.persona = persona
        self.recorder = recorder
        self.realtime = realtime

    async def _elapse(self, ms: int) -> None:
        if ms <= 0:
            return
        if self.realtime:
            await asyncio.sleep(ms / 1000)
        else:
            self.recorder.clock.advance(ms)

    async def speak(self, text: str, *, interrupting: bool = False) -> tuple[int, int]:
        start_ms = self.recorder.clock.now_ms()
        await self._elapse(estimate_speech_ms(text, self.persona.speech_rate))
        end_ms = self.recorder.clock.now_ms()
        self.recorder.caller_turn(text, start_ms=start_ms, end_ms=end_ms, interrupting=interrupting)
        await self.session.say(text, interrupting=interrupting)
        return start_ms, end_ms

    async def dtmf(self, digits: str, *, tone_ms: int = 120, gap_ms: int = 80) -> None:
        await self.session.send_dtmf(digits, tone_ms=tone_ms, gap_ms=gap_ms)

    async def hangup(self) -> None:
        await self.session.hangup()


class AudioMouth:
    """Synthesises speech and streams it through the channel simulator.

    Audio flows continuously for the life of the call, exactly as it does on a
    real line: when the caller is not talking, silence frames keep going out.
    That is not padding — an agent detects end-of-turn by hearing silence, so a
    caller that simply stops transmitting leaves the agent waiting forever for a
    frame that never arrives.

    The caller turn is recorded with the exact text handed to synthesis — the
    ground truth — before a single sample exists. Everything downstream that
    compares what was said against what the agent heard depends on that ordering.
    """

    def __init__(
        self,
        session: AudioWebSocketSession,
        persona: Persona,
        recorder: Recorder,
        tts: TTS,
        channel: ChannelSimulator,
        *,
        realtime: bool = True,
    ) -> None:
        self.session = session
        self.persona = persona
        self.recorder = recorder
        self.tts = tts
        self.channel = channel
        self.realtime = realtime
        self.format = session.audio_format

        self._outbox: asyncio.Queue[tuple[np.ndarray, asyncio.Event | None]] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None
        self._comfort = pcm.silence(self.format.frame_ms, self.format.sample_rate)

    # ── the line ──

    def start(self) -> None:
        self._writer = asyncio.create_task(self._pump())

    async def stop(self) -> None:
        if self._writer is None:
            return
        self._writer.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._writer
        self._writer = None

    async def _pump(self) -> None:
        """Single writer for the outbound leg, running at frame cadence."""
        frame_ms = self.format.frame_ms
        while True:
            try:
                frame, done = self._outbox.get_nowait()
            except asyncio.QueueEmpty:
                frame, done = self._comfort, None

            try:
                await self.session.send_audio(pcm.to_pcm16(frame))
            except Exception:  # noqa: BLE001 - the call is over; stop pumping
                return

            if done is not None:
                done.set()

            if self.realtime:
                await asyncio.sleep(frame_ms / 1000)
            else:
                self.recorder.clock.advance(frame_ms)
                # Yield so the agent's side can keep up with a clock that is
                # running faster than wall time.
                await asyncio.sleep(0)

    # ── speaking ──

    async def speak(self, text: str, *, interrupting: bool = False) -> tuple[int, int]:
        sample_rate = self.format.sample_rate
        clean = self.tts.synthesize(text, sample_rate=sample_rate, rate=self.persona.speech_rate)
        degraded, damage = self.channel.process(clean, sample_rate)

        start_ms = self.recorder.clock.now_ms()
        # Recorded up front: ground truth must not depend on the call completing.
        turn = self.recorder.caller_turn(
            text, start_ms=start_ms, end_ms=start_ms, interrupting=interrupting
        )
        self.recorder.append_caller_audio(degraded)
        if self.channel.describes_damage:
            self.recorder.event("channel.damage", at_ms=start_ms, **damage.as_payload())

        chunks = list(pcm.frames(degraded, self.format.frame_samples))
        spoken = asyncio.Event()
        for index, frame in enumerate(chunks):
            self._outbox.put_nowait((frame, spoken if index == len(chunks) - 1 else None))
        if chunks:
            await spoken.wait()

        end_ms = self.recorder.clock.now_ms()
        self.recorder.set_turn_end(turn, end_ms)
        self.recorder.cost("caller_tts", self.tts.synthesis_cost_usd(text), characters=len(text))
        return start_ms, end_ms

    async def dtmf(self, digits: str, *, tone_ms: int = 120, gap_ms: int = 80) -> None:
        await self.session.send_dtmf(digits, tone_ms=tone_ms, gap_ms=gap_ms)

    async def hangup(self) -> None:
        await self.stop()
        await self.session.hangup()
