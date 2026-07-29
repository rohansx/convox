"""Acoustic and network impairment.

Real callers phone from streets, cars, and call-centre floors, over networks that
drop packets. An agent that only ever hears clean studio audio in testing will
meet all of that for the first time in production.

Every generator is seeded, so a scenario reruns under identical conditions and a
failure can be reproduced exactly.
"""

from __future__ import annotations

import numpy as np

from convox.audio import pcm

#: Named background environments. Each is synthesised rather than sampled, so the
#: package carries no audio assets and results stay bit-reproducible.
NOISE_PROFILES: dict[str, dict] = {
    "quiet_room": {"kind": "white", "band": (20.0, 8000.0), "gain": 0.2},
    "office_open_plan": {"kind": "pink", "band": (100.0, 4000.0), "gain": 0.6},
    "cafe": {"kind": "babble", "band": (200.0, 3500.0), "gain": 0.9},
    "restaurant": {"kind": "babble", "band": (200.0, 3500.0), "gain": 1.0},
    "market_crowd": {"kind": "babble", "band": (150.0, 4000.0), "gain": 1.2},
    "call_center_floor": {"kind": "babble", "band": (250.0, 3400.0), "gain": 0.8},
    "street_traffic": {"kind": "brown", "band": (40.0, 2000.0), "gain": 1.0},
    "street_traffic_india": {"kind": "brown", "band": (40.0, 3000.0), "gain": 1.3, "horns": True},
    "car_interior": {"kind": "brown", "band": (30.0, 800.0), "gain": 0.7},
    "train_station": {"kind": "pink", "band": (60.0, 3000.0), "gain": 1.0},
    "construction": {"kind": "brown", "band": (40.0, 2500.0), "gain": 1.4},
    "wind": {"kind": "brown", "band": (20.0, 600.0), "gain": 1.1},
    "tv_background": {"kind": "babble", "band": (150.0, 5000.0), "gain": 0.7},
    "bad_line_hum": {"kind": "hum", "band": (45.0, 200.0), "gain": 0.8},
}

#: Named network conditions: loss probability, burst persistence, jitter spread.
NETWORK_PROFILES: dict[str, dict] = {
    "wifi_good": {"loss": 0.0, "burst": 0.0, "jitter_ms": 2},
    "wifi_congested": {"loss": 0.01, "burst": 0.3, "jitter_ms": 15},
    "4g_urban": {"loss": 0.005, "burst": 0.2, "jitter_ms": 20},
    "4g_congested": {"loss": 0.03, "burst": 0.5, "jitter_ms": 45},
    "3g_rural": {"loss": 0.06, "burst": 0.6, "jitter_ms": 90},
    "3g_rural_india": {"loss": 0.08, "burst": 0.65, "jitter_ms": 120},
    "pstn_landline": {"loss": 0.0, "burst": 0.0, "jitter_ms": 0},
    "satellite": {"loss": 0.02, "burst": 0.4, "jitter_ms": 250},
    "lossy_5pct": {"loss": 0.05, "burst": 0.0, "jitter_ms": 0},
    "lossy_bursty": {"loss": 0.05, "burst": 0.8, "jitter_ms": 10},
}


def _shaped_noise(length: int, sample_rate: int, exponent: float, rng: np.random.Generator) -> np.ndarray:
    """Noise with a 1/f**exponent spectrum: 0 = white, 1 = pink, 2 = brown."""
    if length <= 0:
        return np.zeros(0, dtype=np.float32)
    spectrum = np.fft.rfft(rng.standard_normal(length))
    freqs = np.fft.rfftfreq(length, d=1.0 / sample_rate)
    scale = np.ones_like(freqs)
    scale[1:] = 1.0 / np.power(freqs[1:], exponent / 2.0)
    scale[0] = 0.0
    shaped = np.fft.irfft(spectrum * scale, n=length)
    peak = np.max(np.abs(shaped)) or 1.0
    return (shaped / peak).astype(np.float32)


def _babble(length: int, sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    """Overlapping speech-like formants — the hardest background for an ASR."""
    out = _shaped_noise(length, sample_rate, 1.0, rng) * 0.3
    t = np.arange(length) / sample_rate
    for _ in range(6):
        formant = rng.uniform(200, 2500)
        wobble = np.sin(2 * np.pi * rng.uniform(1.5, 5.0) * t) * rng.uniform(10, 60)
        envelope = np.clip(np.sin(2 * np.pi * rng.uniform(0.4, 1.8) * t + rng.uniform(0, 6)), 0, 1)
        out += (np.sin(2 * np.pi * (formant + wobble) * t) * envelope * 0.25).astype(np.float32)
    peak = np.max(np.abs(out)) or 1.0
    return (out / peak).astype(np.float32)


def generate_noise(profile: str, length: int, sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    config = NOISE_PROFILES.get(profile)
    if config is None:
        raise ValueError(f"unknown noise profile {profile!r} (available: {', '.join(sorted(NOISE_PROFILES))})")

    match config["kind"]:
        case "white":
            noise = _shaped_noise(length, sample_rate, 0.0, rng)
        case "pink":
            noise = _shaped_noise(length, sample_rate, 1.0, rng)
        case "brown":
            noise = _shaped_noise(length, sample_rate, 2.0, rng)
        case "babble":
            noise = _babble(length, sample_rate, rng)
        case "hum":
            t = np.arange(length) / sample_rate
            noise = (np.sin(2 * np.pi * 50 * t) + 0.4 * np.sin(2 * np.pi * 150 * t)).astype(np.float32)
        case _:  # pragma: no cover - guarded by the table above
            noise = _shaped_noise(length, sample_rate, 1.0, rng)

    low, high = config["band"]
    noise = pcm.bandpass(noise, sample_rate, low, min(high, sample_rate * 0.49))

    if config.get("horns") and length > sample_rate:
        t = np.arange(length) / sample_rate
        for _ in range(max(1, length // (sample_rate * 3))):
            start = rng.uniform(0, max(0.1, length / sample_rate - 0.5))
            window = ((t > start) & (t < start + 0.35)).astype(np.float32)
            noise += (np.sin(2 * np.pi * rng.uniform(400, 700) * t) * window * 0.5).astype(np.float32)

    peak = np.max(np.abs(noise)) or 1.0
    return (noise / peak * config["gain"]).astype(np.float32)


def mix_at_snr(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """Add noise scaled so the speech sits `snr_db` above it."""
    speech_rms = pcm.rms(speech)
    noise_rms = pcm.rms(noise)
    if speech_rms <= 0 or noise_rms <= 0:
        return speech
    target_noise_rms = speech_rms / (10 ** (snr_db / 20))
    return pcm.mix(speech, noise * (target_noise_rms / noise_rms))


def apply_packet_loss(
    samples: np.ndarray,
    sample_rate: int,
    *,
    loss: float,
    burst: float,
    rng: np.random.Generator,
    frame_ms: int = 20,
) -> tuple[np.ndarray, int]:
    """Drop packets using a Gilbert-Elliott burst model.

    Independent per-packet loss underestimates real damage: networks lose packets
    in runs, and a 200 ms hole mid-word is a categorically different failure from
    six isolated 20 ms gaps.

    Returns the damaged audio and the number of frames dropped.
    """
    if loss <= 0 or len(samples) == 0:
        return samples, 0

    frame_samples = max(1, int(sample_rate * frame_ms / 1000))
    out = samples.copy()
    dropped = 0
    in_burst = False

    for start in range(0, len(samples), frame_samples):
        if in_burst:
            in_burst = rng.random() < burst
        else:
            in_burst = rng.random() < loss
        if in_burst:
            out[start : start + frame_samples] = 0.0
            dropped += 1
    return out, dropped


def apply_jitter(samples: np.ndarray, sample_rate: int, *, jitter_ms: int, rng: np.random.Generator) -> np.ndarray:
    """Model jitter-buffer artefacts as small local time warps."""
    if jitter_ms <= 0 or len(samples) == 0:
        return samples
    frame_samples = max(1, int(sample_rate * 0.02))
    out: list[np.ndarray] = []
    for start in range(0, len(samples), frame_samples):
        chunk = samples[start : start + frame_samples]
        wobble = int(rng.normal(0, jitter_ms / 10))
        if wobble > 0:
            chunk = np.pad(chunk, (0, min(wobble, frame_samples // 2)))
        elif wobble < 0 and len(chunk) > abs(wobble):
            chunk = chunk[: len(chunk) - min(abs(wobble), frame_samples // 2)]
        out.append(chunk)
    return pcm.concat(*out)
