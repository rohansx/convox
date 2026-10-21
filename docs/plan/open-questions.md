# Open Questions

This doc lists every question the docs leave unanswered. First come the six open technical questions from the tech spec, which must be settled by **Day 2 (Thu 22 Oct 2026)**. For each one it gives how to test it, the owner, the default to use if no answer arrives in time, and what the answer changes in the code or config. After those come product, process and legal questions raised by the overview and the spec. Other docs that say "TBD — see open questions" point here. When a question is answered, record the answer, the date and the evidence (log line, measurement, or listening-test sheet) in its row, and update the doc that depended on it.

## Technical questions (settle by Day 2, Thu 22 Oct)

Q1 and Q6 need a live telephony account. If neither Exotel nor Plivo access exists by Day 2, the **default** column applies and the question is re-tested on the first real minute. Nothing waits on these answers, because the fake-first rule means the pipeline is built either way.

| # | Question | How to test | Owner | Default if unanswered on Day 2 | What the answer changes |
|---|---|---|---|---|---|
| Q1 | **Exotel 16 kHz.** Does the voicebot applet really deliver 16 kHz when asked (`sample-rate=16000`), or 8 kHz with the param ignored? | First sponsor minute: one leg to the `/ws/leg` endpoint; log `start.media_format`; check the payload byte rate against `chunk`/`timestamp` deltas; listen to a capture | P3 | Assume 8 kHz may arrive: keep soxr 8k ⇄ 16k resampling in `ExotelLeg`, switched by the `start` message, and log it | Whether a resampler sits on the hot path; outbound chunk size (100 ms = 3,200 bytes at 16k, a multiple of 320); STT input quality and EOU tuning base |
| Q2 | **Sarvam streaming EOU.** Does Sarvam streaming STT emit an end-of-utterance or final-on-silence event we can use, or is Silero the only endpoint signal? | `bench.py` with corpus wavs: log every Sarvam stream event with timestamps against the Silero silence onset; compare which fires first and how often Sarvam's event splits sentences | P2 | Silero only: EOU = silence ≥ `eou_ms` (550) AND utterance ≥ 300 ms | The EOU latency floor; whether `endpoint.py` takes the earlier of two signals; per-pair `eou_ms` starting values |
| Q3 | **TTS per language.** For each of kn, ta and mr: Bulbul vs ElevenLabs Flash on naturalness and first-byte latency from Mumbai | 30-minute listening test: the same 10 translated sentences per language on both engines, blind-rated by a native speaker; measure `t_tts_ttfb` from the ap-south-1 VM (or the nearest box until the VM exists) | P3 (P5 runs the listening sheet) | Bulbul for kn, ta, mr (the tech spec default for Indic targets); ElevenLabs for hi/en | `tts`, `voice_m`, `voice_f` per side in `config/pairs.yaml` |
| Q4 | **STT socket lifetime.** Can a Sarvam stream stay open across utterances with a reset, or must it reconnect? (A Pipecat issue reports SarvamSTT dying after 60 s of silence, which suggests a keepalive is needed) | Open one stream, send three utterances separated by 5 s, 30 s and 70 s of silence (or silence frames), with and without a keepalive; record closes and errors | P2 | Reconnect per utterance, plus pre-connect on speech start; add a keepalive if it is found | The warm STT socket design (v1 latency technique, 150–300 ms saved); reconnect logic; chaos test cases |
| Q5 | **Gender detection.** Is a pitch heuristic on the first 2 s good enough for v1? Alternative: the platform passes gender in the API and the consumer flow asks | Run the detector over the corpus (all speakers, all languages, phone quality with noise) and count misclassifications | P2 (detector), Rohan (decision) | Use the pitch heuristic, plus an optional per-leg gender field in `POST /v1/calls` used when present; set it explicitly for stage calls | The voice selection path in `interp/tts.py`; an optional API field (see [api](../tech-spec/api.md)); dial-in flow prompt |
| Q6 | **Plivo 16 kHz.** Does the India region support `audio/x-l16;rate=16000` on bidirectional streams, or only `audio/x-mulaw;rate=8000`? | On the Udyam-verified account: open a stream element with `bidirectional=true`, `audioTrack=inbound`, `contentType=audio/x-l16;rate=16000`; check whether it is accepted and what `start` reports | P3 | Mulaw 8k: decode and resample in `PlivoLeg` | `PlivoLeg` decode path; whether Plivo is a quality-equivalent fallback or a degraded one |

```mermaid
flowchart LR
    Q1[Q1 Exotel 16k?] -->|no| R1[soxr in ExotelLeg]
    Q6[Q6 Plivo 16k?] -->|no| R6[mulaw decode + soxr in PlivoLeg]
    Q2[Q2 Sarvam EOU event?] -->|yes| E2[endpoint = min Silero, Sarvam]
    Q2 -->|no| E2b[Silero only, tune eou_ms]
    Q3[Q3 Bulbul vs Eleven per lang] --> P3[pairs.yaml tts + voices]
    Q4[Q4 socket survives silence?] -->|no| K4[keepalive or reconnect]
    Q5[Q5 pitch heuristic OK?] -->|no| G5[gender in API / ask]
```

## Product, process and legal questions

These were raised by the overview and spec but are not answered in either. Deadlines are suggestions tied to the [10-day plan](buildathon-10-day-plan.md).

| # | Question | Why it matters | Owner | Needed by |
|---|---|---|---|---|
| PQ1 | Who is the native Kannada or Tamil speaker for rehearsal and the stage phone? | Demo needs one; R-NATIVE | P5 | D3 (Fri 23 Oct) |
| PQ2 | Who are the bilingual raters for hi⇄kn, hi⇄ta and mr⇄hi, and can they rate 300 turns per pair? | Meaning-preservation number for the deck; R-EVAL | P5 | D4 (Sat 24 Oct) |
| PQ3 | Where does the "real recording (with consent) of a rider and a customer failing to communicate" come from? Who gives consent? | Opening 20 s of the stage demo | P5 | D7 (Tue 27 Oct) |
| PQ4 | Which language pairs do we offer a judge on stage? The overview says "whatever they have", but v1 only supports measured pairs | Avoid claiming unmeasured pairs on stage | Rohan | D8 (CP2) |
| PQ5 | Exact wording of the opening disclosure line in each supported language (the overview gives the Hindi and Kannada versions) and whether it counts as DPDP notice | Consent risk R-DPDP | P5, Rohan | D7 |
| PQ6 | Data residency: ElevenLabs (hi/en TTS, and cloning in v2) is not India-hosted, yet the DPDP mitigation says "India-region hosting". Is sending translated text to ElevenLabs acceptable, or does Hindi need Bulbul too? | Trust review in Phase 2; latency too (150–250 ms RTT) | Rohan | Phase 1 (before any pilot) |
| PQ7 | Bulbul pricing per character/minute. The overview's ₹1.5–2.5/min TTS line assumed ElevenLabs for everything | Unit economics in [business-model](../product/business-model.md) | P5 | Phase 1 |
| PQ8 | Sponsor credits: how many Exotel, Sarvam, ElevenLabs and Deepgram minutes/credits do we get for the buildathon and the finale? | R-CREDITS | P5 | D2 |
| PQ9 | Who owns `api/` (call setup, webhooks), `store/` and deployment? The spec does not assign them; [roles](roles-and-ownership.md) proposes P3, P4 and Rohan | Avoid orphaned modules | Rohan | D1 |
| PQ10 | Do we own `dubhash.in` and `edge.dubhash.in`? DNS and TLS are needed for the Exotel websocket URL | CP1 | Rohan | D3 |
| PQ11 | Dial-in language detection: is Sarvam language ID fast and accurate enough on one sentence, or do we lead with the DTMF menu? | Dial-in UX (cut item 1) | P3 | D7 |
| PQ12 | Dial-in free tier (first 10 min/month free, then ₹2/min via UPI): how is abuse prevented, and is UPI billing in scope before Phase 1? | Phase 1 launch | P5 | Phase 1 |
| PQ13 | What form does the Phase 1 platform design partner take (LOI, unpaid pilot, paid pilot)? Bengaluru gig or logistics platform: which one? | Phase 1 exit criterion E1.7 | Rohan | Dec 2026 |
| PQ14 | Which hospital and doctor run the ward mode pilot (Mumbai or Pune)? What does "save to chart" integrate with? | Phase 1 E1.5 | Rohan, P4 | Nov 2026 |
| PQ15 | When does entity read-back for numbers and doses ship? It is a risk mitigation but not scheduled | Medical/money mistranslation R-MISTRANS | P2 | Phase 1 planning |
| PQ16 | API language codes: the overview uses `"hi"`/`"kn"`, the spec uses `"hi-IN"`/`"kn-IN"`. Which is canonical in `POST /v1/calls`? **Docs default: BCP-47 with region (`hi-IN`), as in [api](../tech-spec/api.md).** Confirm, or decide to also accept short codes | Platform integration contract | P3 | D4 |
| PQ17 | Websocket path: the overview shows `wss://edge.dubhash.in/leg?…`, the spec `wss://edge.dubhash.in/ws/leg?…`. **Docs default: `/ws/leg`.** Still open: Exotel allows 3 custom params; `call` and `side` are two, and does `sample-rate` count as the third? | Exotel applet config | P3 | D4 |
| PQ18 | For stage calls, is the call created with `record: true` so the console and the per-turn log show text afterwards? (Without it, `src_text`/`tgt_text` are nulled after metrics) | Demo step 5 shows the per-turn log | Rohan | D8 |
| PQ19 | Does CP1 / the never-cut item / the stage demo require **Exotel** specifically, or is Plivo acceptable? The overview's never-cut list says "two real phones through Exotel"; the spec's build order lets CP1 pass on Plivo if Exotel access has not arrived. Docs currently say "Exotel (Plivo if Exotel access slipped)" | CP1 pass condition, demo story with the sponsor | Rohan | D4 |
| PQ20 | Who owns the pitch-based gender detector? Decision 6 lists "pitch" in Rohan's lane, which may mean the on-stage pitch; [roles](roles-and-ownership.md) proposes P2. Docs currently say P2 (proposed) | Avoid an orphaned detector | Rohan | D1 |

## Spec gaps (TBD items referenced from other docs)

Neither PDF answers these. Each row names the doc that holds the current proposed default. Settle them in that doc, then log the answer below.

| # | Gap | Proposed default (where) | Owner | Needed by |
|---|---|---|---|---|
| G1 | **Earcon timing.** The spec's latency table says "earcon at EOU" (heard about 100 ms after EOU); the mix stage and overview say it plays at translation start, which can add 120 ms before the first word | none yet ([audio](../tech-spec/audio.md#unresolved-mixer-details)) | Rohan | D3 |
| G2 | **Which original is ducked.** Live speaker frames during the TTS window, or the buffered utterance replayed under the translation? Does the listener hear the speaker live while they speak? | none yet ([audio](../tech-spec/audio.md#unresolved-mixer-details)) | Rohan | D3 |
| G3 | **Floor controller transitions the spec leaves implicit:** `on_translation` when the source still holds the floor, exit from `X_SPEAKING` on a discarded utterance, barge-in timer only for the listener, speaking-while-playing past 1.2 s | reading in [floor controller §2.1](../tech-spec/floor-controller.md#21-interpretation-where-the-spec-is-terse) | Rohan | D2 |
| G4 | Utterances under 300 ms: dropped as noise, or merged into the next utterance? | discarded, `X_SPEAKING → IDLE` ([floor controller](../tech-spec/floor-controller.md)) | P2 | D2 |
| G5 | `escape_dtmf` is configurable in the API but the spec's controller hard-codes `"0"`; can bypass be undone during a call? | read from the call; no undo ([floor controller](../tech-spec/floor-controller.md#rule-6-dtmf-1-replay-0-bypass)) | Rohan | D7 |
| G6 | Translation cache size bound, eviction, and how a cache hit is marked in `turns` (no column) | in-memory per pair, bound TBD ([interpreter pipeline](../tech-spec/interpreter-pipeline.md#translation-cache)) | P2 | D6 |
| G7 | `t_tts_done` is in the spec's stage table but not in the `turns` schema | bench-only for now ([latency engineering](../engineering/latency-engineering.md#timestamps-to-log)) | P2 | D5 |
| G8 | **Heard first-audio.** Provider playout buffering (~200 ms) is invisible server-side. Publish server first-audio plus a calibrated offset, or align recordings of both legs? | TBD ([latency engineering](../engineering/latency-engineering.md#timestamps-to-log)) | P5, P2 | D6 |
| G9 | Turn timestamps: wall clock or call-relative ms? | call-relative ms anchored at `calls.started_at` ([data model](../tech-spec/data-model.md#turns)) | Rohan | D1 |
| G10 | Exotel outbound `media`/`clear`/`mark` JSON shape; TTS provider output sample rates; Exotel `media_format` parse format | take from vendor docs or the Pipecat serializer ([leg transports](../tech-spec/leg-transports.md)) | P3 | D4 |
| G11 | Auth for Plivo's websocket, `WebRtcLeg`, provider webhooks, the platform API and the console (only Exotel WS auth is in the spec) | proposals in [api](../tech-spec/api.md#authentication) and [security](../tech-spec/security-privacy.md#authentication-and-network) | P3 | D5 |
| G12 | Full `calls.status` enum (only `ringing` is defined) | `ringing / in_progress / completed / failed` ([api](../tech-spec/api.md#call-status-values)) | P3 | D4 |
| G13 | Outbound platform webhook payloads and delivery (the overview names `call.started`, `turn`, `call.ended`; the spec gives only the `webhook` field). Are texts sent when `record: false`? | proposals in [api](../tech-spec/api.md#outbound-webhook-events-dubhash--platform) | P3 | D5 |
| G14 | Retention: how a platform opts in (account flag vs per call), how long retained text is kept, whether `record: true` also covers audio, and phone-number handling | `record` = text only; audio needs its own opt-in ([data model](../tech-spec/data-model.md#text-retention)) | Rohan | Phase 1 |
| G15 | Is the opt-in S3 recordings bucket in v1? The spec's component figure has it; the deploy stack (app, Postgres, Redis, Caddy) does not | not in the v1 Compose stack ([deployment](../engineering/deployment.md#data-handling-on-the-box)) | Rohan | D5 |
| G16 | Deepgram hosting region (caller audio leaves India on the English/Hinglish fallback?); vendor data-retention and no-training settings | TBD ([security](../tech-spec/security-privacy.md#data-residency)) | P5 | Phase 1 |
| G17 | Opening line: it does not say an automated service processes the call or how to turn it off; DPDP counsel review | proposed extra sentence in [security](../tech-spec/security-privacy.md#opening-line) | P5, Rohan | D7 |
| G18 | Dial-in: how leg b's language is chosen; the Passthru request/response contract | TBD ([telephony integration](../architecture/telephony-integration.md#dial-in-ivr-flow-consumer)) | P3 | D7 |
| G19 | Ward mode signalling route and how "save to chart" reaches a hospital record system | TBD ([api](../tech-spec/api.md#ward-mode), [console](../tech-spec/console.md)) | P4 | D7 / Phase 1 |
| G20 | Default voice before gender is detected (first utterance under 2 s); optional per-leg gender field in `POST /v1/calls` | see Q5 ([configuration](../tech-spec/configuration.md#voice-selection-v1)) | P3 | D6 |
| G21 | Controller `tick` period; localised "aap line par hain?" text per language | TBD ([floor controller](../tech-spec/floor-controller.md)) | Rohan | D3 |
| G22 | Max concurrent calls per node; VM size; backups; monitoring | set from CPU profiling ([configuration](../tech-spec/configuration.md), [ADR 0010](../architecture/adr/0010-single-vm-mumbai.md)) | Rohan | D8 |
| G23 | Eval: corpus storage if too large for git; split of the 300 rated turns between bench and live calls; number of PSTN runs per pair before CP2 | report the split with the number ([evaluation](../engineering/evaluation-and-metrics.md)) | P5 | D6 |
| G24 | Missing cost lines: Deepgram, v2 LLM repair, hosting; platform sell price ₹5–8 vs ₹6–8 in two overview tables | TBD ([business model](../product/business-model.md)) | P5 | Phase 1 |

## Answer log

| # | Answer | Date | Evidence |
|---|---|---|---|
| Q1 | TBD | | |
| Q2 | Partly answered. Sarvam's realtime STT emits `vad.speech_end` about **1021 ms** after speech stops, slower than our Silero EOU at 550 ms. Keep Silero as the primary endpoint and treat Sarvam's signal as a backup. STT final arrives about **290 ms** after end-of-stream. | 21 Oct | `make smoke --eou`, bulbul kn sentences, from Bengaluru |
| Q3 | Partly answered. Bulbul kn TTS first byte is 215–367 ms (total 0.8–0.9 s). ElevenLabs is not measured yet (no key). The listening test is still open. | 21 Oct | `make smoke` |
| Q4 | Not measured. Run `tools/smoke.py --idle 5,30,70`. | | |
| Q5 | TBD | | |
| Q6 | TBD | | |
| L1 (new) | **Translation latency is the bottleneck.** Sarvam hi→kn takes **about 1.2 s** at p50, whatever the model or mode (`mayura:v1` modern-colloquial and formal, `sarvam-translate:v1`). en→kn takes about 630 ms and network RTT is about 60 ms, so hi→kn is probably two hops inside Sarvam. Claude streaming was also tried: `claude-haiku-4-5` gave first token at 933 ms and finished at 1.7 s; `claude-opus-5-5` at low effort finished at 2.0 s. Neither is faster. The direct pair works (no English pivot fallback needed). Mayura modern-colloquial keeps English loanwords in Latin script ("gate number 2"), and Bulbul reads them fine. Estimated first-audio is 550 EOU + 290 STT + 1200 MT + 250 TTS + 200 playout = **about 2.5 s**, above the 2.0 s p95 target. Mitigations being built: speculative MT on stable STT partials during the EOU wait, and a trial of Sarvam speech→English plus en→kn. **Pivot trial (live, 10 Bulbul-voiced Hindi sentences, real-time paced, warm sockets, 2 runs):** Sarvam's realtime STT has `mode=translate` (Hindi speech → English final; partials stay Hindi). Its final arrives as fast as a transcript (277 vs 287 ms p50). Route B (STT-translate → en→kn) STT+MT = **962 ms p50 / 1107 ms p95** against route A (hi STT → hi→kn) **1630 / 1872 ms**, which saves about 670 ms at p50 (`saaras:v4` run: 988/1031 against 1667/1882). hi→en (707 ms) + en→kn (716 ms) ≈ hi→kn (1357 ms), which supports the internal-pivot guess. Estimated first-audio with B is about **2.0 s p50 / 2.1 s p95**: at the p95 target, but not the 1.5 s p50 target. Quality: B's English kept every name and number in the set, and B was more faithful than A on 3 of 10 sentences (A misheard one OTP sentence and swapped a pronoun). B's Kannada is more code-mixed. A native Kannada rating is still needed. Opt-in pair `hi-kn-pivot` (`stt: sarvam-translate`, `mt_src: en-IN`). Details in providers/README.md "Pivot trial (L1)". | 21 Oct | `/private/tmp` probe scripts, 6 calls each, warm client; `tools/pivot_probe.py` (live) |
| L1a (new) | **Speculative MT on STT partials does not help with Sarvam STT.** Built (`interp/speculate.py`, `spec_mt` / `spec_after_ms` per pair, bench `--spec/--no-spec`), now **off by default**. Fakes with the live budget (MT 1200, STT final 290, TTS ttfb 250, no playout): first-audio p50 **2320 → 1660 ms** (`spec_after_ms` 0/100/200/300/400 → 1480/1560/1660/1760/1860). Live hi→kn (Sarvam STT + Mayura + Bulbul, 8 Bulbul-hi utterances): hit rate **1/8**, 33 speculative MT calls for 8 utterances, first-audio p50 2451 / p95 3445 ms with speculation vs 2408 / 2552 ms without. Cause: Sarvam realtime partials lag the final. With `stream_type=fast` they are fragments ("जी जी", "आपका"); with `balanced` they trail the speech by ~0.7 s and are still incomplete when the final lands ~850 ms after speech ends. Next: an early final (send `speech_end`/`flush` at ~200 ms silence and speculate on that), or an STT with faster partials. | 21 Oct | `tools/bench.py --pair hi-kn-bulbul`, partial-timing probe, 3 utterances each |
| D1 (decision) | 21 Oct: **Bulbul speaks Hindi** (ElevenLabs off the default path; free tier blocks library voices), and **`hi-kn` defaults to the pivot route** (Sarvam speech→English, then en→kn). The direct route is kept as `hi-kn-direct`. A native Kannada speaker's check of the outputs is still pending. | 21 Oct | ADR 0008 amendment, `config/pairs.yaml`, `tests/test_pair_defaults.py` |
| L3 (new) | **An early STT final takes about 350 ms off first-audio, and no words are lost.** Built in `interp/pipeline.py`, with `early_final_ms` per pair (0 = off) and bench `--early-final-ms`. After `early_final_ms` of silence inside an utterance, the STT stream is ended (`speech_end`), and the final, MT and TTS first byte run during the rest of the 550 ms EOU wait. If speech resumes before EOU, the early result is discarded and its MT/TTS are cancelled. The old `end()` finishes in the background, so its socket returns to the warm pool. A new stream then gets the whole buffered utterance (pre-roll + speech + gap) and continues live. Delivery is still at EOU, never earlier, so the floor's turn-taking is unchanged: playback starts at max(EOU, TTS ready). The backchannel rule applies to the early final. Timings gain `t_early_final`, `early_final_used` and `early_final_restarts`. While an early final is pending, speculation does not start. **Fakes** (`hi-kn`, MT 700, STT final 290, TTS ttfb 250, lockstep, n=3): first-audio p50 with `early_final_ms` 0/150/200/300 = **1820/1420/1460/1560 ms**. **Live** `hi-kn` A side (Sarvam speech→English + en→kn + Bulbul, 10 Bulbul-hi sentences × 2 runs per setting, real time, n=20): first-audio p50/p95 = **1727/1882 ms off vs 1377/1634 ms at 200**. One 1.9 s MT spike in the "on" runs gave a max of 2561 ms. The final arrived about 100 ms *before* EOU at p50. MT and TTS were never finished before EOU, so the EOU hold cost nothing. **Restarts:** 20 of 40 early finals (50%) were discarded on comma pauses (Bulbul pauses 240–480 ms at commas). Every utterance's delivered final still came from an early final. The 20/20 English finals were **identical** to the off runs. Kannada B side, 2 smoke clips: 2252 → 1863 ms p50, 0 restarts, same transcripts. Cost: one extra STT stream per mid-sentence pause, plus a cancelled MT call when the final lands before the speaker resumes (only on pauses longer than ~460 ms). **On for `hi-kn` at 200.** Other pairs stay off until measured. Human speech pauses differ from Bulbul's, so the restart rate on real calls is still unknown. | 21 Oct | `tools/bench.py --stt real --mt real --tts real --early-final-ms 0/200`, `out/early/live_ef*.json`, `tests/test_early_final.py` |

## Related

- [buildathon-10-day-plan.md](buildathon-10-day-plan.md)
- [risks.md](risks.md)
- [roles-and-ownership.md](roles-and-ownership.md)
- [demo-script.md](demo-script.md)
- [../tech-spec/configuration.md](../tech-spec/configuration.md)
- [../tech-spec/leg-transports.md](../tech-spec/leg-transports.md)
- [../tech-spec/interpreter-pipeline.md](../tech-spec/interpreter-pipeline.md)
- [../tech-spec/security-privacy.md](../tech-spec/security-privacy.md)
- [../product/business-model.md](../product/business-model.md)
