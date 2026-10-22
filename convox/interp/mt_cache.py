"""Exact-match LRU translation cache, per call, in memory only (never persisted: it holds
user text), plus an optional store of phrase-pack translations shared by every call in
the process (L10).

Key normalisation (mt_key) only drops what cannot change the meaning: NFC, zero-width
joiners, letter case, repeated whitespace and a final "." / "।" / "!". A "?" is kept: a
question is not a statement.

`shared`: translations written only by prefill() (session/prefill.py, the phrase pack's
canonical lines), never by translate(), so no caller's words reach another call. The
second call of a process prefills without any MT request.
"""

from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict

from convox.interp.contracts import MT

Key = tuple[str, str, str]
_WS = re.compile(r"\s+")
_FINAL_STOP = re.compile(r"[.।!]+$")
_ZW = dict.fromkeys(map(ord, "​‌‍﻿"))

# The process-wide phrase-pack store (build_providers passes it to real-provider calls).
SHARED_PHRASES: dict[Key, str] = {}


def mt_key(text: str, src: str, tgt: str) -> Key:
    t = _WS.sub(" ", unicodedata.normalize("NFC", text).translate(_ZW)).strip().lower()
    return (_FINAL_STOP.sub("", t).rstrip(), src, tgt)


class CachedMT:
    def __init__(self, inner: MT, maxsize: int = 512, *, shared: dict[Key, str] | None = None):
        assert maxsize > 0
        self.inner = inner
        self.name = inner.name
        self.maxsize = maxsize
        self.shared = shared if shared is not None else {}
        self.hits = 0
        self.misses = 0
        self.last_hit = False
        self._cache: OrderedDict[Key, str] = OrderedDict()

    def _get(self, key: Key) -> str | None:
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        return self.shared.get(key)

    def has(self, text: str, src: str, tgt: str) -> bool:
        key = mt_key(text, src, tgt)
        return key in self._cache or key in self.shared

    async def translate(self, text: str, src: str, tgt: str) -> str:
        key = mt_key(text, src, tgt)
        cached = self._get(key)
        if cached is not None:
            self.hits += 1
            self.last_hit = True
            return cached
        self.misses += 1
        self.last_hit = False
        out = await self.inner.translate(text, src, tgt)
        self._put(key, out)
        return out

    async def prefill(self, text: str, src: str, tgt: str) -> str:
        """Translate a phrase-pack line into the shared store (no-op when it is there)."""
        key = mt_key(text, src, tgt)
        cached = self._get(key)
        if cached is None:
            cached = await self.inner.translate(text, src, tgt)
        self.shared[key] = cached
        return cached

    def _put(self, key: Key, out: str) -> None:
        self._cache[key] = out
        if len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)

    def __len__(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()
