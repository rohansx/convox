"""Adapter registry — maps a target `kind` to its adapter implementation."""

from __future__ import annotations

from convox.adapters.base import TargetAdapter
from convox.adapters.websocket import WebSocketAdapter
from convox.adapters.websocket_audio import AudioWebSocketAdapter
from convox.model.target import AdapterCapabilities, Target

_ADAPTERS: dict[str, type[TargetAdapter]] = {}


def register(adapter_cls: type[TargetAdapter]) -> type[TargetAdapter]:
    """Register an adapter. Third-party adapters call this at import time."""
    _ADAPTERS[adapter_cls.kind] = adapter_cls
    return adapter_cls


def build(target: Target) -> TargetAdapter:
    adapter_cls = _ADAPTERS.get(target.kind)
    if adapter_cls is None:
        known = ", ".join(sorted(_ADAPTERS)) or "none"
        raise ValueError(f"unknown target kind {target.kind!r} (registered: {known})")
    return adapter_cls(target)


def capabilities_for(kind: str) -> AdapterCapabilities:
    adapter_cls = _ADAPTERS.get(kind)
    return adapter_cls.capabilities if adapter_cls else AdapterCapabilities()


def kinds() -> list[str]:
    return sorted(_ADAPTERS)


register(WebSocketAdapter)
register(AudioWebSocketAdapter)
