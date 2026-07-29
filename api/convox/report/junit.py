"""JUnit XML output, so CI renders Convox results natively.

Each scenario is a test case. A flaky scenario is reported as a failure rather
than a pass, with its ``pass^k`` in the message: a voice agent that works three
times out of five is not a passing test, and rounding that up is how regressions
reach production.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from convox.model.enums import Verdict
from convox.model.trial import RunResult, ScenarioOutcome


def _failure_text(outcome: ScenarioOutcome) -> str:
    lines = [f"pass^{outcome.total} = {outcome.pass_k:.2f} ({outcome.passed}/{outcome.total} trials passed)"]
    for trial in outcome.trials:
        if trial.artifacts.error:
            lines.append(f"  [repeat {trial.artifacts.repeat_index}] error: {trial.artifacts.error}")
        for failure in trial.failures:
            lines.append(f"  [repeat {trial.artifacts.repeat_index}] {failure.name}: {failure.message}")
            if failure.suspected_layer:
                lines.append(f"      suspected layer: {failure.suspected_layer.value}")
    return "\n".join(lines)


def build_tree(result: RunResult) -> ET.ElementTree:
    total_ms = sum(t.artifacts.duration_ms for t in result.trials)
    failures = sum(1 for o in result.outcomes if o.verdict in (Verdict.FAIL, Verdict.FLAKY))
    errors = sum(1 for o in result.outcomes if o.verdict == Verdict.ERROR)

    suite = ET.Element(
        "testsuite",
        name=f"convox:{result.target_name}",
        tests=str(len(result.outcomes)),
        failures=str(failures),
        errors=str(errors),
        time=f"{total_ms / 1000:.3f}",
        timestamp=result.started_at,
    )

    for outcome in result.outcomes:
        duration = sum(t.artifacts.duration_ms for t in outcome.trials) / 1000
        case = ET.SubElement(
            suite,
            "testcase",
            classname=result.target_name,
            name=outcome.scenario_name,
            time=f"{duration:.3f}",
        )
        match outcome.verdict:
            case Verdict.ERROR:
                node = ET.SubElement(case, "error", message="infrastructure error")
                node.text = _failure_text(outcome)
            case Verdict.FAIL | Verdict.FLAKY:
                label = "failed" if outcome.verdict == Verdict.FAIL else "flaky"
                node = ET.SubElement(case, "failure", message=f"{label}: pass^{outcome.total}={outcome.pass_k:.2f}")
                node.text = _failure_text(outcome)
            case _:
                unmeasured = {a.name for t in outcome.trials for a in t.unsupported}
                if unmeasured:
                    out = ET.SubElement(case, "system-out")
                    out.text = "unmeasured assertions: " + ", ".join(sorted(unmeasured))

    return ET.ElementTree(suite)


def write_junit(result: RunResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = build_tree(result)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
