from dubhash.api.deps import CallRegistry, InMemoryCallRegistry
from dubhash.types import CallEvent, Turn


def make() -> InMemoryCallRegistry:
    r = InMemoryCallRegistry()
    r.put({"call_id": "c_1", "a_lang": "hi-IN", "b_lang": "kn-IN", "provider": "fake"})
    return r


def test_is_a_registry_and_lists_calls():
    r = make()
    assert isinstance(r, CallRegistry)
    calls = r.list_calls()
    assert [c["call_id"] for c in calls] == ["c_1"]
    assert calls[0]["status"] == "ringing" and calls[0]["state"] == "IDLE"
    assert r.get_call("c_1")["turns"] == []
    assert r.get_call("nope") is None


def test_get_call_returns_a_copy():
    r = make()
    r.get_call("c_1")["status"] = "hacked"
    assert r.get_call("c_1")["status"] == "ringing"


def test_apply_event_tracks_state_turns_and_markers():
    r = make()
    r.apply_event(
        CallEvent("c_1", 10, "state", {"from": "IDLE", "to": "A_SPEAKING", "queue_depth": 0})
    )
    r.apply_event(
        CallEvent("c_1", 20, "turn", {"turn": Turn(call_id="c_1", side="a", seq=1, src_text="x")})
    )
    r.apply_event(
        CallEvent(
            "c_1",
            25,
            "turn",
            {"turn": {"call_id": "c_1", "side": "a", "seq": 1, "src_text": "x", "t_play_start": 9}},
        )
    )
    r.apply_event(CallEvent("c_1", 30, "bypass", {"side": "b"}))
    r.apply_event(CallEvent("c_1", 40, "call", {"status": "in_progress"}))
    r.apply_event(CallEvent("c_unknown", 40, "call", {"status": "in_progress"}))
    c = r.get_call("c_1")
    assert c["state"] == "A_SPEAKING" and c["queue_depth"] == 0
    assert len(c["turns"]) == 1 and c["turns"][0]["t_play_start"] == 9  # same seq replaced
    assert c["escape_used"] is True and c["status"] == "in_progress"
    assert [m["kind"] for m in c["markers"]] == ["bypass"]
    assert r.get_call("c_unknown") is None


def test_ended_calls_are_listed_after_active_and_capped():
    r = InMemoryCallRegistry(max_ended=2)
    for i in range(4):
        r.put({"call_id": f"c_{i}", "status": "completed"})
    r.put({"call_id": "c_live", "status": "in_progress"})
    ids = [c["call_id"] for c in r.list_calls()]
    assert ids[0] == "c_live" and len(ids) == 3 and "c_0" not in ids
