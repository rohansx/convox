"""Load and validate scenario, persona, suite, and project files.

The YAML format is the product's public contract, so parsing is strict: unknown
keys are errors, not warnings. Every failure is reported with the file it came
from, because a validation error you cannot locate is worse than no validation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from convox.model.persona import DEFAULT_PERSONA, Persona
from convox.model.scenario import (
    AssertionFormatError,
    AssertionSpec,
    Scenario,
    ScriptFormatError,
    Suite,
    parse_assertion_entries,
)

SPEC_VERSION = "convox/v1"

SCENARIO_GLOBS = ("*.yaml", "*.yml")
SUITE_SUFFIXES = (".suite.yaml", ".suite.yml")


class SpecError(Exception):
    """A scenario, persona, or project file could not be loaded."""

    def __init__(self, path: Path | str, message: str) -> None:
        self.path = str(path)
        self.message = message
        super().__init__(f"{self.path}: {message}")


class ProjectConfig(BaseModel):
    """`convox.yaml` — project-level defaults and target definitions."""

    model_config = ConfigDict(extra="forbid")

    version: str = SPEC_VERSION
    project: str = "convox"
    targets: dict[str, dict[str, Any]] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)
    caller: dict[str, Any] = Field(default_factory=dict)
    judge: dict[str, Any] = Field(default_factory=dict)
    reporting: dict[str, Any] = Field(default_factory=dict)

    root: Path = Field(default=Path("."), exclude=True)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise SpecError(path, f"invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SpecError(path, "expected a mapping at the top level")
    return raw


def _check_version(path: Path, raw: dict[str, Any]) -> None:
    version = raw.pop("version", SPEC_VERSION)
    if version != SPEC_VERSION:
        raise SpecError(path, f"unsupported spec version {version!r} (expected {SPEC_VERSION!r})")


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


# ─── assertions ──────────────────────────────────────────────────────────────


def parse_assertions(raw: Any, path: Path | str) -> list[AssertionSpec]:
    """Parse an `assert:` block, reporting the file a malformed entry came from."""
    try:
        return parse_assertion_entries(raw)
    except AssertionFormatError as exc:
        raise SpecError(path, str(exc)) from exc


# ─── personas ────────────────────────────────────────────────────────────────


def load_persona(path: Path) -> Persona:
    raw = _read_yaml(path)
    _check_version(path, raw)
    try:
        return Persona.model_validate(raw)
    except ValidationError as exc:
        raise SpecError(path, _format_validation_error(exc)) from exc


def load_personas(directory: Path) -> dict[str, Persona]:
    personas: dict[str, Persona] = {DEFAULT_PERSONA.name: DEFAULT_PERSONA}
    if not directory.is_dir():
        return personas
    for pattern in SCENARIO_GLOBS:
        for path in sorted(directory.glob(pattern)):
            persona = load_persona(path)
            personas[persona.name] = persona
    return personas


# ─── scenarios ───────────────────────────────────────────────────────────────


def scenario_hash(scenario: Scenario) -> str:
    """Content hash — identifies a scenario across runs for caching and diffing."""
    payload = scenario.model_dump(mode="json", exclude={"source_path"})
    encoded = repr(sorted(payload.items())).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def load_scenario(path: Path) -> Scenario:
    raw = _read_yaml(path)
    _check_version(path, raw)
    raw["assert"] = parse_assertions(raw.get("assert"), path)
    raw.setdefault("name", path.stem)
    try:
        scenario = Scenario.model_validate(raw)
    except ValidationError as exc:
        raise SpecError(path, _format_validation_error(exc)) from exc
    except (AssertionFormatError, ScriptFormatError) as exc:
        raise SpecError(path, str(exc)) from exc
    scenario.source_path = str(path)
    return scenario


def load_suite(path: Path) -> Suite:
    raw = _read_yaml(path)
    _check_version(path, raw)
    raw.setdefault("name", path.stem.removesuffix(".suite"))
    try:
        suite = Suite.model_validate(raw)
    except ValidationError as exc:
        raise SpecError(path, _format_validation_error(exc)) from exc
    suite.source_path = str(path)
    return suite


def is_suite_file(path: Path) -> bool:
    return any(path.name.endswith(suffix) for suffix in SUITE_SUFFIXES)


def discover_scenarios(target: Path) -> list[Scenario]:
    """Load scenarios from a file, a suite file, or a directory tree."""
    if not target.exists():
        raise SpecError(target, "path does not exist")

    if target.is_file():
        if is_suite_file(target):
            suite = load_suite(target)
            scenarios: list[Scenario] = []
            for ref in suite.scenarios:
                ref_path = (target.parent / ref).resolve()
                scenarios.extend(discover_scenarios(ref_path))
            if suite.repeat is not None:
                for scenario in scenarios:
                    scenario.repeat = suite.repeat
            return scenarios
        return [load_scenario(target)]

    scenarios = []
    for pattern in SCENARIO_GLOBS:
        for path in sorted(target.rglob(pattern)):
            if is_suite_file(path):
                continue
            scenarios.append(load_scenario(path))
    return scenarios


def select(scenarios: list[Scenario], tags: list[str] | None = None) -> list[Scenario]:
    """Apply skip/only flags and tag filters, in that order of precedence."""
    focused = [s for s in scenarios if s.only]
    pool = focused or [s for s in scenarios if not s.skip]
    if tags:
        wanted = set(tags)
        pool = [s for s in pool if wanted & set(s.tags)]
    return pool


# ─── project ─────────────────────────────────────────────────────────────────


def find_project(start: Path | None = None) -> Path | None:
    """Walk upward looking for `convox.yaml`."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        config = candidate / "convox.yaml"
        if config.is_file():
            return config
    return None


def load_project(path: Path | None = None) -> ProjectConfig:
    config_path = path or find_project()
    if config_path is None:
        return ProjectConfig(root=Path.cwd())
    raw = _read_yaml(config_path)
    version = raw.get("version", SPEC_VERSION)
    if version != SPEC_VERSION:
        raise SpecError(config_path, f"unsupported spec version {version!r}")
    try:
        project = ProjectConfig.model_validate(raw)
    except ValidationError as exc:
        raise SpecError(config_path, _format_validation_error(exc)) from exc
    project.root = config_path.parent
    return project
