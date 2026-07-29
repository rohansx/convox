"""Consumes agent-side events and turns them into a recorded conversation.

Runs as a background task for the life of a call so that agent output is captured
with accurate timestamps regardless of what the caller policy is doing. The caller
policy interacts with it only through :meth:`wait_for_turn`.
"""

from __future__ import annotations

import asyncio
import contextlib

from convox.adapters.base import AgentEvent, TargetSession
from convox.model.trial import Turn
from convox.sim.recorder import Recorder


class AgentStream:
    """Assembles agent turns from a session's event stream."""

    def __init__(self, session: TargetSession, recorder: Recorder) -> None:
        self._session = session
        self._recorder = recorder
        self._completed: asyncio.Queue[Turn] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.agent_hung_up = False
        self.transport_error: str | None = None
        self.stream_closed = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _consume(self) -> None:
        try:
            async for event in self._session.events():
                self._handle(event)
        finally:
            # A closed stream ends any wait that is still pending.
            if self._recorder.agent_turn_open:
                turn = self._recorder.agent_speech_end()
                if turn is not None:
                    self._completed.put_nowait(turn)
            self.stream_closed.set()

    def _handle(self, event: AgentEvent) -> None:
        match event.kind:
            case "agent.speech_start":
                self._recorder.agent_speech_start(event.at_ms)

            case "agent.text":
                text = str(event.payload.get("text", ""))
                if text:
                    self._recorder.agent_text(text, event.at_ms)
                # Absent an explicit marker, a text frame is a complete turn.
                if event.payload.get("final", True):
                    turn = self._recorder.agent_speech_end()
                    if turn is not None:
                        self._completed.put_nowait(turn)

            case "agent.speech_end":
                turn = self._recorder.agent_speech_end(event.at_ms)
                if turn is not None:
                    self._completed.put_nowait(turn)

            case "agent.tool_call":
                self._recorder.agent_tool_call(
                    str(event.payload.get("name", "")),
                    dict(event.payload.get("args") or {}),
                    event.at_ms,
                )

            case "agent.hangup":
                self.agent_hung_up = True
                turn = self._recorder.agent_speech_end()
                if turn is not None:
                    self._completed.put_nowait(turn)
                self.stream_closed.set()

            case "error":
                self.transport_error = str(event.payload.get("message", "unknown transport error"))
                self._recorder.event("error", at_ms=event.at_ms, message=self.transport_error)

            case _:
                self._recorder.event(event.kind, at_ms=event.at_ms, **event.payload)

    async def wait_for_turn(self, timeout_ms: int, *, after_ms: int = 0) -> Turn | None:
        """Wait for an agent turn that began at or after ``after_ms``.

        The filter matters: an agent that greets on connect produces a turn before
        the caller has said anything. Without it, that greeting would be consumed
        as the reply to the caller's first utterance and every subsequent turn
        would be attributed one step late — silently corrupting every latency
        measurement on the call.

        Returns ``None`` on timeout or once the agent has hung up.
        """
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while True:
            if self.agent_hung_up and self._completed.empty():
                return None
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                turn = await asyncio.wait_for(self._completed.get(), timeout=remaining)
            except TimeoutError:
                return None
            if turn.speech_start_ms >= after_ms:
                return turn

    def drain(self) -> list[Turn]:
        turns = []
        while not self._completed.empty():
            turns.append(self._completed.get_nowait())
        return turns
