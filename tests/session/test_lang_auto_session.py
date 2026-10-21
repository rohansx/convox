"""lang "auto" through a whole CallSession: FakeLegs, FakeClock, FakeSTT finals carrying a
detected language, FakeLID (docs/tech-spec/language-detection.md)."""

from __future__ import annotations

from pathlib import Path

from dubhash.clock import FakeClock
from dubhash.config.pairs import auto_pair, language_sides, load_pairs
from dubhash.interp.langid import SUPPORTED_LANGS, LangGuess
from dubhash.legs.fake import FakeLeg
from dubhash.providers.fakes import EnergyVAD, FakeLID, FakeMT, FakeSTT, FakeTTS
from dubhash.session.call_session import CallSession
from dubhash.session.language import AutoLang
from dubhash.session.prompts import auto_opening_text, language_menu_text
from dubhash.session.providers import Providers
from tests.audio_fixtures import silence, speech
from tests.floor_helpers import RecordingBus
from tools.bench import drive_lockstep

PAIRS = load_pairs(Path(__file__).resolve().parents[2] / "dubhash" / "config" / "pairs.yaml")
SIDES = language_sides(PAIRS)


def lid(lang: str | None, conf: float) -> LangGuess:
    return LangGuess(lang, conf, "lid")


def talk(ms: int = 2000, f0: float = 140.0) -> bytes:
    """One utterance with ms of speech (over min_speech_ms by default) and a pause."""
    return speech(ms, f0=f0) + silence(1500)


class Call:
    def __init__(
        self,
        langs,
        hints=None,
        *,
        lid_script=(),
        stt=None,
        opening_line=False,
        stt_open="guess",
        **kw,
    ):
        self.clock = FakeClock()
        self.bus = RecordingBus()
        hints = hints or {}
        self.pair = auto_pair(PAIRS, langs, hints)
        self.stt = stt or {"a": FakeSTT(), "b": FakeSTT()}
        self.tts = {"a": FakeTTS(), "b": FakeTTS()}
        self.mt = FakeMT()
        self.lid = FakeLID(list(lid_script))
        prov = Providers(
            stt=self.stt, mt=self.mt, tts=self.tts, vad_factory=EnergyVAD, lid=self.lid
        )
        auto = AutoLang(
            hints={s: hints.get(s) for s in ("a", "b") if langs[s] == "auto"},
            sides=SIDES,
            stt_open=stt_open,
        )
        self.legs = {s: FakeLeg(s, clock=self.clock) for s in ("a", "b")}
        self.session = CallSession(
            "c_auto",
            self.pair,
            prov,
            bus=self.bus,
            clock=self.clock,
            opening_line=opening_line,
            auto=auto,
            **kw,
        )

    async def run(self, inbound, on_frame=None):
        await self.session.attach_leg("a", self.legs["a"])
        await self.session.attach_leg("b", self.legs["b"])
        await drive_lockstep(self.session, self.legs, self.clock, inbound, on_frame=on_frame)
        await self.session.wait_closed()
        return self

    def langs(self):
        return [
            (e.payload["side"], e.payload["lang"], e.payload["state"], e.payload.get("menu"))
            for e in self.bus.kinds("lang")
        ]

    def heard_by(self, listener):
        """(text, voice, lang) synthesised for `listener`, prompts left out."""
        return [r for r in self.tts[listener].requests if not r[0].startswith(PROMPTS)]


PROMPTS = ("Namaste", "Please speak", "Hindi ke liye", "हिंदी.", "English.")


async def test_with_stt_auto_the_finals_language_and_confidence_resolve_the_side():
    """The default: the auto side's STT opens with language_code=auto, whose finals report
    the language and its confidence; one sure final confirms without any LID guess."""
    call = Call(
        {"a": "auto", "b": "kn-IN"},
        stt={"a": FakeSTT(["a one"], detected=[("gu-IN", 0.96)]), "b": FakeSTT()},
        stt_open="auto",
    )
    await call.run({"a": talk() + silence(2000)})
    assert call.stt["a"].langs == ["auto"]
    assert call.langs() == [("a", "gu-IN", "CONFIRMED", None)]
    assert call.bus.kinds("lang")[0].payload["source"] == "stt_final"


async def test_a_hinted_side_is_confirmed_and_the_partner_hears_the_hint_until_then():
    """A (auto, hint mr-IN) and B (hi-IN). B speaks first: A hears Marathi (the hint).
    Two agreeing Marathi guesses from A confirm it."""
    call = Call(
        {"a": "auto", "b": "hi-IN"},
        {"a": "mr-IN"},
        lid_script=[lid("mr-IN", 0.7), lid("mr-IN", 0.75)],
        stt={"a": FakeSTT(["a one", "a two"]), "b": FakeSTT(["b one"])},
    )
    await call.run({"a": silence(4000) + talk() + talk(), "b": talk() + silence(8000)})
    assert call.langs() == [("a", "mr-IN", "PROVISIONAL", None), ("a", "mr-IN", "CONFIRMED", None)]
    assert [(t, v, lang) for t, v, lang in call.heard_by("a")] == [
        ("[mr-IN] b one", "bulbul:mr-male-1", "mr-IN"),
    ]
    assert call.lid.calls and all(c == SUPPORTED_LANGS for _, c in call.lid.calls)
    assert all(n > 0 for n, _ in call.lid.calls)  # the utterance's audio


async def test_an_unknown_side_is_detected_from_its_first_utterance():
    """A (auto, no hint) speaks Tamil: one very confident guess confirms it. B's reply is
    translated into Tamil, and A's next utterance opens STT in Tamil."""
    call = Call(
        {"a": "auto", "b": "kn-IN"},
        lid_script=[lid("ta-IN", 0.95)],
        stt={"a": FakeSTT(["a one", "a two"]), "b": FakeSTT(["b one"])},
    )
    await call.run(
        {"a": talk() + silence(5000) + talk(), "b": silence(4500) + talk() + silence(4000)}
    )
    assert call.langs() == [("a", "ta-IN", "CONFIRMED", None)]
    assert call.stt["a"].langs == ["hi-IN", "ta-IN"]
    assert [t for t, _, _ in call.heard_by("a")] == ["[ta-IN] b one"]
    assert [lang for _, _, lang in call.heard_by("a")] == ["ta-IN"]
    # A's speech goes through the English pivot whatever its language
    assert [t for t, _, _ in call.heard_by("b")] == ["[kn-IN] a one", "[kn-IN] a two"]


async def test_the_listener_is_unknown_at_first_then_resolves():
    """A (hi-IN) talks to B (auto, hint gu-IN): A's first line goes to B in Gujarati (the
    hint). B speaks Kannada; A's later lines go to B in Kannada, in a Kannada voice."""
    call = Call(
        {"a": "hi-IN", "b": "auto"},
        {"b": "gu-IN"},
        lid_script=[lid("kn-IN", 0.95)],
        stt={"a": FakeSTT(["a one", "a two"]), "b": FakeSTT(["b one"])},
    )
    await call.run(
        {"a": talk() + silence(5000) + talk(), "b": silence(4500) + talk() + silence(4000)}
    )
    assert call.langs() == [("b", "kn-IN", "CONFIRMED", None)]
    assert [(t, lang) for t, _, lang in call.heard_by("b")] == [
        ("[gu-IN] a one", "gu-IN"),
        ("[kn-IN] a two", "kn-IN"),
    ]
    assert [v for _, v, _ in call.heard_by("b")] == ["bulbul:gu-male-1", "bulbul:kn-male-1"]


async def test_code_mixed_english_does_not_flip_a_confirmed_side():
    partials = [
        [],
        [(400, "मेरा order number क्या है")],
        [(400, "delivery कब होगी, address वही है")],
    ]
    call = Call(
        {"a": "auto", "b": "kn-IN"},
        lid_script=[lid("hi-IN", 0.95), lid("en-IN", 0.97), lid("en-IN", 0.98)],
        stt={"a": FakeSTT(["x", "y", "z"], partials=partials), "b": FakeSTT()},
    )
    await call.run({"a": talk() + talk() + talk()})
    assert call.langs() == [("a", "hi-IN", "CONFIRMED", None)]
    assert call.session.resolvers["a"].lang == "hi-IN"


async def test_two_unsure_utterances_play_the_menu_and_a_digit_confirms():
    call = Call(
        {"a": "auto", "b": "hi-IN"},
        lid_script=[lid(None, 0.3), lid(None, 0.2)],
        stt={"a": FakeSTT(["one", "two"], detected=["bn-IN", "bn-IN"]), "b": FakeSTT()},
    )
    menu_at: list[int] = []

    def on_frame(i: int) -> None:
        if not menu_at and any(e.payload.get("menu") for e in call.bus.kinds("lang")):
            menu_at.append(i)
        if menu_at and i == menu_at[0] + 25:  # 500 ms into the menu
            call.legs["a"].inject_dtmf("3")

    await call.run({"a": talk() + talk() + silence(4000)}, on_frame=on_frame)
    assert call.langs() == [
        ("a", None, "UNKNOWN", True),
        ("a", "gu-IN", "CONFIRMED", None),
    ]
    assert call.bus.kinds("lang")[-1].payload["source"] == "dtmf"
    menu = [r for r in call.tts["a"].requests if r[0] == language_menu_text()]
    assert menu and menu[0][2] == "hi-IN"  # read in Hindi
    assert not call.session.floor.bypass


async def test_during_the_menu_digits_mean_language_not_replay_or_escape():
    call = Call(
        {"a": "auto", "b": "kn-IN"},
        lid_script=[lid(None, 0.3), lid(None, 0.2)],
        stt={"a": FakeSTT(["one", "two"]), "b": FakeSTT()},
    )
    sent: list[str] = []

    def on_frame(i: int) -> None:
        if not sent and any(e.payload.get("menu") for e in call.bus.kinds("lang")):
            sent.append("0")
            call.legs["a"].inject_dtmf("0")  # escape outside the menu; ignored inside
        elif sent == ["0"] and i % 10 == 0:
            sent.append("1")
            call.legs["a"].inject_dtmf("1")  # Hindi, not replay

    await call.run({"a": talk() + talk() + silence(4000)}, on_frame=on_frame)
    assert not call.session.floor.bypass
    assert not call.session.escape_used
    assert call.langs()[-1] == ("a", "hi-IN", "CONFIRMED", None)


async def test_outside_the_menu_window_digits_keep_their_old_meaning():
    call = Call(
        {"a": "auto", "b": "kn-IN"},
        lid_script=[lid("ta-IN", 0.95)],
        stt={"a": FakeSTT(["one"]), "b": FakeSTT()},
    )

    def on_frame(i: int) -> None:
        if i == 250:
            call.legs["b"].inject_dtmf("0")

    await call.run({"a": talk() + silence(4000)}, on_frame=on_frame)
    assert call.session.floor.bypass and call.session.escape_used


async def test_same_language_on_both_sides_bridges_the_call():
    call = Call(
        {"a": "auto", "b": "hi-IN"},
        lid_script=[lid("hi-IN", 0.95)],
        stt={"a": FakeSTT(["one"]), "b": FakeSTT()},
    )
    at_bypass: list[int] = []

    def on_frame(i: int) -> None:
        if call.session.floor.bypass and not at_bypass:
            at_bypass.append(len(call.legs["a"].outbound))

    b_talk = talk()
    await call.run({"a": talk() + silence(3000), "b": silence(6000) + b_talk}, on_frame)
    assert call.session.floor.bypass
    assert call.bus.kinds("bypass")[-1].payload == {"side": "a", "reason": "same_lang"}
    assert not call.session.escape_used  # nobody pressed escape
    # B's speech reached A raw, frame by frame: no translation was made for it
    assert len(call.legs["a"].outbound) - at_bypass[0] >= len(b_talk)
    assert call.heard_by("a") == []


async def test_two_auto_sides_confirmed_in_one_language_bridge():
    call = Call(
        {"a": "auto", "b": "auto"},
        lid_script=[lid("mr-IN", 0.95), lid("mr-IN", 0.95)],
        stt={"a": FakeSTT(["one"]), "b": FakeSTT(["two"])},
    )
    await call.run({"a": talk() + silence(6000), "b": silence(4000) + talk() + silence(2000)})
    assert [lang for _, lang, _, _ in call.langs()] == ["mr-IN", "mr-IN"]
    assert call.session.floor.bypass


async def test_short_utterances_never_reach_the_lid():
    call = Call(
        {"a": "auto", "b": "kn-IN"},
        lid_script=[lid("ta-IN", 0.95)],
        stt={"a": FakeSTT(["haan ji theek"]), "b": FakeSTT()},
    )
    await call.run({"a": talk(900) + silence(2000)})
    assert call.lid.calls == []
    assert call.langs() == []


async def test_the_opening_line_for_an_auto_side_is_neutral():
    call = Call(
        {"a": "auto", "b": "kn-IN"},
        {"a": "mr-IN"},
        opening_line="short",
        stt={"a": FakeSTT(), "b": FakeSTT()},
    )
    await call.run({"a": silence(6000)})
    assert call.tts["a"].requests[0][0] == auto_opening_text("mr-IN")
    assert call.tts["a"].requests[0][2] == "mr-IN"
    # B does not know A's language yet: its opening only names Kannada
    assert "ಕನ್ನಡ" in call.tts["b"].requests[0][0]


async def test_summary_reports_each_sides_language_state():
    call = Call(
        {"a": "auto", "b": "kn-IN"},
        lid_script=[lid("ta-IN", 0.95)],
        stt={"a": FakeSTT(["one"]), "b": FakeSTT()},
    )
    await call.run({"a": talk() + silence(2000)})
    langs = call.session.summary()["langs"]
    assert langs["a"] == {"lang": "ta-IN", "state": "CONFIRMED", "auto": True}
    assert langs["b"] == {"lang": "kn-IN", "state": "CONFIRMED", "auto": False}
