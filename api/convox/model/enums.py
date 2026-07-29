"""Enumerations shared across the domain model."""

from enum import StrEnum


class CallerMode(StrEnum):
    """How the synthetic caller decides what to say."""

    SCRIPTED = "scripted"
    AGENTIC = "agentic"
    HYBRID = "hybrid"


class Speaker(StrEnum):
    CALLER = "caller"
    AGENT = "agent"


class TrialStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    SKIPPED = "skipped"


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    FLAKY = "flaky"
    ERROR = "error"


class AssertionStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    #: The target platform cannot supply the data this assertion needs.
    #: Never treated as a pass — runs report unsupported counts separately.
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class EndedBy(StrEnum):
    AGENT = "agent"
    CALLER = "caller"
    TIMEOUT = "timeout"
    ERROR = "error"


class Layer(StrEnum):
    """Pipeline layer implicated in a failure."""

    STT = "stt"
    LLM = "llm"
    TTS = "tts"
    TOOL = "tool"
    TIMING = "timing"
    NONE = "none"


class InterruptionStyle(StrEnum):
    NEVER = "never"
    POLITE = "polite"
    FREQUENT = "frequent"
    AGGRESSIVE = "aggressive"


class Intensity(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DisclosurePolicy(StrEnum):
    """How readily the caller volunteers facts from its fact sheet."""

    NEVER = "never"
    ON_REQUEST = "on_request"
    EAGERLY = "eagerly"
