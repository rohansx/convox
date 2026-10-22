# ADR 0003: Telephony: Exotel voicebot applet primary, Plivo audio streaming fallback, behind one LegTransport

This ADR records which CPaaS providers carry the two legs of an interpreted call, how they are abstracted, and how the team develops without telephony minutes.

## Status

Accepted (October 2026).

## Context

- Convox must sit inside a call that already passes through a CPaaS ([telephony-integration.md](../telephony-integration.md)). It needs **two-way** audio per leg, with barge-in (`clear`) and playback marks.
- Exotel is a buildathon sponsor and the vendor behind most masked calls in India. Its voicebot applet is two-way; its stream applet is one-way and unusable.
- Exotel streaming access may slip; it is requested via the sponsor channel in week one.
- Plivo is self-serve once the Udyam registration is verified.
- The team cannot wait for telephony credit to build and measure the pipeline.

## Decision

- **Exotel voicebot applet primary**, two legs per call.
- **Plivo audio streaming fallback.**
- Both behind one **`LegTransport`** interface (~120 lines per provider): `frames()`, `send()`, `clear()`, `mark()`, `on_mark()`, `on_dtmf()`, `close()`. See [leg-transports.md](../../tech-spec/leg-transports.md).
- **Dev/loopback transport:** browser WebRTC via a tiny signalling endpoint (`WebRtcLeg`). Two tabs = two legs. This is also the last-resort demo if credits fail.
- **Test transport:** `FakeLeg` (wav in, wav out).
- Whichever of Exotel/Plivo lands first is built first (Day 4, CP1 target, in the [10-day plan](../../plan/buildathon-10-day-plan.md)); the other follows on Day 5, best-effort.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Twilio | India numbers and quality are worse, and it is not in the room (not a sponsor). |
| Exotel stream applet | One-way: Exotel forks audio to us; we cannot speak. |
| Single provider, no abstraction | Exotel access is a schedule risk; the abstraction is small and also gives us `WebRtcLeg` and `FakeLeg` for free. |

## Consequences

- Provider is chosen per call by `provider` in `POST /v1/calls`; swapping is configuration.
- Each provider's quirks stay inside its transport: Exotel 100 ms outbound chunks (3,200 bytes, multiple of 320); Plivo `playAudio` / `clearAudio` / `checkpoint`.
- Two open questions gate audio handling: does Exotel really deliver 16k, and does Plivo India support `audio/x-l16;rate=16000` bidirectionally ([open-questions.md](../../plan/open-questions.md)).
- Exotel auth via Basic auth header or IP allowlist of the VM.

## Related

- [telephony-integration.md](../telephony-integration.md)
- [leg-transports.md](../../tech-spec/leg-transports.md)
- [ADR 0004](0004-audio-16k-pcm16-20ms.md)
- [risks.md](../../plan/risks.md)
