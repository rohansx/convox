"""TurnLog: one Turn per utterance, built when the floor is done with it.

A translated utterance becomes a Turn when the FloorController ends it (played to its
mark, or clipped): the interpreter's t_* timings plus the floor's t_play_start/t_play_end,
provider names and voice. A backchannel becomes a Turn with backchannel=true and no
translation. Each Turn is published as a "turn" event and handed to the background writer
(which nulls text unless record=true). Nothing here blocks or touches storage directly.

`seq` is the per-call order in which turns are logged, across both directions (the
`turns` table is UNIQUE (call_id, seq)); it is not the interpreter's per-direction seq.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Protocol

from dubhash.clock import Clock
from dubhash.events import EventBus
from dubhash.session.providers import Providers
from dubhash.store.writer import summarize
from dubhash.types import CallEvent, Side, Translation, Turn, other

log = logging.getLogger(__name__)

_TIMING_FIELDS = (
    "t_speech_start",
    "t_speech_end",
    "t_eou",
    "t_stt_final",
    "t_mt",
    "t_tts_ttfb",
    "t_play_start",
    "t_play_end",
)


class TurnSink(Protocol):
    def enqueue_turn(self, turn: Turn, record: bool) -> bool: ...


class TurnLog:
    def __init__(
        self,
        call_id: str,
        *,
        providers: Providers,
        clock: Clock,
        bus: EventBus | None = None,
        writer: TurnSink | None = None,
        record: bool = False,
    ) -> None:
        self.call_id = call_id
        self._providers = providers
        self._clock = clock
        self._bus = bus
        self._writer = writer
        self.record = record
        self.turns: list[Turn] = []

    def on_played(self, tr: Translation) -> Turn:
        """FloorController on_played callback: tr was played fully or clipped."""
        timings = {k: tr.timings.get(k) for k in _TIMING_FIELDS}
        turn = Turn(
            call_id=self.call_id,
            side=tr.side,
            seq=len(self.turns),
            **timings,
            src_text=tr.src_text,
            tgt_text=tr.tgt_text,
            backchannel=False,
            clipped_ms=tr.clipped_ms,
            stt_provider=self._name(self._providers.stt.get(tr.side)),
            mt_provider=self._name(self._providers.mt),
            tts_provider=self._name(self._providers.tts.get(other(tr.side))),
            voice=tr.voice,
        )
        return self._log(turn)

    def on_backchannel(
        self,
        side: Side,
        *,
        src_text: str | None,
        t_speech_start: int | None = None,
        t_speech_end: int | None = None,
        t_eou: int | None = None,
        t_stt_final: int | None = None,
    ) -> Turn:
        """A short utterance the interpreter recognised and deliberately did not translate."""
        turn = Turn(
            call_id=self.call_id,
            side=side,
            seq=len(self.turns),
            t_speech_start=t_speech_start,
            t_speech_end=t_speech_end,
            t_eou=t_eou,
            t_stt_final=t_stt_final,
            src_text=src_text,
            backchannel=True,
            stt_provider=self._name(self._providers.stt.get(side)),
        )
        return self._log(turn)

    def summary(self) -> dict[str, int | None]:
        return summarize(self.turns)

    def _log(self, turn: Turn) -> Turn:
        self.turns.append(turn)
        if self._bus is not None:
            payload: dict[str, Any] = {"turn": dataclasses.asdict(turn)}
            self._bus.publish(CallEvent(self.call_id, self._clock.now_ms(), "turn", payload))
        if self._writer is not None:
            try:
                self._writer.enqueue_turn(turn, self.record)
            except Exception:  # the writer must never take the call down
                log.exception("turn_log.enqueue_failed", extra={"call_id": self.call_id})
        return turn

    @staticmethod
    def _name(provider: object | None) -> str | None:
        return getattr(provider, "name", None) if provider is not None else None
