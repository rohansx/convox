"""Cross-talk and echo gate: which mic speech is a real speaker, per call
(docs/tech-spec/floor-controller.md section "Cross-talk and echo").

Cross-talk (co_located calls only: /join and loopback, two phones in one room). Each mic
also hears the other person, a few metres away: the same voice, later by the acoustic and
network delay and quieter by the distance. Without a gate, B's interpreter takes A's
Marathi for B's Hindi and makes junk turns that queue, play and can even barge in.
The gate keeps a short level history (RMS dBFS per 20 ms frame, VAD voiced flag) for both
mics and decides at three points:
- frame_verdict: a voiced frame on a side that has no utterance open, while the other side
  has one open (it started first) and is voiced now, at least margin_db louder over the
  last level_window_ms. The frame is fed to the endpointer as silence, so no utterance (and
  no STT stream) starts: discarded before STT.
- provisional: an utterance that did start while the other side was talking is "suspect"
  (its speech is not reported to the floor) until it is clearly genuine: either its level
  is within margin_db of the other mic over matched frames, or it is voiced while the
  other mic is not.
- utterance_verdict (at EOU, before the STT final is awaited): the other side had an
  utterance that started first (within start_slack_ms, for jitter between the phones),
  most of this utterance's voiced frames coincide with voiced frames there, and over
  those frames the other mic is at least margin_db louder. Then it is cross-talk and the
  utterance yields no turn.

Echo (every call): a leg hearing a translation can pick it up on its own mic (a phone's
loudspeaker, or line echo on PSTN). While playback to a side runs, and for echo_tail_ms
after it, a mic frame more than echo_margin_db under the playback's level counts as echo
and is fed to the endpointer as silence, so echo never starts speech or a barge-in. The
default 6 dB is the minimum echo return loss of G.168 line echo cancellers; speech into
the handset is normally louder than that, so barge-in still works. The phone page also
sends silence while playing (half-duplex); this covers ?duplex=1 and PSTN.

Levels are compared, never absolute: AGC differs per phone, but both copies of one voice
go through the same room within a few hundred ms.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from dubhash.types import Side, other

CROSSTALK_MARGIN_DB = 6.0  # the far copy of a voice is at least this much quieter
START_SLACK_MS = 300  # "started first" tolerance: jitter between the two phones' streams
MATCH_MS = 40  # a frame on the other mic within this is "at the same time"
OVERLAP_MIN = 0.5  # share of voiced frames that must coincide for cross-talk
LEVEL_WINDOW_MS = 300  # the other side's level when gating one frame
ONSET_WINDOW_MS = 100  # this side's level when gating one frame
RECENT_VOICED_MS = 200  # the other side counts as talking now
PROVISIONAL_MIN_FRAMES = 5  # matched frames before a suspect utterance is cleared
ECHO_MARGIN_DB = 6.0
ECHO_TAIL_MS = 300  # buffered playout and room reverb after the playback ends
HISTORY_MS = 30_000
SILENT_DBFS = -100.0

Verdict = Literal["crosstalk", "echo"]


def frame_dbfs(frame: bytes) -> float:
    x = np.frombuffer(frame[: len(frame) // 2 * 2], dtype=np.int16).astype(np.float64)
    if x.size == 0:
        return SILENT_DBFS
    ms = float(np.mean(x * x))
    return _db(ms)


def _db(mean_square: float) -> float:
    if mean_square <= 0:
        return SILENT_DBFS
    return max(SILENT_DBFS, 10 * math.log10(mean_square / 32768.0**2))


def _power_mean_db(dbs: list[float]) -> float:
    if not dbs:
        return SILENT_DBFS
    return _db(sum(32768.0**2 * 10 ** (d / 10) for d in dbs) / len(dbs))


@dataclass(slots=True)
class _Mic:
    t: list[int] = field(default_factory=list)
    db: list[float] = field(default_factory=list)
    voiced: list[bool] = field(default_factory=list)
    last_voiced: int | None = None

    def add(self, t: int, db: float, voiced: bool) -> None:
        self.t.append(t)
        self.db.append(db)
        self.voiced.append(voiced)
        if voiced:
            self.last_voiced = t
        if len(self.t) > 2 * HISTORY_MS // 20:
            cut = bisect.bisect_left(self.t, t - HISTORY_MS)
            del self.t[:cut], self.db[:cut], self.voiced[:cut]

    def span(self, t0: int, t1: int) -> range:
        return range(bisect.bisect_left(self.t, t0), bisect.bisect_right(self.t, t1))

    def level(self, t0: int, t1: int) -> float:
        return _power_mean_db([self.db[i] for i in self.span(t0, t1)])

    def voiced_near(self, t: int) -> int | None:
        """Index of a voiced frame within MATCH_MS of t (the loudest), or None."""
        best: int | None = None
        for i in self.span(t - MATCH_MS, t + MATCH_MS):
            if self.voiced[i] and (best is None or self.db[i] > self.db[best]):
                best = i
        return best


@dataclass(slots=True)
class _Playback:
    active: bool = False
    ended_at: int | None = None
    power_sum: float = 0.0
    frames: int = 0

    @property
    def level(self) -> float | None:
        return _db(self.power_sum / self.frames) if self.frames else None


@dataclass(frozen=True, slots=True)
class Overlap:
    """How this side's speech compares with the other mic over the same frames."""

    voiced: int  # this side's voiced frames
    matched: int  # of them, with the other mic voiced at the same time
    margin_db: float  # other mic minus this mic over the matched frames

    @property
    def share(self) -> float:
        return self.matched / self.voiced if self.voiced else 0.0


class CrosstalkGate:
    def __init__(
        self,
        *,
        co_located: bool,
        margin_db: float = CROSSTALK_MARGIN_DB,
        echo_margin_db: float = ECHO_MARGIN_DB,
        start_slack_ms: int = START_SLACK_MS,
        echo_tail_ms: int = ECHO_TAIL_MS,
    ) -> None:
        self.co_located = co_located
        self.margin_db = margin_db
        self.echo_margin_db = echo_margin_db
        self.start_slack_ms = start_slack_ms
        self.echo_tail_ms = echo_tail_ms
        self._mic: dict[Side, _Mic] = {"a": _Mic(), "b": _Mic()}
        # real (not discarded) utterances per side: [start, end or None while open]
        self._utts: dict[Side, list[list[int | None]]] = {"a": [], "b": []}
        self._pb: dict[Side, _Playback] = {"a": _Playback(), "b": _Playback()}

    # --- mic frames --------------------------------------------------------------------

    def on_frame(self, side: Side, frame: bytes, t: int, voiced: bool) -> float:
        """Record one mic frame. Returns its level in dBFS."""
        db = frame_dbfs(frame)
        self._mic[side].add(t, db, voiced)
        return db

    def frame_verdict(self, side: Side, t: int, db: float, *, in_utterance: bool) -> Verdict | None:
        """Is this voiced frame echo or cross-talk (feed it to the endpointer as silence)?"""
        pb = self._pb[side]
        level = pb.level
        playing = pb.active or (pb.ended_at is not None and t - pb.ended_at <= self.echo_tail_ms)
        if playing and level is not None and db < level - self.echo_margin_db:
            return "echo"
        if not self.co_located or in_utterance:
            return None
        o = other(side)
        if self._open(o) is None or not self._talking(o, t):
            return None
        mine = self._mic[side].level(t - ONSET_WINDOW_MS, t)
        theirs = self._mic[o].level(t - LEVEL_WINDOW_MS, t)
        return "crosstalk" if theirs - mine >= self.margin_db else None

    # --- utterances --------------------------------------------------------------------

    def utt_start(self, side: Side, t: int) -> None:
        self._utts[side].append([t, None])

    def utt_end(self, side: Side, t: int) -> None:
        for u in reversed(self._utts[side]):
            if u[1] is None:
                u[1] = t
                return

    def forget(self, side: Side, t_start: int) -> None:
        """A discarded (cross-talk) utterance: it never counts as "started first"."""
        self._utts[side] = [u for u in self._utts[side] if u[0] != t_start]

    def other_talking_first(self, side: Side, t: int) -> bool:
        """At this side's speech start: the other side is in an utterance and voiced now."""
        if not self.co_located:
            return False
        o = other(side)
        return self._open(o) is not None and self._talking(o, t)

    def overlap(self, side: Side, t0: int, t1: int) -> Overlap:
        me, them = self._mic[side], self._mic[other(side)]
        mine: list[float] = []
        theirs: list[float] = []
        voiced = 0
        for i in me.span(t0, t1):
            if not me.voiced[i]:
                continue
            voiced += 1
            j = them.voiced_near(me.t[i])
            if j is not None:
                mine.append(me.db[i])
                theirs.append(them.db[j])
        margin = _power_mean_db(theirs) - _power_mean_db(mine) if mine else 0.0
        return Overlap(voiced, len(mine), margin)

    def provisional(self, side: Side, t0: int, t1: int) -> bool:
        """A suspect utterance is genuine so far: as loud as the other mic, or alone."""
        o = self.overlap(side, t0, t1)
        if o.voiced - o.matched >= PROVISIONAL_MIN_FRAMES:
            return True
        return o.matched >= PROVISIONAL_MIN_FRAMES and o.margin_db < self.margin_db

    def utterance_verdict(self, side: Side, t0: int, t1: int) -> Overlap | None:
        """The utterance [t0, t1] on `side` is cross-talk: returns the evidence, else None."""
        if not self.co_located:
            return None
        first = any(
            s is not None and s <= t0 + self.start_slack_ms and (e is None or e >= t0)
            for s, e in self._utts[other(side)]
        )
        if not first:
            return None
        o = self.overlap(side, t0, t1)
        if o.share >= OVERLAP_MIN and o.margin_db >= self.margin_db:
            return o
        return None

    # --- playback (echo reference) -----------------------------------------------------

    def playback_start(self, listener: Side) -> None:
        self._pb[listener] = _Playback(active=True)

    def playback_frame(self, listener: Side, frame: bytes) -> None:
        db = frame_dbfs(frame)
        if db <= SILENT_DBFS + 1:
            return
        pb = self._pb[listener]
        pb.power_sum += 32768.0**2 * 10 ** (db / 10)
        pb.frames += 1

    def playback_end(self, listener: Side, t: int) -> None:
        pb = self._pb[listener]
        pb.active = False
        pb.ended_at = t

    # --- internals ---------------------------------------------------------------------

    def _open(self, side: Side) -> list[int | None] | None:
        return next((u for u in reversed(self._utts[side]) if u[1] is None), None)

    def _talking(self, side: Side, t: int) -> bool:
        last = self._mic[side].last_voiced
        return last is not None and t - last <= RECENT_VOICED_MS
