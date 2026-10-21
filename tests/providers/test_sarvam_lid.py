import io
import json
import logging
import wave

import httpx

from dubhash.interp.langid import SUPPORTED_LANGS, LangGuess, LanguageID
from dubhash.providers.sarvam_lid import SarvamLID

SEC = b"\x01\x00" * 16000  # 1 s of PCM16 @16k
HI_MR = ["hi-IN", "mr-IN"]


def _form(req: httpx.Request) -> tuple[dict[str, str], bytes]:
    """Parse the multipart body: (text fields, file bytes)."""
    ctype = req.headers["content-type"]
    boundary = ctype.split("boundary=")[1].encode()
    fields: dict[str, str] = {}
    file = b""
    for part in req.content.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, body = part.split(b"\r\n\r\n", 1)
        body = body.rstrip(b"\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        if b"filename=" in head:
            file = body
        else:
            fields[name] = body.decode()
    return fields, file


def _lid(handler, **kw) -> SarvamLID:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SarvamLID(api_key="sk-test", client=client, base_url="https://mock.sarvam", **kw)


def _stt_reply(lang: str | None, prob: float | None, text: str = "मला उद्या") -> httpx.Response:
    body: dict = {"request_id": "r", "transcript": text, "language_code": lang}
    if prob is not None:
        body["language_probability"] = prob
    return httpx.Response(200, json=body)


async def test_identify_posts_the_first_window_as_wav_with_unknown_language():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _stt_reply("mr-IN", 0.98)

    lid = _lid(handler, window_ms=2000)
    assert isinstance(lid, LanguageID) and lid.name == "sarvam"
    guess = await lid.identify(SEC * 3, HI_MR)

    assert guess == LangGuess("mr-IN", 0.98, "lid")
    req = seen[0]
    assert req.method == "POST"
    assert str(req.url) == "https://mock.sarvam/speech-to-text"
    assert req.headers["api-subscription-key"] == "sk-test"
    fields, file = _form(req)
    assert fields == {"model": "saaras:v3", "language_code": "unknown"}
    with wave.open(io.BytesIO(file)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert w.getnframes() == 32000  # clipped to the 2 s window


async def test_window_none_sends_the_whole_utterance():
    sizes: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        with wave.open(io.BytesIO(_form(req)[1])) as w:
            sizes.append(w.getnframes())
        return _stt_reply("hi-IN", 0.99)

    await _lid(handler, window_ms=None).identify(SEC * 3, HI_MR)
    assert sizes == [48000]


async def test_low_confidence_is_none_with_the_score_kept():
    lid = _lid(lambda req: _stt_reply("mr-IN", 0.55), threshold=0.8)
    assert await lid.identify(SEC, HI_MR) == LangGuess(None, 0.55, "lid")


async def test_a_language_outside_the_candidates_is_never_claimed():
    lid = _lid(lambda req: _stt_reply("ne-IN", 0.99))
    assert await lid.identify(SEC, HI_MR) == LangGuess(None, 0.99, "lid")
    bengali = _lid(lambda req: _stt_reply("bn-IN", 0.99))
    assert (await bengali.identify(SEC, SUPPORTED_LANGS)).lang is None


async def test_missing_probability_or_language_is_none():
    assert (await _lid(lambda req: _stt_reply("hi-IN", None)).identify(SEC, HI_MR)).lang is None
    assert await _lid(lambda req: _stt_reply(None, None, "")).identify(SEC, HI_MR) == LangGuess(
        None, 0.0, "lid"
    )


async def test_empty_audio_skips_the_call():
    def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request expected")

    assert await _lid(handler).identify(b"", HI_MR) == LangGuess(None, 0.0, "lid")


async def test_http_error_or_timeout_falls_back_to_none_and_logs(caplog):
    caplog.set_level(logging.WARNING)

    def boom(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "x"}})

    def slow(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    assert await _lid(boom).identify(SEC, HI_MR) == LangGuess(None, 0.0, "lid")
    assert await _lid(slow).identify(SEC, HI_MR) == LangGuess(None, 0.0, "lid")
    assert sum(r.getMessage() == "lid.error" for r in caplog.records) == 2


async def test_detect_returns_the_raw_answer_and_transcript():
    lid = _lid(lambda req: _stt_reply("gu-IN", 0.91, "મારે"))
    d = await lid.detect(SEC)
    assert (d.lang, d.confidence, d.transcript) == ("gu-IN", 0.91, "મારે")
    assert d.latency_ms >= 0


async def test_text_lid_posts_the_transcript():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == "https://mock.sarvam/text-lid"
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"language_code": "mr-IN", "script_code": "Deva"})

    assert await _lid(handler).text_lid("मला उद्या") == "mr-IN"
    assert seen == [{"input": "मला उद्या"}]


async def test_text_check_vetoes_a_disagreeing_speech_guess():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/text-lid":
            return httpx.Response(200, json={"language_code": "hi-IN", "script_code": "Deva"})
        return _stt_reply("mr-IN", 0.9)

    assert await _lid(handler, text_check=True).identify(SEC, HI_MR) == LangGuess(None, 0.9, "lid")


async def test_text_check_keeps_an_agreeing_speech_guess():
    paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        if req.url.path == "/text-lid":
            return httpx.Response(200, json={"language_code": "mr-IN", "script_code": "Deva"})
        return _stt_reply("mr-IN", 0.9)

    assert (await _lid(handler, text_check=True).identify(SEC, HI_MR)).lang == "mr-IN"
    assert paths == ["/speech-to-text", "/text-lid"]


async def test_measured_defaults_threshold_0_7_and_english_needs_0_95():
    """L8: on PSTN audio no Indic guess at >= 0.7 was wrong; the wrong guesses that pass
    0.7 are en-IN on loanword-only utterances ("Cash on delivery ₹400", 0.93 clean)."""
    assert (await _lid(lambda req: _stt_reply("mr-IN", 0.71)).identify(SEC, HI_MR)).lang == "mr-IN"
    assert (await _lid(lambda req: _stt_reply("mr-IN", 0.69)).identify(SEC, HI_MR)).lang is None
    en = ["en-IN", "ta-IN"]
    assert (await _lid(lambda req: _stt_reply("en-IN", 0.93)).identify(SEC, en)).lang is None
    assert (await _lid(lambda req: _stt_reply("en-IN", 0.96)).identify(SEC, en)).lang == "en-IN"


async def test_per_language_thresholds_are_configurable():
    lid = _lid(lambda req: _stt_reply("en-IN", 0.8), threshold=0.5, lang_thresholds={})
    assert (await lid.identify(SEC, ["en-IN"])).lang == "en-IN"
    lid = _lid(lambda req: _stt_reply("hi-IN", 0.8), lang_thresholds={"hi-IN": 0.9})
    assert await lid.identify(SEC, ["hi-IN"]) == LangGuess(None, 0.8, "lid")


async def test_model_is_configurable():
    models: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        models.append(_form(req)[0]["model"])
        return _stt_reply("hi-IN", 1.0)

    await _lid(handler, model="saaras:v4").identify(SEC, HI_MR)
    assert models == ["saaras:v4"]
