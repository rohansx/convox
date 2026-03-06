# Convox — Python vs Go Analysis

> Last updated: February 2026
> Decision: **Python. Unanimously.**

---

## TL;DR

Go is the wrong language for Convox. Python is the only viable choice. This is not a close call.

---

## The Dealbreaker: Pipecat Is Python

Pipecat is not a service you call over HTTP. It is a Python library you import in-process:

```python
pipeline = Pipeline([transport.input(), stt, llm, tts, transport.output()])
```

If you use Go, every voice call requires spawning a **separate Python subprocess**, communicating via IPC (gRPC, Redis, Unix socket), and serializing every audio frame, transcript event, cost event, and compliance hook across the process boundary. You gain nothing — the voice pipeline (the CPU/IO-intensive part) is still Python.

**Result:** Go adds a subprocess architecture for your core product feature. More complexity, zero benefit.

---

## SDK Ecosystem: Python Wins 9/9

| Provider | Python SDK | Go SDK |
|----------|-----------|--------|
| Pipecat | Native | None |
| Sarvam AI | Official (`sarvamai` on PyPI) | None |
| Gnani.ai Vachana | REST API (Python examples) | None |
| NVIDIA Riva | Official + `nvidia-pipecat` | gRPC stubs only (no SDK) |
| Deepgram | Official, full streaming | Official, streaming |
| ElevenLabs | Official | Community only (unofficial) |
| OpenAI | Official, full-featured | Official (beta, no Realtime API) |
| Anthropic | Official | Official |
| Smallest.ai | Python SDK | None |

The India-specific providers (Sarvam, Gnani, Smallest.ai) — the ones critical to Convox's beachhead — have **zero Go SDKs**. You would be writing and maintaining raw HTTP clients for every Indian provider without vendor support.

---

## Performance: Doesn't Matter Here

| Operation | Latency |
|-----------|---------|
| STT (Sarvam/Deepgram) | 200–500ms |
| LLM (GPT-4o / Claude) | 500–1500ms |
| TTS (Gnani/ElevenLabs) | 200–400ms |
| **Total voice cascade** | **900–2400ms** |
| Python API overhead | ~5ms |
| Go API overhead | ~0.5ms |

The difference between Go and Python server overhead is **4.5ms**. Your voice pipeline takes **1500ms**. Server overhead is **0.3% of total latency**. Invisible to the user.

Concurrent calls target: 50–100 per node. Python asyncio handles this trivially. You need 10,000+ concurrent calls per single node before Go's concurrency model provides meaningful advantage.

---

## Hiring: Python 10–15x Larger Pool in India

| Metric | Python | Go |
|--------|--------|-----|
| Open jobs in India (2025) | ~56,000 | ~2,000 |
| Share of Indian tech postings | 24% | ~2–3% |
| Voice AI / ML developer overlap | Near-total | Minimal |

Every ML engineer, voice AI developer, and data scientist you'd want to hire in India already knows Python. Finding Go + voice AI + Indic language developers is near-impossible.

---

## Hybrid (Go API + Python Workers): Worse Than Either

What you gain: ~4ms faster dashboard responses.

What you lose:
- Two runtimes in every Docker image
- Duplicated models (Go structs + Python Pydantic) — every schema change touches two codebases
- IPC overhead for every voice call event (cost tracking, compliance hooks, transcripts)
- Double the debugging surface (bug in Go? Python? IPC boundary?)
- Double the CI/CD (Go modules + pip/poetry)
- Hiring: now need devs comfortable in both languages

For a solo founder building an MVP, this doubles engineering effort for zero user-facing benefit.

---

## Illuminate Comparison: Wrong Domain

Illuminate uses Go because it's a **CRUD web app** (HTTP request/response, OAuth, database queries). Go excels at that.

Convox is a **real-time voice AI orchestration platform** (audio streaming, 3+ AI providers per request, pipeline management). Different domain, different constraints:

| Illuminate | Convox |
|-----------|--------|
| HTTP request/response | Real-time audio streaming |
| No AI provider dependency | 3+ AI providers per call |
| No language-specific library | Hard dependency on Pipecat (Python) |
| Simple CRUD | Complex pipeline orchestration |
| No provider SDK ecosystem | 9+ SDKs, all Python-first |

---

## What We Take From Illuminate

Not the language. The **patterns**:

- Monolith architecture (single container: frontend + backend)
- Makefile-driven development (`make dev`, `make db-up`, `make migrate-up`)
- Docker Compose with Postgres + Redis
- dbmate for SQL migrations
- `.env.example` for configuration
- Multi-stage Dockerfile (frontend build → backend build → Alpine runtime)
- SvelteKit frontend with static adapter (compiled to static files, served by backend)
- Repository pattern for data access
- Service layer for business logic
- Middleware chain for auth/CORS/rate-limit

All of these patterns work identically in Python. We adopt the structure, swap Go for FastAPI, and swap SvelteKit for Vite + React (better component ecosystem for dashboards — shadcn/ui, React Flow for visual builder).

---

## When to Revisit

Consider Go only if:
1. 5,000+ concurrent calls per single node (unlikely before $10M ARR)
2. Building a standalone WebRTC media router separate from AI orchestration
3. Dashboard/analytics becomes a separate product with its own team

None of these apply to MVP or the first 12–18 months.

---

## Verdict

```
Factor                  | Python        | Go            | Winner
Pipecat integration     | Native        | Subprocess    | Python (dealbreaker)
Provider SDKs (9)       | 9/9 official  | 3/9 official  | Python
Performance             | Adequate      | Faster (irrelevant) | Tie
Audio streaming         | Proven (Pipecat Cloud) | Superior raw | Python (proven in domain)
India hiring pool       | 56K jobs      | 2K jobs       | Python
Illuminate reuse        | Patterns yes  | Language yes  | Python (patterns > language)
Hybrid complexity       | Single runtime| Doubled effort| Python
Time to MVP             | 8-12 weeks    | 16-24 weeks   | Python
```

**Ship Python. Adopt Illuminate's patterns. Don't look back.**
