"""Live loopback: one scripted call through the real app, no telephony.

    STT_IMPL=real MT_IMPL=real TTS_IMPL=real uv run python tools/live_loopback.py
    STT_IMPL=real MT_IMPL=real TTS_IMPL=real uv run python tools/live_loopback.py \
        --pair hi-kn-direct
    STT_IMPL=real MT_IMPL=real TTS_IMPL=real uv run python tools/live_loopback.py \
        --join --a-lang mr-IN --b-lang hi-IN    # the stage QR path (token sockets)
    uv run python tools/live_loopback.py --fake          # all fakes, no network

The app runs in-process (uvicorn on a free port, in a thread). The tool creates a loopback
call (POST /loopback/calls, record=true so the turn log keeps text) and connects two
websocket clients that behave like static/loopback.html: each sends 20 ms PCM16 16 kHz
frames in real time (silence between turns), plays what it receives at real time into a
recording, and acks each mark when its playout reaches it.

Scripts (--script, default: the one written for --a-lang/--b-lang): delivery-hi-kn (rider
Hindi on A, customer Kannada on B; docs/product/problem-and-users.md example call 1),
delivery-mr-hi (Marathi customer, Hindi rider), clinic-mr-en (Marathi patient, English
doctor), and the same two calls in Gujarati (delivery-gu-hi, clinic-gu-en). Utterances are
synthesised once with Bulbul and cached in out/live/. THE KANNADA, MARATHI AND GUJARATI
LINES NEED A NATIVE SPEAKER'S REVIEW. Rows whose translation lost a number of
the scripted line are flagged (the ward number check).

    STT_IMPL=real MT_IMPL=real TTS_IMPL=real uv run python tools/live_loopback.py \
        --script clinic-mr-en

Per turn it measures what the listener hears: from the end of the speaker's last voiced
frame to the earcon onset (earcon_ms) and to the first non-silent frame of the translation
after the earcon (heard_ms). It joins the server's turn log (GET /v1/calls/{id}/turns) for
the stage breakdown. Writes out/live/<call>_a.wav, <call>_b.wav (what each leg heard) and
out/live/<call>.json, and prints a table with p50/p95.

Timing is the client's 20 ms tick clock: no browser jitter buffer or device latency is
modelled, so heard_ms is a lower bound on a real tab's (leg-transports.md section 5).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import html
import json
import logging
import math
import os
import re
import socket
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import websockets  # noqa: E402

from dubhash.audio.earcon import EARCON  # noqa: E402
from dubhash.audio.frames import FRAME_BYTES, FRAME_MS  # noqa: E402
from dubhash.audio.wav import read_wav, write_wav  # noqa: E402

OUT = ROOT / "out" / "live"
SILENCE = b"\x00" * FRAME_BYTES
VOICED_DBFS = -50.0  # a frame above this RMS is "non-silent"
EARCON_FRAMES = len(EARCON) // FRAME_BYTES


@dataclass(frozen=True)
class Line:
    side: str  # "a" speaks a_lang, "b" speaks b_lang
    text: str
    review: bool = False  # written by us; needs a native speaker's check


@dataclass(frozen=True)
class Script:
    a_lang: str
    b_lang: str
    voices: dict[str, str]  # side -> Bulbul voice the line is spoken in
    lines: tuple[Line, ...]


SCRIPTS: dict[str, Script] = {
    # Rider (A, Hindi, male) and customer (B, Kannada, female). Kannada lines: NATIVE REVIEW.
    "delivery-hi-kn": Script(
        "hi-IN",
        "kn-IN",
        {"a": "bulbul:hi-male-1", "b": "bulbul:kn-female-1"},
        (
            Line("a", "भैया, मैं गेट के बाहर खड़ा हूँ। कौन सी बिल्डिंग है?"),
            Line("b", "ಸಿ ಬ್ಲಾಕ್, ಮೂರನೇ ಮಹಡಿ. ಲಿಫ್ಟ್ ಕೆಲಸ ಮಾಡ್ತಿಲ್ಲ.", review=True),
            Line("a", "ठीक है, मैं सीढ़ियों से आ रहा हूँ, दो मिनट।"),
            Line("b", "ಸರಿ, ಮೆಟ್ಟಿಲುಗಳು ಎಡಗಡೆ ಇವೆ.", review=True),
            Line("a", "मैडम, ऑर्डर देने के लिए ओटीपी बता दीजिए।"),
            Line("b", "ಓಟಿಪಿ ನಾಲ್ಕು ಏಳು ಎರಡು ಒಂಬತ್ತು.", review=True),
        ),
    ),
    # Customer (A, Marathi, female) and rider (B, Hindi, male). Marathi lines: NATIVE REVIEW.
    "delivery-mr-hi": Script(
        "mr-IN",
        "hi-IN",
        {"a": "bulbul:mr-female-1", "b": "bulbul:hi-male-1"},
        (
            Line("a", "हॅलो, मी फ्लॅट 302 मधून बोलतेय. माझं पार्सल कुठे आहे?", review=True),
            Line("b", "मैडम, मैं आपकी बिल्डिंग के गेट पर खड़ा हूँ। कौन सा विंग है?"),
            Line("a", "बी विंग, तिसरा मजला. लिफ्ट बंद आहे, जिना डावीकडे आहे.", review=True),
            Line("b", "ठीक है, दो मिनट में ऊपर आता हूँ। ओटीपी बता दीजिए।"),
            Line("a", "ओटीपी 4729 आहे.", review=True),
            Line("b", "धन्यवाद मैडम, शाम को 6 बजे दूसरा पार्सल भी आएगा।"),
            Line("a", "ठीक आहे, मी 6 वाजता घरी असेन.", review=True),
        ),
    ),
    # Patient (A, Marathi, female) and clinic doctor (B, English, male). Marathi: NATIVE REVIEW.
    "clinic-mr-en": Script(
        "mr-IN",
        "en-IN",
        {"a": "bulbul:mr-female-1", "b": "bulbul:en-male-1"},
        (
            Line("a", "डॉक्टर, मला तीन दिवसांपासून ताप आहे.", review=True),
            Line("b", "Okay. Do you also have a cough or a sore throat?"),
            Line("a", "हो, थोडा खोकला आहे. रात्री जास्त होतो.", review=True),
            Line("b", "Take this tablet twice a day, after breakfast and dinner."),
            Line("a", "मी फ्लॅट 302, शिवाजी नगरमध्ये राहते. औषध घरी पाठवाल का?", review=True),
            Line("b", "Yes. Your delivery OTP is 4729. It will reach you by 6 pm."),
            Line("a", "ठीक आहे, मी 6 वाजता घरी असेन.", review=True),
        ),
    ),
    # Customer (A, Gujarati, female) and rider (B, Hindi, male). Gujarati: NATIVE REVIEW.
    "delivery-gu-hi": Script(
        "gu-IN",
        "hi-IN",
        {"a": "bulbul:gu-female-1", "b": "bulbul:hi-male-1"},
        (
            Line("a", "હેલો, હું ફ્લેટ 302 માંથી બોલું છું. મારું પાર્સલ ક્યાં છે?", review=True),
            Line("b", "मैडम, मैं आपकी बिल्डिंग के गेट पर खड़ा हूँ। कौन सा विंग है?"),
            Line("a", "બી વિંગ, ત્રીજો માળ. લિફ્ટ બંધ છે, દાદર ડાબી બાજુ છે.", review=True),
            Line("b", "ठीक है, दो मिनट में ऊपर आता हूँ। ओटीपी बता दीजिए।"),
            Line("a", "ઓટીપી 4729 છે.", review=True),
            Line("b", "धन्यवाद मैडम, शाम को 6 बजे दूसरा पार्सल भी आएगा।"),
            Line("a", "સારું, હું 6 વાગ્યે ઘરે હોઈશ.", review=True),
        ),
    ),
    # Patient (A, Gujarati, female) and clinic doctor (B, English, male). Gujarati: NATIVE
    # REVIEW.
    "clinic-gu-en": Script(
        "gu-IN",
        "en-IN",
        {"a": "bulbul:gu-female-1", "b": "bulbul:en-male-1"},
        (
            Line("a", "ડૉક્ટર, મને ત્રણ દિવસથી તાવ છે.", review=True),
            Line("b", "Okay. Do you also have a cough or a sore throat?"),
            Line("a", "હા, થોડી ઉધરસ છે. રાત્રે વધારે થાય છે.", review=True),
            Line("b", "Take this tablet twice a day, after breakfast and dinner."),
            Line("a", "હું ફ્લેટ 302, નવરંગપુરામાં રહું છું. દવા ઘરે મોકલશો?", review=True),
            Line("b", "Yes. Your delivery OTP is 4729. It will reach you by 6 pm."),
            Line("a", "સારું, હું 6 વાગ્યે ઘરે હોઈશ.", review=True),
        ),
    ),
}
DEFAULT_SCRIPT = "delivery-hi-kn"
SCRIPT: tuple[Line, ...] = SCRIPTS[DEFAULT_SCRIPT].lines
VOICES = SCRIPTS[DEFAULT_SCRIPT].voices


def script_for(a_lang: str, b_lang: str) -> str | None:
    """The script written for these side languages, if any."""
    return next(
        (k for k, sc in SCRIPTS.items() if (sc.a_lang, sc.b_lang) == (a_lang, b_lang)), None
    )


@dataclass
class Utt:
    side: str
    pcm: bytes
    text: str = ""


@dataclass
class Opts:
    pair: str | None = None
    a_lang: str = "hi-IN"
    b_lang: str = "kn-IN"
    pause_ms: int = 800  # silence after a translation finished before the next line
    quiet_ms: int = 700  # listener silence that counts as "translation finished"
    turn_timeout_s: float = 12.0
    opening: str = "play"  # "play": wait it out like a person; "skip": drop it once received
    opening_timeout_s: float = 20.0
    join: bool = False  # start from the stage host page (POST /join), QR-token sockets


# --- audio helpers ----------------------------------------------------------------------


def frames_of(pcm: bytes) -> list[bytes]:
    out = [pcm[i : i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)]
    if out and len(out[-1]) < FRAME_BYTES:
        out[-1] = out[-1].ljust(FRAME_BYTES, b"\x00")
    return out


def dbfs(frame: bytes) -> float:
    x = np.frombuffer(frame[: len(frame) // 2 * 2], dtype=np.int16).astype(np.float64)
    if x.size == 0:
        return -120.0
    rms = math.sqrt(float(np.mean(x * x)))
    return -120.0 if rms <= 0 else 20 * math.log10(rms / 32768.0)


def voiced(frame: bytes) -> bool:
    return dbfs(frame) > VOICED_DBFS


def last_voiced(frames: list[bytes]) -> int:
    """Index of the last non-silent frame (or -1)."""
    for i in range(len(frames) - 1, -1, -1):
        if voiced(frames[i]):
            return i
    return -1


def pct(xs: list[int], p: float) -> int | None:
    """Nearest-rank percentile."""
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


# --- the browser tab --------------------------------------------------------------------


class Tab:
    """One loopback page: mic frames out in real time, received audio played in real time
    (a FIFO drained 640 bytes per tick), marks acked when playout reaches them."""

    def __init__(self, side: str, ws: Any) -> None:
        self.side = side
        self.ws = ws
        self.buf = bytearray()
        self.received = 0  # bytes received in total
        self.played = 0  # bytes of received audio played (or dropped by clear)
        self.marks: deque[tuple[int, str]] = deque()  # (received offset, name)
        self.acks: list[str] = []
        self.rec = bytearray()  # what was played: exactly FRAME_BYTES per tick
        self.mic: deque[bytes] = deque()
        self.t_last_rx: float | None = None  # monotonic time of the last audio received
        self.clears = 0
        self.closed = False

    async def reader(self) -> None:
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    self.buf += msg
                    self.received += len(msg)
                    self.t_last_rx = time.monotonic()
                    continue
                with contextlib.suppress(ValueError):
                    m = json.loads(msg)
                    if m.get("type") == "mark":
                        self.marks.append((self.received, str(m.get("name"))))
                    elif m.get("type") == "clear":
                        self.clears += 1
                        self.played += len(self.buf)
                        self.buf.clear()
                        self.marks.clear()
        except websockets.ConnectionClosed:
            pass
        finally:
            self.closed = True

    async def tick(self) -> None:
        out = bytes(self.buf[:FRAME_BYTES])
        del self.buf[:FRAME_BYTES]
        self.played += len(out)
        self.rec += out.ljust(FRAME_BYTES, b"\x00")
        while self.marks and self.marks[0][0] <= self.played:
            name = self.marks.popleft()[1]
            self.acks.append(name)
            await self._send(json.dumps({"type": "mark", "name": name}))
        await self._send(self.mic.popleft() if self.mic else SILENCE)

    async def _send(self, data: bytes | str) -> None:
        if self.closed:
            return
        try:
            await self.ws.send(data)
        except websockets.ConnectionClosed:
            self.closed = True

    @property
    def idle(self) -> bool:
        """Nothing buffered to play."""
        return not self.buf

    def quiet_for(self, ms: int) -> bool:
        return self.idle and (
            self.t_last_rx is None or time.monotonic() - self.t_last_rx >= ms / 1000
        )


class Ticker:
    """Drives both tabs on one absolute 20 ms schedule. tick n covers [n*20, (n+1)*20) ms."""

    def __init__(self, tabs: list[Tab]) -> None:
        self.tabs = tabs
        self.n = 0
        self.t0 = time.monotonic()
        self.max_late_ms = 0.0
        self._cond = asyncio.Condition()

    async def run(self) -> None:
        self.t0 = time.monotonic()
        while True:
            due = self.t0 + self.n * FRAME_MS / 1000
            delay = due - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:  # behind: catch up, but always yield so the readers keep running
                self.max_late_ms = max(self.max_late_ms, -delay * 1000)
                await asyncio.sleep(0)
            for tab in self.tabs:
                await tab.tick()
            self.n += 1
            async with self._cond:
                self._cond.notify_all()

    async def wait_ticks(self, n: int) -> None:
        target = self.n + n
        async with self._cond:
            await self._cond.wait_for(lambda: self.n >= target)


async def _until(pred: Callable[[], bool], timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


# --- measuring --------------------------------------------------------------------------


@dataclass
class TurnRow:
    i: int
    side: str
    text: str
    n_start: int  # tick the first mic frame went out
    n_voice_end: int  # tick after the last voiced frame (end of speech)
    n_next: int = 0  # tick the next line started (end of this turn's listen window)
    earcon_ms: int | None = None
    heard_ms: int | None = None  # first non-silent translation frame after the earcon
    heard_audio_ms: int = 0  # non-silent ms the listener got in this window
    heard_span_ms: int = 0  # first to last non-silent frame
    clears: int = 0
    # server t_play_start -> earcon heard (transport + tick quantisation), in-process only
    play_to_ear_ms: int | None = None
    # server t_speech_end minus the real end of speech (negative: the VAD ended it early)
    vad_end_lag_ms: int | None = None
    server: list[dict[str, Any]] = field(default_factory=list)


def measure(row: TurnRow, rec: bytes) -> None:
    """Latencies are from the end of speech; the window opens when the line starts, so a
    translation that starts before the speaker finished shows up as a negative number."""
    frames = frames_of(rec[row.n_start * FRAME_BYTES : row.n_next * FRAME_BYTES])
    hits = [k for k, f in enumerate(frames) if voiced(f)]
    if not hits:
        return
    k = hits[0]
    end = row.n_voice_end - row.n_start
    row.heard_audio_ms = len(hits) * FRAME_MS
    row.heard_span_ms = (hits[-1] - k + 1) * FRAME_MS
    speech_from = k
    if frames[k] == EARCON[:FRAME_BYTES]:
        row.earcon_ms = (k - end) * FRAME_MS
        speech_from = k + EARCON_FRAMES
    after = [h for h in hits if h >= speech_from]
    row.heard_ms = (after[0] - end) * FRAME_MS if after else None


STAGES = (
    ("eou", "t_speech_end", "t_eou"),
    ("stt", "t_eou", "t_stt_final"),
    ("mt", "t_stt_final", "t_mt"),
    ("tts", "t_mt", "t_tts_ttfb"),
    ("queue", "t_tts_ttfb", "t_play_start"),
    ("first_audio", "t_speech_end", "t_play_start"),
)
_PATH = ("t_stt_final", "t_mt", "t_tts_ttfb", "t_play_start")


def stages(turn: dict[str, Any]) -> dict[str, int | None]:
    """Each stage's share of the critical path. With early final, STT/MT/TTS can finish
    inside the EOU wait; work done before the cursor counts 0, so eou + stt + mt + tts +
    queue == first_audio either way."""
    get = turn.get
    out: dict[str, int | None] = {name: None for name, _, _ in STAGES}
    if get("t_speech_end") is not None and get("t_eou") is not None:
        out["eou"] = get("t_eou") - get("t_speech_end")
    if get("t_speech_end") is not None and get("t_play_start") is not None:
        out["first_audio"] = get("t_play_start") - get("t_speech_end")
    cursor = get("t_eou")
    for (name, _, _), key in zip(STAGES[1:5], _PATH, strict=True):
        t = get(key)
        if cursor is None or t is None:
            cursor = None
            continue
        out[name] = max(0, t - cursor)
        cursor = max(cursor, t)
    return out


def numbers_check(row: TurnRow) -> str | None:
    """Numbers in the scripted line that the listener's translation lost (ward's check)."""
    from dubhash.ward.service import check_numbers

    if not row.server:
        return None
    return check_numbers(row.text, " ".join(str(t.get("tgt_text") or "") for t in row.server))


def join_server(rows: list[TurnRow], turns: list[dict[str, Any]], offset_ms: float) -> None:
    """Attach each server turn to the scripted turn whose window holds its t_speech_end.
    offset_ms converts server call-relative ms to the tick clock (ms since tick 0)."""
    for t in turns:
        if t.get("backchannel") or t.get("t_speech_end") is None:
            continue
        end = t["t_speech_end"] + offset_ms
        for r in rows:
            if t["side"] == r.side and r.n_start * FRAME_MS <= end < r.n_next * FRAME_MS:
                r.server.append(t)
                break


# --- the call ---------------------------------------------------------------------------


@dataclass
class Server:
    base: str  # http://127.0.0.1:port
    app: Any
    headers_console: dict[str, str]
    headers_api: dict[str, str]

    @property
    def ws_base(self) -> str:
        return "ws" + self.base.removeprefix("http")

    def session_t0(self, call_id: str) -> float | None:
        """Monotonic start of the session clock (in-process only), for joining t_* fields."""
        reg = getattr(self.app.state, "sessions", None)
        session = reg.get(call_id) if reg is not None else None
        return getattr(getattr(session, "clock", None), "_t0", None)


async def _start_join(
    srv: Server, http: httpx.AsyncClient, opts: Opts
) -> tuple[str, dict[str, str]]:
    """Start the call like the stage host page and read the QR URLs off it. Returns the
    call id and each side's socket URL carrying that side's join token."""
    if opts.pair:
        raise SystemExit("--join picks the pair by languages (find_pair); drop --pair")
    form = {"a_lang": opts.a_lang, "b_lang": opts.b_lang, "record": "true"}
    r = await http.post("/join", data=form, headers=srv.headers_console)
    if r.status_code != 303:
        raise RuntimeError(f"POST /join: {r.status_code} {r.text[:200]}")
    call_id = r.headers["location"].rsplit("/", 1)[1]
    page = await http.get(r.headers["location"], headers=srv.headers_console)
    page.raise_for_status()
    found = re.findall(r'<figure class="qr" data-side="(\w)" data-url="([^"]+)"', page.text)
    sockets = {}
    for side, url in found:
        token = parse_qs(urlparse(html.unescape(url)).query)["t"][0]
        sockets[side] = f"{srv.ws_base}/ws/loopback?call={call_id}&side={side}&t={token}"
    if set(sockets) != {"a", "b"}:
        raise RuntimeError("host page did not show two QR codes")
    return call_id, sockets


async def run_call(srv: Server, utts: list[Utt], opts: Opts) -> dict[str, Any]:
    body: dict[str, Any] = {"a_lang": opts.a_lang, "b_lang": opts.b_lang, "record": True}
    if opts.pair:
        body["pair"] = opts.pair
    async with httpx.AsyncClient(base_url=srv.base, timeout=30) as http:
        if opts.join:
            call_id, sockets = await _start_join(srv, http, opts)
        else:
            r = await http.post("/loopback/calls", json=body, headers=srv.headers_console)
            r.raise_for_status()
            call_id = r.json()["call_id"]
            url = f"{srv.ws_base}/ws/loopback?call={call_id}"
            sockets = {"a": f"{url}&side=a", "b": f"{url}&side=b"}
        session_t0 = srv.session_t0(call_id)

        async with (
            websockets.connect(sockets["a"], max_size=None) as wa,
            websockets.connect(sockets["b"], max_size=None) as wb,
        ):
            tabs = {"a": Tab("a", wa), "b": Tab("b", wb)}
            for side, ws in (("a", wa), ("b", wb)):
                lang = opts.a_lang if side == "a" else opts.b_lang
                await ws.send(json.dumps({"type": "hello", "side": side, "lang": lang}))
            ticker = Ticker(list(tabs.values()))
            tasks = [asyncio.create_task(t.reader()) for t in tabs.values()]
            tasks.append(asyncio.create_task(ticker.run()))
            try:
                opening = await _opening(tabs, ticker, opts)
                rows = await _script(tabs, ticker, utts, opts)
                await ticker.wait_ticks(10)
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        # tabs closed: the call ends; turns are logged by teardown
        await _until_ended(http, call_id, srv.headers_api)
        r = await http.get(f"/v1/calls/{call_id}/turns", headers=srv.headers_api)
        r.raise_for_status()
        turns = r.json()["turns"]
        r = await http.get(f"/v1/calls/{call_id}", headers=srv.headers_api)
        call = r.json() if r.status_code == 200 else {}

    for r_ in rows:
        measure(r_, bytes(tabs[_other(r_.side)].rec))
    first = rows[0].n_start if rows else ticker.n
    for s, t in tabs.items():  # when each caller first hears the opening line
        hits = [
            k for k, f in enumerate(frames_of(bytes(t.rec[: first * FRAME_BYTES]))) if voiced(f)
        ]
        opening.setdefault("onset_ms", {})[s] = hits[0] * FRAME_MS if hits else None
    offset = (session_t0 - ticker.t0) * 1000 if session_t0 is not None else None
    if offset is not None:
        join_server(rows, turns, offset)
        for r_ in rows:
            if r_.server and r_.earcon_ms is not None:
                ear = r_.n_voice_end * FRAME_MS + r_.earcon_ms
                r_.play_to_ear_ms = round(ear - (r_.server[0]["t_play_start"] + offset))
                r_.vad_end_lag_ms = round(
                    r_.server[0]["t_speech_end"] + offset - r_.n_voice_end * FRAME_MS
                )
    return {
        "call_id": call_id,
        "pair": call.get("pair") or opts.pair,
        "sockets": sockets,
        "status": call.get("status"),
        "opening": opening,
        "rows": rows,
        "turns": turns,
        "server_offset_ms": None if offset is None else round(offset),
        "max_tick_late_ms": round(ticker.max_late_ms),
        "acks": {s: len(t.acks) for s, t in tabs.items()},
        "clears": {s: t.clears for s, t in tabs.items()},
        "rec": {s: bytes(t.rec) for s, t in tabs.items()},
    }


def _other(side: str) -> str:
    return "b" if side == "a" else "a"


async def _opening(tabs: dict[str, Tab], ticker: Ticker, opts: Opts) -> dict[str, Any]:
    """Wait for the opening line on both legs, then for it to finish playing (or drop it)."""
    t0 = ticker.n
    got = await _until(lambda: all(t.received for t in tabs.values()), opts.opening_timeout_s)
    first_rx = {s: t.received for s, t in tabs.items()}
    if opts.opening == "skip":
        await _until(
            lambda: all(
                t.t_last_rx is not None and time.monotonic() - t.t_last_rx > 0.3
                for t in tabs.values()
            ),
            opts.opening_timeout_s,
        )
        for t in tabs.values():
            t.played += len(t.buf)
            t.buf.clear()
    else:
        await _until(
            lambda: all(t.quiet_for(opts.quiet_ms) for t in tabs.values()), opts.opening_timeout_s
        )
    await ticker.wait_ticks(opts.pause_ms // FRAME_MS)
    return {
        "received": got,
        "bytes": {s: t.received for s, t in tabs.items()},
        "first_chunk_bytes": first_rx,
        "ms": {s: t.received * 1000 // 32000 for s, t in tabs.items()},
        "wait_ms": (ticker.n - t0) * FRAME_MS,
    }


async def _script(
    tabs: dict[str, Tab], ticker: Ticker, utts: list[Utt], opts: Opts
) -> list[TurnRow]:
    rows: list[TurnRow] = []
    for i, u in enumerate(utts):
        spk, lst = tabs[u.side], tabs[_other(u.side)]
        fr = frames_of(u.pcm)
        lv = last_voiced(fr)
        rx0, clears0 = lst.received, lst.clears
        n_start = ticker.n + len(spk.mic)
        spk.mic.extend(fr)
        row = TurnRow(i, u.side, u.text, n_start=n_start, n_voice_end=n_start + lv + 1)
        if rows:
            rows[-1].n_next = n_start
        rows.append(row)
        await _until(lambda s=spk: not s.mic, len(fr) * FRAME_MS / 1000 + 5)
        await _until(lambda t=lst, n=rx0: t.received > n, opts.turn_timeout_s)
        await _until(lambda t=lst: t.quiet_for(opts.quiet_ms), opts.turn_timeout_s)
        row.clears = lst.clears - clears0
        await ticker.wait_ticks(opts.pause_ms // FRAME_MS)
    if rows:
        rows[-1].n_next = ticker.n
    return rows


async def _until_ended(http: httpx.AsyncClient, call_id: str, headers: dict[str, str]) -> None:
    for _ in range(200):
        r = await http.get(f"/v1/calls/{call_id}", headers=headers)
        if r.status_code != 200 or r.json().get("status") in ("completed", "failed"):
            return
        await asyncio.sleep(0.05)


# --- report -----------------------------------------------------------------------------


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    rows: list[TurnRow] = result["rows"]
    cols: dict[str, list[int]] = {"heard": [], "earcon": []}
    for r in rows:
        if r.heard_ms is not None:
            cols["heard"].append(r.heard_ms)
        if r.earcon_ms is not None:
            cols["earcon"].append(r.earcon_ms)
        if r.server:
            for k, v in stages(r.server[0]).items():
                if v is not None:
                    cols.setdefault(k, []).append(v)
    return {
        k: {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95), "max": max(v) if v else None}
        for k, v in cols.items()
    }


def table(result: dict[str, Any], summary: dict[str, Any]) -> str:
    head = ("#", "dir", "earcon", "heard", "eou", "stt", "mt", "tts", "queue", "srv_fa", "audio")
    lines = ["  ".join(f"{h:>6}" for h in head)]
    for r in result["rows"]:
        st = stages(r.server[0]) if r.server else {}
        cells = [
            r.i,
            f"{r.side}->{_other(r.side)}",
            r.earcon_ms,
            r.heard_ms,
            st.get("eou"),
            st.get("stt"),
            st.get("mt"),
            st.get("tts"),
            st.get("queue"),
            st.get("first_audio"),
            r.heard_audio_ms,
        ]
        lines.append("  ".join(f"{'-' if c is None else c!s:>6}" for c in cells))
        for t in r.server:
            lines.append(f"        src: {t.get('src_text')}")
            lines.append(f"        tgt: {t.get('tgt_text')}")
        if len(r.server) > 1:
            lines.append(f"        ({len(r.server)} server turns: the utterance was split)")
        if (warn := numbers_check(r)) is not None:
            lines.append(f"        !! {warn}")
    lines.append("")
    for k, v in summary.items():
        lines.append(f"{k:>12}: n={v['n']} p50={v['p50']} p95={v['p95']} max={v['max']}")
    return "\n".join(lines)


def write_outputs(result: dict[str, Any], summary: dict[str, Any], out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    cid = result["call_id"]
    for side, pcm in result["rec"].items():
        write_wav(out / f"{cid}_{side}.wav", pcm)
    doc = {k: v for k, v in result.items() if k not in ("rec", "rows", "sockets")}
    doc["rows"] = [asdict(r) | {"stages": stages(r.server[0]) if r.server else None}
                   for r in result["rows"]]  # fmt: skip
    doc["summary"] = summary
    path = out / f"{cid}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
    return path


# --- server and utterances ---------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def serve(settings_kw: dict[str, Any], env_file: Path | None) -> Iterator[Server]:
    """Run the app with uvicorn in a thread (its own event loop) on a free port."""
    import uvicorn

    from dubhash.api.app import create_app
    from dubhash.api.auth import parse_api_keys
    from dubhash.config.settings import Settings

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    settings = Settings(
        _env_file=env_file,  # type: ignore[call-arg]
        public_base_url=base,
        edge_wss_base=f"ws://127.0.0.1:{port}",
        **settings_kw,
    )
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
        ws_per_message_deflate=False,  # as make dev and the Dockerfile (L7)
    )
    srv = uvicorn.Server(config)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    deadline = time.monotonic() + 15
    while not srv.started:
        if time.monotonic() > deadline or not th.is_alive():
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    console: dict[str, str] = {}
    api: dict[str, str] = {}
    if settings.dubhash_env != "test":
        cred = base64.b64encode(f"dev:{settings.console_password}".encode()).decode()
        console = {"authorization": f"Basic {cred}"}
        keys = list(parse_api_keys(settings.platform_api_keys).values())
        api = {"authorization": f"Bearer {keys[0]}"} if keys else {}
    try:
        yield Server(base, app, console, api)
    finally:
        srv.should_exit = True
        th.join(timeout=15)


async def synth_script(
    lines: tuple[Line, ...],
    langs: dict[str, str],
    out: Path,
    voices: dict[str, str] | None = None,
) -> list[Utt]:
    """Bulbul-voiced utterances, cached by (voice, lang, text) in out/."""
    from dubhash.config.settings import Settings
    from dubhash.providers.sarvam_tts import BulbulTTS

    utts: list[Utt] = []
    tts: BulbulTTS | None = None
    try:
        for line in lines:
            voice, lang = (voices or VOICES)[line.side], langs[line.side]
            h = hashlib.sha1(f"{voice}|{lang}|{line.text}".encode()).hexdigest()[:10]
            path = out / f"utt_{lang}_{h}.wav"
            if not path.exists():
                if tts is None:
                    key = Settings(_env_file=ROOT / ".env").sarvam_api_key  # type: ignore[call-arg]
                    if not key:
                        raise SystemExit("SARVAM_API_KEY not set (needed to voice the script)")
                    tts = BulbulTTS(key)
                pcm = b"".join([c async for c in tts.synth(line.text, voice, lang)])
                write_wav(path, pcm)
            utts.append(Utt(line.side, read_wav(path), line.text))
    finally:
        if tts is not None:
            await tts.aclose()
    return utts


def fake_speech(ms: int, f0: float) -> bytes:
    """A voiced burst Silero accepts as speech (like tests/audio_fixtures.speech): f0 plus
    7 harmonics at 1/k, a 5 Hz syllable envelope, 20 ms fades. f0 glides +-15% at 2 Hz:
    Silero stops accepting a perfectly steady tone after it has heard it once."""
    n = 16 * ms
    t = np.arange(n) / 16000
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.15 * np.sin(2 * np.pi * 2 * t))) / 16000
    x = sum(np.sin(k * phase) / k for k in range(1, 9) if k * f0 < 7000)
    x = x / np.max(np.abs(x)) * 8000
    env = 0.65 + 0.35 * np.sin(2 * np.pi * 5 * t)
    fade = 16 * 20
    ramp = np.linspace(0.0, 1.0, fade)
    env[:fade] *= ramp
    env[n - fade :] *= ramp[::-1]
    return (x * env).astype(np.int16).tobytes()


def fake_utts(lines: tuple[Line, ...], ms: int = 1200) -> list[Utt]:
    """Stand-ins for speech (the fake STT counts words by duration). Silero accepts only the
    first one or two per side reliably, so --fake runs are for plumbing, not numbers."""
    return [Utt(ln.side, fake_speech(ms, 140.0 if ln.side == "a" else 220.0), ln.text)
            for ln in lines]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--pair", default=None, help="pairs.yaml key (default: by languages)")
    p.add_argument(
        "--script",
        choices=sorted(SCRIPTS),
        default=None,
        help=f"scripted call (default: the one for the languages, else {DEFAULT_SCRIPT})",
    )
    p.add_argument("--a-lang", default=None, help="default: the script's")
    p.add_argument("--b-lang", default=None, help="default: the script's")
    p.add_argument("--n", type=int, default=None, help="script lines to play (default: all)")
    p.add_argument("--join", action="store_true", help="stage path: POST /join, token sockets")
    p.add_argument("--fake", action="store_true", help="fake providers, fake utterances")
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--pause-ms", type=int, default=800)
    p.add_argument("--log", default="WARNING", help="dubhash log level to stderr")
    a = p.parse_args(argv)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(relativeCreated)8.0f %(levelname)s %(message)s"))
    logging.getLogger("dubhash").addHandler(handler)
    logging.getLogger("dubhash").setLevel(a.log.upper())
    name = a.script or script_for(a.a_lang, a.b_lang) or DEFAULT_SCRIPT
    sc = SCRIPTS[name]
    a_lang, b_lang = a.a_lang or sc.a_lang, a.b_lang or sc.b_lang
    lines = sc.lines[: a.n]
    opts = Opts(pair=a.pair, a_lang=a_lang, b_lang=b_lang, pause_ms=a.pause_ms, join=a.join)
    if a.fake:
        utts = fake_utts(lines)
        kw: dict[str, Any] = {"dubhash_env": "test"}
        env_file = None
    else:
        a.out.mkdir(parents=True, exist_ok=True)
        utts = asyncio.run(synth_script(lines, {"a": a_lang, "b": b_lang}, a.out, sc.voices))
        kw = {}
        env_file = ROOT / ".env"
        impl = {k: os.environ.get(k) for k in ("STT_IMPL", "MT_IMPL", "TTS_IMPL")}
        if any(v != "real" for v in impl.values()):
            print(f"warning: provider switches are {impl}; set all three to real", file=sys.stderr)
    with serve(kw, env_file) as srv:
        result = asyncio.run(run_call(srv, utts, opts))
    summary = summarize(result)
    print(f"call {result['call_id']}  pair {result['pair']}  script {name}  {result['status']}")
    print(table(result, summary))
    print(f"opening: {result['opening']}  max tick late: {result['max_tick_late_ms']} ms")
    print(f"wrote {write_outputs(result, summary, a.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
