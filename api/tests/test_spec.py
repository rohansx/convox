"""The scenario format is the public contract, so parsing it is tested like one."""

import pytest

from convox.model.scenario import Say, WaitSilence
from convox.spec import loader


def write(tmp_path, relative, content):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


SCENARIO = """version: convox/v1
name: refill
persona: impatient
mode: scripted
tags: [critical, hindi]

caller:
  goal: Refill a prescription.
  facts:
    phone: "+91 98765 43210"
  opening: "Hi, I need a refill."
  script:
    - say: "Metformin 500"
    - wait_silence: { ms: 2000 }

assert:
  - tool.called: create_refill_order
  - latency.response_ms: { p95: { lt: 1200 } }
  - call.no_error_frames
  - soft: { talk_ratio: { between: [0.3, 0.7] } }

repeat: 4
"""


def test_scenario_round_trips_from_yaml(tmp_path):
    path = write(tmp_path, "scenarios/refill.yaml", SCENARIO)
    scenario = loader.load_scenario(path)

    assert scenario.name == "refill"
    assert scenario.persona_name == "impatient"
    assert scenario.repeat == 4
    assert scenario.tags == ["critical", "hindi"]
    assert isinstance(scenario.caller.script[0], Say)
    assert isinstance(scenario.caller.script[1], WaitSilence)
    assert scenario.source_path == str(path)


def test_all_three_assertion_shapes_parse(tmp_path):
    scenario = loader.load_scenario(write(tmp_path, "s.yaml", SCENARIO))
    by_name = {a.name: a for a in scenario.assertions}

    # single-key mapping
    assert by_name["tool.called"].value == "create_refill_order"
    # nested comparison
    assert by_name["latency.response_ms"].value == {"p95": {"lt": 1200}}
    # bare string
    assert by_name["call.no_error_frames"].value is True
    # soft wrapper preserves the inner assertion and marks it non-fatal
    assert by_name["talk_ratio"].soft is True
    assert by_name["talk_ratio"].value == {"between": [0.3, 0.7]}


def test_unknown_keys_are_rejected_with_the_file_named(tmp_path):
    path = write(tmp_path, "bad.yaml", "version: convox/v1\nname: x\nnonsense: 1\n")
    with pytest.raises(loader.SpecError) as exc:
        loader.load_scenario(path)
    assert "bad.yaml" in str(exc.value)
    assert "nonsense" in str(exc.value)


def test_wrong_spec_version_is_rejected(tmp_path):
    path = write(tmp_path, "old.yaml", "version: convox/v0\nname: x\n")
    with pytest.raises(loader.SpecError, match="unsupported spec version"):
        loader.load_scenario(path)


def test_malformed_assertion_entry_names_the_file(tmp_path):
    path = write(tmp_path, "bad.yaml", "version: convox/v1\nname: x\nassert:\n  - [1, 2]\n")
    with pytest.raises(loader.SpecError) as exc:
        loader.load_scenario(path)
    assert "single-key mapping" in str(exc.value)


def test_discovery_walks_directories_and_skips_suites(tmp_path):
    write(tmp_path, "scenarios/a.yaml", "version: convox/v1\nname: a\n")
    write(tmp_path, "scenarios/nested/b.yaml", "version: convox/v1\nname: b\n")
    write(tmp_path, "scenarios/x.suite.yaml", "version: convox/v1\nname: x\nscenarios: [a.yaml]\n")

    found = {s.name for s in loader.discover_scenarios(tmp_path / "scenarios")}
    assert found == {"a", "b"}


def test_suite_expands_members_and_applies_its_repeat(tmp_path):
    write(tmp_path, "scenarios/a.yaml", "version: convox/v1\nname: a\nrepeat: 1\n")
    write(tmp_path, "scenarios/b.yaml", "version: convox/v1\nname: b\nrepeat: 1\n")
    suite = write(
        tmp_path,
        "scenarios/critical.suite.yaml",
        "version: convox/v1\nname: critical\nrepeat: 5\nscenarios: [a.yaml, b.yaml]\n",
    )

    scenarios = loader.discover_scenarios(suite)
    assert [s.name for s in scenarios] == ["a", "b"]
    assert all(s.repeat == 5 for s in scenarios)


def test_tag_and_focus_selection(tmp_path):
    write(tmp_path, "s/a.yaml", "version: convox/v1\nname: a\ntags: [critical]\n")
    write(tmp_path, "s/b.yaml", "version: convox/v1\nname: b\ntags: [slow]\n")
    write(tmp_path, "s/c.yaml", "version: convox/v1\nname: c\nskip: true\n")
    scenarios = loader.discover_scenarios(tmp_path / "s")

    assert {s.name for s in loader.select(scenarios)} == {"a", "b"}
    assert {s.name for s in loader.select(scenarios, ["critical"])} == {"a"}

    scenarios[1].only = True
    assert {s.name for s in loader.select(scenarios)} == {"b"}


def test_scenario_hash_tracks_content_not_location(tmp_path):
    first = loader.load_scenario(write(tmp_path, "one/refill.yaml", SCENARIO))
    second = loader.load_scenario(write(tmp_path, "two/refill.yaml", SCENARIO))
    assert loader.scenario_hash(first) == loader.scenario_hash(second)

    changed = loader.load_scenario(
        write(tmp_path, "three/refill.yaml", SCENARIO.replace("repeat: 4", "repeat: 9"))
    )
    assert loader.scenario_hash(changed) != loader.scenario_hash(first)


def test_persona_loads_with_defaults_filled_in(tmp_path):
    path = write(
        tmp_path,
        "personas/p.yaml",
        "version: convox/v1\nname: impatient\nspeech_rate: 1.4\ninterruption:\n  style: frequent\n  probability: 0.6\n",
    )
    persona = loader.load_persona(path)

    assert persona.name == "impatient"
    assert persona.speech_rate == 1.4
    assert persona.interruption.style == "frequent"
    assert persona.patience_turns == 12  # default preserved


def test_default_persona_is_always_available(tmp_path):
    personas = loader.load_personas(tmp_path / "does-not-exist")
    assert "default" in personas


def test_project_config_discovers_targets(tmp_path):
    write(
        tmp_path,
        "convox.yaml",
        "version: convox/v1\nproject: demo\ntargets:\n  demo:\n    kind: websocket\n    url: ws://x\n",
    )
    project = loader.load_project(tmp_path / "convox.yaml")

    assert project.project == "demo"
    assert project.targets["demo"]["kind"] == "websocket"
    assert project.root == tmp_path
