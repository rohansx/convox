"""The solo demo's agent (demo/agent.py): its leg plays speech at real time and silence
otherwise, and it answers the other side's lines once they have played to it."""

import asyncio
from types import SimpleNamespace

import pytest

from convox.audio.frames import FRAME_BYTES
from convox.demo import agent as agents
from convox.demo.agent import Agent, AgentLeg, clean, scripted, system_prompt, transcript
from convox.demo.stories import STORIES
from convox.events import EventBus
from convox.types import CallEvent

SPEECH = b"\x01\x00" * 800  # 50 ms: three frames once padded


async def take(leg: AgentLeg, n: int) -> list[bytes]:
    out = []
    async for f in leg.frames():
        out.append(f)
        if len(out) == n:
            break
    return out


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(agents, "REPLY_PAUSE_S", 0.01)
    monkeypatch.setattr(agents, "OPENING_PAUSE_S", 0.0)


async def test_leg_plays_speech_then_silence():
    leg = AgentLeg("a")
    leg.say(SPEECH)
    assert leg.speaking
    frames = await take(leg, 5)
    assert all(len(f) == FRAME_BYTES for f in frames)
    assert frames[0] == SPEECH[:FRAME_BYTES]
    assert frames[2][: len(SPEECH) - 2 * FRAME_BYTES] == SPEECH[2 * FRAME_BYTES :]
    assert frames[3] == frames[4] == bytes(FRAME_BYTES)
    assert not leg.speaking
    await leg.close()


async def test_leg_marks_fire_after_the_audio_would_have_played():
    leg = AgentLeg("a")
    fired: list[str] = []
    leg.on_mark(fired.append)
    await leg.send(bytes(320))  # 10 ms at 16 kHz
    await leg.mark("b:0")
    assert fired == []
    await asyncio.sleep(0.05)
    assert fired == ["b:0"]
    await leg.close()


async def test_closed_leg_ends_its_frames():
    leg = AgentLeg("a")
    await leg.close()
    assert await take(leg, 1) == []


class Session:
    def __init__(self) -> None:
        self.call_id = "c_1"
        self.status = "ringing"
        self.idle = True


def turn(side: str, tgt: str, backchannel: bool = False) -> CallEvent:
    t = {"side": side, "seq": 0, "src_text": "x", "tgt_text": tgt, "backchannel": backchannel}
    return CallEvent("c_1", 0, "turn", {"turn": t})


async def until(pred, within_s: float = 2.0) -> None:
    for _ in range(int(within_s / 0.01)):
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


async def test_agent_opens_then_answers_what_it_heard():
    bus, session, leg = EventBus(), Session(), AgentLeg("a")
    spoken: list[str] = []
    asked: list[list] = []

    async def speak(text: str) -> bytes:
        spoken.append(text)
        return bytes(FRAME_BYTES)

    async def reply(history):
        asked.append(history)
        return "ಸರಿ, ಬರ್ತೀನಿ."

    agent = Agent(session, bus, "a", leg, reply=reply, speak=speak, opener="ಹಲೋ")
    task = asyncio.create_task(agent.run())
    pump = asyncio.create_task(take(leg, 10_000))
    await asyncio.sleep(0.05)
    assert spoken == []  # still ringing: nobody to talk to
    session.status = "active"
    await until(lambda: spoken == ["ಹಲೋ"] and not leg.speaking)
    bus.publish(turn("a", "its own line, as the other side heard it"))
    bus.publish(turn("b", "ಹೂಂ", backchannel=True))
    bus.publish(turn("b", "ನಾನು ಗೇಟ್ 2 ಹತ್ತಿರ ಇದ್ದೀನಿ"))
    await until(lambda: len(spoken) == 2)
    assert asked == [[("me", "ಹಲೋ"), ("them", "ನಾನು ಗೇಟ್ 2 ಹತ್ತಿರ ಇದ್ದೀನಿ")]]
    assert spoken[1] == "ಸರಿ, ಬರ್ತೀನಿ."
    session.status = "ended"
    await leg.close()
    await asyncio.wait_for(task, 2)
    pump.cancel()


async def test_agent_waits_until_nothing_is_in_flight():
    bus, session, leg = EventBus(), Session(), AgentLeg("a")
    spoken: list[str] = []

    async def speak(text: str) -> bytes:
        spoken.append(text)
        return bytes(FRAME_BYTES)

    agent = Agent(session, bus, "a", leg, reply=scripted(["two"]), speak=speak, opener="one")
    session.status = "active"
    task = asyncio.create_task(agent.run())
    pump = asyncio.create_task(take(leg, 10_000))
    await until(lambda: spoken == ["one"] and not leg.speaking)
    session.idle = False  # the visitor is still talking
    bus.publish(turn("b", "first half"))
    await asyncio.sleep(0.1)
    assert spoken == ["one"]
    session.idle = True
    await until(lambda: spoken == ["one", "two"])
    assert agent.history[1] == ("them", "first half")
    session.status = "ended"
    await leg.close()
    await asyncio.wait_for(task, 2)
    pump.cancel()


async def test_scripted_replies_run_out():
    reply = scripted(["a", "b"])
    assert [await reply([]), await reply([]), await reply([])] == ["a", "b", None]


def test_clean_keeps_one_spoken_line():
    assert clean('Driver: "ಸರಿ, ಬರ್ತೀನಿ."\nextra') == "ಸರಿ, ಬರ್ತೀನಿ."
    assert clean("  “ಹಲೋ”  ") == "ಹಲೋ"
    assert clean("ಸಮಯ 10:30 ಕ್ಕೆ") == "ಸಮಯ 10:30 ಕ್ಕೆ"
    assert clean(" \n ") is None


def test_prompt_plays_the_role_in_its_own_language():
    story = STORIES["auto"]
    p = system_prompt(story, "a", "Kannada", "Hindi")
    assert "Basavaraj" in p and "driver" in p and "passenger" in p
    assert "only Kannada" in p and "only Hindi" in p and "white auto" in p
    t = transcript([("me", "ಹಲೋ"), ("them", "ಹೌದು")], "Driver", "Passenger")
    assert t.endswith("Driver: ಹಲೋ\nPassenger: ಹೌದು\n\nWhat do you say next, Driver?")


async def test_claude_reply_cleans_the_answer_and_survives_failures():
    calls = []

    class Messages:
        async def create(self, **kw):
            calls.append(kw)
            if len(calls) > 1:
                raise RuntimeError("overloaded")
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="Driver: ಸರಿ")])

    client = SimpleNamespace(messages=Messages())
    reply = agents.claude_reply(client, "m", STORIES["auto"], "a", ("Kannada", "Hindi"))
    assert await reply([("me", "ಹಲೋ")]) == "ಸರಿ"
    assert calls[0]["model"] == "m" and "Kannada" in calls[0]["system"]
    assert await reply([("me", "ಹಲೋ")]) is None
