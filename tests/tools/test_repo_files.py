"""Keep the Makefile, CI workflow and top-level docs honest."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    "install",
    "test",
    "test-live",
    "lint",
    "fmt",
    "dev",
    "up",
    "down",
    "bench",
    "smoke",
    "fixtures",
    "label",
)


def _makefile_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text()
    return set(re.findall(r"^([a-z][a-z0-9-]*):", text, flags=re.M))


def test_makefile_has_targets():
    assert set(TARGETS) <= _makefile_targets()


def test_makefile_dev_runs_one_uvicorn_worker_with_reload():
    text = (ROOT / "Makefile").read_text()
    assert "uvicorn dubhash.api.app:app" in text
    assert "--reload" in text
    assert "--workers" not in text or "--workers 1" in text


def test_makefile_recipes_use_tabs():
    for line in (ROOT / "Makefile").read_text().splitlines():
        if line.startswith(" ") and line.strip() and not line.strip().startswith("#"):
            pytest.fail(f"recipe line indented with spaces: {line!r}")


def test_ci_runs_lint_format_and_offline_tests():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = [s for job in wf["jobs"].values() for s in job["steps"]]
    runs = " ".join(s.get("run", "") for s in steps)
    uses = " ".join(s.get("uses", "") for s in steps)
    assert "astral-sh/setup-uv" in uses
    assert "uv sync" in runs
    assert "ruff check" in runs
    assert "ruff format --check" in runs
    assert "pytest" in runs
    assert "-m live" not in runs  # live tests never run in CI


@pytest.mark.parametrize("doc", ["README.md", "docs/engineering/dev-setup.md"])
def test_relative_links_resolve(doc):
    path = ROOT / doc
    text = path.read_text()
    links = re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text)
    assert links, f"no links found in {doc}"
    for link in links:
        if re.match(r"^[a-z]+://", link) or link.startswith("mailto:"):
            continue
        assert (path.parent / link).exists(), f"{doc}: broken link {link}"


def test_dev_setup_mentions_real_commands():
    text = (ROOT / "docs" / "engineering" / "dev-setup.md").read_text()
    for cmd in ("make test", "make dev", "make smoke", "uv sync", "tools/label.py"):
        assert cmd in text
    assert "proposed" not in text.lower().split("## ")[0]  # intro no longer says proposed
