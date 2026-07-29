"""CLI behaviour, including the exit codes CI depends on."""

import asyncio
import threading

import pytest
from typer.testing import CliRunner

from convox.cli.main import EXIT_ASSERTION_FAILURE, EXIT_INFRASTRUCTURE, EXIT_OK, EXIT_USAGE, app
from convox.testing.reference_agent import Bugs, ReferenceAgent

runner = CliRunner()


@pytest.fixture
def agent_server():
    """Run a reference agent in a background thread with its own event loop.

    Threaded rather than in-process because CliRunner drives ``asyncio.run``, and
    a server sharing that loop would deadlock. Shutdown is cooperative so teardown
    stays silent — warnings from our own suite are exactly the noise this project
    exists to eliminate.
    """
    running: list[tuple[asyncio.AbstractEventLoop, asyncio.Event, threading.Thread]] = []

    def start(bugs: Bugs | None = None) -> str:
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        url: dict[str, str] = {}
        stop: dict[str, asyncio.Event] = {}

        async def serve_forever() -> None:
            from websockets.asyncio.server import serve

            stop["event"] = asyncio.Event()
            agent = ReferenceAgent(bugs)
            async with serve(agent.handle, "127.0.0.1", 0) as server:
                host, port = next(iter(server.sockets)).getsockname()[:2]
                url["value"] = f"ws://{host}:{port}"
                ready.set()
                await stop["event"].wait()

        def main() -> None:
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(serve_forever())
            finally:
                loop.close()

        thread = threading.Thread(target=main, daemon=True)
        thread.start()
        assert ready.wait(timeout=5), "reference agent did not start"
        running.append((loop, stop["event"], thread))
        return url["value"]

    yield start

    for loop, stop_event, thread in running:
        loop.call_soon_threadsafe(stop_event.set)
        thread.join(timeout=5)


def scaffold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "."])
    assert result.exit_code == EXIT_OK, result.output
    return tmp_path


def test_init_scaffolds_a_runnable_project(tmp_path, monkeypatch):
    scaffold(tmp_path, monkeypatch)

    assert (tmp_path / "convox.yaml").is_file()
    assert (tmp_path / "personas/impatient_mobile.yaml").is_file()
    assert (tmp_path / "scenarios/refill_happy_path.yaml").is_file()
    assert (tmp_path / "scenarios/audio/refill_over_audio.yaml").is_file()

    # The scaffolded project must lint clean out of the box.
    assert runner.invoke(app, ["lint", "scenarios/"]).exit_code == EXIT_OK


def test_scaffolded_audio_scenario_is_skipped_until_a_target_exists(tmp_path, monkeypatch, agent_server):
    """A fresh project must be green against the demo agent it ships with.

    The audio example needs an audio target, so it stays skipped rather than
    failing on the first command a new user runs.
    """
    scaffold(tmp_path, monkeypatch)
    url = agent_server()
    result = runner.invoke(app, ["run", "scenarios/", "--target", f"websocket:{url}", "--fast"])

    assert result.exit_code == EXIT_OK, result.output
    assert "refill_over_audio" not in result.output


def test_init_is_idempotent_without_force(tmp_path, monkeypatch):
    scaffold(tmp_path, monkeypatch)
    result = runner.invoke(app, ["init", "."])
    assert "nothing to do" in result.output


def test_lint_rejects_an_unknown_assertion(tmp_path, monkeypatch):
    scaffold(tmp_path, monkeypatch)
    (tmp_path / "scenarios/bad.yaml").write_text(
        "version: convox/v1\nname: bad\nassert:\n  - not.a.real.assertion: true\n"
    )
    result = runner.invoke(app, ["lint", "scenarios/"])

    assert result.exit_code == EXIT_USAGE
    assert "unknown assertion" in result.output


def test_lint_warns_when_a_scenario_cannot_fail(tmp_path, monkeypatch):
    scaffold(tmp_path, monkeypatch)
    (tmp_path / "scenarios/empty.yaml").write_text("version: convox/v1\nname: empty\n")
    result = runner.invoke(app, ["lint", "scenarios/"])

    assert result.exit_code == EXIT_OK
    assert "cannot fail" in result.output


def test_run_against_a_healthy_agent_exits_zero(tmp_path, monkeypatch, agent_server):
    scaffold(tmp_path, monkeypatch)
    url = agent_server()
    result = runner.invoke(app, ["run", "scenarios/", "--target", f"websocket:{url}", "--fast"])

    assert result.exit_code == EXIT_OK, result.output
    assert "1/1 scenarios green" in result.output
    # Unmeasured checks are always surfaced, never quietly dropped.
    assert "could not be measured" in result.output


def test_run_against_a_buggy_agent_fails_with_evidence(tmp_path, monkeypatch, agent_server):
    scaffold(tmp_path, monkeypatch)
    url = agent_server(Bugs(mangle_digits=True))
    result = runner.invoke(app, ["run", "scenarios/", "--target", f"websocket:{url}", "--fast"])

    assert result.exit_code == EXIT_ASSERTION_FAILURE
    assert "slot.captured" in result.output
    assert "pass^" in result.output
    assert "suspected layer" in result.output


def test_unreachable_target_exits_with_the_infrastructure_code(tmp_path, monkeypatch):
    """CI must be able to tell a broken agent from a broken harness."""
    scaffold(tmp_path, monkeypatch)
    result = runner.invoke(
        app, ["run", "scenarios/", "--target", "websocket:ws://127.0.0.1:1", "--fast"]
    )
    assert result.exit_code == EXIT_INFRASTRUCTURE


def test_run_writes_junit_and_json(tmp_path, monkeypatch, agent_server):
    scaffold(tmp_path, monkeypatch)
    url = agent_server()
    result = runner.invoke(
        app,
        [
            "run", "scenarios/", "--target", f"websocket:{url}", "--fast",
            "--junit", "out/junit.xml", "--json", "out/result.json",
        ],
    )

    assert result.exit_code == EXIT_OK, result.output
    junit = (tmp_path / "out/junit.xml").read_text()
    assert "<testsuite" in junit and 'name="refill_happy_path"' in junit
    assert (tmp_path / "out/result.json").is_file()


def test_unknown_target_is_a_usage_error(tmp_path, monkeypatch):
    scaffold(tmp_path, monkeypatch)
    result = runner.invoke(app, ["run", "scenarios/", "--target", "nope"])
    assert result.exit_code == EXIT_USAGE


def test_assertions_and_adapters_are_discoverable(tmp_path, monkeypatch):
    scaffold(tmp_path, monkeypatch)

    assertions = runner.invoke(app, ["assertions"])
    assert "slot.captured" in assertions.output
    assert "barge_in.stop_ms" in assertions.output

    adapters = runner.invoke(app, ["adapters"])
    assert "websocket" in adapters.output
