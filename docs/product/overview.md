# Product overview

This is the one-page answer to "what are we building and why" for the five people building convox. It covers what the product is and is not, why it is possible in 2026, the three ways it ships, and what has to be on stage at the Bolna Symphony Buildathon. The technical detail lives in the [architecture](../architecture/system-overview.md) and [tech spec](../tech-spec/interpreter-pipeline.md) sections. The buildathon schedule lives in [the 10-day plan](../plan/buildathon-10-day-plan.md).

> **Name.** *Convox*: **con**versation + **vox** (voice), and a nod to **con**vert. Earlier working name: *dubhash*, from *dubhashi*, the old Indian word for a court interpreter. Prepared for the Bolna Symphony Buildathon (submission **31 Oct 2026**, finale **20 Nov 2026**, Bengaluru) and as the v1 plan for a standalone company. Team: Rohan Sharma + 4.

## One-line pitch

> Every Indian phone call where the two people do not share a language fails today. Convox is the interpreter on the line.

## What Convox is

Convox is a server that sits in the middle of a phone call and interprets both directions live.

- Leg A speaks Hindi. Leg B hears Kannada about 1.4 to 1.8 s after A stops speaking, in a **gender-matched natural voice**.
- Leg B replies in Kannada. Leg A hears Hindi.
- The original voice plays softly underneath (ducked by 14 dB), so each person knows the other one spoke.
- It attaches to any call that already runs through a CPaaS (Exotel, Plivo). Every gig platform's masked call and every telemedicine consult already runs this way.
- It also runs as a phone web app for in-person use (**ward mode**: a nurse and a patient on either side of a bed) and as a **dial-in number** anyone can call.

```mermaid
flowchart LR
    A["Leg A<br/>speaks Hindi"] -- "hi audio" --> D["Convox<br/>hi ⇄ kn<br/>~1.4–1.8 s"]
    D -- "kn TTS + ducked hi" --> B["Leg B<br/>hears Kannada"]
    B -- "kn audio" --> D
    D -- "hi TTS + ducked kn" --> A
```

### Voice in v1 (read this before writing any copy)

v1 uses a **fixed natural voice per target language and detected speaker gender**. Gender comes from a pitch heuristic on the first 2 s. A male Hindi speaker is heard in Kannada as a male voice. It does **not** sound like the speaker. Voice cloning from the first 30 s of a speaker is a **v2** feature and needs explicit consent at call start, because cloning a customer's voice without asking is a DPDP problem and a trust problem. Most of the "this is a real person" signal already comes from the ducked original. Do not write "in a voice that sounds like the speaker" anywhere, including the pitch, the deck or the submission form. Write "a gender-matched natural voice". Background: [audio](../tech-spec/audio.md), [open questions](../plan/open-questions.md) (gender detection).

### Engines per stage (v1)

| Stage | Engine | Notes |
|---|---|---|
| Telephony | Exotel voicebot applet (primary), Plivo audio streaming (fallback) | Two legs per call, 16 kHz requested. See [telephony integration](../architecture/telephony-integration.md). |
| VAD / endpoint | Silero VAD v5 + Sarvam end-of-utterance signal | EOU silence threshold set per pair, starting at 550 ms. |
| STT | Sarvam streaming (Saarika/Saaras) for Indic; Deepgram Nova as the English/Hinglish fallback | Selectable per leg. |
| Translation | Sarvam Mayura (sentence-level) | LLM repair on low-confidence sentences is v2. |
| TTS | Sarvam Bulbul for Indic targets (kn, ta, mr, te, ml, bn, gu, pa, od); ElevenLabs Flash v2.5 for Hindi and English targets (and cloning in v2) | Chosen per target language in `config/pairs.yaml`. See [configuration](../tech-spec/configuration.md). |

## What it is not

| It is not | Why that matters |
|---|---|
| A translation app you talk into (Google Translate, Jio Translate) | Convox lives inside a live call between two humans on two phones. |
| A voice agent | There is no AI persona. Both parties are human. Convox only carries meaning across. |
| A model | Sarvam, Deepgram and ElevenLabs are the engines, and we train no models. Convox is the bridge, the turn-taking ([floor controller](../tech-spec/floor-controller.md)) and the integrations. |
| A BPO tool | BPOs hire native speakers for each language queue. Convox is for calls where nobody can hire around the language gap. |

## Why now

- **Indic STT and translation became usable in 2026.** Sarvam Saaras and Mayura cover 10+ languages, and Deepgram handles Hinglish. Two years ago the pipeline would have been wrong too often to trust.
- **CPaaS vendors stream both directions on each call leg.** Exotel and Plivo built this for voice bots, and the same socket can carry an interpreter.
- **Language friction on gig calls is visible.** Fights over language on gig calls in Bengaluru and Chennai keep making the news, and they hurt platform ratings.
- **Streaming TTS starts fast enough.** Flash-class streaming TTS can start audio in about 300 ms (ElevenLabs Flash; Bulbul's first byte is described as similar, still to be verified). That speed is what makes consecutive interpretation feel like a pause rather than a failure.

The latency target is speaker stops → first translated word heard: EOU wait (starting at 550 ms) + about 1.2 s (STT final + MT + TTS TTFB + playout), so **about 1.4 to 1.8 s**. Targets are **p50 < 1.5 s, p95 < 2.0 s, measured on real PSTN, not loopback.** See [latency engineering](../engineering/latency-engineering.md).

## Three ways it ships

| | Inside a platform's masked calls | Ward mode (one phone, no telephony) | Dial-in number |
|---|---|---|---|
| **Buyer** | Gig platforms, telemedicine | Hospitals | Consumers (migrant workers); platforms may bundle it for their workforce |
| **How it works** | The platform backend already calls Exotel to bridge partner and customer. It calls Convox instead, and Convox calls Exotel with two voicebot legs. Invoked only when the two profiles' languages differ. | A nurse holds a phone between herself and a patient. Push-to-talk per side, same pipeline with no telephony transport, so latency is lower. | Dial one Convox number, enter the number to reach, say one sentence so it detects your language. Convox dials the other side and bridges. No app. |
| **Pricing** | Per interpreted minute | Per ward device per month (+ per minute for phone consults) | Free for the first 10 minutes a month, then pay per minute via UPI |
| **What gets it adopted** | The DTMF 0 escape and the per-turn audit log are what get it through trust review | No integration or procurement beyond a phone. The nurse champions it, and compliance wants the chart attachment. | No app needed, so anyone with a phone can use it |

The platform integration changes one API call. Today the backend calls Exotel's `Calls/connect`. With Convox it calls `POST /v1/calls`, and Convox places two Exotel calls, each with a voicebot applet. The full request and response contract is in [api](../tech-spec/api.md). Details: [business model](business-model.md), [problem and users](problem-and-users.md).

## Buildathon deliverable

**What's on stage:** an Exotel number, a judge's phone and ours. The judge speaks Kannada, we speak Hindi, and the hall hears both phones on speakers as the two sides understand each other, with the first translated word in under 1.5 s. Then ward mode on a single phone. Then the Exotel applet config and the one-line platform API on screen, to show Convox is one integration away from any platform's masked calls. The minute-by-minute run is in the [demo script](../plan/demo-script.md).

**Definition of done.** A stranger gives us their number and their language. We call them and a teammate. They have a two-minute conversation with someone who does not speak their language, and at the end both can say what was agreed. The overview sets this "in the hall", which only exists at the 20 Nov finale; the 31 Oct version (run once with someone outside the team, plus exit criteria and submission) is in the [10-day plan](../plan/buildathon-10-day-plan.md#definition-of-done-for-31-oct).

**Checkpoints** (dates in the [10-day plan](../plan/buildathon-10-day-plan.md)):

- **CP1:** two real phones, both directions, no drops.
- **CP2:** feature freeze.

**Never cut:** two real phones through Exotel (Plivo if Exotel access slipped, per the [10-day plan](../plan/buildathon-10-day-plan.md#never-cut); Rohan to confirm), both directions, the floor controller, the per-turn log.
**Cut list, in order:** (1) dial-in IVR (demo from the console instead), (2) ward mode (show it in the deck), (3) third language pair, (4) ducking (plain earcon only).

Rehearsed pairs: **hi ⇄ kn, hi ⇄ ta, mr ⇄ hi**.

## Related

- [Problem and users](problem-and-users.md)
- [Business model](business-model.md)
- [Glossary](glossary.md)
- [System overview](../architecture/system-overview.md)
- [Telephony integration](../architecture/telephony-integration.md)
- [Interpreter pipeline](../tech-spec/interpreter-pipeline.md)
- [Floor controller](../tech-spec/floor-controller.md)
- [Phases](../plan/phases.md)
- [Buildathon 10-day plan](../plan/buildathon-10-day-plan.md)
- [Demo script](../plan/demo-script.md)
- [Risks](../plan/risks.md)
