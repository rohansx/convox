import pytest

from dubhash.config.settings import Settings
from dubhash.providers.deepgram_stt import DeepgramSTT
from dubhash.providers.eleven_tts import ElevenTTS
from dubhash.providers.fakes import FakeLID
from dubhash.providers.registry import (
    ProviderUnavailable,
    build_lid,
    build_mt,
    build_stt,
    build_tts,
)
from dubhash.providers.sarvam_lid import SarvamLID
from dubhash.providers.sarvam_mt import SarvamMT
from dubhash.providers.sarvam_stt import SarvamSTT
from dubhash.providers.sarvam_stt_translate import SarvamSTTTranslate
from dubhash.providers.sarvam_tts import BulbulTTS


def _settings(**kw) -> Settings:
    base = dict(
        _env_file=None,
        dubhash_env="dev",
        stt_impl="real",
        mt_impl="real",
        tts_impl="real",
        sarvam_api_key="",
        elevenlabs_api_key="",
        deepgram_api_key="",
    )
    base.update(kw)
    return Settings(**base)


def test_builds_real_stt_providers():
    s = _settings(sarvam_api_key="sk", deepgram_api_key="dg")
    assert isinstance(build_stt("sarvam", s), SarvamSTT)
    assert isinstance(build_stt("deepgram", s), DeepgramSTT)
    assert isinstance(build_stt("sarvam-translate", s), SarvamSTTTranslate)


def test_builds_real_mt_and_tts_providers():
    s = _settings(sarvam_api_key="sk", elevenlabs_api_key="xi")
    assert isinstance(build_mt("sarvam", s), SarvamMT)
    assert isinstance(build_tts("bulbul", s), BulbulTTS)
    assert isinstance(build_tts("eleven", s), ElevenTTS)


@pytest.mark.parametrize(
    ("build", "name", "env"),
    [
        (build_stt, "sarvam", "SARVAM_API_KEY"),
        (build_stt, "sarvam-translate", "SARVAM_API_KEY"),
        (build_stt, "deepgram", "DEEPGRAM_API_KEY"),
        (build_mt, "sarvam", "SARVAM_API_KEY"),
        (build_tts, "bulbul", "SARVAM_API_KEY"),
        (build_tts, "eleven", "ELEVENLABS_API_KEY"),
    ],
)
def test_missing_key_raises_provider_unavailable_naming_the_env_var(build, name, env):
    with pytest.raises(ProviderUnavailable, match=env):
        build(name, _settings())


@pytest.mark.parametrize("build", [build_stt, build_mt, build_tts])
def test_unknown_name_raises_provider_unavailable(build):
    with pytest.raises(ProviderUnavailable, match="nope"):
        build("nope", _settings(sarvam_api_key="sk", elevenlabs_api_key="xi"))


def test_build_lid_real_fake_and_errors():
    assert isinstance(build_lid("sarvam", _settings(sarvam_api_key="sk")), SarvamLID)
    # LID follows STT_IMPL (it is a speech provider) and DUBHASH_ENV=test
    assert isinstance(build_lid("sarvam", _settings(stt_impl="fake")), FakeLID)
    assert isinstance(build_lid("sarvam", _settings(dubhash_env="test")), FakeLID)
    assert isinstance(build_lid("fake", _settings()), FakeLID)
    with pytest.raises(ProviderUnavailable, match="SARVAM_API_KEY"):
        build_lid("sarvam", _settings())
    with pytest.raises(ProviderUnavailable, match="nope"):
        build_lid("nope", _settings(sarvam_api_key="sk"))
