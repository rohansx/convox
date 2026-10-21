"""The provider set for one call, chosen from the pair config and settings.

- STT per source side, by `pair.side(s).stt` (one instance per side: each holds its own
  stream/socket state).
- TTS keyed by the side whose language it speaks, by `pair.side(s).tts`. Interpreter
  A->B synthesises with `tts["b"]`; the opening line and line check to leg X use `tts[X]`.
- MT by settings ("sarvam" unless MT_IMPL=fake or the pair is all-fake), wrapped in the
  per-call CachedMT.
- VAD: SileroVAD when the model loads, else EnergyVAD; one fresh instance per call to
  `vad_factory()` (Silero keeps recurrent state per leg).

Fake names, *_IMPL=fake and DUBHASH_ENV=test all map to the fakes (providers.registry).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dubhash.config.pairs import PairConfig
from dubhash.config.settings import Settings
from dubhash.interp.contracts import MT, STT, TTS, VAD
from dubhash.interp.mt_cache import CachedMT
from dubhash.interp.vad import SileroVAD
from dubhash.providers.fakes import EnergyVAD
from dubhash.providers.registry import build_mt, build_stt, build_tts
from dubhash.types import Side

log = logging.getLogger(__name__)

SIDES: tuple[Side, Side] = ("a", "b")


@dataclass(slots=True)
class Providers:
    stt: dict[Side, STT]
    mt: MT
    tts: dict[Side, TTS]  # keyed by the side whose language it speaks (the TARGET side)
    vad_factory: Callable[[], VAD]


def _all_fake(pair: PairConfig) -> bool:
    return all(pair.side(s).stt == "fake" and pair.side(s).tts == "fake" for s in SIDES)


def _vad_factory() -> Callable[[], VAD]:
    try:
        SileroVAD()
    except Exception as e:  # missing model, broken onnxruntime, ...
        log.warning("vad.fallback", extra={"error": repr(e), "vad": "energy"})
        return EnergyVAD
    return SileroVAD


async def prewarm(providers: Providers, pair: PairConfig) -> None:
    """Open every provider connection the call will need before anyone speaks (L7): the
    STT socket per source side (in that side's language), the TTS socket per target side,
    and the MT HTTPS connection. Providers without a prewarm() are skipped; failures are
    logged and never raised (the first utterance then connects as before)."""
    jobs: list[tuple[str, Any]] = []
    for s in SIDES:
        cfg = pair.side(s)
        if (fn := getattr(providers.stt[s], "prewarm", None)) is not None:
            jobs.append((f"stt:{s}", fn(cfg.lang)))
        if (fn := getattr(providers.tts[s], "prewarm", None)) is not None:
            jobs.append((f"tts:{s}", fn(cfg.voice_f)))
    mt = getattr(providers.mt, "inner", providers.mt)
    if (fn := getattr(mt, "prewarm", None)) is not None:
        jobs.append(("mt", fn()))
    if not jobs:
        return
    t0 = time.monotonic()
    results = await asyncio.gather(*(j for _, j in jobs), return_exceptions=True)
    for (what, _), res in zip(jobs, results, strict=True):
        if isinstance(res, BaseException):
            log.warning(
                "provider.prewarm_failed %s",
                what,
                extra={"provider": what, "stage": "prewarm", "error": repr(res)},
            )
    log.info(
        "provider.prewarm",
        extra={"pair": pair.key, "latency_ms": int((time.monotonic() - t0) * 1000)},
    )


def build_providers(pair: PairConfig, settings: Settings) -> Providers:
    mt_name = "fake" if _all_fake(pair) else "sarvam"
    return Providers(
        stt={s: build_stt(pair.side(s).stt, settings) for s in SIDES},
        mt=CachedMT(build_mt(mt_name, settings)),
        tts={s: build_tts(pair.side(s).tts, settings) for s in SIDES},
        vad_factory=_vad_factory(),
    )
