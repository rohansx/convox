"""The phone page's audio code, run under node with small stand-ins for the Web Audio and
DOM APIs (tests/join/js/*.cjs, L7): playout starts at once, mic frames go out one by one,
and the page reports telemetry over the leg's websocket with exactly these field names
(the call recorder reads them):

  {"type":"telemetry","kind":"play_start"|"play_end","tag","client_ms","audio_ctx_ms"}
  {"type":"telemetry","kind":"mic_level","rms","client_ms"}          every 500 ms
  {"type":"telemetry","kind":"pong","server_ms","client_ms"}         answers {"type":"ping"}
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "convox" / "console" / "static"
JS = Path(__file__).resolve().parent / "js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def node(harness: str, *args: str) -> dict:
    r = subprocess.run(
        [str(NODE), str(JS / harness), *args], capture_output=True, text=True, timeout=20
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def run_worklet(steps: list[list[object]]) -> dict:
    return node("worklet_harness.cjs", str(STATIC / "join_worklet.js"), json.dumps(steps))


def of(posted: list[dict], kind: str) -> list[dict]:
    return [m for m in posted if m.get("type") == kind]


# --- the player and capture worklets ------------------------------------------------------


def test_playout_starts_in_the_first_block_after_audio_arrives():
    out = run_worklet([["blocks", 2], ["push", 320], ["blocks", 1]])["out"]
    assert [x[1] for x in out] == [False, False, True]  # no jitter buffer before playing


def test_play_start_carries_the_tag_and_the_context_time_of_the_first_sample():
    """The mark (the tag) comes after the audio: play_start waits for it but keeps the
    time the first sample actually played."""
    got = run_worklet(
        [["blocks", 3], ["push", 320], ["blocks", 2], ["mark", "a:0"], ["blocks", 6]]
    )["posted"]
    (start,) = of(got, "play_start")
    assert start["name"] == "a:0"
    assert start["ctx_ms"] == pytest.approx(3 * 128 / 48, abs=0.01)  # block 3, sample 0
    (end,) = of(got, "play_end")
    assert end["name"] == "a:0" and end["ctx_ms"] > start["ctx_ms"]
    assert [m["name"] for m in of(got, "mark")] == ["a:0"]  # the ack is still sent


def test_play_start_is_immediate_when_the_mark_is_already_queued():
    got = run_worklet([["push", 320], ["mark", "b:1"], ["blocks", 1]])["posted"]
    (start,) = of(got, "play_start")
    assert start["name"] == "b:1" and start["ctx_ms"] == 0


def test_a_clear_ends_the_playing_segment():
    got = run_worklet([["push", 4800], ["mark", "a:2"], ["blocks", 2], ["clear"], ["blocks", 1]])
    posted = got["posted"]
    (end,) = of(posted, "play_end")
    assert end["name"] == "a:2" and end["cleared"] is True
    assert of(posted, "mark") == []  # cleared: never acked


def test_capture_frames_carry_the_rms_before_the_gate():
    f = node("capture_harness.cjs", str(STATIC / "join_worklet.js"))
    assert f["rms"] == pytest.approx(0.5, abs=0.01) and f["gated"] and f["zero"]


# --- the page (join_phone.js) -------------------------------------------------------------


@pytest.fixture(scope="module")
def page() -> dict:
    return node("page_harness.cjs", str(STATIC / "join_phone.js"))


def telemetry(page: dict, kind: str) -> list[dict]:
    return [m for m in page["sent"] if m.get("type") == "telemetry" and m.get("kind") == kind]


def test_mic_uses_echo_cancellation_noise_suppression_and_agc(page):
    audio = page["constraints"]["audio"]
    assert audio["echoCancellation"] and audio["noiseSuppression"] and audio["autoGainControl"]


def test_every_mic_frame_is_sent_at_once(page):
    assert page["binary"] == 25


def test_pong_answers_a_ping(page):
    (pong,) = telemetry(page, "pong")
    assert set(pong) == {"type", "kind", "server_ms", "client_ms"}
    assert pong["server_ms"] == 4242 and pong["client_ms"] == pytest.approx(1.7e12 + 1500)


def test_play_start_and_end_map_the_context_time_to_the_client_clock(page):
    (start,) = telemetry(page, "play_start")
    (end,) = telemetry(page, "play_end")
    assert set(start) == {"type", "kind", "tag", "client_ms", "audio_ctx_ms"}
    assert start["tag"] == "b:0" and start["audio_ctx_ms"] == 2100
    # output timestamp: context time 2.0 s was heard at performance time 900 ms
    assert start["client_ms"] == pytest.approx(1.7e12 + 900 + 100)
    assert end["tag"] == "b:0" and end["client_ms"] == pytest.approx(1.7e12 + 900 + 1100)


def test_mic_level_every_500_ms(page):
    (lvl,) = telemetry(page, "mic_level")
    assert lvl["rms"] == pytest.approx(0.1) and "client_ms" in lvl


def test_marks_are_still_acked(page):
    assert {"type": "mark", "name": "b:0"} in page["sent"]
