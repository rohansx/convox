import httpx

from dubhash.api.app import create_app
from dubhash.api.console import mask_number, turn_view
from dubhash.api.deps import InMemoryCallRegistry
from dubhash.config.settings import Settings
from dubhash.types import CallEvent, Turn


def client(reg) -> httpx.AsyncClient:
    app = create_app(Settings(_env_file=None, dubhash_env="test"), registry=reg)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_mask_number():
    assert mask_number("+919876543210") == "+91 98•••••210"
    assert mask_number(None) == ""
    assert "9876543" not in mask_number("+919876543210")


def test_turn_view_stages():
    v = turn_view(
        {
            "t_speech_end": 7450,
            "t_eou": 8000,
            "t_stt_final": 8290,
            "t_mt": 8500,
            "t_tts_ttfb": 8800,
            "t_play_start": 8950,
        }
    )
    assert v["stages"] == [("eou", 550), ("stt", 290), ("mt", 210), ("tts", 300), ("play", 150)]
    assert v["first_audio_ms"] == 1500 and v["grade"] == "warn"
    assert turn_view({"t_speech_end": 0, "t_play_start": 1200})["grade"] == "good"
    assert turn_view({"t_speech_end": 0, "t_play_start": 2500})["grade"] == "bad"


async def test_call_view_renders_markers_backchannel_and_summary():
    reg = InMemoryCallRegistry()
    reg.put({"call_id": "c_91", "a_lang": "hi-IN", "b_lang": "kn-IN", "status": "in_progress"})
    t = Turn(call_id="c_91", side="a", seq=1, src_text="bhaiya main gate", tgt_text="ಗೇಟ್",
             t_speech_end=7450, t_play_start=8950)  # fmt: skip
    bc = Turn(call_id="c_91", side="b", seq=2, src_text="ಸರಿ", backchannel=True, t_speech_end=9000)
    reg.apply_event(CallEvent("c_91", 1, "turn", {"turn": t}))
    reg.apply_event(CallEvent("c_91", 2, "turn", {"turn": bc}))
    reg.apply_event(CallEvent("c_91", 9500, "barge_in", {"side": "b", "clipped_ms": 840}))
    reg.apply_event(CallEvent("c_91", 9600, "dtmf", {"side": "a", "digit": "1"}))
    reg.apply_event(CallEvent("c_91", 9700, "gap", {"side": "a", "gap_ms": 2000}))
    summary = {
        "status": "completed",
        "duration_s": 95,
        "interpreted_s": 80,
        "turns": 1,
        "p50_first_audio_ms": 1500,
        "p95_first_audio_ms": 1820,
        "escape_used": False,
    }
    reg.apply_event(CallEvent("c_91", 10_000, "call", summary))
    async with client(reg) as c:
        body = (await c.get("/console/calls/c_91")).text
        index = (await c.get("/console")).text
    assert "backchannel · not translated" in body
    assert "barge-in" in body and "clipped 840 ms" in body
    assert "DTMF 1" in body and "replay" in body and "audio gap 2000 ms" in body
    assert "Call summary" in body and "95 s" in body and "1.82 s" in body
    assert "bhaiya main gate" in body  # the turn list survives the summary event
    assert "completed" in index


async def test_call_view_shows_dash_for_text_not_retained():
    reg = InMemoryCallRegistry()
    reg.put({"call_id": "c_2", "status": "completed"})
    t = Turn(call_id="c_2", side="a", seq=1, t_speech_end=100, t_play_start=1300)
    reg.apply_event(CallEvent("c_2", 1, "turn", {"turn": t}))
    async with client(reg) as c:
        r = await c.get("/console/calls/c_2")
    assert r.status_code == 200 and "—" in r.text and "1.20 s" in r.text
    assert 'id="summary"' in r.text


async def test_index_shows_running_p50_and_empty_state():
    reg = InMemoryCallRegistry()
    async with client(reg) as c:
        assert "No calls yet" in (await c.get("/console")).text
    reg.put({"call_id": "c_3", "status": "in_progress"})
    t = Turn(call_id="c_3", side="a", seq=1, t_speech_end=100, t_play_start=1400)
    reg.apply_event(CallEvent("c_3", 1, "turn", {"turn": t}))
    async with client(reg) as c:
        assert "1.30 s" in (await c.get("/console")).text
