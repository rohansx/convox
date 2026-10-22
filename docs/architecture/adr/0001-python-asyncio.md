# ADR 0001: Python 3.12 + asyncio, hot path in numpy/onnx

This ADR records the choice of implementation language and concurrency model for the Convox server, and the condition under which part of it would be ported to Rust.

## Status

Accepted (October 2026).

## Context

- Five people, a compressed schedule: the spec planned 13 days; the build is now 10 working days (Day 1 = Wed 21 Oct 2026, Day 10 = Fri 30 Oct, submission Sat 31 Oct). See [buildathon-10-day-plan.md](../../plan/buildathon-10-day-plan.md).
- Every vendor we depend on (Sarvam, ElevenLabs, Exotel examples, Silero) ships Python first.
- The latency budget is dominated by vendor round trips (STT final, MT, TTS first byte, provider playout), not interpreter CPU. See [latency-engineering.md](../../engineering/latency-engineering.md).
- The CPU-bound pieces are small and well defined: resampling, mixing/ducking, VAD inference on 20 ms frames.

## Decision

- **Python 3.12, asyncio.** One asyncio task owns a call; all vendor and provider I/O is async websockets.
- **Hot path (resample, mixer, VAD) in numpy / onnx.** soxr for resampling, Silero VAD v5 via onnx.
- **Rust port of the hot path only if CPU profiling says so.** A Rust mixer is roughly a two-day swap later; Rohan does it when a profile justifies it.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Rust | Integration speed. Vendor SDKs and examples are Python-first; writing clients from scratch costs days the team does not have. Kept as a targeted later port of the hot path (resample, mixer, VAD) only. |
| Go | Same integration-speed problem as Rust, with no compensating gain on the bottleneck (vendor latency). |

## Consequences

- Fast integration with every vendor; Pipecat service classes can be borrowed as libraries ([ADR 0002](0002-own-topology-not-pipecat.md)).
- The event loop must never block: numpy/onnx calls are short per frame, and no sync I/O is allowed in the hot path ([system-overview.md](../system-overview.md#hot-path-rules)).
- CPU per call must be profiled under load before claiming capacity; if the mixer or VAD shows up, the Rust port is the planned escape.

## Related

- [system-overview.md](../system-overview.md)
- [ADR 0002](0002-own-topology-not-pipecat.md)
- [latency-engineering.md](../../engineering/latency-engineering.md)
- [conventions.md](../../engineering/conventions.md)
