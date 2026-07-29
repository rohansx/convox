"""Target: the agent under test, and how to reach it."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AdapterCapabilities(BaseModel):
    """What a target platform can actually provide.

    Declared per adapter and carried into the trial bundle. The evaluation engine
    reads these to decide whether an assertion can be measured at all — an
    assertion whose inputs the platform cannot supply reports ``unsupported``
    rather than silently passing. Declaring a capability the adapter does not
    honour is a contract-test failure.
    """

    model_config = ConfigDict(extra="forbid")

    #: Bidirectional audio (as opposed to a text-framed channel).
    audio: bool = False
    #: Platform exposes its own transcript of what it heard.
    agent_transcript: bool = False
    tool_calls: bool = False
    dtmf: bool = False
    recording: bool = False
    traces: bool = False
    #: Can receive a call placed by the agent (for testing outbound agents).
    inbound: bool = False
    #: Caller can interrupt mid-utterance and the stop can be measured.
    barge_in: bool = False

    max_concurrency: int | None = None

    def as_flags(self) -> dict[str, bool]:
        return {
            k: v
            for k, v in self.model_dump().items()
            if isinstance(v, bool)
        }


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    config: dict[str, Any] = Field(default_factory=dict)
    #: Agent prompt/version captured at run time, so a regression can be diffed
    #: against the configuration change that caused it.
    snapshot: dict[str, Any] | None = None
