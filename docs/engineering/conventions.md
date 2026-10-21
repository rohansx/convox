# Engineering conventions

This document lists the rules every dubhash PR follows. Some protect the hot path and the floor controller: interfaces with fakes, a single owner of transport control, no storage in the hot path, one asyncio task per call. The rest keep five people working in parallel for 10 days without stepping on each other: logging and metric names, branching, commit style. Rules marked **(spec)** come from the tech spec. The rest are proposed team conventions, and changing one means a PR to this file.

## Architecture rules

### 1. Every component has an interface and a fake (spec)

- Every leg, STT, MT, TTS and VAD sits behind a `typing.Protocol`. Each Protocol ships with a fake in the same PR: `FakeLeg`, `FakeSTT`, `FakeMT`, `FakeTTS`.
- Fakes are deterministic and need no network or keys. They can run at real time (latency-shaped, with configurable delays) or faster than real time (tests).
- A new provider is "done" when it passes the same contract test as its fake (proposed: `tests/contract/test_<interface>.py`, parametrised over implementations).
- Pipecat service classes may be borrowed inside a provider or leg as libraries. Pipecat's pipeline, frames and processors never appear outside that file.

### 2. Only the FloorController calls `send()`, `clear()`, `mark()` (spec)

- Interpreters never talk to transports. They hand finished `Translation` objects (text plus an async audio iterator) to `FloorController.on_translation()`.
- The controller is the only code that calls `send()`, `clear()` or `mark()` on either leg. The mixer produces frames for it and does not call the leg itself.
- Enforced in review, and with a test that greps for `.send(`, `.clear(`, `.mark(` on leg objects outside `session/floor.py` and `legs/` (proposed).

### 3. Nothing in the hot path touches Postgres or Redis (spec)

- The hot path is frames → VAD → endpoint → STT → MT → TTS → controller → mixer → transport.
- Turn rows go onto an in-memory `asyncio.Queue`. A background task in `store/writer.py` batch-inserts them.
- If the queue is full or the DB is down, the writer drops or buffers and logs. A call never blocks on storage.
- Redis is read and written only at call setup and teardown (registry), never per frame or per turn.

### 4. One asyncio task per call (spec)

- One `CallSession` per call, owned by one task. The `FloorController` is that task's state machine.
- Child tasks (frame readers per leg, interpreter per direction, provider sockets) are created through the session, using an `asyncio.TaskGroup` (Python 3.12). If one fails, the session decides whether to restart it or tear down. No orphan tasks.
- No threads in the app, except where a library forces one (e.g. onnxruntime). CPU work in the hot path (resample, mix, VAD) must stay well under 20 ms per frame. If it doesn't, profile before reaching for threads.
- Never call blocking I/O (`requests`, sync DB drivers, `time.sleep`) in the app. Use `httpx.AsyncClient`/websockets and `await asyncio.sleep`.
- Cancellation-safe: every provider socket closes in `finally`. `clear()` must work at any moment.

### 5. Audio format (spec)

- Internally the format is always PCM16 mono, 16 kHz, 20 ms frames (640 bytes). Resampling (soxr) happens only at the leg boundary.
- The mixer must never exceed int16. Clip-check in the mixer and set the turn's clipped flag.
- Silero VAD v5 gets 512-sample windows (≈32 ms) with a 20 ms hop.

### 6. Config, not code, for per-pair choices (spec)

- STT provider, TTS provider (Bulbul for kn, ta, mr, te, ml, bn, gu, pa, od; ElevenLabs Flash v2.5 for hi and en), voices, `eou_ms`, `backchannel_max_ms`, `barge_in_min_ms` and `duck_db` live in `config/pairs.yaml`. Never hard-code them.
- v1 voices are fixed per target language and detected gender (`voice_m` / `voice_f`). No voice cloning code in v1.

## Naming

| Thing | Convention | Example |
|---|---|---|
| modules / functions | `snake_case` | `sarvam_stt.py`, `on_barge_in` |
| classes | `PascalCase`, vendor prefix for providers | `SarvamStream`, `BulbulStream`, `ExotelLeg` |
| sides | `"a"` / `"b"`; directions `"ab"` / `"ba"` | `PLAYING_AB` |
| language codes | BCP-47 with region in config and provider calls | `hi-IN`, `kn-IN` |
| pair keys | `<src>-<tgt>` short codes, as in `pairs.yaml` | `hi-kn`, `mr-hi` |
| call ids | `c_` prefix | `c_91` |
| timestamps | `t_<stage>` in ms, monotonic per call | `t_stt_final` |

## Logging

- Structured JSON to stdout via the standard `logging` module plus a JSON formatter (proposed: `structlog` or `python-json-logger`).
- Every log line in a call's context carries `call_id`. Add `side` and `turn_seq` when known. Bind them once with a context var, not by hand in every call.
- Event names are dotted `area.event`, lowercase:

| Event | Level | Fields |
|---|---|---|
| `leg.start` | info | `side`, `provider`, `sample_rate`, `stream_sid` |
| `leg.gap` | warning | `side`, `gap_ms` |
| `leg.close` | info | `side`, `close_reason` |
| `leg.resample` | warning | `side`, `from_hz`, `to_hz` (logged once when Exotel/Plivo sends 8k) |
| `vad.eou` | debug | `side`, `t_eou`, `utt_ms`, `backchannel` |
| `stt.final` | info | `side`, `provider`, `latency_ms` |
| `mt.done` | info | `pair`, `latency_ms`, `cache_hit` |
| `tts.ttfb` | info | `provider`, `voice`, `latency_ms` |
| `floor.state` | info | `from`, `to`, `queue_depth` |
| `floor.barge_in` | info | `side`, `clipped_ms` |
| `floor.dtmf` | info | `side`, `digit` |
| `provider.error` | error | `provider`, `stage`, `error`, `reconnecting` |

- **Never log** API keys, phone numbers in full (mask them: `+91******1234`), or transcript text unless the call has `record: true`. At the default level, log lengths and latencies, not content.

## Metrics

- The `turns` table is the metrics store for latency. Field names match the schema exactly: `t_speech_start`, `t_speech_end`, `t_eou`, `t_stt_final`, `t_mt`, `t_tts_ttfb`, `t_play_start`, `t_play_end`, `clipped_ms`.
- In-process metric names, if we add an exporter later: `dubhash_<area>_<name>_<unit>`, e.g. `dubhash_stt_final_latency_ms`, `dubhash_floor_queue_depth`, `dubhash_leg_gaps_total`. Labels: `pair`, `side`, `provider`. Never `call_id` (unbounded cardinality).
- Reports always state p50 **and** p95, plus n, and whether the source is PSTN, bench or loopback.

## Code style

- Python 3.12 with full type hints, checked by mypy or pyright in CI (proposed, non-blocking until CP1).
- `ruff` for lint and format (proposed line length 100). `pytest` + `pytest-asyncio` for tests.
- Dataclasses (`slots=True`) for value types such as `Translation` and `Turn`. Pydantic only at the API boundary.
- No new dependency without a one-line reason in the PR description. The hot path stays numpy/onnx/soxr.

## Git workflow

- `main` is the default branch and is always runnable: the all-fakes pipeline passes.
- Branch from `main` with short-lived branches named `<owner>/<topic>`, e.g. `rohan/floor-barge-in`, `p2/sarvam-stt-warm`, `p3/exotel-leg`, `p4/console-sse`, `p5/chaos-socket-kill`.
- Open a small PR early. One reviewer is enough. Anything touching `session/floor.py`, `audio/mixer.py` or `legs/base.py` needs Rohan's review.
- Squash-merge into `main`. Rebase on `main` before merging. Don't merge your own PR without a review unless it is docs-only.
- After CP2 (feature freeze), only fix-list items merge.
- Never commit `.env`, keys, recordings of real calls, or customer audio.

## Commit style

```text
<area>: <imperative summary, lowercase, ≤ 72 chars>

<optional body: why, not what. reference open question or plan day if relevant.>
```

- `area` is a module or doc folder: `floor`, `mixer`, `legs/exotel`, `providers/sarvam`, `api`, `console`, `bench`, `docs`, `infra`.
- Examples: `floor: clear listener leg on barge-in over barge_in_min_ms`, `legs/exotel: resample when media_format reports 8k`, `docs: add latency budget table`.
- AI-assisted commits keep whatever attribution trailer the tool adds.

## Related

- [Repo structure](repo-structure.md)
- [Testing strategy](testing-strategy.md)
- [Floor controller](../tech-spec/floor-controller.md)
- [Interpreter pipeline](../tech-spec/interpreter-pipeline.md)
- [Configuration](../tech-spec/configuration.md)
- [Security and privacy](../tech-spec/security-privacy.md)
- [Roles and ownership](../plan/roles-and-ownership.md)
