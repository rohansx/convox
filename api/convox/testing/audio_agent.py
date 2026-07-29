"""The reference agent, speaking over an audio channel.

Same conversation logic as the text reference agent, wrapped in a real voice
stack: it listens to streamed PCM, segments turns with its own VAD, decodes what
it heard, and replies with synthesised audio. That makes it a genuine subject for
the measurements that only exist on an audio channel — response latency to first
sample, barge-in stop time, and TTS truncation.

The audio-only faults it can be told to exhibit are the ones a transcript can
never reveal:

* ``ignore_barge_in`` — keep talking straight through an interruption
* ``truncate_audio``  — cut the last utterance off mid-word
* ``tts_artifact``    — splice noise into the reply
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from dataclasses import dataclass

import numpy as np
import websockets
from websockets.asyncio.server import ServerConnection, serve

from convox.audio import pcm
from convox.testing.reference_agent import Bugs, CallState, ReferenceAgent
from convox.voice.tone import ToneVoice

#: Silence that ends a caller turn, from the agent's point of view.
CALLER_SILENCE_MS = 300
SPEECH_RMS = 0.01


@dataclass
class AudioBugs(Bugs):
    """Faults that only exist once there is audio."""

    #: Keep speaking through an interruption instead of yielding.
    ignore_barge_in: bool = False
    #: Cut the final utterance off mid-word.
    truncate_audio: bool = False
    #: Splice a burst of noise into replies.
    tts_artifact: bool = False

    @classmethod
    def from_names(cls, names: list[str]) -> AudioBugs:
        audio_only = {"ignore_barge_in", "truncate_audio", "tts_artifact"}
        base = Bugs.from_names([n for n in names if n not in audio_only])
        bugs = cls(**base.__dict__)
        for name in names:
            if name in audio_only:
                setattr(bugs, name, True)
        return bugs


class AudioReferenceAgent:
    def __init__(self, bugs: AudioBugs | None = None, *, sample_rate: int = 16000) -> None:
        self.bugs = bugs or AudioBugs()
        self.sample_rate = sample_rate
        self.voice = ToneVoice()
        self._brain = ReferenceAgent(self.bugs)

    async def handle(self, ws: ServerConnection) -> None:
        state = CallState()
        session = _Session(self, ws, state)
        await session.run()


class _Session:
    """One call. Owns the listening loop and the speaking task."""

    def __init__(self, agent: AudioReferenceAgent, ws: ServerConnection, state: CallState) -> None:
        self.agent = agent
        self.ws = ws
        self.state = state
        self.sample_rate = agent.sample_rate
        self._heard: list[np.ndarray] = []
        self._silence_ms = 0
        self._receiving = False
        self._speaking: asyncio.Task[None] | None = None

    async def run(self) -> None:
        if not self.agent.bugs.no_greeting:
            await self._speak("Thanks for calling Cedarwood Pharmacy. How can I help you today?")
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    await self._on_audio(pcm.from_pcm16(raw))
                    continue
                msg = json.loads(raw)
                match msg.get("type"):
                    case "caller.hangup":
                        return
                    case "caller.dtmf":
                        await self._speak(f"I got the option {msg.get('digits')}.")
        except websockets.exceptions.ConnectionClosed:
            return
        finally:
            await self._stop_speaking()

    # ── listening ──

    async def _on_audio(self, samples: np.ndarray) -> None:
        level = pcm.rms(samples)
        frame_ms = pcm.duration_ms(samples, self.sample_rate) or 20

        if level > SPEECH_RMS:
            if not self._receiving:
                self._receiving = True
                # A caller speaking over us is an interruption: a well-behaved
                # agent stops. `ignore_barge_in` is what a bad one does.
                if not self.agent.bugs.ignore_barge_in:
                    await self._stop_speaking()
            self._heard.append(samples)
            self._silence_ms = 0
            return

        if self._receiving:
            self._heard.append(samples)
            self._silence_ms += frame_ms
            if self._silence_ms >= CALLER_SILENCE_MS:
                await self._end_of_caller_turn()

    async def _end_of_caller_turn(self) -> None:
        audio = pcm.concat(*self._heard)
        self._heard = []
        self._receiving = False
        self._silence_ms = 0
        text = self.agent.voice.transcribe(audio, sample_rate=self.sample_rate)
        if not text.strip():
            return
        # Real platforms expose their own transcript of the caller. Publishing it
        # is what lets Convox compute true agent-side recognition accuracy rather
        # than a proxy measured on its own copy of the audio.
        await self.ws.send(json.dumps({"type": "agent.transcript", "of": "caller", "text": text}))
        await self._respond(text)

    # ── speaking ──

    async def _respond(self, text: str) -> None:
        """Run the shared conversation logic, rendering its replies as audio."""
        await self.agent._brain._respond(_AudioSink(self), self.state, text)

    async def _speak(self, text: str) -> None:
        await self._stop_speaking()
        audio = self.agent.voice.synthesize(text, sample_rate=self.sample_rate)

        if self.agent.bugs.tts_artifact:
            midpoint = len(audio) // 2
            burst = np.random.default_rng(0).standard_normal(self.sample_rate // 20) * 0.5
            audio = pcm.concat(audio[:midpoint], burst.astype(np.float32), audio[midpoint:])

        if self.agent.bugs.truncate_audio:
            # Stop mid-word at full amplitude — the classic teardown race.
            audio = audio[: int(len(audio) * 0.7)]

        self._speaking = asyncio.create_task(self._stream(audio))

    async def _stream(self, audio: np.ndarray) -> None:
        frame_samples = int(self.sample_rate * 0.02)
        try:
            for frame in pcm.frames(audio, frame_samples):
                await self.ws.send(pcm.to_pcm16(frame))
                await asyncio.sleep(0.02)
        except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
            return

    async def _stop_speaking(self) -> None:
        if self._speaking is None:
            return
        self._speaking.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._speaking
        self._speaking = None


class _AudioSink:
    """Adapts the text agent's send helpers onto the audio session."""

    def __init__(self, session: _Session) -> None:
        self._session = session

    async def send(self, payload: str) -> None:
        message = json.loads(payload)
        match message.get("type"):
            case "agent.text":
                await self._session._speak(str(message.get("text", "")))
            case _:
                await self._session.ws.send(payload)


async def run_server(host: str = "127.0.0.1", port: int = 8770, bugs: AudioBugs | None = None) -> None:
    agent = AudioReferenceAgent(bugs)
    async with serve(agent.handle, host, port, max_size=None):
        await asyncio.Future()


@contextlib.asynccontextmanager
async def running_audio_agent(bugs: AudioBugs | None = None, host: str = "127.0.0.1", port: int = 0):
    """Start the audio agent on an ephemeral port. Yields its ws:// URL."""
    agent = AudioReferenceAgent(bugs)
    async with serve(agent.handle, host, port, max_size=None) as server:
        bound = next(iter(server.sockets)).getsockname()
        yield f"ws://{bound[0]}:{bound[1]}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Convox reference voice agent (audio)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument(
        "--bug",
        action="append",
        default=[],
        help="inject a known bug (repeatable): slow_responses, slow_tools, mangle_digits, "
        "skip_confirmation, repeat_loop, no_greeting, hang_up_early, skip_tool_call, "
        "ignore_barge_in, truncate_audio, tts_artifact",
    )
    args = parser.parse_args()
    bugs = AudioBugs.from_names(args.bug)
    print(f"audio reference agent on ws://{args.host}:{args.port}  bugs={args.bug or 'none'}")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_server(args.host, args.port, bugs))


if __name__ == "__main__":
    main()
