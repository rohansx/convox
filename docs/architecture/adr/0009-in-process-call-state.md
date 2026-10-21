# ADR 0009: State in-process per call; Redis for the call registry only; Postgres off the hot path

This ADR records where call state lives and why nothing in the hot path touches a database.

## Status

Accepted (October 2026).

## Context

- A call's two legs share one `FloorController`, so they must land on the same process.
- Each provider opens a separate websocket per leg; both carry the call id in the URL (`/ws/leg?call=c_91&side=a`).
- Every millisecond in the hot path counts against a p95 of 2.0 s.

## Decision

- **In-process per call:** one asyncio task owns a call (`CallSession` + `FloorController`).
- **Route both websockets to the same node via the call id in the URL.** No distributed state in the hot path.
- **Redis only for the call registry and cross-node lookup.**
- **Postgres for calls, legs, turns, events and latency rows**, written by a background task (`store/writer.py`, async batch insert) from an in-memory queue.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Shared state in Redis, legs on any node | Puts network round trips in the controller's decision loop; adds failure modes for no v1 benefit. |
| Synchronous turn writes to Postgres | Database latency in the hot path. |

## Consequences

- A process crash drops the calls it owns; there is no live failover in v1.
- Scaling beyond one node later requires the `/ws/leg` attach path to look up the owning node in Redis and route there.
- The session dies when both legs close; final `calls` metrics are computed then.
- Turn text is nulled after latency metrics are computed unless `record: true` or the platform opted in ([security-privacy.md](../../tech-spec/security-privacy.md)).

## Related

- [system-overview.md](../system-overview.md)
- [data-model.md](../../tech-spec/data-model.md)
- [ADR 0010](0010-single-vm-mumbai.md)
