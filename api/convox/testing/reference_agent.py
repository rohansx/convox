"""A reference voice agent with deliberately injectable bugs.

This exists so Convox can be tested against known ground truth. A testing tool
whose own reliability is unproven is worthless, so the self-test suite runs
Convox against this agent with specific bugs switched on and asserts that Convox
catches exactly those bugs — no more, no fewer.

It doubles as the demo target in the quickstart: a small pharmacy refill agent
that behaves plausibly until you tell it not to.

Run standalone::

    python -m convox.testing.reference_agent --port 8765 --bug slow_tools
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection, serve

GREETING = "Thanks for calling Cedarwood Pharmacy. How can I help you today?"

# Callers say phone numbers as words. A real agent sees whatever its ASR produced,
# so the reference agent accepts both forms rather than only accepting digits.
_SPOKEN_DIGITS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def heard_digits(text: str) -> str:
    out: list[str] = []
    for token in re.split(r"[^\w]+", text.casefold()):
        if token.isdigit():
            out.append(token)
        elif token in _SPOKEN_DIGITS:
            out.append(_SPOKEN_DIGITS[token])
    return "".join(out)


@dataclass
class Bugs:
    """Faults the agent can be told to exhibit."""

    #: Extra latency on every response, in milliseconds.
    slow_responses_ms: int = 0
    #: Extra latency on turns that involve a tool call.
    slow_tools_ms: int = 0
    #: Corrupt the last digit of any captured phone number (an ASR-style fault).
    mangle_digits: bool = False
    #: Never confirm the pickup time back to the caller.
    skip_confirmation: bool = False
    #: Repeat the same clarification instead of progressing.
    repeat_loop: bool = False
    #: Do not greet — the caller has to speak first.
    no_greeting: bool = False
    #: Hang up as soon as the order is placed, before confirming.
    hang_up_early: bool = False
    #: Never call the order tool, but claim success anyway.
    skip_tool_call: bool = False

    @classmethod
    def from_names(cls, names: list[str]) -> Bugs:
        bugs = cls()
        for name in names:
            match name:
                case "slow_responses":
                    bugs.slow_responses_ms = 1800
                case "slow_tools":
                    bugs.slow_tools_ms = 3000
                case "mangle_digits":
                    bugs.mangle_digits = True
                case "skip_confirmation":
                    bugs.skip_confirmation = True
                case "repeat_loop":
                    bugs.repeat_loop = True
                case "no_greeting":
                    bugs.no_greeting = True
                case "hang_up_early":
                    bugs.hang_up_early = True
                case "skip_tool_call":
                    bugs.skip_tool_call = True
                case _:
                    raise SystemExit(f"unknown bug: {name}")
        return bugs


@dataclass
class CallState:
    phone: str | None = None
    medication: str | None = None
    order_placed: bool = False
    clarifications: int = 0
    facts: dict[str, Any] = field(default_factory=dict)


class ReferenceAgent:
    def __init__(self, bugs: Bugs | None = None) -> None:
        self.bugs = bugs or Bugs()

    async def handle(self, ws: ServerConnection) -> None:
        state = CallState()
        if not self.bugs.no_greeting:
            await self._say(ws, GREETING)

        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                match msg.get("type"):
                    case "caller.variables":
                        state.facts.update(msg.get("variables") or {})
                    case "caller.text":
                        await self._respond(ws, state, str(msg.get("text", "")))
                    case "caller.dtmf":
                        await self._say(ws, f"I got the option {msg.get('digits')}.")
                    case "caller.hangup":
                        return
        except websockets.exceptions.ConnectionClosed:
            return

    # ── protocol helpers ──

    async def _say(self, ws: ServerConnection, text: str, *, final: bool = True) -> None:
        await ws.send(json.dumps({"type": "agent.text", "text": text, "final": final}))

    async def _tool(self, ws: ServerConnection, name: str, args: dict[str, Any]) -> None:
        await ws.send(json.dumps({"type": "agent.tool_call", "name": name, "args": args}))

    async def _hangup(self, ws: ServerConnection) -> None:
        await ws.send(json.dumps({"type": "agent.hangup"}))

    async def _delay(self, *, tool: bool = False) -> None:
        total = self.bugs.slow_responses_ms + (self.bugs.slow_tools_ms if tool else 0)
        if total:
            await asyncio.sleep(total / 1000)

    # ── conversation ──

    async def _respond(self, ws: ServerConnection, state: CallState, text: str) -> None:
        lowered = text.casefold()
        digits = heard_digits(text)

        if self.bugs.repeat_loop:
            state.clarifications += 1
            await self._delay()
            await self._say(ws, "Sorry, I didn't catch that. Could you say it again please?")
            return

        # Capture a phone number.
        if len(digits) >= 10 and state.phone is None:
            state.phone = self._capture(digits)
            await self._delay(tool=True)
            await self._tool(ws, "lookup_patient", {"phone": state.phone})
            await self._say(ws, f"Thanks, I have your number as {state.phone}. Which medication is it?")
            return

        # Capture a medication.
        medication = self._medication(text)
        if medication and state.medication is None:
            state.medication = medication
            await self._delay()
            await self._say(ws, f"Got it, {medication}. Can I confirm your phone number?")
            return

        # Place the order once we have both.
        if state.phone and state.medication and not state.order_placed:
            state.order_placed = True
            await self._delay(tool=True)
            if not self.bugs.skip_tool_call:
                await self._tool(
                    ws,
                    "create_refill_order",
                    {"phone": state.phone, "medication": state.medication},
                )
            if self.bugs.hang_up_early:
                await self._say(ws, "Your order is in.")
                await self._hangup(ws)
                return
            if self.bugs.skip_confirmation:
                await self._say(ws, "All done. Anything else?")
                return
            await self._say(
                ws,
                f"Your refill for {state.medication} is booked for pickup tomorrow at 6 PM. Anything else?",
            )
            return

        if any(word in lowered for word in ("no", "nothing", "that's all", "thats all", "bye")):
            await self._delay()
            await self._say(ws, "Thanks for calling Cedarwood Pharmacy. Goodbye.")
            await self._hangup(ws)
            return

        if "refill" in lowered or "dawai" in lowered or "prescription" in lowered:
            await self._delay()
            await self._say(ws, "Happy to help with a refill. What's the phone number on the account?")
            return

        await self._delay()
        await self._say(ws, "I can help with prescription refills. What would you like to do?")

    def _capture(self, digits: str) -> str:
        national = digits[-10:]
        if self.bugs.mangle_digits:
            last = str((int(national[-1]) + 1) % 10)
            national = national[:-1] + last
        return national

    @staticmethod
    def _medication(text: str) -> str | None:
        known = ("metformin", "atorvastatin", "amlodipine", "insulin", "levothyroxine")
        lowered = text.casefold()
        for drug in known:
            if drug in lowered:
                dose = re.search(r"(\d+)\s*(mg|milligram)", lowered)
                return f"{drug.title()} {dose.group(1)}mg" if dose else drug.title()
        return None


async def run_server(host: str = "127.0.0.1", port: int = 8765, bugs: Bugs | None = None) -> None:
    agent = ReferenceAgent(bugs)
    async with serve(agent.handle, host, port):
        await asyncio.Future()


@contextlib.asynccontextmanager
async def running_agent(bugs: Bugs | None = None, host: str = "127.0.0.1", port: int = 0):
    """Start the agent on an ephemeral port. Yields its ws:// URL."""
    agent = ReferenceAgent(bugs)
    async with serve(agent.handle, host, port) as server:
        bound = next(iter(server.sockets)).getsockname()
        yield f"ws://{bound[0]}:{bound[1]}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Convox reference voice agent")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--bug",
        action="append",
        default=[],
        help="inject a known bug (repeatable): slow_responses, slow_tools, mangle_digits, "
        "skip_confirmation, repeat_loop, no_greeting, hang_up_early, skip_tool_call",
    )
    args = parser.parse_args()
    bugs = Bugs.from_names(args.bug)
    print(f"reference agent listening on ws://{args.host}:{args.port}  bugs={args.bug or 'none'}")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_server(args.host, args.port, bugs))


if __name__ == "__main__":
    main()
