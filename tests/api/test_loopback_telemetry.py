"""The /ws/loopback socket pings the phone page and logs what its telemetry says (L7), so
a real-phone test shows heard latency even without the call recorder."""

from __future__ import annotations

import json
import logging

from dubhash.api import loopback
from tests.api.test_loopback import Env, _close_clients, wait_for  # noqa: F401


def test_the_socket_pings_with_the_session_clock_and_logs_telemetry(monkeypatch, caplog):
    monkeypatch.setattr(loopback, "PING_INTERVAL_S", 0.02)
    caplog.set_level(logging.INFO, logger="dubhash.api.loopback")
    env = Env()
    call_id = env.client.post(
        "/loopback/calls", json={"a_lang": "mr-IN", "b_lang": "hi-IN"}
    ).json()["call_id"]
    with env.client.websocket_connect(f"/ws/loopback?call={call_id}&side=a") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "ping" and isinstance(msg["server_ms"], int)
        ws.send_text(
            json.dumps(
                {
                    "type": "telemetry",
                    "kind": "pong",
                    "server_ms": msg["server_ms"],
                    "client_ms": 1.7e12,
                }
            )
        )
        ws.send_text(
            json.dumps(
                {
                    "type": "telemetry",
                    "kind": "play_start",
                    "tag": "b:0",
                    "client_ms": 1.7e12,
                    "audio_ctx_ms": 5.0,
                }
            )
        )

        def logged() -> list[logging.LogRecord]:
            return [r for r in caplog.records if r.getMessage() == "leg.telemetry"]

        wait_for(lambda: len(logged()) >= 2)
    pong, start = logged()[:2]
    assert pong.kind == "pong" and pong.rtt_ms >= 0 and pong.call_id == call_id  # type: ignore[attr-defined]
    assert start.kind == "play_start" and start.tag == "b:0" and start.side == "a"  # type: ignore[attr-defined]
