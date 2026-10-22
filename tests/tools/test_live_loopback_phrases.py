"""tools/live_loopback.py phrase-cache runs (L10): the phrase script, --vertical,
--prefill wait|off|bg, and the new summary columns (from speech start, cache hit rates)."""

from __future__ import annotations

import pytest

from convox.interp.phrases import load_pack
from tools import live_loopback as ll


def test_phrase_script_is_mostly_pack_lines():
    sc = ll.SCRIPTS["phrases-mr-hi"]
    assert (sc.a_lang, sc.b_lang) == ("mr-IN", "hi-IN")
    pack = load_pack("delivery")
    hits = [
        ln for ln in sc.lines if pack.match(ln.text, sc.a_lang if ln.side == "a" else sc.b_lang)
    ]
    assert len(hits) >= 0.7 * len(sc.lines)
    assert any("4729" in ln.text for ln in sc.lines)  # one number line for the read-back


def test_monologue_script_keeps_the_listener_quiet_for_over_30_s():
    """For the next-turn prewarm: B answers only after six A lines (Bulbul TTS sockets idle
    past max_idle_s, 30 s, are reconnected)."""
    sc = ll.SCRIPTS["monologue-mr-hi"]
    sides = "".join(ln.side for ln in sc.lines)
    assert sides == "aaaaaab" * 2


def test_cli_flags():
    a = ll.parse_args(["--vertical", "clinic", "--prefill", "wait"])
    assert a.vertical == "clinic" and a.prefill == "wait"
    assert ll.parse_args([]).prefill == "bg" and ll.parse_args([]).vertical is None


def test_settings_for_the_prefill_modes():
    assert ll.prefill_settings("off") == {"phrase_prefill": False, "tts_cache_dir": ""}
    assert ll.prefill_settings("wait") == {} and ll.prefill_settings("bg") == {}


def test_summary_adds_from_start_and_hit_rates():
    rows = [
        ll.TurnRow(0, "a", "x", n_start=0, n_voice_end=50, n_next=200, heard_ms=900),
        ll.TurnRow(1, "b", "y", n_start=200, n_voice_end=230, n_next=400, heard_ms=300),
    ]
    rows[0].server = [{"t_speech_end": 1000, "t_eou": 1550, "t_stt_final": 1600, "t_mt": 2000,
                       "t_tts_ttfb": 2200, "t_play_start": 2200,
                       "flags": {"phrase_hit": 0, "tts_cache_hit": 0, "mt_cache_hit": 0,
                                 "stt_open_ms": 40}}]  # fmt: skip
    rows[1].server = [{"t_speech_end": 5000, "t_eou": 5550, "t_stt_final": 5560, "t_mt": 5560,
                       "t_tts_ttfb": 5560, "t_play_start": 5570,
                       "flags": {"phrase_hit": 1, "ack": 1, "tts_cache_hit": 1,
                                 "mt_cache_hit": 1, "stt_open_ms": 2}}]  # fmt: skip
    s = ll.summarize({"rows": rows})
    assert s["from_start"]["p50"] in (900 + 1000, 300 + 600)  # heard + speech length
    assert s["from_start"]["n"] == 2
    assert s["stt_open"]["n"] == 2
    assert s["tts_hit"]["n"] == 1 and s["tts_hit"]["p50"] == 0  # ttfb when the cache served it
    assert s["tts_miss"]["p50"] == 200
    rates = ll.hit_rates({"rows": rows})
    assert rates == {"turns": 2, "phrase_hit": 1, "ack": 1, "mt_cache_hit": 1,
                     "tts_cache_hit": 1, "number_fix": 0}  # fmt: skip


@pytest.fixture
def server():
    with ll.serve({"convox_env": "test", "phrase_prefill_rps": 1000.0}, None) as srv:
        yield srv


async def test_prefill_wait_waits_for_the_sessions_prefill(server):
    utts = ll.fake_utts(ll.SCRIPT[:1])
    opts = ll.Opts(opening="skip", pause_ms=300, quiet_ms=400, turn_timeout_s=6,
                   vertical="delivery", prefill="wait")  # fmt: skip
    result = await ll.run_call(server, utts, opts)
    assert result["status"] == "completed"
    assert result["prefill"]["done"] and result["prefill"]["lines"] > 0
