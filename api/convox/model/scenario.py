"""Scenario: one test — a persona, a goal, and assertions about what should happen."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from convox.model.enums import CallerMode, DisclosurePolicy
from convox.model.persona import Persona

# ─── Script steps ────────────────────────────────────────────────────────────


class _Step(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Say(_Step):
    kind: Literal["say"] = "say"
    text: str


class SayFact(_Step):
    """Speak a value from the caller's fact sheet, formatted for speech."""

    kind: Literal["say_facts"] = "say_facts"
    field: str


class WaitForAgent(_Step):
    kind: Literal["wait_for_agent"] = "wait_for_agent"
    timeout_ms: int = 15_000


class WaitSilence(_Step):
    """Stay deliberately silent — probes VAD and silence-timeout handling."""

    kind: Literal["wait_silence"] = "wait_silence"
    ms: int = 3000


class BargeIn(_Step):
    kind: Literal["barge_in"] = "barge_in"
    after_ms: int = 800
    say: str


class Backchannel(_Step):
    kind: Literal["backchannel"] = "backchannel"
    text: str = "mm-hm"
    after_ms: int = 500


class Dtmf(_Step):
    kind: Literal["dtmf"] = "dtmf"
    digits: str
    tone_ms: int = 120
    gap_ms: int = 80


class Hangup(_Step):
    kind: Literal["hangup"] = "hangup"


ScriptStep = Annotated[
    Say | SayFact | WaitForAgent | WaitSilence | BargeIn | Backchannel | Dtmf | Hangup,
    Field(discriminator="kind"),
]


class ScriptFormatError(ValueError):
    """A `script` entry is not a recognised step."""


#: Scalar shorthand: ``- say: "hello"`` fills this field on the step.
_STEP_SCALAR_FIELD = {
    "say": "text",
    "say_facts": "field",
    "dtmf": "digits",
    "wait_silence": "ms",
    "wait_for_agent": "timeout_ms",
    "backchannel": "text",
    "barge_in": "say",
    "hangup": None,
}


def parse_script_entries(raw: Any) -> list[dict[str, Any]]:
    """Parse a `script:` block from its public YAML form.

    Steps are written as single-key mappings — ``- say: "Metformin 500"`` or
    ``- barge_in: {after_ms: 800, say: "No, 850"}`` — and are expanded here into
    the tagged form the discriminated union expects. Keeping this on the model
    means the documented format and the parsed format cannot drift apart.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ScriptFormatError("`script` must be a list")

    steps: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, _Step):
            steps.append(entry)
            continue
        if isinstance(entry, str):
            steps.append({"kind": entry})
            continue
        if not isinstance(entry, dict) or len(entry) != 1:
            if isinstance(entry, dict) and "kind" in entry:
                steps.append(entry)
                continue
            raise ScriptFormatError(
                f"each script step must be a single-key mapping or a string, got: {entry!r}"
            )

        ((kind, value),) = entry.items()
        if kind not in _STEP_SCALAR_FIELD:
            known = ", ".join(sorted(_STEP_SCALAR_FIELD))
            raise ScriptFormatError(f"unknown script step {kind!r} (known: {known})")

        if isinstance(value, dict):
            steps.append({"kind": kind, **value})
        elif value is None or value is True:
            steps.append({"kind": kind})
        else:
            scalar_field = _STEP_SCALAR_FIELD[kind]
            if scalar_field is None:
                steps.append({"kind": kind})
            else:
                steps.append({"kind": kind, scalar_field: value})
    return steps


# ─── Caller ──────────────────────────────────────────────────────────────────


class CallerBehavior(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volunteer_facts: DisclosurePolicy = DisclosurePolicy.ON_REQUEST
    #: Turns without progress before the caller gives up.
    patience_turns: int | None = None
    persistence: str = "normal"
    off_topic_probability: float = Field(default=0.0, ge=0.0, le=1.0)


class CallerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str | None = None
    #: Facts the caller knows. These are the ground truth for slot assertions.
    facts: dict[str, Any] = Field(default_factory=dict)
    opening: str | None = None
    script: list[ScriptStep] = Field(default_factory=list)
    handoff_after_turn: int | None = None
    behavior: CallerBehavior = Field(default_factory=CallerBehavior)

    @field_validator("script", mode="before")
    @classmethod
    def _parse_script(cls, raw: Any) -> Any:
        return parse_script_entries(raw)


# ─── Assertions ──────────────────────────────────────────────────────────────


class AssertionSpec(BaseModel):
    """A single assertion, parsed from one entry of the scenario's `assert` list.

    YAML entries are single-key mappings (``- tool.called: create_order``), so the
    key becomes ``name`` and the value becomes ``value``. ``soft`` assertions are
    recorded but never fail a trial.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    value: Any = None
    soft: bool = False

    def describe(self) -> str:
        if self.value is None or self.value is True:
            return self.name
        return f"{self.name}: {self.value}"


class AssertionFormatError(ValueError):
    """An `assert` entry is not a recognised shape."""


def parse_assertion_entries(raw: Any) -> list[AssertionSpec]:
    """Parse an `assert:` block from its public YAML form.

    Entries are single-key mappings (``- tool.called: create_order``) or bare
    strings (``- audio.no_truncation``). ``soft:`` wraps an entry so it is
    recorded without failing the trial.

    This lives on the model rather than in the loader so that the format has one
    definition — the file loader, the API, and the SDK cannot drift apart.
    """
    if raw is None:
        return []
    if isinstance(raw, AssertionSpec):
        return [raw]
    if not isinstance(raw, list):
        raise AssertionFormatError("`assert` must be a list")

    specs: list[AssertionSpec] = []
    for entry in raw:
        if isinstance(entry, AssertionSpec):
            specs.append(entry)
        elif isinstance(entry, str):
            specs.append(AssertionSpec(name=entry, value=True))
        elif isinstance(entry, dict) and len(entry) == 1:
            ((name, value),) = entry.items()
            if name == "soft":
                for spec in parse_assertion_entries([value]):
                    specs.append(spec.model_copy(update={"soft": True}))
            elif set(entry) == {"name"} or "name" in entry:
                specs.append(AssertionSpec.model_validate(entry))
            else:
                specs.append(AssertionSpec(name=name, value=value))
        elif isinstance(entry, dict) and "name" in entry:
            specs.append(AssertionSpec.model_validate(entry))
        else:
            raise AssertionFormatError(
                f"each assertion must be a single-key mapping or a string, got: {entry!r}"
            )
    return specs


# ─── Scenario ────────────────────────────────────────────────────────────────


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    description: str | None = None

    persona: str | Persona | None = None
    mode: CallerMode = CallerMode.HYBRID

    caller: CallerSpec = Field(default_factory=CallerSpec)
    assertions: list[AssertionSpec] = Field(default_factory=list, alias="assert")

    repeat: int = Field(default=1, ge=1)
    timeout_s: int = Field(default=300, ge=1)
    max_turns: int = Field(default=40, ge=1)
    max_cost_usd: float | None = None

    tags: list[str] = Field(default_factory=list)
    matrix: dict[str, list[Any]] | None = None

    skip: bool = False
    only: bool = False

    #: Set by the loader — where this scenario came from, for error reporting.
    source_path: str | None = None

    @field_validator("assertions", mode="before")
    @classmethod
    def _parse_assertions(cls, raw: Any) -> Any:
        return parse_assertion_entries(raw)

    @property
    def persona_name(self) -> str | None:
        if isinstance(self.persona, str):
            return self.persona
        if isinstance(self.persona, Persona):
            return self.persona.name
        return None


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    target: str | None = None
    repeat: int | None = None
    scenarios: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)
    fail_on: str = "any-failure"  # any-failure | regression | threshold
    thresholds: dict[str, Any] = Field(default_factory=dict)
    source_path: str | None = None
