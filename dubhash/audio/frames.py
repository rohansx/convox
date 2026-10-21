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
