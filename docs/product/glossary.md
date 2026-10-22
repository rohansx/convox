# Glossary

This page defines the shared vocabulary used across the Convox docs, code and standups, so that "leg", "floor", "mark" or "clear" mean the same thing to all five of us. Terms are grouped by area and alphabetical within each group. Where a term maps to a code identifier, the identifier is shown in `code`.

## Telephony

| Term | Definition |
|---|---|
| **Applet** | One step in an Exotel call flow. A flow is a chain of applets (Gather, Passthru, Voicebot, ...). |
| **Bridge / plain bridge** | Connecting two call legs so audio passes straight through. This is what a normal masked call does. Convox replaces the CPaaS bridge with its own, and becomes a plain bridge again in **bypass mode**. |
| **`call_sid` / `stream_sid`** | Provider identifiers that arrive in Exotel's `start` message. Stored per leg in the `legs` table as `provider_call_sid` and `stream_sid`. |
| **CPaaS** | Communications Platform as a Service: Exotel, Plivo, Ozonetel. These companies own virtual numbers, carrier interconnects and a media server. A call that touches one of their numbers terminates in their cloud, and their API decides what happens next (bridge, play, record, stream). This is the only legal, scalable way to be "inside" an Indian phone call. |
| **Dial-in** | The consumer product. Call one public Convox number, enter the target number (Gather, 10 digits), and Convox dials leg B. Leg A is placed on the voicebot applet. Leg A's language is auto-detected from the first utterance via Sarvam language ID, with a DTMF menu as fallback. See [api](../tech-spec/api.md). |
| **DTMF** | Dual-tone multi-frequency: keypad digits pressed during a call. Convox uses **1** (replay the last translation to that side) and **0** (escape into bypass mode). |
| **DTMF escape** | Pressing the escape digit (`escape_dtmf`, default `"0"`) on either leg turns interpretation off for the rest of the call and switches to bypass mode. This is the escape hatch that makes platforms comfortable. Tracked as `escape_used` and as the **escape rate** metric (target < 5%, every case reviewed). |
| **Exophone** | An Exotel virtual phone number. Used as the `CallerId` when Convox places the two outbound legs, so neither party sees the other's real number. |
| **Gather / Passthru** | Exotel applets used in the dial-in flow. Gather collects digits (`digits=10`). Passthru calls our server (`/dialin/target`) so it can create the session and dial leg B. |
| **Leg** | One side of a call: the connection between one human's phone and convox. Every interpreted call has exactly two legs, **A** and **B** (`side=a` / `side=b`). For Exotel, each leg is a separate outbound call running its own voicebot applet. |
| **Masked call / number masking** | How gig platforms connect a partner and a customer. The platform backend asks the CPaaS to dial both people and bridge them, and shows each person a virtual number (for example 0806...). Every such call already passes through a CPaaS cloud, so Convox sits in an existing interception point rather than creating one. |
| **Media server** | The CPaaS component that terminates call audio. In a masked call it bridges A and B. With Convox it streams each leg to our websocket instead. |
| **PSTN** | Public Switched Telephone Network: the ordinary phone network (Airtel, Jio switches). PSTN audio reaches the ear at 8 kHz through codecs, so all latency and accuracy numbers must be measured on real PSTN calls, not loopback. |
| **Stream applet** | Exotel's one-way streaming applet. It forks audio to you, but you cannot speak back. Not usable for convox. |
| **Voicebot applet** | Exotel's two-way streaming applet. Exotel opens a websocket to our server and sends `media` messages (raw 16-bit PCM, mono, base64; the overview says 8 kHz by default or 16 kHz on request, the spec calls this unconfirmed until tested (Q1); chunks of 100 ms, a multiple of 320 bytes). We send `media` back and Exotel plays it to the caller. Supports `clear` and `mark`. Bolna and Pipecat use this same applet to connect to Exotel. Plivo's equivalent is its audio stream element with `bidirectional=true`. See [leg transports](../tech-spec/leg-transports.md). |
| **Udyam** | Indian MSME registration. Needed to verify a self-serve Plivo account (the fallback telephony path). |

## Leg transport messages

| Term | Definition |
|---|---|
| **`clear`** | Control message that drops queued, not-yet-played audio on a leg. Used for barge-in. Exotel `clear`, Plivo `clearAudio`. Exposed as `LegTransport.clear()`. |
| **`connected` / `start` / `media` / `dtmf` / `stop`** | The inbound event sequence on an Exotel voicebot websocket. `start` carries `stream_sid`, `call_sid`, `from`, `to` and `media_format`. The session starts interpreting once both legs have sent `start`. |
| **`mark`** | Control message asking the provider to call back when all audio sent before it has played. This is how Convox knows a translation finished playing. Exotel `mark`, Plivo `checkpoint`. Exposed as `LegTransport.mark(tag)` / `on_mark(cb)`. |
| **`LegTransport`** | The Python protocol every telephony provider implements: `frames()`, `send()`, `clear()`, `mark()`, `on_mark()`, `on_dtmf()`, `close()`. Implementations are `ExotelLeg`, `PlivoLeg`, `WebRtcLeg` (dev) and `FakeLeg` (test). |
| **`FakeLeg`** | Test transport. It plays a wav file as inbound frames, in real time or faster, and captures outbound frames to a wav. |
| **`WebRtcLeg`** | Dev transport. A browser page with a mic, where two tabs act as two legs. Used for development without telephony minutes and as the last-resort demo fallback. |

## Audio

| Term | Definition |
|---|---|
| **Ducking** | Mixing the speaker's original voice under the translation at reduced volume (`duck_db: -20`) for the first `duck_ms` (600 ms) of the TTS, on telephony calls only (co-located /join and loopback calls play no original). The listener knows the other person really spoke, so the delay reads as interpretation, not failure. Cut #4 on the cut list (fallback is plain earcon only). |
| **Earcon** | A short (120 ms) soft tone, so the listener never hears more than about 700 ms of silence without a signal. Whether it plays at EOU or when the translation starts is TBD ([audio](../tech-spec/audio.md#unresolved-mixer-details)). It is not a real latency cut, but it lowers perceived latency more than any real cut. |
| **Frame** | The internal audio unit: 20 ms of PCM16 mono at 16 kHz = **640 bytes**. Stamped by `FrameClock` with leg-relative ms. |
| **`FrameClock`** | Stamps each inbound frame with leg-relative milliseconds. Used to measure jitter and gaps. |
| **Gap** | A stretch of audio missing from the provider stream. Logged as an `events` row (`kind = gap`). The mixer must not play garbage across a gap (chaos test: 2 s Exotel gap). |
| **Mixer** | Builds each listener's outbound stream: earcon + TTS at 0 dB + the speaker's original at −14 dB. Writes 20 ms frames, which the transport chunks to 100 ms (3,200 bytes at 16 kHz, a multiple of 320). Must never exceed the int16 range. |
| **PCM16** | Signed 16-bit linear PCM, mono. The internal format, at 16 kHz. |
| **soxr** | The resampling library used for 8 kHz ⇄ 16 kHz conversion when a provider does not deliver 16 kHz. |

## Turn-taking (floor control)

| Term | Definition |
|---|---|
| **Backchannel** | A short acknowledgement ("haan", "hmm", "sari", "ok"). Defined as an utterance under 600 ms (`backchannel_max_ms`) with ≤ 2 words. Backchannels are not translated, never change floor state and never clear audio. |
| **Barge-in** | The listener speaks during playback for longer than `barge_in_min_ms` (1,200 ms). The controller `clear`s the remaining playback on that leg, marks the translation as clipped, and gives the floor to the new speaker. Speech under 600 ms is a backchannel; 600 to 1,200 ms is a normal utterance, translated and queued without clearing playback. |
| **Bypass mode** | Entered via DTMF 0 (or `mode: "bridge"` at call creation). The controller becomes a plain mixer: each leg's frames are forwarded to the other at 0 dB and the interpreters stop. Logged as an `events` row (`kind = bypass`). |
| **Clipped** | A translation cut short by barge-in. Recorded as `clipped_ms` (ms played) on the turn row. A clipped translation is not a dropped one. |
| **Dropped utterance** | Something someone said that was never played to the other side. Target: **0**. A drop is a bug. |
| **False clear** | A translation cut by speech that should have counted as a backchannel. Target: < 3% of turns. |
| **Floor** | Who currently "holds" the conversation: one person speaking or one translation playing. Only one translation plays at a time. |
| **`FloorController`** | One asyncio task per call. It owns the floor state, the queue, and the only code allowed to call `send()`, `clear()` and `mark()` on either leg. States: `IDLE`, `A_SPEAKING`, `B_SPEAKING`, `PLAYING_AB`, `PLAYING_BA`. See [floor controller](../tech-spec/floor-controller.md). |
| **Queued** | The condition where the queue is non-empty while a translation is playing. It is **not** a separate enum state, even though diagrams draw it as a box. Queued items play when the current `mark` fires, or are force-played after 4 s of silence on both legs. Nothing is dropped. |
| **Silence prompt** | After 10 s of silence on both legs, a soft "aap line par hain?" plays in both languages. |
| **Speaking-while-playing** | A speaks while B→A is still playing. A's interpreter keeps running, and the resulting translation is queued and played to B after B→A's `mark`. This holds while A's overlapping speech stays under 1.2 s; past that, the barge-in rule applies ([floor controller](../tech-spec/floor-controller.md#5-the-speaking-while-playing-case)). |

## Interpreter pipeline

| Term | Definition |
|---|---|
| **Bench harness** | `tools/bench.py`. Runs two FakeLegs with scripted wav utterances through the real providers and reports p50/p95 for each stage, plus WER. |
| **Clause-level partial start / partial preview (v2)** | Start translating and synthesising a clause when the STT partial ends at a clause boundary and the text has been stable for 400 ms, while the speaker keeps talking. This is where the next ~500 ms of latency savings lives. |
| **Consecutive interpretation** | The interpreter speaks after the speaker finishes an utterance. This is Convox v1. **Simultaneous** (overlapping) interpretation is a non-goal for v1. |
| **EOU (end of utterance)** | The moment Convox decides the speaker has finished. It fires when silence ≥ `eou_ms` (per pair, starting at 550 ms) **and** the utterance ≥ 300 ms, or earlier if Sarvam's stream emits an end-of-utterance or final event. EOU detection is the biggest single latency and quality lever: too short splits sentences, too long feels dead. |
| **False EOU** | EOU fired mid-sentence, splitting one utterance into two. Measured per language against latency when tuning `eou_ms`. |
| **First-audio latency** | Time from the speaker stopping to the listener hearing the first translated word, on real PSTN legs. EOU wait (~550 ms) + ~1.2 s (STT final + MT + TTS TTFB + playout) ≈ 1.4 to 1.8 s. Targets: **p50 < 1.5 s, p95 < 2.0 s**. |
| **Hinglish** | Mixed Hindi-English speech. Handled by Deepgram Nova as the fallback STT. |
| **Interpreter** | One direction of the pipeline (A→B or B→A): VAD + EOU → STT stream → translate → TTS stream. A call has two interpreters. Each hands finished `Translation` objects to the floor controller. |
| **Language pair** | Two languages joined by ⇄, e.g. `hi ⇄ kn`. Configured in `config/pairs.yaml` with BCP-47-style codes (`hi-IN`, `kn-IN`). v1 rehearsed pairs: hi ⇄ kn, hi ⇄ ta, mr ⇄ hi. |
| **Opening line** | A prompt of about five seconds at call start, in each language: "aap hindi mein boliye, saamne wala kannada mein sunega. chhote vaakya boliye." It sets the rhythm, asks for short sentences and discloses Convox (DPDP). |
| **`pairs.yaml`** | Per-pair config: STT, TTS and voices for each side (`voice_m`, `voice_f`), plus `eou_ms`, `backchannel_max_ms`, `barge_in_min_ms`, `duck_db`. See [configuration](../tech-spec/configuration.md). |
| **Translation (object)** | What an interpreter hands to the floor controller: text plus an async audio iterator. |
| **Translation cache** | Exact-match cache for each pair. Repeated greetings, confirmations and numbers skip MT entirely (~200 ms). |
| **TTFB** | Time to first byte: for TTS, how long until the first audio chunk arrives (`t_tts_ttfb`). |
| **Turn** | One translated utterance. Each is logged as a `turns` row with side, text, every stage timestamp, `backchannel`, `clipped_ms` and provider ids. Platforms receive it as a `turn` webhook event. |
| **VAD** | Voice activity detection. Silero VAD v5 (onnx) runs on every frame of each leg: 32 ms windows (512 samples at 16 kHz), hop 20 ms, speech when prob > 0.5. It also computes RMS for ducking and backchannel duration. |
| **Voice selection (v1)** | A fixed natural voice per target language and detected speaker gender (pitch heuristic on the first 2 s). The listener hears **a gender-matched natural voice**, not the speaker's voice. Voice **cloning** is v2 and needs explicit consent. |
| **Warm sockets** | Keeping STT (one per leg) and TTS (one per leg-direction) websockets open across utterances, to avoid 150 to 300 ms of connect and auth each time. |

## Vendors and engines

| Term | Definition |
|---|---|
| **Bulbul** | Sarvam's TTS. Primary for Indic targets: kn, ta, mr, te, ml, bn, gu, pa, od. |
| **Deepgram Nova** | Streaming STT used as the English/Hinglish fallback. |
| **ElevenLabs Flash v2.5** | Streaming TTS for Hindi and English targets, and for voice cloning in v2. Its RTT from Mumbai is 150 to 250 ms. |
| **Mayura** | Sarvam's translation model/API. Sentence-level, called with source/target codes, colloquial mode and digits kept. |
| **Saarika / Saaras** | Sarvam's streaming STT (websocket) for Indic languages. |
| **Silero VAD v5** | Open, CPU-only voice activity detection model run via onnx. |

## Product and product modes

| Term | Definition |
|---|---|
| **Console** | Server-rendered HTML + SSE (htmx) live view: active calls, two columns for each call, turns with stage latencies inline, state badge, queue depth, DTMF and barge-in markers. It is both the stage demo surface and the trust-review surface. |
| **Interpreted minute** | The billing unit for platforms: a minute of a call in `interpret` mode. Stored as `interpreted_s`. |
| **Mode** | `interpret` (default) or `bridge` (pass-through), set on `POST /v1/calls`. |
| **Platform integration** | A platform's backend calls `POST /v1/calls` instead of Exotel's `Calls/connect`, and Convox places the two legs. |
| **Ward mode** | One phone, no telephony. A web app where a nurse and a patient each press their side to talk (push-to-talk). It uses the same pipeline without a telephony transport. Both texts show on screen, and "save to chart" attaches the bilingual transcript. |

## Process and compliance

| Term | Definition |
|---|---|
| **ap-south-1** | AWS Mumbai region. The single VM (Docker Compose: app, Postgres, Redis, Caddy) lives here to keep RTT to Exotel and Sarvam low. |
| **CP1** | Checkpoint 1: two real phones, both directions, no drops. |
| **CP2** | Checkpoint 2: feature freeze. |
| **Cut list** | The order in which scope is dropped if time runs out: dial-in IVR → ward mode → third pair → ducking. Never cut: two real phones through Exotel (Plivo if Exotel access slipped) in both directions, the floor controller, the per-turn log. See [10-day plan](../plan/buildathon-10-day-plan.md#cut-list-in-order). |
| **DPDP** | India's Digital Personal Data Protection Act. Drives the opening-line disclosure, no voice cloning without consent, India-region hosting and opt-in transcript retention. See [security and privacy](../tech-spec/security-privacy.md). |
| **Entity accuracy** | Share of numbers, addresses, names and times that survive the round trip. Target > 95%. |
| **Meaning preservation** | A bilingual rater labels each turn as kept / partly / lost (`tools/label.py`), on 300 turns per pair. Target > 90% "kept" on hi ⇄ kn, hi ⇄ ta, mr ⇄ hi. |
| **Task completion** | Scripted scenarios (find the building, confirm a dose, agree a time) completed through Convox, compared with a plain bridge between speakers who share no language. Target > 85% vs a < 40% baseline. |
| **WER** | Word error rate of STT against reference transcripts. |

## Related

- [Product overview](overview.md)
- [Problem and users](problem-and-users.md)
- [Business model](business-model.md)
- [Telephony integration](../architecture/telephony-integration.md)
- [Leg transports](../tech-spec/leg-transports.md)
- [Interpreter pipeline](../tech-spec/interpreter-pipeline.md)
- [Floor controller](../tech-spec/floor-controller.md)
- [Audio](../tech-spec/audio.md)
- [Data model](../tech-spec/data-model.md)
- [Evaluation and metrics](../engineering/evaluation-and-metrics.md)
