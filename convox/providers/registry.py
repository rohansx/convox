"""Provider errors and selection by name.

build_* return the real client for a vendor name, or the fake when the name is "fake",
the matching *_IMPL setting is "fake", or CONVOX_ENV=test. Vendor modules and fakes are
imported lazily so a missing optional dependency never breaks the others.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from convox.providers.errors import ProviderError, ProviderUnavailable  # noqa: F401

if TYPE_CHECKING:
    from convox.config.settings import Settings
    from convox.interp.contracts import MT, STT, TTS
    from convox.interp.langid import LanguageID


def _key(value: str, env: str, name: str) -> str:
    if not value:
        raise ProviderUnavailable(f"{name} needs {env} in the environment or .env")
    return value


def _unknown(kind: str, name: str, known: tuple[str, ...]) -> ProviderUnavailable:
    return ProviderUnavailable(f"unknown {kind} provider {name!r}; expected one of {known}")


def build_stt(name: str, settings: Settings) -> STT:
    if name == "fake" or settings.stt_impl == "fake" or settings.forces_fakes:
        from convox.providers.fakes import FakeSTT

        return FakeSTT()
    if name == "sarvam":
        from convox.providers.sarvam_stt import SarvamSTT

        return SarvamSTT(_key(settings.sarvam_api_key, "SARVAM_API_KEY", name))
    if name == "sarvam-translate":
        from convox.providers.sarvam_stt_translate import SarvamSTTTranslate

        return SarvamSTTTranslate(_key(settings.sarvam_api_key, "SARVAM_API_KEY", name))
    if name == "deepgram":
        from convox.providers.deepgram_stt import DeepgramSTT

        return DeepgramSTT(_key(settings.deepgram_api_key, "DEEPGRAM_API_KEY", name))
    raise _unknown("stt", name, ("sarvam", "sarvam-translate", "deepgram", "fake"))


def build_mt(name: str, settings: Settings) -> MT:
    if name == "fake" or settings.mt_impl == "fake" or settings.forces_fakes:
        from convox.providers.fakes import FakeMT

        return FakeMT()
    if name == "sarvam":
        from convox.providers.sarvam_mt import SarvamMT

        return SarvamMT(_key(settings.sarvam_api_key, "SARVAM_API_KEY", name))
    raise _unknown("mt", name, ("sarvam", "fake"))


def build_tts(name: str, settings: Settings) -> TTS:
    if name == "fake" or settings.tts_impl == "fake" or settings.forces_fakes:
        from convox.providers.fakes import FakeTTS

        return FakeTTS()
    if name == "bulbul":
        from convox.providers.sarvam_tts import BulbulTTS

        return BulbulTTS(_key(settings.sarvam_api_key, "SARVAM_API_KEY", name))
    if name == "eleven":
        from convox.providers.eleven_tts import ElevenTTS

        return ElevenTTS(_key(settings.elevenlabs_api_key, "ELEVENLABS_API_KEY", name))
    raise _unknown("tts", name, ("bulbul", "eleven", "fake"))


def build_lid(name: str, settings: Settings) -> LanguageID:
    """Spoken-language ID for lang "auto". A speech provider, so it follows STT_IMPL."""
    if name == "fake" or settings.stt_impl == "fake" or settings.forces_fakes:
        from convox.providers.fakes import FakeLID

        return FakeLID()
    if name == "sarvam":
        from convox.providers.sarvam_lid import SarvamLID

        return SarvamLID(_key(settings.sarvam_api_key, "SARVAM_API_KEY", name))
    raise _unknown("lid", name, ("sarvam", "fake"))
