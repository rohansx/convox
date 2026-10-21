"""FrameClock: leg-relative timestamps for inbound frames, plus jitter and gap detection.

Frame n is stamped n * 20 ms. Arrival times (leg-relative or any monotonic ms; the first
arrival is the anchor) are compared with the expected arrival anchor + n * 20 ms. A frame
arriving more than gap_threshold_ms late is a gap: the gap is returned and the clock skips
ahead by whole frames so later stamps stay true (docs/tech-spec/audio.md section 3).
"""

from __future__ import annotations

from dubhash.audio.frames import FRAME_MS

DEFAULT_GAP_THRESHOLD_MS = 200


class FrameClock:
    def __init__(
        self, frame_ms: int = FRAME_MS, gap_threshold_ms: int = DEFAULT_GAP_THRESHOLD_MS
    ) -> None:
        self.frame_ms = frame_ms
        self.gap_threshold_ms = gap_threshold_ms
        self._n = 0  # index of the next frame
        self._anchor: int | None = None
        self.last_ms = 0  # stamp of the most recent frame
        self.jitter_ms = 0  # arrival minus expected, for the most recent observed frame

    def stamp(self) -> int:
        """Stamp the next frame by count alone (no arrival time, no gap detection)."""
        self.last_ms = self._n * self.frame_ms
        self._n += 1
        return self.last_ms

    def observe(self, arrival_ms: int) -> int | None:
        """Stamp the next frame (see last_ms) and return gap_ms if it arrived too late."""
        if self._anchor is None:
            self._anchor = arrival_ms
        self.jitter_ms = arrival_ms - (self._anchor + self._n * self.frame_ms)
        gap: int | None = None
        if self.jitter_ms > self.gap_threshold_ms:
            gap = self.jitter_ms
            self._n += gap // self.frame_ms
        self.stamp()
        return gap
