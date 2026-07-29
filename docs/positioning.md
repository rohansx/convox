# Convox — Positioning

> Last updated: July 2026
> Status: Pre-implementation design
> Related: [Product Overview](product-overview.md) · [Features](features.md) · [Research](market-research.md)

---

## 1. The one-line positioning

> **Convox is the open-source testing and observability platform for voice AI agents.**
> Simulate thousands of real callers against your agent, score every call on transcript *and* audio, and run all of it inside your own infrastructure.

Short form for GitHub / social: **"pytest for voice agents."**

## 2. The positioning statement (long form)

**For** engineering teams shipping production voice AI agents,
**who** currently QA by calling their own agent and listening for problems,
**Convox is** an open-source, self-hosted testing and observability platform
**that** simulates real callers over real audio channels, evaluates every call with deterministic assertions plus calibrated LLM judges, and monitors production calls with the same metrics.
**Unlike** Cekura, Coval, Hamming, Bluejay, and Roark — closed SaaS products that require shipping your call recordings to a vendor and metering you per test minute —
**Convox** is Apache-2.0 licensed, runs in your own VPC, works across every agent platform, and lets you bring your own model keys.

## 3. Why this pivot

### What we're moving away from

Convox v1 was a voice-agent **orchestration** platform — a self-hosted Bolna/Retell for India. That layer is commoditized:

- 12+ credible funded platforms (Vapi $500M valuation, Bland $100M+ raised, Retell ~$50M ARR, ElevenLabs, Synthflow, Bolna, LiveKit at $1B) with converging feature sets and prices.
- Every one of them ships the same checklist: flow builder, multi-LLM, multi-TTS, telephony/SIP, KB/RAG, batch calling, post-call analytics.
- Model-layer deflation (OpenAI Realtime pricing + caching, Pipecat/LiveKit orchestration at $0.0015–$0.01/min) is compressing the margin out of "pipes."
- a16z's read on the market: value is moving *from infrastructure to applications*.

Winning there requires capital we don't have and buys us a price war we don't want.

### What we're moving toward

The **trust layer** — testing, evaluation, and observability — is the fastest-growing adjacent market, and it's where the builders themselves are rushing:

- Retell shipped **Assure** (automated QA on 100% of production calls) in Dec 2025.
- Vapi shipped test suites and evals; ElevenLabs shipped agent Tests + a simulation API; LiveKit shipped `agents.evals`; Pipecat shipped audio-in-the-loop Evals in v1.4.
- A dedicated vendor ecosystem formed and got funded: Coval **$31M**, Hamming ~$4.5M, Bluejay $4M, Cekura $2.4M, Roark, Evalion.

Four YC companies and a $28M Series A is not a market to be talked out of — it's a market that has been validated for us.

### Why *open source* is the wedge, not just a license choice

**Every credible player in this category is closed SaaS.** That is the entire opportunity.

- Cekura sells **self-hosting as an enterprise upsell** — which proves enterprises are asking for it and paying extra to get it.
- The only open-source attempt, **fixa** (YC F24), reached 117 GitHub stars and went dormant in Q1 2026 — the lane was vacated, not disproven.
- Meanwhile **dograh** — a self-hosted Vapi/Retell alternative — went from zero to ~5,000 stars in ten months. The appetite for self-hosted voice-AI infrastructure is demonstrated. Nobody has built the testing layer that sits next to it.
- The general OSS eval stacks people reach for — promptfoo (23.7K★), deepeval (17.2K★), Langfuse (32K★), Phoenix (10.8K★) — **all stop at transcripts and traces**. None does telephony simulation, synthetic callers, audio-layer metrics, or load testing.

Open source is our distribution strategy, our credibility in a market where every vendor scores differently and asks you to trust the number, and our answer to the one objection closed SaaS can never overcome: *"we cannot send recordings of patient/customer calls to a third party."*

## 4. The four things we say that nobody else can

### 4.1 It runs in your infrastructure

Apache 2.0, `docker compose up`, no phone-home, no per-minute meter, bring your own model keys. Call recordings, transcripts, and PII never leave your network. For healthcare (HIPAA), Indian financial services and healthtech (DPDP Act), and EU deployments (GDPR), this is not a feature — it is the difference between being adoptable and not.

### 4.2 We know exactly what the caller said

This is the technical insight the closed vendors under-exploit. **Because Convox generates the caller's speech from text, the ground-truth transcript of the caller side is known perfectly, before the audio is ever synthesized.**

That means we can measure things a transcript-only tool literally cannot compute:

- **Exact effective WER** of the agent's ASR — not an estimate, a diff against known truth.
- **Slot-level capture accuracy** — we spoke the phone number `98765 43210`; did the agent read it back correctly? That is a deterministic string comparison, not a judgment call.
- **Failure attribution by pipeline layer** — if the caller said X, the agent's transcript shows Y, and the agent's reply addresses Y, the fault is STT, not the LLM. Convox can point at the guilty component.
- **Codec-attributable degradation** — run the same ground truth through wideband and through 8kHz G.711 and diff the agent's behavior.

Hamming's marketing claim is that **42% of production voice-agent failures are audio-layer** and invisible to transcript-only evals. We think the right response is not to add "audio metrics" as a feature bullet, but to make ground-truth audio the foundation of the whole evaluation model.

### 4.3 Deterministic first, judges second — and the judges are calibrated

The loudest complaint about every tool in this category is **LLM-judge flakiness**: the same call scored differently on two runs, so nobody trusts the dashboard. Cekura shipped "Conditional Actions" (deterministic rules) specifically to fight this; decibench's entire pitch is "deterministic + semantic."

Convox's answer is structural:

- Assertions are deterministic by default (tool called, latency budget, digits captured, disclosure present, no dead air over 2s). No model in the loop, no variance.
- LLM judges are used only for genuinely semantic claims, and they run with temperature 0, **self-consistency voting**, and a requirement to **cite the turn IDs** that justify the verdict.
- **Judge calibration is a first-class feature**: label a set of calls once, and Convox reports your judge's agreement (precision/recall/F1, Cohen's κ) against those labels — and re-reports it whenever you change the judge prompt or model. You get to know how much to trust your evals.
- **Reliability scoring over pass/fail**: a voice test that passes once is noise. Convox runs each scenario *k* times and reports `pass^k` — the probability the agent handles the scenario every time.

### 4.4 We are neutral, and we are the only ones who can stay neutral

Retell will never test Vapi agents. Vapi will never test Retell agents. Their native testing is text/LLM-level, framework-locked, and structurally incapable of being the cross-platform layer. Convox targets Retell, Vapi, Bland, ElevenLabs, LiveKit, Pipecat, and raw SIP/WebSocket through one adapter interface — and because we're open source, a platform we haven't integrated yet can be added by the people who want it.

Neutrality also gives us a marketing engine the platforms can't run: a **reproducible public benchmark** comparing agent platforms on identical agents. Cekura runs one (benchmarks.cekura.ai) and it works; ours is the one anyone can re-run and audit.

## 5. Differentiation matrix

| | Convox | Cekura | Coval | Hamming | Roark | Bluejay | Platform-native (Retell/Vapi/11L) |
|---|---|---|---|---|---|---|---|
| Open source | **Apache 2.0** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Self-hostable | **Default** | Enterprise tier | ✗ | ✗ | ✗ | ✗ | ✗ |
| Bring your own keys | **Yes** | ✗ (credits) | ✗ | ✗ | ✗ | ✗ | ✗ |
| Cross-platform targets | **Yes** | Yes | Yes | Yes | Partial | Yes | ✗ (own agents only) |
| Audio-layer evaluation | **Yes + ground truth** | Yes | Partial | Yes | Partial | Partial | ✗ (text-level) |
| Known-truth WER / slot accuracy | **Yes** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Judge calibration reporting | **Yes** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| `pass^k` reliability scoring | **Yes** | Benchmarks only | ✗ | ✗ | ✗ | ✗ | ✗ |
| Load testing | **Yes** | 2,000+ concurrent | Yes | 50K claim | ✗ | Yes | ✗ |
| Production monitoring + replay | **Yes** | Yes | Yes | Yes | Yes | Yes | Retell Assure only |
| Indic / code-switch depth | **First-class** | 9 Indic languages | Generic | Generic | ✗ | Generic | Generic |
| Entry price | **$0** | $30/mo | $100/mo | Custom | — | — | Bundled |

## 6. Who we're for

### Primary ICP — the wedge

**Voice-AI product teams, 2–25 engineers, already in production on Retell / Vapi / LiveKit / Pipecat.**

They ship prompt changes weekly, they've been burned by a regression they found out about from a customer, and their current QA is a person with a phone and a checklist. They are technical, they live in GitHub, and they will try an OSS tool the same afternoon they hear about it. They convert on the CLI and the GitHub Action, not on a dashboard demo.

*Trigger event:* a bad deploy reached production. *Job to be done:* "tell me before my customers do."

### Secondary ICP — the money

**Regulated enterprises deploying voice agents where call data cannot leave the perimeter.**

Healthcare (HIPAA), Indian BFSI and healthtech (DPDP Act, RBI localization), EU (GDPR), government. They will pay for support, SSO/RBAC, compliance reporting, and air-gapped deployment. For them, "self-hosted" is not a preference, it's procurement's hard requirement — and it's the requirement that disqualifies every competitor by default.

### Tertiary — leverage

- **Agencies and SIs** building voice agents for clients, who need per-client reporting and can't expense a per-minute SaaS on every engagement.
- **Platform vendors** who want to embed or white-label an eval harness rather than build one.
- **Researchers and benchmark authors** who need a reproducible harness — the group that makes the public leaderboard credible.

## 7. Messaging by audience

| Audience | Headline | Proof point |
|---|---|---|
| Voice AI engineer | "pytest for voice agents." | `convox run scenarios/ --target retell:agent_abc` in 60 seconds |
| Head of Engineering | "Stop shipping voice regressions." | GitHub Action fails the PR on a latency or task-success regression |
| CTO / Platform lead | "Your call data never leaves your VPC." | Apache 2.0, `docker compose up`, BYO keys |
| Compliance / Security | "Testing that satisfies DPDP, HIPAA, and GDPR." | On-prem deploy, audit log, PII redaction, retention policy |
| India-market team | "The only tool that actually tests Hinglish." | Code-switching personas + Indic WER with proper normalization |
| Open-source community | "The eval layer voice AI never got." | Reproducible public benchmark across all platforms |

## 8. Objection handling

**"Retell/Vapi already has testing built in."**
Their simulators verify that flows execute, at the text level, against their own agents. They don't test barge-in latency, TTS truncation, ASR degradation under noise, code-switching, 1,000-concurrent behavior, or your agent after you migrate platforms. Every one of those platforms co-markets with third-party QA vendors precisely because native testing isn't the same product.

**"fixa was open source and it died."**
fixa was a thin transcript-judging wrapper over phone calls, shipped by a two-person team that moved on. It didn't fail because open source can't work here; it failed at 117 stars because it stopped being maintained. Also, timing: in January 2025 far fewer teams had voice agents in production than do now.

**"Coval raised $28M — you can't compete."**
We aren't competing for the same deal. Coval sells top-down to Zoom and GEICO. We win the developer who tries it Saturday, the team that can't send recordings offsite, and the buyer who wants the tool auditable. Capital doesn't help them in any of those three.

**"LLM judges are unreliable, so eval tools are theater."**
Agreed, which is why deterministic assertions are the default, judges vote, and we publish our judges' measured agreement with your own labels. We'd rather tell you the eval is 0.84 F1 than pretend it's an oracle.

**"Running test calls is expensive."**
WebSocket/WebRTC testing costs no telephony money at all, and you use your own model keys with no markup. Telephony is reserved for full-fidelity runs. A closed vendor charging ~5 credits/minute cannot make that offer.

## 9. Naming and brand

We keep the name **Convox** — "conversation" + "vox" reads correctly for a conversation-testing product, the domain and repo already exist, and the Indic/voice DNA carries over cleanly. What changes is the sub-line:

- **Before:** "Open-source voice AI orchestration for India."
- **After:** "Open-source testing and observability for voice AI agents."

The India angle moves from *identity* to *unfair advantage*: we're not the India voice platform, we're the voice testing platform that happens to be the only one that genuinely handles Hinglish, Indic WER normalization, and Indian telephony conditions.

## 10. Go-to-market

**Phase 1 — Earn the developers (months 0–4).**
Ship the CLI, the scenario format, and the GitHub Action first; the dashboard second. Launch on Show HN and Product Hunt with a live demo that tests a real public voice agent. Publish integration guides per platform ("Testing your Retell agent", "…your Vapi assistant", "…your Pipecat bot") — these are the exact queries the ICP searches. Be present in the Pipecat, LiveKit, and Vapi communities as a contributor, not an advertiser.

**Phase 2 — The benchmark (months 3–8).**
Publish an open, reproducible leaderboard: one identical agent spec, run across Retell / Vapi / Pipecat / LiveKit / ElevenLabs / Bland, scored on reliability (`pass^k`), turn latency, barge-in handling, and multilingual accuracy — with the harness, scenarios, and raw recordings public so anyone can re-run it. This is how a small OSS project gets covered, gets vendors engaged, and becomes the thing people cite.

**Phase 3 — Land the enterprises (months 6–15).**
The compliance story converts the second ICP: DPDP/HIPAA/GDPR-aligned deployment, audit logs, RBAC/SSO, air-gapped installs, support SLAs. Expect these to arrive inbound *because* the tool is self-hostable.

**Phase 4 — Monetize (months 9+).**
Open core. The self-hosted core stays free and genuinely complete — no crippled OSS edition. Convox Cloud offers managed workers, hosted telephony numbers, and team features for people who don't want to run infra. Enterprise adds SSO/RBAC, compliance reporting, air-gapped support, and SLAs. Cekura's $30/mo self-serve tier proves the low end converts; Coval's ~$4.5K/mo enterprise tier shows the ceiling.

## 11. What would tell us this is working

| Horizon | Signal |
|---|---|
| Month 3 | 500+ GitHub stars; 10 teams running Convox in CI; 3 external adapter PRs |
| Month 6 | 2,000+ stars (past every dedicated OSS competitor combined); benchmark cited by a platform vendor; 5 inbound enterprise self-host conversations |
| Month 12 | 5,000+ stars (dograh's trajectory); 50+ production monitoring deployments; first paid enterprise contracts; a platform vendor links to us in their docs |

## 12. What would tell us we're wrong

Kill/rethink signals, defined in advance so we notice them:

- Platform-native testing (Retell Assure, Vapi evals) becomes genuinely audio-level and cross-platform — the neutrality argument weakens.
- A funded competitor open-sources a credible core and out-maintains us.
- Developers adopt the CLI but nobody ever runs the monitoring side — meaning the market is a feature, not a platform, and we should specialize into CI testing only.
- Self-hosting demand turns out to be cheap talk: enterprises ask for it, then buy the SaaS anyway. If six months of enterprise conversations produce no deployments, the compliance wedge is imaginary.
