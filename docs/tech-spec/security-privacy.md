# Security and privacy

dubhash puts a third party (us, and through us Sarvam, ElevenLabs and Deepgram) in the middle of a private phone call between two humans, so both parties' speech is processed by a system neither of them dialled directly. This document defines how v1 handles that: DPDP consent and disclosure, what we store and for how long, where data lives, how provider and platform traffic is authenticated, the audit log, the trust-review package platforms need before signing, and the safeguards against mistranslation in medical and money contexts. Items from the source PDFs are stated as decisions. Items marked **proposed** are ours. Nothing here is legal advice. A DPDP review by counsel is TBD, see [open questions](../plan/open-questions.md).

## Summary of v1 commitments

| Area | v1 commitment | Source |
|---|---|---|
| Disclosure | opening line at call start, in **both** languages, before interpreting begins | overview §4, risks |
| Voice | fixed natural voice per target language and detected gender. **No voice cloning in v1** | spec non-goals, overview §5 |
| Hosting | India region only: one VM in AWS ap-south-1 (Mumbai). Postgres, Redis and S3 in the same region | spec §1, overview risks |
| Text | `src_text`/`tgt_text` nulled after metrics, unless `record: true` or the platform opted in | spec §6 |
| Audio | never stored by default. S3 recordings opt-in only | spec §6 |
| Escape | DTMF 0 turns interpretation off for the rest of the call (plain bridge) | spec §5, overview §5 |
| Telephony auth | Exotel WS: Basic auth header or IP allowlist | spec §3 |
| Audit | per-turn log plus events (dtmf, barge_in, gap, provider_error, bypass) | spec §6 |
| Safety | do not claim pairs we have not measured. Entity read-back for numbers and doses. Both texts on screen in ward mode | overview risks |

## DPDP consent and disclosure

Both parties are being processed by a third party. The overview's mitigation list: the opening line discloses in both languages, platform T&C already covers masked calls, no cloning in v1, India-region hosting, and transcripts retained only if the platform opts in.

### Opening line

Played to each leg in its own language as soon as both legs have sent `start`, before any interpretation. It is about 5 s. It does two jobs: it discloses that an interpreter is on the line, and it sets the rhythm (short sentences).

| Leg | Text (from the overview) |
|---|---|
| Hindi side | "aap hindi mein boliye, saamne wala kannada mein sunega. chhote vaakya boliye." |
| Kannada side | "ನೀವು ಕನ್ನಡದಲ್ಲಿ ಮಾತನಾಡಿ, ಅವರು ಹಿಂದಿಯಲ್ಲಿ ಕೇಳುತ್ತಾರೆ." |

As built (`session/prompts.py`, [L4](../plan/open-questions.md#answer-log)): one sentence of about 3.5 s per language, because the two-sentence line ran 7–9 s and held up the first translation. Hindi: "aap hindi mein chhote vaakya boliye, woh kannada mein sunenge." Kannada: the sentence above. Marathi and English lines exist for the Marathi pairs; the Marathi, Tamil and Kannada texts still need a native speaker's review (PQ5). The line goes through the floor controller and is marked. Since L7 a translation for a leg still hearing its opening cuts it, and `/join` and loopback calls play a short line instead (the two language names and "speak", ~2 s, without the record sentence); PSTN calls keep the full disclosure (`PSTN_OPENING_LINE=full`). Counsel should confirm the disclosure may be cut short once the other party is already talking (G17).

Gaps to close (**proposed**, TBD with counsel, see [open questions](../plan/open-questions.md)):

- The source lines describe the mechanics but do not explicitly say the call is processed by an automated service, or how to turn it off. Proposed addition (both languages, about 2 s): "yeh call ek automatic dubhash dwara anuvaad ki ja rahi hai. band karne ke liye 0 dabaiye." (*This call is being translated automatically by dubhash. Press 0 to turn it off.*)
- If `record: true`, the opening line must also say the conversation text is being saved.
- Opening lines per language are stored as pre-rendered audio per pair (proposed), so they do not depend on TTS latency at call start.
- For platform calls the platform's T&C covers the masked call, but the platform remains responsible for its own users' consent to interpretation. Put this in the platform contract (trust-review package).

### Escape hatch

DTMF `escape_dtmf` (default `0`) switches the call to bypass: interpreters stop and the controller forwards each leg's frames to the other at 0 dB. It is logged as an `events` row (`kind=bypass`) and sets `calls.escape_used`. The target escape rate is < 5% of calls, and every escape is reviewed. Escape is the control that "makes platforms comfortable".

## Voice and cloning

- v1: a **gender-matched natural voice** per target language (pitch heuristic on the first 2 s). It does not sound like the speaker, and no doc, UI, deck or pitch may claim it does. The ducked original voice underneath carries most of the "this is a real person" signal.
- v2: voice cloning (ElevenLabs) from the first 30 s of a speaker, **only behind explicit consent at call start**, per party. Cloning a customer's voice without asking is a DPDP problem and a trust problem. Consent capture (DTMF confirm? spoken yes?), storage and deletion of voice embeddings are all TBD before v2, see [open questions](../plan/open-questions.md).

## Data residency

| Data | Where | Notes |
|---|---|---|
| App, Postgres, Redis | one VM in ap-south-1 (Mumbai), Docker Compose, Caddy for TLS | also good for latency: Exotel media servers and Sarvam are in India |
| Recordings (opt-in) | S3 ap-south-1 | |
| Sarvam STT/MT/TTS | India | |
| Deepgram (English/Hinglish fallback) | not stated in the source | audio leaves India when this path is used. TBD, see [open questions](../plan/open-questions.md) |
| ElevenLabs (hi, en targets) | **outside India** (150 to 250 ms RTT from Mumbai) | only translated **text** is sent for synthesis, not caller audio. Disclose this sub-processor in the trust-review package |

Proposed: the trust-review package lists every sub-processor, what data it receives (audio vs text), and its region.

## Retention

| Data | Default | With `record: true` or platform opt-in |
|---|---|---|
| Turn timestamps, flags, provider ids | kept (needed for latency metrics and audit) | kept |
| `src_text`, `tgt_text` | nulled after latency metrics are computed | kept. Retention window TBD (proposed: 30 days, then purge job) |
| Caller audio | never stored. Exists only in process memory during the call | S3 only with an explicit audio opt-in (see [data model](data-model.md#s3-recordings-opt-in)) |
| Phone numbers (`calls.a_number`, `b_number`) | **proposed:** store a salted hash for dedupe after the call ends. Keep the raw number only while the call is live | same |
| Vendor-side retention | per vendor terms. TBD: confirm Sarvam/ElevenLabs/Deepgram data-retention and no-training settings | |

Details and the exact nulling flow are in [data model → text retention](data-model.md#text-retention). Logs (stdout, Caddy) must not contain transcript text or full phone numbers (proposed: a log redaction filter, with numbers masked as `+91•••••678`).

## Authentication and network

| Path | Control |
|---|---|
| Exotel → `/ws/leg` | Basic auth header **or** IP allowlist (ask Exotel to allowlist the VM). Proposed: both, if Exotel supports Basic auth on the voicebot WS |
| `/ws/leg` routing | the `call` id must exist in this process's session map **and** `side` must be unattached. A second socket for the same `(call, side)` is rejected (proposed) |
| Plivo → `/ws/leg` | TBD, see [open questions](../plan/open-questions.md) |
| Provider webhooks `/webhooks/*` | provider signature validation if offered, otherwise IP allowlist (**proposed**) |
| Platform → `/v1/*` | per-platform bearer API key, stored hashed (**proposed**) |
| dubhash → platform webhook | HMAC-SHA256 signature header over the body (**proposed**) |
| `/console` | auth required (proposed: Caddy basic auth for the buildathon; per-user login later). The console shows live transcripts, so it is never public |
| Vendor credentials | env vars only ([configuration](configuration.md)). Never in git, never logged |
| TLS | Caddy terminates TLS for all HTTP and WSS |
| VM | security group open to 443 only (plus 22 from team IPs). Postgres and Redis are not exposed outside the Compose network (**proposed**) |

## Audit log

The audit log is the data that lets a platform's support team "see exactly what each side heard when a dispute comes in".

- `turns`: every utterance, per side, with all stage timestamps, `backchannel`, `clipped_ms`, the providers and the voice used. Text only if retained.
- `events`: `dtmf`, `barge_in`, `gap`, `provider_error`, `bypass`, each with a timestamp and payload.
- `legs`: answered and closed times, `close_reason`, actual `sample_rate`.
- `calls`: `escape_used`, `p50/p95_first_audio_ms`, `interpreted_s`.

The floor controller invariant gives the audit log its value: every Translation is either played to completion or marked clipped with the ms played, and none is silently dropped. A row exists for every utterance, so "the other side never heard X" is checkable.

**Proposed** additions for platform trust:

- An access log of who viewed which call in the console or via `GET /v1/calls/{id}/turns` (an `events` row with `kind=access`, or a separate table).
- Rows are append-only from the app's point of view. The only update is the retention nulling job.

## Trust-review package

What a gig platform or hospital asks for before letting dubhash into its call path. The overview puts the hardened version in Feb to Jun ("webhooks, trust-review package (audit log, DPDP consent flow, data residency), SLA"). For the buildathon we show its pieces: the console, the per-turn log, the DTMF escape, and the Exotel applet config.

| Item | v1 (buildathon) | Hardened (Feb to Jun) |
|---|---|---|
| Audit log | `turns` + `events`, console, `GET /v1/calls/{id}/turns` | access log, export API |
| DPDP consent flow | opening line in both languages, escape DTMF | counsel-reviewed script, consent records, data-principal request handling (TBD) |
| Data residency | ap-south-1 VM and S3. Sub-processor list | written DPA, sub-processor change notice |
| Retention | text nulled by default, audio never stored | configurable per platform, deletion API |
| Accuracy | per-pair numbers (targets: meaning kept > 90% on 300 turns per pair, entity accuracy > 95%); labelled "preliminary, n = …" until 300 turns are rated ([evaluation](../engineering/evaluation-and-metrics.md)) | ongoing per-pair dashboards |
| Escape and fallback | DTMF 0 bypass, `mode: "bridge"` | SLA, auto-bypass on provider failure (proposed) |
| Security | TLS, auth on all surfaces, secrets in env | pen test, SOC-type controls (TBD) |

## Mistranslation safeguards (medical and money contexts)

The risk: one bad discharge instruction is a disaster. The same applies to an amount, an address or an OTP. Mitigations from the overview, plus how they land in the build:

| Safeguard | Where | Status |
|---|---|---|
| Do not claim pairs we have not measured. Publish per-pair accuracy | `pairs.yaml` contains only measured pairs. Eval publishes meaning and entity numbers per pair | v1 |
| Keep digits as digits | Sarvam translate with `numerals_format=native` **off** (keep digits) | v1 |
| Both texts on screen | ward mode shows every turn as text in both languages so the nurse can show the patient and get a nod. "save to chart" attaches the bilingual transcript | v1 (ward mode) |
| Entity read-back for numbers and doses | when a turn contains numbers, doses, times or amounts, confirm them back to the speaker. Mechanism TBD (proposed: in ward mode, highlight entities in both texts. On calls, a v2 option to read the entity back to the speaker in their language) | partly v1 / TBD |
| Replay | DTMF 1 replays the last translation to that side | v1 |
| Escape | DTMF 0 to plain bridge | v1 |
| Ducked original | the listener hears the original speaker under the translation, so tone (anger, urgency) carries even when words do not | v1 |
| Entity accuracy metric | numbers, addresses, names, times survive the round trip, target > 95% | eval |
| LLM repair of low-confidence sentences | v2 only. Not on the v1 hot path | v2 |
| Honest limits | low-resource languages (Santali, Bhojpuri, Tulu, the Northeast) not supported in v1. Results published on real PSTN, not browser audio | v1 |

Medical use is ward mode first: no telephony, both texts visible, and the nurse is the confirmation step. dubhash should not be positioned as a substitute for a qualified medical interpreter for consent-critical conversations until per-pair numbers support it (**proposed** positioning; see [risks](../plan/risks.md)).

## Related

- [Data model](data-model.md)
- [API](api.md)
- [Console](console.md)
- [Configuration](configuration.md)
- [Floor controller](floor-controller.md)
- [Telephony integration](../architecture/telephony-integration.md)
- [Deployment](../engineering/deployment.md)
- [Evaluation and metrics](../engineering/evaluation-and-metrics.md)
- [Risks](../plan/risks.md)
- [Business model](../product/business-model.md)
- [Open questions](../plan/open-questions.md)
