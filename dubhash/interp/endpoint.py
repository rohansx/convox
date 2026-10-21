"""End-of-utterance detection from per-frame speech probabilities.

Times are call-relative ms stamped on each frame by the caller (`now_ms`). An utterance
starts on the first frame with prob > speech_threshold and ends (EOU) on the first frame
where silence since the last speech frame is >= eou_ms, or when force_eou() is called
(the STT vendor signalled end-of-utterance first). Silence gaps shorter than eou_ms are
part of the utterance (hangover). Utterances shorter than min_utterance_ms are discarded
as noise; whether to merge them into the next utterance instead is an open question.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from dubhash.audio.frames import bytes_to_ms, ms_to_bytes


@dataclass(frozen=True, slots=True)
class SpeechStart:
    t: int
    preroll: bytes  # audio just before the first speech frame, for the STT stream


@dataclass(frozen=True, slots=True)
class Utterance:
    t_start: int  # first speech frame
    t_end: int  # last speech frame
    t_eou: int  # when EOU fired
    audio: bytes  # pre-roll + utterance + up to tail_ms of trailing silence
    duration_ms: int  # speech span, first to last speech frame inclusive
    short: bool  # duration_ms < backchannel_max_ms (backchannel candidate)


@dataclass(frozen=True, slots=True)
class Discard:
    t_start: int
    t_end: int
    t_eou: int
    duration_ms: int


EndpointEvent = SpeechStart | Utterance | Discard


class Endpointer:
    def __init__(
        self,
        eou_ms: int = 550,
        min_utterance_ms: int = 300,
        backchannel_max_ms: int = 600,
        speech_threshold: float = 0.5,
        preroll_ms: int = 200,
        tail_ms: int = 100,
    ) -> None:
        self.eou_ms = eou_ms
        self.min_utterance_ms = min_utterance_ms
        self.backchannel_max_ms = backchannel_max_ms
        self.speech_threshold = speech_threshold
        self.preroll_bytes = ms_to_bytes(preroll_ms)
        self.tail_bytes = ms_to_bytes(tail_ms)
        self._ring: deque[bytes] = deque()
        self._ring_len = 0
        self._in_speech = False
        self._buf = bytearray()
        self._t_start = 0
        self._t_last = 0
        self._last_frame_ms = 0
        self._speech_end_len = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def t_speech_start(self) -> int:
        """First speech frame of the current utterance (valid while in_speech)."""
        return self._t_start

    @property
    def t_last_speech(self) -> int:
        """Latest speech frame of the current utterance (valid while in_speech)."""
        return self._t_last

    def feed(self, frame: bytes, prob: float, now_ms: int) -> list[EndpointEvent]:
        is_speech = prob > self.speech_threshold
        if not self._in_speech:
            if not is_speech:
                self._remember(frame)
                return []
            preroll = b"".join(self._ring)
            self._ring.clear()
            self._ring_len = 0
            self._in_speech = True
            self._buf = bytearray(preroll)
            self._buf += frame
            self._t_start = self._t_last = now_ms
            self._last_frame_ms = bytes_to_ms(len(frame))
            self._speech_end_len = len(self._buf)
            return [SpeechStart(now_ms, preroll)]

        self._buf += frame
        if is_speech:
            self._t_last = now_ms
            self._last_frame_ms = bytes_to_ms(len(frame))
            self._speech_end_len = len(self._buf)
            return []
        if now_ms - self._t_last >= self.eou_ms:
            return [self._finish(now_ms)]
        return []

    def force_eou(self, now_ms: int) -> list[EndpointEvent]:
        if not self._in_speech:
            return []
        return [self._finish(now_ms)]

    def reset(self) -> None:
        self._in_speech = False
        self._buf = bytearray()
        self._ring.clear()
        self._ring_len = 0

    def _remember(self, frame: bytes) -> None:
        if self.preroll_bytes <= 0:
            return
        self._ring.append(frame)
        self._ring_len += len(frame)
        while self._ring and self._ring_len - len(self._ring[0]) >= self.preroll_bytes:
            self._ring_len -= len(self._ring.popleft())

    def _finish(self, now_ms: int) -> Utterance | Discard:
        duration = self._t_last - self._t_start + self._last_frame_ms
        audio = bytes(self._buf[: self._speech_end_len + self.tail_bytes])
        self._in_speech = False
        self._buf = bytearray()
        if duration < self.min_utterance_ms:
            return Discard(self._t_start, self._t_last, now_ms, duration)
        return Utterance(
            t_start=self._t_start,
            t_end=self._t_last,
            t_eou=now_ms,
            audio=audio,
            duration_ms=duration,
            short=duration < self.backchannel_max_ms,
        )
