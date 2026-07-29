"""The channel simulator: what happens to the caller's voice on the way out.

A persona describes a caller in a place, on a device, over a network. This turns
that description into the actual damage — background noise at a chosen SNR, a
narrowband codec, packet loss, jitter — so the agent hears what it would hear on
a real call rather than a studio recording.

Deterministic given a seed. That is a hard requirement rather than a nicety: a
suite whose conditions shift between runs cannot tell a regression from weather.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from convox.audio import codec, impair
from convox.model.persona import Persona


@dataclass
class ChannelDamage:
    """What the channel actually did, recorded alongside the audio."""

    codec: str = "none"
    noise_profile: str | None = None
    snr_db: float | None = None
    network: str | None = None
    frames_dropped: int = 0
    extras: dict = field(default_factory=dict)

    def as_payload(self) -> dict:
        return {
            "codec": self.codec,
            "noise_profile": self.noise_profile,
            "snr_db": self.snr_db,
            "network": self.network,
            "frames_dropped": self.frames_dropped,
            **self.extras,
        }


class ChannelSimulator:
    def __init__(self, persona: Persona, seed: int = 0) -> None:
        self.persona = persona
        self._rng = np.random.default_rng(seed)

    @property
    def describes_damage(self) -> bool:
        """True when this channel does anything at all to the signal."""
        return bool(
            self.persona.channel.codec not in ("none", None)
            or self.persona.environment.noise_profile
            or self.persona.channel.network
        )

    def process(self, samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, ChannelDamage]:
        damage = ChannelDamage()
        if len(samples) == 0:
            return samples, damage

        out = samples

        # Volume first: a quieter caller sits closer to the noise floor, which is
        # exactly how loudness affects recognition in the real world.
        if self.persona.volume_db:
            out = out * float(10 ** (self.persona.volume_db / 20))

        environment = self.persona.environment
        if environment.noise_profile:
            noise = impair.generate_noise(environment.noise_profile, len(out), sample_rate, self._rng)
            out = impair.mix_at_snr(out, noise, environment.snr_db)
            damage.noise_profile = environment.noise_profile
            damage.snr_db = environment.snr_db

        network = self.persona.channel.network
        if network:
            profile = impair.NETWORK_PROFILES.get(network)
            if profile is None:
                raise ValueError(
                    f"unknown network profile {network!r} "
                    f"(available: {', '.join(sorted(impair.NETWORK_PROFILES))})"
                )
            out, dropped = impair.apply_packet_loss(
                out, sample_rate, loss=profile["loss"], burst=profile["burst"], rng=self._rng
            )
            out = impair.apply_jitter(out, sample_rate, jitter_ms=profile["jitter_ms"], rng=self._rng)
            damage.network = network
            damage.frames_dropped = dropped

        channel_codec = self.persona.channel.codec or "none"
        if channel_codec != "none":
            out = codec.roundtrip(out, sample_rate, channel_codec)
            damage.codec = channel_codec

        return out.astype(np.float32), damage
