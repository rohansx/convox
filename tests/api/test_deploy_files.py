from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_compose_services_and_single_worker():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    assert set(services) == {"app", "postgres", "redis", "caddy"}
    assert services["postgres"]["image"] == "postgres:16"
    assert services["redis"]["image"] == "redis:7"
    assert services["caddy"]["image"] == "caddy:2"
    cmd = " ".join(services["app"]["command"])
    assert "dubhash.api.app:app" in cmd and "--workers 1" in cmd
    assert all(" " not in arg for arg in services["app"]["command"])  # one argv item each
    # only caddy is exposed; the stores stay on the compose network
    assert "ports" not in services["postgres"] and "ports" not in services["redis"]
    assert "ports" not in services["app"]
    assert {"pgdata", "caddy_data"} <= set(compose["volumes"])


def test_caddyfile_serves_both_domains_without_buffering():
    caddy = (ROOT / "Caddyfile").read_text()
    assert "{$EDGE_DOMAIN" in caddy and "{$API_DOMAIN" in caddy
    assert "edge.dubhash.in" in caddy and "api.dubhash.in" in caddy
    assert "reverse_proxy app:8000" in caddy
    assert "flush_interval -1" in caddy


def test_dockerfile_is_slim_uv_and_non_root():
    df = (ROOT / "Dockerfile").read_text()
    assert "FROM python:3.12-slim" in df
    assert "uv sync" in df and "--frozen" in df
    assert "\nUSER " in df and "USER root" not in df
    assert "--workers" in df and "1" in df
