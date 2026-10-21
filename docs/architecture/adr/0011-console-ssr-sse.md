# ADR 0011: Console: server-rendered HTML + SSE (htmx), no SPA

This ADR records how the live console is built.

## Status

Accepted (October 2026).

## Context

- The console shows active calls; per call, two columns (A, B), each turn as src → tgt with stage latencies inline, state badge, queue depth, DTMF and barge-in markers.
- It is the stage demo surface and the platform trust-review surface; it has to look finished.
- Data flows one way, server → browser, from the controller's event stream.
- P4 owns both the console and ward mode within a 10-day build.

## Decision

- **Server-rendered HTML + SSE**, htmx (or plain JS). **No SPA.**
- `GET /console` for the live view; SSE at `/console/events?call=…` from the controller's event stream. No polling.
- Served by the same app on the same VM.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| React SPA | A React build is a day the team does not have; a non-goal for v1. |
| WebSockets to the browser | Data is one-directional; SSE is enough. |
| Polling | Laggy for live transcripts; extra load. |

## Consequences

- Console events must be emitted by the controller without blocking the hot path (non-blocking fan-out).
- Styling effort goes into templates, since it must look finished on stage.

## Related

- [console.md](../../tech-spec/console.md)
- [api.md](../../tech-spec/api.md)
- [demo-script.md](../../plan/demo-script.md)
