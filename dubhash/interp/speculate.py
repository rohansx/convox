"""Speculative translation during the end-of-utterance wait.

While the speaker pauses, the latest STT partial is usually the final transcript. A
Speculator (one per utterance) starts MT on it once silence has lasted `after_ms`, so the
translation is often ready when the STT final arrives instead of starting then.

Rules (docs: open question L1):
- Trigger on a silence frame when silence >= after_ms and a partial exists that has been
  unchanged for >= stable_ms or arrived after the last speech frame (the newest).
  Backchannel-sized utterances (speech < backchannel_max_ms and <= 2 words) are skipped:
  the interpreter would discard them anyway.
- A newer partial that normalizes differently cancels the speculation and restarts it.
- Speech resuming (a speech frame before EOU) cancels it; it may re-trigger at the next pause.
- take(final): if normalize(final) == normalize(speculated text), the speculative result is
  reused (awaited if still running). Otherwise it is cancelled and the caller translates
  the final. A translation of text that differs from the final is never returned.

MT input is the partial as recognised (stripped), not its normalized form: punctuation such
as "?" carries meaning into translation and TTS. Matching uses normalize().
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

BACKCHANNEL_MAX_WORDS = 2
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """casefold, drop punctuation (Unicode P* incl. the danda), collapse whitespace."""
    folded = text.casefold()
    kept = "".join(" " if unicodedata.category(c).startswith("P") else c for c in folded)
    return _SPACES.sub(" ", kept).strip()


@dataclass(frozen=True, slots=True)
class SpecOutcome:
    text: str | None  # the reusable translation, or None: translate the final yourself
    hit: bool  # the final matched the speculated text


class Speculator:
    def __init__(
        self,
        translate: Callable[[str], Awaitable[str]],
        *,
        after_ms: int = 200,
        stable_ms: int = 150,
        backchannel_max_ms: int = 600,
    ) -> None:
        self._translate = translate
        self.after_ms = after_ms
        self.stable_ms = stable_ms
        self.backchannel_max_ms = backchannel_max_ms
        self.partial = ""
        self._partial_norm = ""
        self._partial_t = 0
        self._task: asyncio.Task[str] | None = None
        self._norm = ""
        self.started = 0
        self.cancelled = 0

    @property
    def running(self) -> bool:
        return self._task is not None

    def on_partial(self, text: str, now: int) -> None:
        norm = normalize(text)
        if norm != self._partial_norm:
            self._partial_t = now
        self.partial = text.strip()
        self._partial_norm = norm

    def on_speech(self, now: int) -> None:
        """A speech frame before EOU: the speaker resumed."""
        self.cancel()

    def on_silence(self, now: int, t_last_speech: int, speech_ms: int) -> None:
        """A silence frame before EOU: start (or rebase) the speculation if due."""
        norm = self._partial_norm
        if not norm or now - t_last_speech < self.after_ms:
            return
        if speech_ms < self.backchannel_max_ms and len(norm.split()) <= BACKCHANNEL_MAX_WORDS:
            return
        if self._task is not None:
            if self._norm == norm:
                return
            self.cancel()  # a newer partial: rebase on it
        stable = now - self._partial_t >= self.stable_ms
        newest = self._partial_t >= t_last_speech
        if not (stable or newest):
            return
        self._norm = norm
        self._task = asyncio.create_task(self._translate(self.partial), name="spec-mt")
        self._task.add_done_callback(_retrieve)
        self.started += 1

    def cancel(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            self.cancelled += 1

    async def take(self, final: str) -> SpecOutcome:
        """Resolve the speculation against the STT final (see module docstring)."""
        task, norm = self._task, self._norm
        if task is None:
            return SpecOutcome(None, False)
        if normalize(final) != norm:
            self.cancel()
            return SpecOutcome(None, False)
        self._task = None
        try:
            await asyncio.wait({task})
        finally:
            if not task.done():  # we were cancelled while waiting
                task.cancel()
                self.cancelled += 1
        if task.cancelled():
            return SpecOutcome(None, True)
        exc = task.exception()
        if exc is not None:
            log.debug("spec.mt_error", extra={"error": repr(exc)})
            return SpecOutcome(None, True)
        return SpecOutcome(task.result(), True)


def _retrieve(task: asyncio.Task[str]) -> None:
    """Mark a speculative task's exception as retrieved (it may never be awaited)."""
    if not task.cancelled():
        with contextlib.suppress(Exception):
            task.exception()
