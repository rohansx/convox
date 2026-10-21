import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).parents[2] / "dubhash" / "legs" / "static" / "loopback.html"
NODE = shutil.which("node")


def _scripts() -> tuple[str, str]:
    html = PAGE.read_text()
    worklet = re.search(
        r'<script id="capture-worklet" type="text/worklet">(.*?)</script>', html, re.S
    )
    main = re.search(r"<script>\s*(\(\(\) => \{.*?)</script>", html, re.S)
    assert worklet and main
    return worklet.group(1), main.group(1)


def test_page_is_self_contained_and_speaks_the_protocol():
    html = PAGE.read_text()
    assert "<script src" not in html and "<link" not in html
    for needle in (
        "qs.get('ws')",
        "qs.get('side')",
        "qs.get('lang')",
        "audioWorklet.addModule",
        "binaryType = 'arraybuffer'",
        "type: 'hello'",
        "type: 'dtmf'",
        "m.type === 'clear'",
        "sendJson({ type: 'mark'",
        'data-digit="1"',
        'data-digit="0"',
    ):
        assert needle in html, needle


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_page_scripts_parse(tmp_path):
    worklet, main = _scripts()
    for name, src in (("worklet.js", worklet), ("main.js", main)):
        f = tmp_path / name
        f.write_text(src)
        subprocess.run([NODE, "--check", str(f)], check=True, capture_output=True)


HARNESS = """
globalThis.sampleRate = 48000;
const procs = {};
globalThis.registerProcessor = (n, c) => { procs[n] = c; };
globalThis.AudioWorkletProcessor = class { constructor() {
  this.port = { sent: [], onmessage: null, postMessage(m) { this.sent.push(m); } }; } };
%WORKLET%
const out = {};
// capture: 60 ms of 48 kHz full-scale-ish input -> three 320-sample frames
const cap = new procs.capture();
const block = new Float32Array(128).fill(0.5);
for (let i = 0; i < 23; i++) cap.process([[block]]);
out.frames = cap.port.sent.map((m) => new Int16Array(m.pcm).length);
out.firstSample = new Int16Array(cap.port.sent[0].pcm)[0];
// player: 20 ms at 16k then a mark -> mark acked after ~960 output samples
const pl = new procs.player();
const send = (m) => pl.port.onmessage({ data: m });
send({ type: 'push', pcm: new Float32Array(320).fill(0.25).buffer });
send({ type: 'mark', name: 'a:1' });
let n = 0; const o = [[new Float32Array(128)]];
while (!pl.port.sent.length && n < 5000) { pl.process([], o); n += 128; }
out.markAfter = n; out.mark = pl.port.sent[0];
// clear drops queued audio and its mark
send({ type: 'push', pcm: new Float32Array(16000).buffer });
send({ type: 'mark', name: 'gone' });
send({ type: 'clear' });
pl.process([], o);
send({ type: 'mark', name: 'b:2' });
pl.process([], o);
out.afterClear = pl.port.sent.slice(1).map((m) => m.name);
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_worklets_downsample_play_ack_marks_and_clear(tmp_path):
    worklet, _ = _scripts()
    f = tmp_path / "harness.js"
    f.write_text(HARNESS.replace("%WORKLET%", worklet))
    res = subprocess.run([NODE, str(f)], check=True, capture_output=True, text=True, timeout=10)
    out = json.loads(res.stdout)
    assert out["frames"] == [320, 320, 320]
    assert abs(out["firstSample"] - 16383) <= 1
    assert out["mark"] == {"type": "mark", "name": "a:1"}
    assert 960 <= out["markAfter"] <= 960 + 128
    assert out["afterClear"] == ["b:2"]
