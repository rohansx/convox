# ADR 0010: Deploy on one VM in ap-south-1 (Mumbai) with Docker Compose

This ADR records the v1 deployment topology.

## Status

Accepted (October 2026).

## Context

- Exotel's media servers are in India; Sarvam is in India. Every 100 ms of RTT shows up in the latency budget.
- ElevenLabs is not in India (150 to 250 ms RTT from Mumbai).
- v1 call volume is a buildathon demo, a hospital pilot and a dial-in number: nothing to orchestrate yet.
- India-region hosting is part of the DPDP / consent position ([security-privacy.md](../../tech-spec/security-privacy.md)).

## Decision

- **One VM in AWS ap-south-1 (Mumbai).**
- **Docker Compose:** `app`, `postgres`, `redis`, `caddy` (TLS).
- **Same box for the console.**
- Ask Exotel to IP-allowlist the VM.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Kubernetes | Nothing to orchestrate yet; a non-goal for v1. |
| Region outside India | RTT to Exotel and Sarvam lands directly in the latency budget; data residency. |

## Consequences

- Single point of failure; acceptable for v1. Compose restart policies and a rehearsed redeploy are the mitigation ([deployment.md](../../engineering/deployment.md)).
- Both legs of every call are trivially on the same process ([ADR 0009](0009-in-process-call-state.md)).
- VM size, backups and monitoring: TBD — see [open-questions.md](../../plan/open-questions.md).

## Related

- [system-overview.md](../system-overview.md#deployment-topology)
- [deployment.md](../../engineering/deployment.md)
- [ADR 0008](0008-tts-bulbul-indic-eleven-hi-en.md)
