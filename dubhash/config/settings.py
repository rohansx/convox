"""Environment settings. Missing vendor keys never crash startup; the provider just
can't be selected and fakes keep working."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    dubhash_env: Literal["dev", "test", "prod"] = "dev"
    public_base_url: str = "http://localhost:8000"
    edge_wss_base: str = "ws://localhost:8000"
    pairs_file: Path = PACKAGE_DIR / "config" / "pairs.yaml"
    default_provider: Literal["exotel", "plivo", "webrtc", "fake"] = "exotel"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://dubhash:dubhash@localhost:5432/dubhash"
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
    exotel_api_host: str = ""
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

    @property
    def forces_fakes(self) -> bool:
        return self.dubhash_env == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
