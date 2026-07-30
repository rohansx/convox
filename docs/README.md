# Convox — Documentation

> Last updated: July 2026
> Status: Pre-implementation design

Convox repositions the project from **voice-agent orchestration** to **voice-agent testing and observability**: the open-source, self-hosted alternative to Cekura, Coval, and Hamming.

## Start here

| Document | What it answers |
|---|---|
| **[Status & Plan](status.md)** | What is built today, what it is proven to do, known gaps, and the phased plan forward |
| **[Positioning](positioning.md)** | Why this pivot, who it's for, what we say that competitors can't, and how we go to market |
| **[Product Overview](product-overview.md)** | What the product is, core concepts, user journeys, deployment shapes, and explicit non-goals |
| **[Features](features.md)** | The complete feature inventory across 25 capability areas, each phase-tagged P0–P3 |
| **[Architecture](architecture.md)** | System design, components, data flow, deployment topologies, scaling, risks |
| **[Tech Spec](tech-spec.md)** | Stack, repo layout, domain models, database schema, adapter interface, engines, REST API, CLI, env vars, performance targets |
| **[Scenario Spec](scenario-spec.md)** | The scenario/persona YAML contract — the product's real interface |
| **[Metrics Catalog](metrics.md)** | Normative definitions and formulas for every metric Convox reports |
| **[Roadmap](roadmap.md)** | Phased build plan, sequencing principles, non-goals, risks |
| **[Market Research](market-research.md)** | The Cekura / Retell / competitor / open-source research behind the pivot |

## The thesis in five bullets

1. **The orchestration layer is commoditized.** Twelve-plus funded platforms, converging features, deflating prices. That was v1.
2. **The testing layer is where value moved.** Coval raised $28M; Cekura, Hamming, Bluejay, Roark all funded; Retell, Vapi, and ElevenLabs all shipped QA products within months of each other.
3. **Nobody open-sourced it.** Every credible player is closed SaaS. The one OSS attempt (fixa, YC F24) died at 117 stars. Meanwhile dograh proved self-hosted voice infra draws ~5,000 stars in ten months.
4. **We have an architectural advantage.** Because Convox generates the caller's speech, ground truth is known exactly — enabling precise WER, slot accuracy, and per-layer failure attribution that transcript-scoring tools cannot compute.
5. **India is the beachhead, not the ceiling.** Code-switching personas and Indic-correct scoring are a real gap every vendor claims to cover and none does.

## Reading paths

**Catching up on where things stand** → [Status & Plan](status.md)

**Evaluating the direction** → [Market Research](market-research.md) → [Positioning](positioning.md) → [Roadmap](roadmap.md)

**Building it** → [Product Overview](product-overview.md) → [Architecture](architecture.md) → [Tech Spec](tech-spec.md)

**Designing the user experience** → [Product Overview](product-overview.md) → [Scenario Spec](scenario-spec.md) → [Metrics](metrics.md)

**Writing the pitch** → [Positioning](positioning.md) → [Features](features.md)

## Status

These documents define the target state. For what actually exists today — and what does not — see [status.md](status.md), which is kept honest about gaps and limitations.

An earlier iteration of this repo explored voice-agent orchestration. That work is preserved on the `archive/orchestration-platform` branch; [market-research.md](market-research.md) records why the direction changed.
