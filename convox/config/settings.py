"""Environment settings. Missing vendor keys never crash startup; the provider just
can't be selected and fakes keep working."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # DUBHASH_ENV: the product's old name, still read so an old .env keeps working
    convox_env: Literal["dev", "test", "prod"] = Field(
        "dev", validation_alias=AliasChoices("convox_env", "dubhash_env")
    )
    public_base_url: str = "http://localhost:8000"
    edge_wss_base: str = "ws://localhost:8000"
    pairs_file: Path = PACKAGE_DIR / "config" / "pairs.yaml"
    default_provider: Literal["exotel", "plivo", "webrtc", "fake"] = "exotel"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://convox:convox@localhost:5432/convox"
    redis_url: str = "redis://localhost:6379/0"

    sarvam_api_key: str = ""
    elevenlabs_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"
    llm_repair_enabled: bool = False
    deepgram_api_key: str = ""

    exotel_account_sid: str = ""
    exotel_api_key: str = ""
    exotel_api_token: str = ""
    exotel_api_host: str = "api.exotel.com"  # Singapore; India: api.in.exotel.com
    exotel_exophone: str = ""
    exotel_flow_url: str = ""
    exotel_dialin_number: str = ""
    exotel_ws_basic_user: str = ""
    exotel_ws_basic_pass: str = ""
    exotel_allowed_ips: str = ""

    plivo_auth_id: str = ""
    plivo_auth_token: str = ""
    plivo_number: str = ""
    plivo_stream_content_type: str = "audio/x-l16;rate=16000"

    platform_api_keys: str = "dev:change-me"
    webhook_signing_secret: str = "change-me"
    console_password: str = "change-me"

    stt_impl: Literal["real", "fake"] = "fake"
    mt_impl: Literal["real", "fake"] = "fake"
    tts_impl: Literal["real", "fake"] = "fake"
    enable_webrtc_leg: bool = True
    bench_realtime: bool = False
    # The opening line per call kind (L7): "short" = the two language names and "speak"
    # (~1.5-2 s), "full" = the disclosure sentence (+ record sentence), "off" = none.
    loopback_opening_line: Literal["short", "full", "off"] = "short"  # /loopback and /join
    pstn_opening_line: Literal["short", "full", "off"] = "full"  # POST /v1/calls
    # lang "auto" (docs/tech-spec/language-detection.md): how an auto side's STT opens
    # ("guess": its best language so far; "auto": Sarvam's language_code=auto, whose finals
    # report a language, unreliable on short lines, L8) and the shortest utterance decided
    # on (L8: 2.0 s).
    auto_stt_open: Literal["guess", "auto"] = "guess"
    lang_min_speech_ms: int = 2000

    # Per-call recording for diagnosis (convox/store/recorder.py): mic and playout wavs,
    # every CallEvent, client telemetry and a summary under call_recording_dir. Dev and
    # test only (consent, DPDP): None means "on in dev, off otherwise"; never on by
    # default in prod.
    call_recording: bool | None = None
    call_recording_dir: Path = Path("out/calls")
    log_file: Path | None = Path("logs/convox.log")  # JSON lines, rotating; empty = off

    # Umami page analytics on the public pages (landing, live demo); off unless both are set.
    umami_website_id: str = ""
    umami_script_url: str = ""

    # Lobby waitlist form on the landing page (github.com/rohansx/lobby); off unless both are
    # set. The project key is the public pk_ key, safe in a browser.
    lobby_project_key: str = ""
    lobby_script_url: str = ""

    # Phrase / audio cache (L10). Every call's TTS goes through a byte-bounded LRU shared by
    # the process; phrase-pack audio is also written to tts_cache_dir (empty = memory only).
    tts_cache_mb: int = 64
    tts_cache_dir: Path | None = Path("out/tts_cache")
    # Prefill the call's phrase pack (config/phrases/<vertical>.yaml) into the MT and TTS
    # caches in the background at call creation, at most this many vendor requests/s.
    phrase_prefill: bool = True
    phrase_prefill_rps: float = 4.0
    # While a translation plays to a leg, open that leg's STT socket and the TTS socket
    # for its reply's voice, so the likely next turn pays no connect (L10).
    next_turn_prewarm: bool = True

    @field_validator("exotel_api_host")
    @classmethod
    def _exotel_host(cls, v: str) -> str:
        v = v.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
        return v or "api.exotel.com"

    @field_validator("pairs_file")
    @classmethod
    def _resolve_pairs_file(cls, v: Path) -> Path:
        """Relative paths resolve against the cwd, then the repo root, then the package."""
        if v.is_absolute() or v.exists():
            return v
        for base in (PACKAGE_DIR.parent, PACKAGE_DIR):
            if (base / v).exists():
                return base / v
        return v

    @field_validator("log_file", "tts_cache_dir", mode="before")
    @classmethod
    def _empty_log_file(cls, v: object) -> object:
        return None if v == "" else v

    @model_validator(mode="after")
    def _recording_default(self) -> Settings:
        if self.call_recording is None:
            self.call_recording = self.convox_env == "dev"
        return self

    @property
    def forces_fakes(self) -> bool:
        return self.convox_env == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
