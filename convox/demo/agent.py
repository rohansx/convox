"""The solo demo's other person: someone who speaks only one language, played by Claude
(/try, demo/solo.py).

AgentLeg is a LegTransport with nobody behind it. frames() yields its speech paced at real
time and silence otherwise; audio played to it is dropped, and its marks fire when that
audio would have finished playing. Agent listens to the call's turn events. Once the
visitor's line has played to it (the floor logs a turn when playout ends), it answers in
its own language, and the answer is synthesised with that side's TTS and fed in as its
mic. So the visitor hears it only through the real pipeline: STT on the agent's voice, MT,
TTS and the floor, with the same timings as a phone.

The agent hears what Convox delivered (the turn's tgt_text, in its language), never the
visitor's own words. Replies come from `reply(history)`: Claude in the story's role, or
the story's lines in order when there is no key (local runs, tests).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from convox.audio.frames import FRAME_BYTES, FRAME_MS
from convox.clock import Clock, MonotonicClock
from convox.legs.ws import PlayoutTracker
from convox.types import Side, other

log = logging.getLogger(__name__)

SILENCE = bytes(FRAME_BYTES)
REPLY_PAUSE_S = 0.6  # after the visitor's line has played: a person draws breath
OPENING_PAUSE_S = 0.8  # after the opening line, before the agent's first words
NUDGE_S = 25.0  # silence after the agent's line before it asks once more
MAX_LINES = 12  # the agent stops answering after this many lines

History = list[tuple[str, str]]  # ("me" | "them", text in the agent's language)
Reply = Callable[[History], Awaitable[str | None]]


class AgentLeg:
    def __init__(self, leg_id: Side, *, clock: Clock | None = None) -> None:
        self.leg_id = leg_id
        self.clock: Clock = clock if clock is not None else MonotonicClock()
        self.closed = False
        self._speech = bytearray()
        self._quiet = asyncio.Event()
        self._quiet.set()
        self._mark_cbs: list[Callable[[str], None]] = []
        self._tracker = PlayoutTracker(self.clock, self._fire_mark)

    @property
    def speaking(self) -> bool:
        return not self._quiet.is_set()

    def say(self, pcm16_16k: bytes) -> None:
        """Queue speech (PCM16@16k) for the mic; it plays out at real time."""
        if self.closed or not pcm16_16k:
            return
        pad = -len(pcm16_16k) % FRAME_BYTES
        self._speech += pcm16_16k + bytes(pad)
        self._quiet.clear()

    async def wait_quiet(self) -> None:
        await self._quiet.wait()

    async def frames(self) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        t0, i = loop.time(), 0
        while not self.closed:
            if self._speech:
                frame = bytes(self._speech[:FRAME_BYTES])
                del self._speech[:FRAME_BYTES]
                if not self._speech:
                    self._quiet.set()
            else:
                frame = SILENCE
            yield frame
            i += 1
            delay = t0 + i * FRAME_MS / 1000 - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -1.0:  # the loop stalled: resync rather than burst
                t0, i = loop.time(), 0

    async def send(self, pcm16_16k: bytes) -> None:
        self._tracker.played(len(pcm16_16k))

    async def clear(self) -> None:
        self._tracker.clear()

    async def mark(self, tag: str) -> None:
        self._tracker.mark(tag)

    def on_mark(self, cb: Callable[[str], None]) -> None:
        self._mark_cbs.append(cb)

    def on_dtmf(self, cb: Callable[[str], None]) -> None:
        pass

    async def close(self) -> None:
        self.closed = True
        self._speech.clear()
        self._quiet.set()
        self._tracker.close()

    def _fire_mark(self, tag: str) -> None:
        for cb in self._mark_cbs:
            cb(tag)


class Agent:
    """Drives an AgentLeg on `side` of `session` from the call's events on `bus`."""

    def __init__(
        self,
        session: Any,
        bus: Any,
        side: Side,
        leg: AgentLeg,
        *,
        reply: Reply,
        speak: Callable[[str], Awaitable[bytes]],
        opener: str,
        nudge: str | None = None,
    ) -> None:
        self.session = session
        self.bus = bus
        self.side = side
        self.leg = leg
        self.reply = reply
        self.speak = speak
        self.opener = opener
        self.nudge = nudge
        self.history: History = []
        self.lines = 0
        self._events: asyncio.Queue[Any] = asyncio.Queue()

    async def run(self) -> None:
        reader = asyncio.create_task(self._read(), name=f"{self.session.call_id}-agent-events")
        try:
            await self._converse()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # the agent must never take the call down by itself
            log.exception("agent.failed", extra={"call_id": self.session.call_id, "error": repr(e)})
        finally:
            reader.cancel()
            with contextlib.suppress(BaseException):
                await reader

    async def _read(self) -> None:
        async for ev in self.bus.subscribe(self.session.call_id):
            self._events.put_nowait(ev)

    def _ended(self) -> bool:
        return self.session.status == "ended" or self.leg.closed

    async def _next(self, wait_s: float | None) -> Any:
        try:
            return await asyncio.wait_for(self._events.get(), wait_s)
        except TimeoutError:
            return None

    async def _converse(self) -> None:
        while self.session.status != "active":
            if self._ended():
                return
            await self._next(0.2)
        await self._settle(OPENING_PAUSE_S)
        await self._say(self.opener)
        loop = asyncio.get_running_loop()
        heard: list[str] = []
        nudged = False
        reply_at = quiet_since = loop.time()
        while not self._ended():
            wait = max(0.05, reply_at - loop.time()) if heard else 1.0
            ev = await self._next(wait)
            if ev is not None:
                if ev.kind == "call" and ev.payload.get("status") == "ended":
                    return
                text = self._heard(ev)
                if text:
                    heard.append(text)
                    reply_at = quiet_since = loop.time() + REPLY_PAUSE_S
            busy = not self.session.idle or self.leg.speaking
            if heard and loop.time() >= reply_at and not busy:
                self.history.append(("them", " ".join(heard)))
                heard = []
                if self.lines >= MAX_LINES:
                    continue
                answer = await self.reply(list(self.history))
                if answer and not self._ended():
                    await self._say(answer)
                    nudged = False
                quiet_since = loop.time()
            elif not heard and self.nudge and not nudged and not busy:
                if loop.time() - quiet_since > NUDGE_S:
                    nudged = True
                    await self._say(self.nudge)
                    quiet_since = loop.time()

    def _heard(self, ev: Any) -> str | None:
        if ev.kind != "turn":
            return None
        turn = ev.payload.get("turn") or {}
        if turn.get("side") != other(self.side) or turn.get("backchannel"):
            return None
        text = (turn.get("tgt_text") or "").strip()
        return text or None

    async def _settle(self, pause_s: float) -> None:
        """Wait until nothing is in flight (the opening line has played), then pause."""
        for _ in range(100):
            if self.session.idle or self._ended():
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(pause_s)

    async def _say(self, text: str) -> None:
        try:
            audio = await self.speak(text)
        except Exception as e:
            log.warning(
                "agent.tts_failed", extra={"call_id": self.session.call_id, "error": repr(e)}
            )
            return
        if self._ended():
            return
        self.history.append(("me", text))
        self.lines += 1
        log.info("agent.line", extra={"call_id": self.session.call_id, "line": self.lines})
        self.leg.say(audio)
        await self.leg.wait_quiet()


# --- replies ----------------------------------------------------------------------------


def scripted(lines: list[str]) -> Reply:
    """The story's lines in order (after the opener), then nothing: runs without a key."""
    queue = list(lines)

    async def reply(history: History) -> str | None:
        return queue.pop(0) if queue else None

    return reply


def system_prompt(story: dict[str, Any], side: Side, lang_name: str, them_lang: str) -> str:
    me, them = story[side], story[other(side)]
    persona = (story.get("agent") or {}).get("persona") or ""
    return (
        f"You are {me['name']}, the {me['role'].lower()}, on a phone call with the "
        f"{them['role'].lower()}, someone you have never met. You speak only {lang_name}; "
        f"they speak only {them_lang}, and a live interpreter carries the call both ways. "
        f"Situation: {story['headline']}. {persona}\n\n"
        f"Reply with what you say next: one short spoken sentence, at most 15 words, in "
        f"{lang_name} written in its own script, casual and polite, as people talk on the "
        "phone. Write numbers as digits. No English words, no Latin letters, no quotes, no "
        "stage directions, never mention the interpreter. If what you heard makes no sense, "
        "ask them to say it again. Once you have agreed where and when to meet, confirm it "
        "in one line and say goodbye."
    )


def transcript(history: History, me: str, them: str) -> str:
    lines = [f"{me if who == 'me' else them}: {text}" for who, text in history]
    return "The call so far:\n" + "\n".join(lines) + f"\n\nWhat do you say next, {me}?"


def clean(text: str) -> str | None:
    """First line, without quotes or a speaker label; None if nothing is left."""
    line = next((x.strip() for x in text.strip().splitlines() if x.strip()), "")
    line = re.sub(r"^[A-Za-z][\w .]{0,20}[:：]\s*", "", line)  # "Driver: ..."
    line = line.strip(" \"'“”‘’«»")
    return line or None


def claude_reply(
    client: Any, model: str, story: dict[str, Any], side: Side, langs: tuple[str, str]
) -> Reply:
    me, them = story[side], story[other(side)]
    system = system_prompt(story, side, langs[0], langs[1])

    async def reply(history: History) -> str | None:
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=200,
                system=system,
                messages=[
                    {"role": "user", "content": transcript(history, me["role"], them["role"])}
                ],
            )
        except Exception as e:  # the visitor just hears nothing back this time
            log.warning("agent.reply_failed", extra={"error": repr(e)})
            return None
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return clean(text)

    return reply


async def synthesise(tts: Any, text: str, voice: str, lang: str) -> bytes:
    out = bytearray()
    async for chunk in tts.synth(text, voice, lang):
        out += chunk
    return bytes(out)
