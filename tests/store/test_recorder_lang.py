"""The call recorder with lang "auto": rec.meta marks auto sides, summary.json lists the
lang decisions, the console registry shows them as markers."""

from __future__ import annotations

import json
from types import SimpleNamespace

from dubhash.api.deps import InMemoryCallRegistry
from dubhash.clock import FakeClock
from dubhash.config.pairs import SideConfig
from dubhash.store.recorder import CallRecorder, session_meta
from dubhash.types import CallEvent

LANG = {"side": "a", "lang": "ta-IN", "state": "CONFIRMED", "confidence": 0.95, "source": "lid"}


def test_session_meta_marks_auto_sides_and_their_hints():
    side = SideConfig("hi-IN", "sarvam-translate", "bulbul", "m", "f", mt_src="en-IN")
    fixed = SideConfig("kn-IN", "sarvam", "bulbul", "m", "f")
    pair = SimpleNamespace(key="auto-kn", side=lambda s: side if s == "a" else fixed)
    session = SimpleNamespace(
        pair=pair, mode="interpret", record=False, auto=SimpleNamespace(hints={"a": "mr-IN"})
    )
    meta = session_meta(session)
    assert meta["sides"]["a"]["auto"] is True and meta["sides"]["a"]["hint"] == "mr-IN"
    assert "auto" not in meta["sides"]["b"]


async def test_summary_lists_lang_decisions(tmp_path):
    clock = FakeClock()
    rec = CallRecorder("c_l", tmp_path, clock, meta={"pair": "auto-kn"}, date="2026-10-21")
    await rec.start()
    clock.set(4000)
    rec.event(CallEvent("c_l", 4000, "lang", LANG))
    await rec.close()
    s = json.loads((rec.dir / "summary.json").read_text())
    assert s["langs"] == [{"ms": 4000, **LANG, "menu": False}]


def test_console_registry_shows_lang_markers_and_current_languages():
    r = InMemoryCallRegistry()
    r.put({"call_id": "c_1", "a_lang": "auto", "b_lang": "kn-IN"})
    r.apply_event(CallEvent("c_1", 10, "lang", LANG))
    c = r.get_call("c_1")
    assert [m["kind"] for m in c["markers"]] == ["lang"]
    assert c["langs"] == {"a": {"lang": "ta-IN", "state": "CONFIRMED"}}


def test_a_same_language_bridge_is_not_an_escape():
    r = InMemoryCallRegistry()
    r.put({"call_id": "c_1", "a_lang": "auto", "b_lang": "hi-IN"})
    r.apply_event(CallEvent("c_1", 10, "bypass", {"side": "a", "reason": "same_lang"}))
    assert r.get_call("c_1")["escape_used"] is False
    r.apply_event(CallEvent("c_1", 20, "bypass", {"side": "b", "digit": "0"}))
    assert r.get_call("c_1")["escape_used"] is True
