"""Real-vendor smoke run. Deselected by default; `uv run pytest -m live -s tests/tools`."""

from pathlib import Path

import pytest

from tools import smoke

pytestmark = pytest.mark.live


def test_live_smoke(tmp_path: Path):
    keys = smoke.load_keys(Path(".env"))
    if not (keys.sarvam or keys.eleven):
        pytest.skip("no SARVAM_API_KEY or ELEVENLABS_API_KEY in .env")
    assert smoke.main(["--fast", "--out", str(tmp_path)]) == 0
