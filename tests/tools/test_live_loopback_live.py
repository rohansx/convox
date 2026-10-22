"""Real-vendor loopback call (two lines). Deselected by default:
`STT_IMPL=real MT_IMPL=real TTS_IMPL=real uv run pytest -m live -s tests/tools`."""

import asyncio
import os
from pathlib import Path

import pytest

from convox.config.settings import Settings
from tools import live_loopback as ll

pytestmark = pytest.mark.live


def test_live_loopback_two_lines(tmp_path: Path):
    if not Settings(_env_file=Path(".env")).sarvam_api_key:  # type: ignore[call-arg]
        pytest.skip("no SARVAM_API_KEY in .env")
    if any(os.environ.get(k) != "real" for k in ("STT_IMPL", "MT_IMPL", "TTS_IMPL")):
        pytest.skip("set STT_IMPL=real MT_IMPL=real TTS_IMPL=real")
    utts = asyncio.run(ll.synth_script(ll.SCRIPT[:2], {"a": "hi-IN", "b": "kn-IN"}, ll.OUT))
    with ll.serve({"convox_env": "dev"}, Path(".env")) as srv:
        result = asyncio.run(ll.run_call(srv, utts, ll.Opts()))
    summary = ll.summarize(result)
    print(ll.table(result, summary))
    ll.write_outputs(result, summary, tmp_path)
    assert result["status"] == "completed"
    assert all(r.heard_ms is not None and r.server for r in result["rows"])
