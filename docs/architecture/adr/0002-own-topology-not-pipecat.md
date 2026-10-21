# ADR 0002: Own the topology (FastAPI + websockets + asyncio); borrow Pipecat classes, not its pipeline

This ADR records why dubhash writes its own call topology and floor controller instead of running an existing voice-agent framework's pipeline.

## Status

Accepted (October 2026).

## Context

- Pipecat, LiveKit Agents and Bolna model **one human + one bot on one transport**.
- dubhash is **two humans and two transports joined by a controller**: two legs, two mirrored interpreter pipelines, one `FloorController` that is the only code allowed to `send()`, `clear()` and `mark()` on either leg. See [floor-controller.md](../../tech-spec/floor-controller.md).
- Some Pipecat pieces are useful on their own: Silero VAD wrapper, Exotel serializer, Sarvam/ElevenLabs clients.

## Decision

- **Own the topology:** FastAPI + `websockets` + asyncio tasks.
- **Borrow Pipecat's service classes** (Silero VAD, Exotel serializer, Sarvam/ElevenLabs clients) as libraries where they save time.
- **Do not run Pipecat's pipeline.**

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Pipecat pipeline | Single-transport, single-human model. Bending it to cross two legs costs more than writing a ~400-line controller. |
| LiveKit Agents | Same one-human-one-bot model. |
| Bolna | Same reason, plus it is an agent runtime, not a bridge. |

## Consequences

- The controller (~400 lines) and the session are our code and our IP; they are fully testable with `FakeLeg` and scripted events (`test_floor.py`).
- We maintain our own frame plumbing, mixer and transport classes ([leg-transports.md](../../tech-spec/leg-transports.md), [audio.md](../../tech-spec/audio.md)).
- Borrowed Pipecat classes are pinned dependencies; known issues (for example the reported SarvamSTT stream dying after 60 s of silence) are ours to work around. See [open-questions.md](../../plan/open-questions.md).

## Related

- [system-overview.md](../system-overview.md)
- [interpreter-pipeline.md](../../tech-spec/interpreter-pipeline.md)
- [floor-controller.md](../../tech-spec/floor-controller.md)
- [ADR 0001](0001-python-asyncio.md)
