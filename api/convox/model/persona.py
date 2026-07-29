"""Persona: who is calling, and how they behave.

A persona is a reusable bundle of voice, language, and behaviour that is applied
to a scenario at run time. Fields that only affect the audio path (noise, codec,
device) are carried here even where the current transport ignores them, so that
persona files stay valid as audio support lands.
"""

from pydantic import BaseModel, ConfigDict, Field

from convox.model.enums import Intensity, InterruptionStyle


class VoiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: "none" renders no audio — used while the caller runs on a text channel.
    provider: str = "none"
    voice_id: str = "default"
    language: str = "en-IN"
    accent: str | None = None


class CodeSwitchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secondary: str
    #: Fraction of tokens drawn from the secondary language.
    density: float = Field(default=0.3, ge=0.0, le=1.0)
    granularity: str = "intra_sentence"  # sentence | intra_sentence


class PauseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thinking_ms: tuple[int, int] = (200, 600)
    max_gap_ms: int = 1500


class BackchannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    probability: float = Field(default=0.0, ge=0.0, le=1.0)
    phrases: list[str] = Field(default_factory=lambda: ["mm-hm", "yeah", "right"])


class InterruptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: InterruptionStyle = InterruptionStyle.NEVER
    probability: float = Field(default=0.0, ge=0.0, le=1.0)
    #: How far into the agent's turn the caller starts speaking.
    delay_ms_range: tuple[int, int] = (400, 1200)


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    noise_profile: str | None = None
    snr_db: float = 30.0
    device: str = "handset"
    room: str | None = None


class ChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codec: str = "none"  # none | g711u | g711a | g722 | gsm | opus
    sample_rate: int = 16000
    network: str | None = None


class HangupPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on_unproductive_turns: int | None = None


class Persona(BaseModel):
    """A reusable synthetic-caller definition."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None

    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    language: str = "en-IN"
    code_switch: CodeSwitchConfig | None = None

    speech_rate: float = Field(default=1.0, ge=0.25, le=3.0)
    volume_db: float = 0.0
    emotion: str = "calm"
    disfluency: Intensity = Intensity.NONE

    pause: PauseConfig = Field(default_factory=PauseConfig)
    backchannel: BackchannelConfig = Field(default_factory=BackchannelConfig)

    comprehension: str = "normal"
    verbosity: str = "normal"

    interruption: InterruptionConfig = Field(default_factory=InterruptionConfig)
    patience_turns: int = 12
    hangup: HangupPolicy | None = None

    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    channel: ChannelConfig = Field(default_factory=ChannelConfig)


#: Used when a scenario names no persona. Deliberately neutral: no interruptions,
#: no noise, normal pace — so a bare scenario measures the agent, not the caller.
DEFAULT_PERSONA = Persona(name="default", description="Neutral cooperative caller.")
