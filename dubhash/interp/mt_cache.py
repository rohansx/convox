"""Exact-match LRU translation cache, per process, in memory only (never persisted: it
holds user text). Key normalisation is strip + lowercase; measure the hit rate before
normalising further."""

from __future__ import annotations

from collections import OrderedDict

from dubhash.interp.contracts import MT


class CachedMT:
    def __init__(self, inner: MT, maxsize: int = 512) -> None:
        assert maxsize > 0
        self.inner = inner
        self.name = inner.name
        self.maxsize = maxsize
        self.hits = 0
        self.misses = 0
        self.last_hit = False
        self._cache: OrderedDict[tuple[str, str, str], str] = OrderedDict()

    async def translate(self, text: str, src: str, tgt: str) -> str:
        key = (text.strip().lower(), src, tgt)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self.hits += 1
            self.last_hit = True
            return cached
        self.misses += 1
        self.last_hit = False
        out = await self.inner.translate(text, src, tgt)
        self._cache[key] = out
        if len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)
        return out

    def __len__(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()
