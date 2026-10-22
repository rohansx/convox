"""TTS audio cache: wraps any TTS so a phrase already synthesised in that voice and
language streams from memory (or disk) with ~0 ms first byte (L10).

- Key: (text, voice, lang), the text normalised only where that cannot change what is
  heard: NFC, whitespace collapsed, a final "." / "।" / "!" dropped. Case and "?" are kept
  ("OTP" may be read as letters, "otp" as a word; a question is intoned differently).
- TTSAudioCache: an LRU bounded by bytes (default 64 MB), shared by every call in the
  process, plus an optional directory (out/tts_cache/, gitignored) of raw PCM16 16 kHz
  files so phrase-pack audio survives restarts. Only entries stored with persist=True (the
  phrase-pack prefill, session/prefill.py) go to disk: an ordinary turn's audio is the
  user's content and stays in memory only.
- CachedTTS: per call, around that call's own TTS instance. A miss streams from the inner
  TTS as before and stores the audio once the stream completes; an abandoned (barge-in,
  discarded early final) or failed stream is never stored, so a cut-off clip can never be
  served as the whole phrase.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import unicodedata
from collections import OrderedDict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from convox.audio.frames import ms_to_bytes
from convox.interp.contracts import TTS

log = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = ms_to_bytes(100)  # a hit is streamed in 100 ms chunks, like a vendor
_WS = re.compile(r"\s+")
_FINAL_STOP = re.compile(r"[.।!]+$")

Key = tuple[str, str, str]


def tts_key(text: str, voice: str, lang: str) -> Key:
    t = _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()
    t = _FINAL_STOP.sub("", t).rstrip()
    return (t, voice, lang)


def _file_name(key: Key) -> str:
    return hashlib.sha1("\x1f".join(key).encode()).hexdigest() + ".pcm"


class TTSAudioCache:
    """Process-wide PCM store: LRU in memory by bytes, optional files on disk."""

    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES, disk_dir: Path | None = None) -> None:
        self.max_bytes = max_bytes
        self.disk_dir = disk_dir
        self._mem: OrderedDict[Key, bytes] = OrderedDict()
        self.bytes = 0
        self.hits = 0
        self.misses = 0
        self.disk_hits = 0
        self.evictions = 0
        self._on_disk: set[str] = set()
        if disk_dir is not None:
            try:
                disk_dir.mkdir(parents=True, exist_ok=True)
                self._on_disk = {p.name for p in disk_dir.glob("*.pcm")}
            except OSError as e:
                log.warning("tts_cache.disk_unavailable", extra={"error": repr(e)})
                self.disk_dir = None

    def __contains__(self, key: Key) -> bool:
        return key in self._mem or _file_name(key) in self._on_disk

    def __len__(self) -> int:
        return len(self._mem)

    async def get(self, key: Key) -> bytes | None:
        pcm = self._mem.get(key)
        if pcm is not None:
            self._mem.move_to_end(key)
            self.hits += 1
            return pcm
        name = _file_name(key)
        if self.disk_dir is not None and name in self._on_disk:
            try:
                pcm = await asyncio.to_thread((self.disk_dir / name).read_bytes)
            except OSError:
                self._on_disk.discard(name)
                pcm = None
            if pcm:
                self.hits += 1
                self.disk_hits += 1
                self._remember(key, pcm)
                return pcm
        self.misses += 1
        return None

    async def put(self, key: Key, pcm: bytes, *, persist: bool = False) -> None:
        if not pcm:
            return
        self._remember(key, pcm)
        if persist and self.disk_dir is not None:
            name = _file_name(key)
            if name not in self._on_disk:
                try:
                    await asyncio.to_thread(_write_atomic, self.disk_dir / name, pcm)
                    self._on_disk.add(name)
                except OSError as e:
                    log.warning("tts_cache.write_failed", extra={"error": repr(e)})

    def _remember(self, key: Key, pcm: bytes) -> None:
        if len(pcm) > self.max_bytes:
            return
        old = self._mem.pop(key, None)
        if old is not None:
            self.bytes -= len(old)
        self._mem[key] = pcm
        self.bytes += len(pcm)
        while self.bytes > self.max_bytes:
            _, dropped = self._mem.popitem(last=False)
            self.bytes -= len(dropped)
            self.evictions += 1

    def stats(self) -> dict[str, int]:
        return {
            "entries": len(self._mem),
            "bytes": self.bytes,
            "hits": self.hits,
            "misses": self.misses,
            "disk_hits": self.disk_hits,
            "evictions": self.evictions,
            "on_disk": len(self._on_disk),
        }


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class CachedTTS:
    """A TTS whose synth() is served from `store` when it can (see the module doc)."""

    def __init__(self, inner: TTS, store: TTSAudioCache) -> None:
        self.inner = inner
        self.name = inner.name
        self.store = store
        self.hits = 0  # this call's
        self.misses = 0

    def has(self, text: str, voice: str, lang: str) -> bool:
        return tts_key(text, voice, lang) in self.store

    def synth(self, text: str, voice: str, lang: str) -> AsyncIterator[bytes]:
        return self._synth(text, voice, lang)

    async def _synth(self, text: str, voice: str, lang: str) -> AsyncIterator[bytes]:
        key = tts_key(text, voice, lang)
        pcm = await self.store.get(key)
        if pcm is not None:
            self.hits += 1
            for i in range(0, len(pcm), CHUNK_BYTES):
                yield pcm[i : i + CHUNK_BYTES]
            return
        self.misses += 1
        out = bytearray()
        it = aiter(self.inner.synth(text, voice, lang))
        try:
            async for chunk in it:
                out += chunk
                yield chunk
        finally:
            with contextlib.suppress(Exception):
                aclose: Any = getattr(it, "aclose", None)
                if aclose is not None:
                    await aclose()
        # only reached when the inner stream completed and the consumer took all of it
        await self.store.put(key, bytes(out))

    async def prefill(self, text: str, voice: str, lang: str, *, persist: bool = False) -> bytes:
        """Synthesise into the cache unless it is there already; returns the audio."""
        key = tts_key(text, voice, lang)
        if key in self.store:
            pcm = await self.store.get(key)
            if pcm is not None:
                if persist:
                    await self.store.put(key, pcm, persist=True)
                return pcm
        pcm = b"".join([c async for c in self.inner.synth(text, voice, lang)])
        await self.store.put(key, pcm, persist=persist)
        return pcm

    async def prewarm(self, voice: str) -> None:
        fn = getattr(self.inner, "prewarm", None)
        if fn is not None:
            await fn(voice)

    async def aclose(self) -> None:
        fn = getattr(self.inner, "aclose", None)
        if fn is not None:
            res = fn()
            if asyncio.iscoroutine(res):
                await res
