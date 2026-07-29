"""Turn detection and interruption measurement on the agent's audio.

On a text channel a turn is a message. On an audio channel it is a stretch of
sound whose edges have to be found, and finding them accurately is what makes the
interesting measurements possible:

* **Response latency** is measured to the agent's first audible sample, not to the
  arrival of a transcript — that is what the caller actually waits through.
* **Barge-in stop time** is the gap between the caller starting to speak and the
  agent's audio going quiet. An agent that keeps talking for 900 ms after being
  interrupted feels broken to a human and is invisible in a transcript.
* **TTS overrun** is the audio that arrived after the caller began speaking, which
  is distinct from stop time: some stacks stop generating but keep flushing a
  buffer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from convox.audio import pcm

#: Silence this long ends an agent turn. Long enough to survive natural pauses
#: mid-sentence, short enough not to merge two replies into one turn.
END_OF_TURN_SILENCE_MS = 400

#: Floor for the adaptive speech threshold, so a silent line is never "speech".
MIN_SPEECH_RMS = 0.005

#: How long an agent may keep talking after being interrupted before it counts as
#: having talked through the interruption rather than yielded to it. Stopping
#: eventually is not the same as stopping.
BARGE_IN_GRACE_MS = 1000


@dataclass
class BargeIn:
    """One measured interruption."""

    caller_start_ms: int
    agent_stopped_ms: int | None = None
    overrun_ms: int = 0
    overrun_bytes: int = 0

    @property
    def stop_ms(self) -> int | None:
        if self.agent_stopped_ms is None:
            return None
        return max(0, self.agent_stopped_ms - self.caller_start_ms)

    @property
    def yielded(self) -> bool:
        """Did the agent actually give way, rather than finish its sentence?"""
        return self.stop_ms is not None and self.stop_ms <= BARGE_IN_GRACE_MS


@dataclass
class AgentAudioTracker:
    """Streaming VAD over the agent leg."""

    sample_rate: int
    frame_ms: int = 20

    speaking: bool = False
    speech_start_ms: int | None = None
    last_voiced_ms: int | None = None

    buffer: list[np.ndarray] = field(default_factory=list)
    turn_buffer: list[np.ndarray] = field(default_factory=list)
    barge_ins: list[BargeIn] = field(default_factory=list)

    #: When the last frame arrived, so a turn can end on silence rather than
    #: waiting for audio that will never come.
    last_frame_ms: int = 0
    _noise_floor: float = 0.0
    _frames_seen: int = 0

    def _threshold(self) -> float:
        return max(self._noise_floor * 4.0, MIN_SPEECH_RMS)

    def feed(self, samples: np.ndarray, at_ms: int) -> str | None:
        """Consume one frame of agent audio.

        Returns ``"start"`` when speech begins and ``"end"`` when it ends, so the
        caller of this method can open and close turns at the right moments.
        """
        self.buffer.append(samples)
        self._frames_seen += 1
        self.last_frame_ms = at_ms
        level = pcm.rms(samples)

        pending = self.barge_ins[-1] if self.barge_ins else None
        if pending is not None and pending.agent_stopped_ms is None and self.speaking:
            pending.overrun_bytes += len(samples) * 2
            pending.overrun_ms += pcm.duration_ms(samples, self.sample_rate)

        if level <= self._threshold():
            # Track the noise floor only while nobody is speaking.
            self._noise_floor = (
                level if self._frames_seen <= 3 else self._noise_floor * 0.95 + level * 0.05
            )
            if self.speaking and self.last_voiced_ms is not None:
                if at_ms - self.last_voiced_ms >= END_OF_TURN_SILENCE_MS:
                    self.speaking = False
                    self._resolve_pending()
                    return "end"
            return None

        self.turn_buffer.append(samples)
        self.last_voiced_ms = at_ms + pcm.duration_ms(samples, self.sample_rate)
        if not self.speaking:
            self.speaking = True
            self.speech_start_ms = at_ms
            self.turn_buffer = [samples]
            return "start"
        return None

    def silence_elapsed(self, now_ms: int) -> bool:
        """Has the agent been quiet long enough to have finished its turn?

        An agent that finishes speaking simply stops sending frames, so turn end
        cannot be detected from the next frame — there isn't one. This is checked
        on a timer instead.
        """
        if not self.speaking:
            return False
        reference = self.last_voiced_ms if self.last_voiced_ms is not None else self.last_frame_ms
        return (now_ms - reference) >= END_OF_TURN_SILENCE_MS

    def note_caller_started(self, at_ms: int) -> BargeIn | None:
        """Record that the caller began speaking, possibly over the agent."""
        if not self.speaking:
            return None
        event = BargeIn(caller_start_ms=at_ms)
        self.barge_ins.append(event)
        return event

    def _resolve_pending(self) -> None:
        """Stamp when the agent actually went quiet on any open interruption.

        Must happen wherever a turn ends. An agent that yields simply stops
        sending frames, so the stop is usually observed by the silence timer
        rather than by another frame arriving — recording it in only one of those
        paths loses the measurement exactly when the agent behaved well.
        """
        for event in self.barge_ins:
            if event.agent_stopped_ms is None:
                event.agent_stopped_ms = self.last_voiced_ms

    def close_turn(self, at_ms: int) -> tuple[np.ndarray, int]:
        """Finish the current turn, returning its audio and end timestamp."""
        audio = pcm.concat(*self.turn_buffer)
        self.turn_buffer = []
        self.speaking = False
        self._resolve_pending()
        end_ms = self.last_voiced_ms if self.last_voiced_ms is not None else at_ms
        return audio, end_ms

    @property
    def all_audio(self) -> np.ndarray:
        return pcm.concat(*self.buffer)
