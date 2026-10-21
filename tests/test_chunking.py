"""Chunked interpretation in the Interpreter (pair.chunking, interp/chunker.py, L9).

Inside one utterance, a pause of chunk_pause_ms after at least chunk_min_ms of speech
commits the chunk: its early final is translated, synthesised and delivered while the
speaker goes on, and the rest of the utterance continues in a fresh STT stream that does
NOT get the committed audio again. The final chunk is delivered at EOU as before.

Live-like budget on the FakeClock as in test_early_final: MT 700, TTS first byte 250.
"""

import dataclasses

from tests.audio_fixtures import conversation
from tests.test_early_final import BASE, run

CHUNK = dataclasses.replace(
    BASE, early_final_ms=200, chunking=True, chunk_pause_ms=300, chunk_min_ms=1200
)
TWO = conversation(
    [("silence", 300), ("speech", 1500), ("silence", 400), ("speech", 1500), ("silence", 3000)]
)
C0, C1 = "main gate ke bahar khada hoon", "building ka naam bataiye"


async def test_a_pause_commits_a_chunk_that_plays_while_the_speaker_goes_on():
    interp, sink, stt, mt = await run(CHUNK, [C0, C1], TWO, stt_ms=50)
    t0, t1 = sink.translations
    assert (t0.src_text, t1.src_text) == (C0, C1)
    assert mt.texts == [C0, C1]
    assert (t0.seq, t1.seq) == (0, 1)
    assert t0.utterance_id == t1.utterance_id is not None
    assert (t0.chunk, t0.final_chunk, t1.chunk, t1.final_chunk) == (0, False, 1, True)
    # chunk 0 reached the sink before the speaker finished the second clause
    end_of_speech = 300 + 1500 + 400 + 1500
    assert sink.t_delivered[0] < end_of_speech
    assert sink.kinds() == ["speech_start", "translation", "speech_end", "translation"]
    # each chunk has its own speech span; both know the utterance's start
    a, b = t0.timings, t1.timings
    assert abs(a["t_speech_start"] - 300) <= 20 and abs(a["t_speech_end"] - 1780) <= 20
    assert abs(b["t_speech_start"] - 2200) <= 40 and abs(b["t_speech_end"] - 3680) <= 20
    assert a["t_utt_start"] == b["t_utt_start"] == a["t_speech_start"]
    assert a["t_eou"] < b["t_speech_start"]  # the chunk's cut, not the utterance's EOU
    assert interp.chunks_committed == 1


async def test_b_the_next_chunk_stream_does_not_replay_the_committed_audio():
    # the second clause at another pitch, so its audio cannot be mistaken for the first
    pcm = conversation([("silence", 300), ("speech", 1500), ("silence", 400)]) + conversation(
        [("speech", 1500), ("silence", 3000)], f0=210.0
    )
    _, _, stt, _ = await run(CHUNK, [C0, C1], pcm, stt_ms=50)
    assert stt.opened == 2
    first, second = (bytes(b) for b in stt.pushed)
    assert len(first) == 32 * (200 + 1500 + 200)  # pre-roll, clause 1, silence to the cut
    # the second stream starts inside the pause (200 ms of it as pre-roll), then clause 2
    at = pcm.find(second[: 32 * 400])
    assert 32 * (300 + 1500) <= at <= 32 * (300 + 1500 + 400)
    assert len(second) <= 32 * (200 + 1500 + 200 + 40)


async def test_c_verdict_still_pending_when_speech_resumes():
    # STT final takes 290 ms: the early final (cut at 200 ms) is not back when speech
    # resumes 400 ms into the pause; the worker waits for it, then commits
    interp, sink, stt, mt = await run(CHUNK, [C0, C1], TWO)
    assert [t.src_text for t in sink.translations] == [C0, C1]
    assert stt.opened == 2 and interp.chunks_committed == 1


async def test_d_fewer_than_4_words_is_not_committed_and_restarts_with_replay():
    full = "main gate ke bahar khada hoon"
    interp, sink, stt, mt = await run(CHUNK, ["main gate", full], TWO, stt_ms=50)
    (tr,) = sink.translations
    assert tr.src_text == full and (tr.chunk, tr.final_chunk) == (0, True)
    first, second = (bytes(b) for b in stt.pushed)
    assert second.startswith(first)  # the early-final restart replays everything
    assert interp.chunks_committed == 0


async def test_e_text_ending_in_a_number_waits_for_more():
    full = "mera otp hai 4729 aur flat 302"
    interp, sink, _, _ = await run(CHUNK, ["mera otp hai 47", full], TWO, stt_ms=50)
    assert [t.src_text for t in sink.translations] == [full]


async def test_f_no_commit_inside_the_first_chunk_min_ms():
    pcm = conversation(
        [("silence", 300), ("speech", 800), ("silence", 400), ("speech", 1500), ("silence", 3000)]
    )
    full = "main gate ke bahar khada hoon abhi"
    interp, sink, _, _ = await run(CHUNK, ["main gate ke bahar", full], pcm, stt_ms=50)
    assert [t.src_text for t in sink.translations] == [full]
    assert interp.chunks_committed == 0


async def test_g_a_pause_shorter_than_chunk_pause_ms_does_not_commit():
    pcm = conversation(
        [("silence", 300), ("speech", 1500), ("silence", 260), ("speech", 1500), ("silence", 3000)]
    )
    full = "main gate ke bahar khada hoon building"
    interp, sink, _, _ = await run(CHUNK, [C0, full], pcm, stt_ms=50)
    assert [t.src_text for t in sink.translations] == [full]


async def test_h_three_chunks_in_order_one_utterance():
    pcm = conversation(
        [("silence", 300), ("speech", 1500), ("silence", 400), ("speech", 1300),
         ("silence", 400), ("speech", 1000), ("silence", 3000)]
    )  # fmt: skip
    texts = ["pehla hissa yeh hai bhai", "doosra hissa yeh hai bhai", "teesra hissa"]
    interp, sink, _, _ = await run(CHUNK, texts, pcm, stt_ms=50)
    trs = sink.translations
    assert [t.src_text for t in trs] == texts
    assert [t.seq for t in trs] == [0, 1, 2] and [t.chunk for t in trs] == [0, 1, 2]
    assert [t.final_chunk for t in trs] == [False, False, True]
    assert len({t.utterance_id for t in trs}) == 1
    assert sink.t_delivered == sorted(sink.t_delivered)


async def test_i_a_pause_then_eou_is_the_last_chunk_as_an_early_final():
    pcm = conversation([("silence", 300), ("speech", 1500), ("silence", 3000)])
    interp, sink, _, _ = await run(CHUNK, [C0], pcm, stt_ms=50)
    (tr,) = sink.translations
    assert (tr.chunk, tr.final_chunk, tr.timings["early_final_used"]) == (0, True, 1)
    assert interp.chunks_committed == 0


async def test_j_empty_rest_after_a_chunk_discards_only_the_rest():
    interp, sink, _, _ = await run(CHUNK, [C0, ""], TWO, stt_ms=50)
    (tr,) = sink.translations
    assert tr.src_text == C0 and tr.final_chunk is False
    assert sink.kinds() == ["speech_start", "translation", "speech_end", "discarded"]


async def test_k_two_utterances_keep_order_and_distinct_ids():
    pcm = TWO + conversation([("speech", 1500), ("silence", 3000)])
    texts = [C0, C1, "doosri baat yeh hai ki"]
    interp, sink, _, _ = await run(CHUNK, texts, pcm, stt_ms=50)
    trs = sink.translations
    assert [t.src_text for t in trs] == texts and [t.seq for t in trs] == [0, 1, 2]
    assert trs[0].utterance_id == trs[1].utterance_id != trs[2].utterance_id


async def test_l_chunking_off_is_unchanged_but_ids_and_utt_start_are_set():
    off = dataclasses.replace(CHUNK, chunking=False)
    full = "main gate ke bahar khada hoon building"
    interp, sink, stt, _ = await run(off, [C0, full], TWO, stt_ms=50)
    (tr,) = sink.translations
    assert tr.src_text == full and (tr.chunk, tr.final_chunk) == (0, True)
    assert tr.utterance_id is not None
    assert tr.timings["t_utt_start"] == tr.timings["t_speech_start"]
    assert interp.chunks_committed == 0


async def test_m_chunking_without_early_final_cuts_at_chunk_pause_ms():
    no_early = dataclasses.replace(CHUNK, early_final_ms=0)
    interp, sink, stt, _ = await run(no_early, [C0, C1], TWO, stt_ms=50)
    assert [t.src_text for t in sink.translations] == [C0, C1]
    assert abs(stt.t_end[0] - (1780 + 300)) <= 40  # end() at the chunk pause
