"""FakeSTT reports a scripted language per final, like SarvamSTTStream.detected_lang."""

from dubhash.providers.fakes import FakeSTT

AUDIO = b"\x10\x00" * 16000  # 1 s


async def utter(stt: FakeSTT) -> tuple[str, str | None]:
    s = await stt.open("hi-IN")
    assert s.detected_lang is None
    await s.push(AUDIO)
    final = await s.end()
    return final, s.detected_lang


async def test_detected_langs_are_reported_per_final_in_order():
    stt = FakeSTT(script=["one", "two", "three"], detected=["mr-IN", None])
    assert await utter(stt) == ("one", "mr-IN")
    assert await utter(stt) == ("two", None)
    assert await utter(stt) == ("three", None)  # script ran out


async def test_no_audio_consumes_no_detected_lang():
    stt = FakeSTT(detected=["kn-IN"])
    s = await stt.open("hi-IN")
    assert await s.end() == ""
    assert s.detected_lang is None
    assert (await utter(stt))[1] == "kn-IN"
