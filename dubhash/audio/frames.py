"""Internal audio format constants. PCM16 mono, 16 kHz, 20 ms frames (640 bytes)."""

SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2  # bytes, int16
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_WIDTH  # 640


def ms_to_bytes(ms: int, sample_rate: int = SAMPLE_RATE) -> int:
    return sample_rate * ms // 1000 * SAMPLE_WIDTH


def bytes_to_ms(n: int, sample_rate: int = SAMPLE_RATE) -> int:
    return n * 1000 // (sample_rate * SAMPLE_WIDTH)


class Reframer:
    """Turns arbitrary-length PCM16 byte chunks into fixed-size frames (default 640 B)."""

    def __init__(self, frame_bytes: int = FRAME_BYTES) -> None:
        assert frame_bytes % SAMPLE_WIDTH == 0
        self.frame_bytes = frame_bytes
        self._buf = bytearray()

    def push(self, chunk: bytes) -> list[bytes]:
        self._buf += chunk
        n = len(self._buf) // self.frame_bytes * self.frame_bytes
        out = [bytes(self._buf[i : i + self.frame_bytes]) for i in range(0, n, self.frame_bytes)]
        del self._buf[:n]
        return out

    def flush(self, pad: bool = True) -> bytes | None:
        """Return the leftover partial frame (zero-padded if pad), or None if empty."""
        if not self._buf:
            return None
        rest = bytes(self._buf)
        self._buf.clear()
        return rest.ljust(self.frame_bytes, b"\x00") if pad else rest

    @property
    def pending(self) -> int:
        return len(self._buf)
