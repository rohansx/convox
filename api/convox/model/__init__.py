"""Domain models.

The vocabulary is small on purpose — everything in the CLI, the API, and the
dashboard is built from these nouns: a Target is the agent under test, a Persona
is who calls it, a Scenario is one test, a Trial is one call, and the artifacts a
Trial produces are the contract every downstream consumer reads.
"""

from convox.model.enums import (
    AssertionStatus,
    CallerMode,
    DisclosurePolicy,
    EndedBy,
    Intensity,
    InterruptionStyle,
    Layer,
    Speaker,
    TrialStatus,
    Verdict,
)
from convox.model.persona import DEFAULT_PERSONA, Persona
from convox.model.scenario import AssertionSpec, CallerSpec, Scenario, Suite
from convox.model.target import AdapterCapabilities, Target
from convox.model.trial import (
    ARTIFACT_VERSION,
    AssertionResult,
    AudioAnalysis,
    AudioTrack,
    BargeInEvent,
    CostEntry,
    Event,
    MetricValue,
    RunResult,
    ScenarioOutcome,
    ToolCall,
    TrialArtifacts,
    TrialResult,
    Turn,
)

__all__ = [
    "ARTIFACT_VERSION",
    "AdapterCapabilities",
    "AssertionResult",
    "AssertionSpec",
    "AssertionStatus",
    "AudioAnalysis",
    "AudioTrack",
    "BargeInEvent",
    "CallerMode",
    "CallerSpec",
    "CostEntry",
    "DEFAULT_PERSONA",
    "DisclosurePolicy",
    "EndedBy",
    "Event",
    "Intensity",
    "InterruptionStyle",
    "Layer",
    "MetricValue",
    "Persona",
    "RunResult",
    "Scenario",
    "ScenarioOutcome",
    "Speaker",
    "Suite",
    "Target",
    "ToolCall",
    "TrialArtifacts",
    "TrialResult",
    "TrialStatus",
    "Turn",
    "Verdict",
]
