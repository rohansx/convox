"""tools/live_loopback.py L7 variants: --noise-snr (pink room noise on the mic, during
and between the lines), --speak-at-start (the first line starts as soon as the opening
does) and --opening-line; the measuring copes with an opening still playing."""

from __future__ import annotations

import numpy as np
import pytest

from dubhash.audio.earcon import EARCON
from dubhash.audio.frames import FRAME_BYTES
from tests.audio_fixtures import dbfs, speech
from tools import live_loopback as ll


def test_measure_finds_the_earcon_behind_an_opening_still_playing():
    voice = b"\x00\x10" * (FRAME_BYTES // 2)
    rec = voice * 12 + EARCON + ll.SILENCE + voice * 3  # opening, cut, then the translation
    row = ll.TurnRow(0, "a", "x", n_start=0, n_voice_end=5, n_next=len(rec) // FRAME_BYTES)
    ll.measure(row, rec)
    assert row.earcon_ms == (12 - 5) * 20
    assert row.heard_ms == (12 + len(EARCON) // FRAME_BYTES + 1 - 5) * 20


def test_room_noise_is_snr_below_the_speech_and_continuous():
    utts = [ll.Utt("a", speech(1000)), ll.Utt("b", speech(800, f0=200))]
    noise = ll.room_noise(utts, snr_db=15, ms=2000, seed=1)
    assert len(noise) == 100 and all(len(f) == FRAME_BYTES for f in noise)
    level = dbfs(b"".join(noise))
    assert abs((dbfs(speech(1000)) - level) - 15) < 1.0


async def test_tab_mixes_noise_into_every_mic_frame():
    class WS:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        async def send(self, data: bytes) -> None:
            self.sent.append(data)

    ws = WS()
    tab = ll.Tab("a", ws)
    noise = [np.full(FRAME_BYTES // 2, 100, dtype=np.int16).tobytes()] * 3
    tab.noise = noise
    tab.mic.append(np.full(FRAME_BYTES // 2, 1000, dtype=np.int16).tobytes())
    for _ in range(4):
        await tab.tick()
    vals = [int(np.frombuffer(f, dtype=np.int16)[0]) for f in ws.sent]
    assert vals == [1100, 100, 100, 100]  # speech + noise, then noise alone, looping


@pytest.fixture
def server():
    with ll.serve({"dubhash_env": "test"}, None) as srv:
        yield srv


async def test_speak_at_start_does_not_wait_for_the_opening(server):
    utts = ll.fake_utts(ll.SCRIPT[:1])
    opts = ll.Opts(opening="talk", pause_ms=300, quiet_ms=400, turn_timeout_s=6,
                   opening_line="full")  # fmt: skip
    result = await ll.run_call(server, utts, opts)
    assert result["opening"]["wait_ms"] <= 300  # started while the opening played
    assert result["status"] == "completed"
    (row,) = result["rows"]
    # the turn window waits for the translation, not for the opening still arriving
    assert row.earcon_ms is not None and 0 < row.heard_ms < 1500, row


async def test_opening_line_is_passed_to_the_call(server):
    utts = ll.fake_utts(ll.SCRIPT[:1])
    opts = ll.Opts(opening="skip", pause_ms=300, quiet_ms=400, turn_timeout_s=6,
                   opening_line="off", opening_timeout_s=1.0)  # fmt: skip
    result = await ll.run_call(server, utts, opts)
    assert result["opening"]["bytes"] == {"a": 0, "b": 0} or not result["opening"]["received"]


async def test_tab_keeps_every_mark_name_it_received():
    """The turn window waits for the translation's own mark (not "opening"): with an
    opening still arriving, "any bytes received" was the opening, not the translation."""

    class WS:
        def __init__(self, msgs: list) -> None:
            self.msgs = msgs

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.msgs:
                raise StopAsyncIteration
            return self.msgs.pop(0)

    msgs = [b"\x00" * 640, '{"type": "mark", "name": "opening"}', '{"type": "mark", "name": "a:0"}']
    tab = ll.Tab("b", WS(msgs))
    await tab.reader()
    assert tab.mark_names == ["opening", "a:0"]
    assert ll.translation_marks(tab, 0) == ["a:0"]


def test_cli_flags():
    a = ll.parse_args(["--noise-snr", "15", "--speak-at-start", "--opening-line", "short"])
    assert a.noise_snr == 15 and a.speak_at_start and a.opening_line == "short"


def test_skip_starts_the_script_later():
    """--skip 4 --n 1 on delivery-mr-hi: the short "OTP 4729" line first, for the
    speak-at-start variant (a short first line is what the opening hold delayed most)."""
    a = ll.parse_args(["--script", "delivery-mr-hi", "--skip", "4", "--n", "2"])
    lines = ll.script_lines(ll.SCRIPTS["delivery-mr-hi"], a.skip, a.n)
    assert [ln.text for ln in lines] == [ll.SCRIPTS["delivery-mr-hi"].lines[i].text for i in (4, 5)]
