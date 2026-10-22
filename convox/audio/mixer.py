"""Builds the listener's outbound stream for one playback: earcon, then TTS at 0 dB with,
optionally, the speaker's original mixed underneath at duck_db.

duck_ms limits the original to the first duck_ms of the TTS (a short "a real person spoke"
cue on telephony calls); None keeps it under the whole translation. Calls with co-located
phones (/join, loopback) pass no source at all: the listener already heard the original in
the room, and replaying it is the "double sound" (docs/tech-spec/audio.md section 3).

Decisions (open questions in docs/tech-spec/audio.md section 6):
- The earcon plays at translation start, immediately before the TTS audio.
- The ducked original is the speaker's buffered utterance replayed under the translation,
  aligned to the first TTS sample, not the speaker's live frames.

All sums are done in int32 and saturated to int16; nothing ever wraps.

Clipping is reported through a caller-owned `RenderStats` passed as `stats=` to
`render_playback`; it is updated in place as frames are yielded, so it is valid even if the
playback is cut short (barge-in) and the generator is closed early.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import numpy as np

from convox.audio.earcon import EARCON
from convox.audio.frames import FRAME_BYTES, Reframer, ms_to_bytes

# Bulbul starts every clip with 100-320 ms of (near-)digital silence (live, 21 Oct): heard
# after the earcon as a gap. Dropping it brings the voice forward by that much.
TRIM_LEAD_MS = 400
TRIM_LEAD_PEAK = 8  # |sample| <= this counts as silence (about -72 dBFS)
_I16_MIN = -32768
_I16_MAX = 32767


def db_to_gain(db: float) -> float:
    return 10 ** (db / 20)


def _saturate(acc: np.ndarray) -> tuple[bytes, bool]:
    clipped = bool((acc > _I16_MAX).any() or (acc < _I16_MIN).any())
    return np.clip(acc, _I16_MIN, _I16_MAX).astype(np.int16).tobytes(), clipped


def mix(base: bytes, overlay: bytes, overlay_db: float) -> tuple[bytes, bool]:
    """base at unity + overlay at overlay_db. Output length == len(base); overlay is
    zero-padded or truncated. Returns (pcm16, clipped)."""
    b = np.frombuffer(base, dtype=np.int16)
    o = np.frombuffer(overlay, dtype=np.int16)[: len(b)]
    if len(o) == 0:
        return base, False
    acc = b.astype(np.int32)
    acc[: len(o)] += np.rint(o.astype(np.float32) * db_to_gain(overlay_db)).astype(np.int32)
    return _saturate(acc)


def passthrough_gain(frame: bytes, db: float = 0.0) -> bytes:
    """Bypass mode: forward a frame at db gain (0 dB returns it unchanged)."""
    if db == 0.0:
        return frame
    x = np.frombuffer(frame, dtype=np.int16).astype(np.float32) * db_to_gain(db)
    return _saturate(np.rint(x).astype(np.int32))[0]


def _trim_zeros(chunk: bytes, limit: int) -> tuple[bytes, int]:
    """Drop leading near-zero samples, at most `limit` bytes. Returns (rest, limit left)."""
    n = len(chunk) // 2 * 2
    x = np.frombuffer(chunk[:n], dtype=np.int16).astype(np.int32)
    nz = np.flatnonzero(np.abs(x) > TRIM_LEAD_PEAK)
    lead = int(nz[0]) * 2 if nz.size else n
    cut = min(lead, limit // 2 * 2)
    return chunk[cut:], limit - cut


@dataclass(slots=True)
class RenderStats:
    frames: int = 0  # frames yielded, earcon included
    clipped: bool = False


async def render_playback(
    tts: AsyncIterator[bytes],
    *,
    source_audio: bytes = b"",
    duck_db: float = -14.0,
    duck_ms: int | None = None,
    earcon: bytes | None = EARCON,
    stats: RenderStats | None = None,
    trim_lead_ms: int = TRIM_LEAD_MS,
) -> AsyncIterator[bytes]:
    """Yield 640-byte frames: earcon, then TTS reframed, with source_audio mixed underneath at
    duck_db from the first TTS sample until either runs out, or for at most duck_ms of TTS
    when duck_ms is given (0 = no source). Ends when the TTS ends; the last
    partial frame is zero-padded (source is not mixed into the padding). Streams: each frame
    is yielded as soon as the TTS bytes for it arrive. Up to trim_lead_ms of leading
    near-silent TTS samples (|x| <= TRIM_LEAD_PEAK) are dropped (the source then aligns
    to the first kept sample)."""
    st = stats if stats is not None else RenderStats()
    if earcon:
        framer = Reframer()
        pieces = framer.push(earcon)
        tail = framer.flush()
        for f in pieces + ([tail] if tail else []):
            st.frames += 1
            yield f

    offset = 0  # bytes of TTS (and so of source) consumed
    if duck_ms is not None:
        source_audio = source_audio[: ms_to_bytes(max(0, duck_ms))]

    def _duck(chunk: bytes) -> bytes:
        nonlocal offset
        overlay = source_audio[offset : offset + len(chunk)]
        offset += len(chunk)
        if not overlay:
            return chunk
        out, clipped = mix(chunk, overlay, duck_db)
        st.clipped |= clipped
        return out

    framer = Reframer()
    trim_left = ms_to_bytes(trim_lead_ms)  # leading zero bytes we may still drop
    async for chunk in tts:
        if trim_left:
            chunk, trim_left = _trim_zeros(chunk, trim_left)
            if not chunk:
                continue
            trim_left = 0  # first kept sample: never trim again
        for f in framer.push(chunk):
            out = _duck(f)
            st.frames += 1
            yield out
    rest = framer.flush(pad=False)
    if rest:
        out = _duck(rest).ljust(FRAME_BYTES, b"\x00")
        st.frames += 1
        yield out
