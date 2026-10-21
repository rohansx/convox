"""Phrase-pack prefill (L10): at call creation, in the background, translate and voice the
call's phrase pack (config/phrases/<vertical>.yaml) for both directions of its pair, so a
matching turn later costs only STT.

For each source side: the pack's canonical texts in the language that side's STT outputs
(English on a pivot side), acknowledgements first, the two sides interleaved; MT through
CachedMT.prefill (the process-wide phrase store: the next call skips MT) unless the STT
already outputs the target language; the number read-back as the interpreter applies it;
then TTS into the audio cache in both of the target side's voices (gender is not known yet),
persisted to disk. Lines already cached cost nothing.

Never in the call's way: it is its own task (cancelled at teardown), one request at a time,
at most `rps` vendor requests per second, and it waits while `busy()` (a turn in flight or
playing). A failed line is logged and skipped. Sleeps run on the call clock, so FakeClock
tests drive it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from itertools import zip_longest
from typing import Any

from dubhash.clock import Clock
from dubhash.config.pairs import PairConfig
from dubhash.interp.phrases import PhrasePack
from dubhash.session.providers import SIDES, Providers
from dubhash.types import Side, other

log = logging.getLogger(__name__)

BUSY_POLL_MS = 100


@dataclass(slots=True)
class PrefillStats:
    lines: int = 0  # (side, text) lines considered
    mt_calls: int = 0  # vendor MT requests made
    tts_calls: int = 0  # vendor TTS requests made
    cached: int = 0  # MT/TTS items that were already cached
    errors: int = 0
    busy_waits: int = 0  # times it paused for the call
    done: bool = False
    t_start: int | None = None
    t_done: int | None = None


class _Pacer:
    def __init__(self, clock: Clock, rps: float, busy: Callable[[], bool] | None, st: PrefillStats):
        self._clock = clock
        self._gap_ms = 1000 / rps if rps > 0 else 0.0
        self._busy = busy
        self._st = st
        self._last: float | None = None

    async def _sleep(self, ms: float) -> None:
        sleep = getattr(self._clock, "sleep_ms", None)
        if sleep is not None:
            await sleep(int(ms))
        else:
            await asyncio.sleep(ms / 1000)

    async def wait(self) -> None:
        """Before a vendor request: the call is not busy and the rate allows it."""
        while self._busy is not None and self._busy():
            self._st.busy_waits += 1
            await self._sleep(BUSY_POLL_MS)
        if self._gap_ms and self._last is not None:
            left = self._last + self._gap_ms - self._clock.now_ms()
            if left > 0:
                await self._sleep(left)
        self._last = self._clock.now_ms()


def _jobs(pack: PhrasePack, pair: PairConfig) -> list[tuple[Side, str]]:
    per_side = [[(s, t) for t in pack.texts(pair.side(s).mt_lang)] for s in SIDES]
    return [j for pair_ in zip_longest(*per_side) for j in pair_ if j is not None]


async def prefill_phrases(
    pack: PhrasePack,
    pair: PairConfig,
    providers: Providers,
    *,
    clock: Clock,
    rps: float,
    busy: Callable[[], bool] | None = None,
    readback: Callable[[Side, str, str], str] | None = None,
    stats: PrefillStats | None = None,
    call_id: str | None = None,
) -> PrefillStats:
    st = stats if stats is not None else PrefillStats()
    st.t_start = clock.now_ms()
    pace = _Pacer(clock, rps, busy, st)
    mt: Any = providers.mt
    for side, text in _jobs(pack, pair):
        st.lines += 1
        src, tgt = pair.side(side), pair.side(other(side))
        tts: Any = providers.tts[other(side)]
        try:
            if src.mt_lang == tgt.lang:  # the STT already speaks the target (mr-en)
                out = text
            elif hasattr(mt, "prefill"):
                if mt.has(text, src.mt_lang, tgt.lang):
                    st.cached += 1
                else:
                    await pace.wait()
                    st.mt_calls += 1
                out = await mt.prefill(text, src.mt_lang, tgt.lang)
                if readback is not None:
                    out = readback(side, text, out)
            else:
                continue
            if not hasattr(tts, "prefill"):
                continue
            for voice in dict.fromkeys((tgt.voice_m, tgt.voice_f)):
                if tts.has(out, voice, tgt.lang):
                    st.cached += 1
                else:
                    await pace.wait()
                    st.tts_calls += 1
                await tts.prefill(out, voice, tgt.lang, persist=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            st.errors += 1
            log.warning(
                "prefill.error",
                extra={"call_id": call_id, "side": side, "stage": "prefill", "error": repr(e)},
            )
    st.done = True
    st.t_done = clock.now_ms()
    log.info(
        "prefill.done",
        extra={
            "call_id": call_id,
            "pair": pair.key,
            "vertical": pack.vertical,
            "lines": st.lines,
            "mt_calls": st.mt_calls,
            "tts_calls": st.tts_calls,
            "cached": st.cached,
            "errors": st.errors,
            "latency_ms": st.t_done - st.t_start,
        },
    )
    return st
