"""The `convox` command line — the primary interface.

The CLI comes before the dashboard on purpose: the people who adopt a testing tool
convert on a terminal command that finds a real bug, not on a screenshot.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from convox.adapters import registry as adapter_registry
from convox.cli import scaffold
from convox.eval import registry as assertion_registry
from convox.eval.evaluator import unsupported_summary
from convox.model.enums import TrialStatus, Verdict
from convox.model.target import Target
from convox.model.trial import RunResult, TrialResult
from convox.service.run import RunSpec, execute
from convox.sim.runner import RunOptions
from convox.spec import loader

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Testing and observability for voice AI agents.",
)
console = Console()

# Exit codes let CI tell "the agent is broken" apart from "the harness broke".
EXIT_OK = 0
EXIT_ASSERTION_FAILURE = 1
EXIT_REGRESSION = 2
EXIT_INFRASTRUCTURE = 3
EXIT_USAGE = 4


def _die(message: str, code: int = EXIT_USAGE) -> None:
    console.print(f"[bold red]error[/] {message}")
    raise typer.Exit(code)


def _resolve_target(project: loader.ProjectConfig, spec: str | None) -> Target:
    """Accept a project target name, or an inline ``kind:value`` shorthand."""
    if spec and spec in project.targets:
        config = dict(project.targets[spec])
        kind = config.pop("kind", None)
        if not kind:
            _die(f"target {spec!r} in convox.yaml has no `kind`")
        return Target(name=spec, kind=str(kind), config=config)

    if spec and ":" in spec:
        kind, _, value = spec.partition(":")
        if kind == "websocket":
            return Target(name=spec, kind="websocket", config={"url": value})
        return Target(name=spec, kind=kind, config={"id": value})

    if spec and spec.startswith("ws://") or spec and spec.startswith("wss://"):
        return Target(name=spec, kind="websocket", config={"url": spec})

    if not spec and len(project.targets) == 1:
        only = next(iter(project.targets))
        return _resolve_target(project, only)

    known = ", ".join(project.targets) or "none defined in convox.yaml"
    _die(f"unknown target {spec!r} (known: {known})")
    raise AssertionError("unreachable")


# ─── init ────────────────────────────────────────────────────────────────────


@app.command()
def init(
    directory: Path = typer.Argument(Path("."), help="Where to scaffold the project."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
) -> None:
    """Scaffold convox.yaml, a persona, and an example scenario."""
    created = scaffold.write_project(directory, force=force)
    for path in created:
        console.print(f"  [green]created[/] {path}")
    if not created:
        console.print("[yellow]nothing to do[/] — files already exist (use --force to overwrite)")
        return
    console.print(
        "\nNext:\n"
        "  1. [cyan]python -m convox.testing.reference_agent[/]   (a demo agent to test against)\n"
        "  2. [cyan]convox run scenarios/[/]\n"
    )


# ─── lint ────────────────────────────────────────────────────────────────────


@app.command()
def lint(
    path: Path = typer.Argument(Path("scenarios"), help="Scenario file or directory."),
) -> None:
    """Validate scenarios before spending call time on them."""
    project = loader.load_project()
    try:
        scenarios = loader.discover_scenarios(path)
    except loader.SpecError as exc:
        _die(str(exc))
        return

    personas = loader.load_personas(project.root / "personas")
    problems = 0
    warnings = 0

    for scenario in scenarios:
        where = scenario.source_path or scenario.name

        name = scenario.persona_name
        if name and name not in personas:
            console.print(f"[red]✗[/] {where}: unknown persona {name!r}")
            problems += 1

        if not scenario.assertions:
            console.print(f"[yellow]![/] {where}: no assertions — this scenario cannot fail")
            warnings += 1

        for spec in scenario.assertions:
            if not assertion_registry.is_registered(spec.name):
                console.print(f"[red]✗[/] {where}: unknown assertion {spec.name!r}")
                problems += 1
                continue
            capability = assertion_registry.required_capability(spec.name)
            if capability:
                console.print(
                    f"[yellow]![/] {where}: `{spec.name}` needs target capability "
                    f"`{capability}` — it will report as unsupported where unavailable"
                )
                warnings += 1

        fact_keys = set(scenario.caller.facts)
        for spec in scenario.assertions:
            if spec.name == "slot.captured" and isinstance(spec.value, dict):
                field = spec.value.get("field")
                if field and field not in fact_keys and "value" not in spec.value:
                    console.print(f"[red]✗[/] {where}: slot.captured references unknown fact {field!r}")
                    problems += 1

    total_trials = sum(s.repeat for s in scenarios)
    console.print(
        f"\n{len(scenarios)} scenario(s), {total_trials} trial(s) planned · "
        f"[red]{problems} error(s)[/], [yellow]{warnings} warning(s)[/]"
    )
    if problems:
        raise typer.Exit(EXIT_USAGE)


# ─── run ─────────────────────────────────────────────────────────────────────


@app.command()
def run(
    path: Path = typer.Argument(Path("scenarios"), help="Scenario file, suite, or directory."),
    target: str | None = typer.Option(None, "--target", "-t", help="Target name or kind:value."),
    repeat: int | None = typer.Option(None, "--repeat", "-r", help="Override repeats per scenario."),
    tag: list[str] = typer.Option([], "--tag", help="Only scenarios carrying this tag."),
    concurrency: int = typer.Option(4, "--concurrency", "-c", help="Trials in flight."),
    fast: bool = typer.Option(False, "--fast", help="Skip realistic speech pacing (CI smoke runs)."),
    json_out: Path | None = typer.Option(None, "--json", help="Write the full result bundle here."),
    junit: Path | None = typer.Option(None, "--junit", help="Write JUnit XML here."),
) -> None:
    """Run scenarios against a target."""
    project = loader.load_project()
    try:
        scenarios = loader.select(loader.discover_scenarios(path), tag or None)
    except loader.SpecError as exc:
        _die(str(exc))
        return

    if not scenarios:
        _die(f"no scenarios found in {path}")

    resolved = _resolve_target(project, target)
    personas = loader.load_personas(project.root / "personas")

    spec = RunSpec(
        target=resolved,
        scenarios=scenarios,
        personas=personas,
        repeat_override=repeat,
        options=RunOptions(realtime=not fast),
        concurrency=concurrency,
    )

    planned = sum(repeat or s.repeat for s in scenarios)
    console.print(
        f"\n[bold]{len(scenarios)}[/] scenario(s) × repeats = [bold]{planned}[/] trial(s) "
        f"· target [cyan]{resolved.name}[/] ({resolved.kind})\n"
    )

    try:
        result = asyncio.run(execute(spec))
    except KeyError as exc:
        _die(str(exc))
        return

    _print_report(result)

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(result.model_dump_json(indent=2))
        console.print(f"  results → {json_out}")
    if junit:
        from convox.report.junit import write_junit

        write_junit(result, junit)
        console.print(f"  junit   → {junit}")

    raise typer.Exit(_exit_code(result))


def _exit_code(result: RunResult) -> int:
    if any(t.artifacts.status == TrialStatus.INFRASTRUCTURE_ERROR for t in result.trials):
        return EXIT_INFRASTRUCTURE
    return EXIT_OK if result.ok else EXIT_ASSERTION_FAILURE


def _verdict_mark(verdict: Verdict) -> str:
    return {
        Verdict.PASS: "[green]✓[/]",
        Verdict.FAIL: "[red]✗[/]",
        Verdict.FLAKY: "[yellow]~[/]",
        Verdict.ERROR: "[magenta]![/]",
    }[verdict]


def _print_report(result: RunResult) -> None:
    for outcome in result.outcomes:
        mark = _verdict_mark(outcome.verdict)
        p95 = _median_metric(outcome.trials, "latency.response_ms.p95")
        latency = f"p95 {p95:.0f}ms" if p95 is not None else "—"
        detail = f"{outcome.passed}/{outcome.total}"
        if outcome.verdict != Verdict.PASS:
            detail += f"  pass^{outcome.total}={outcome.pass_k:.2f}"
        console.print(f"  {mark} [bold]{outcome.scenario_name:<34}[/] {detail:<22} {latency}")

        shown: set[str] = set()
        for trial in outcome.trials:
            if trial.artifacts.error:
                console.print(f"      [magenta]![/] {trial.artifacts.error}")
                break
            for failure in trial.failures:
                if failure.name in shown:
                    continue
                shown.add(failure.name)
                console.print(f"      [red]✗[/] {failure.name}  {failure.message}")
                if failure.suspected_layer:
                    console.print(f"        [dim]→ suspected layer: {failure.suspected_layer.value}[/]")

    unmeasured = unsupported_summary(result.trials)
    console.print(
        f"\n  {result.trials_passed}/{len(result.trials)} trials passed · "
        f"{result.scenarios_passed}/{len(result.outcomes)} scenarios green"
    )
    if result.cost_usd:
        console.print(f"  cost ${result.cost_usd:.4f}")
    if unmeasured:
        console.print(f"\n  [yellow]{len(unmeasured)} assertion(s) could not be measured:[/]")
        for name, reason in sorted(unmeasured.items()):
            console.print(f"    [dim]{name}: {reason}[/]")
    console.print()


def _median_metric(trials: list[TrialResult], name: str) -> float | None:
    values = [m.value for t in trials for m in t.metrics if m.name == name and m.value is not None]
    if not values:
        return None
    return sorted(values)[len(values) // 2]


# ─── targets ─────────────────────────────────────────────────────────────────

targets_app = typer.Typer(help="Inspect and verify targets.", no_args_is_help=True)
app.add_typer(targets_app, name="target")


@targets_app.command("list")
def target_list() -> None:
    """List targets defined in convox.yaml."""
    project = loader.load_project()
    if not project.targets:
        console.print("no targets defined in convox.yaml")
        return
    table = Table("name", "kind", "config")
    for name, config in project.targets.items():
        rest = {k: v for k, v in config.items() if k != "kind"}
        table.add_row(name, str(config.get("kind", "?")), json.dumps(rest))
    console.print(table)


@targets_app.command("test")
def target_test(name: str | None = typer.Argument(None)) -> None:
    """Place a short call to verify a target is reachable and configured."""
    project = loader.load_project()
    target = _resolve_target(project, name)
    adapter = adapter_registry.build(target)
    reachable, detail = asyncio.run(adapter.health_check())
    if reachable:
        console.print(f"[green]✓[/] {target.name} reachable")
        caps = ", ".join(k for k, v in adapter.capabilities.as_flags().items() if v) or "none"
        console.print(f"  capabilities: [dim]{caps}[/]")
        return
    console.print(f"[red]✗[/] {target.name} unreachable: {detail}")
    raise typer.Exit(EXIT_INFRASTRUCTURE)


# ─── introspection ───────────────────────────────────────────────────────────


@app.command("assertions")
def list_assertions() -> None:
    """List every available assertion and the capability it needs."""
    table = Table("assertion", "requires")
    for name in assertion_registry.names():
        table.add_row(name, assertion_registry.required_capability(name) or "[dim]—[/]")
    console.print(table)


@app.command("adapters")
def list_adapters() -> None:
    """List registered target adapters and their declared capabilities."""
    table = Table("kind", "capabilities")
    for kind in adapter_registry.kinds():
        caps = adapter_registry.capabilities_for(kind).as_flags()
        enabled = ", ".join(k for k, v in caps.items() if v) or "[dim]—[/]"
        table.add_row(kind, enabled)
    console.print(table)


def main() -> None:
    try:
        app()
    except loader.SpecError as exc:
        console.print(f"[bold red]error[/] {exc}")
        sys.exit(EXIT_USAGE)


if __name__ == "__main__":
    main()
