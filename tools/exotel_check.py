"""Check the Exotel setup in .env without placing a call (no credits spent).

    uv run python tools/exotel_check.py

Checks: required env values, API key/token against Balance.json, the ExoPhone belongs to
the account, the flow URL shape (http://my.exotel.com/{sid}/exoml/start_voice/{app_id}),
and that PUBLIC_BASE_URL is publicly reachable (Exotel must reach /telephony/exotel/stream
and /ws/leg; localhost will not work, use a tunnel or the VM).
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from convox.config.settings import Settings  # noqa: E402

REQUIRED = {
    "EXOTEL_ACCOUNT_SID": "exotel_account_sid",
    "EXOTEL_API_KEY": "exotel_api_key",
    "EXOTEL_API_TOKEN": "exotel_api_token",
    "EXOTEL_EXOPHONE": "exotel_exophone",
    "EXOTEL_FLOW_URL": "exotel_flow_url",
}
FLOW_RE = re.compile(r"^https?://my\.exotel\.com/(?P<sid>[^/]+)/exoml/start_voice/(?P<app>\d+)$")


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def _error(resp: httpx.Response) -> str:
    try:
        msg = (resp.json().get("RestException") or {}).get("Message")
    except ValueError:
        msg = None
    return msg or f"http {resp.status_code}"


async def run_checks(settings: Settings, *, client: httpx.AsyncClient) -> list[Check]:
    out: list[Check] = []
    missing = [env for env, attr in REQUIRED.items() if not getattr(settings, attr)]
    out.append(Check("env", not missing, "missing: " + ", ".join(missing) if missing else "ok"))

    m = FLOW_RE.match(settings.exotel_flow_url.strip())
    if not settings.exotel_flow_url:
        out.append(Check("flow_url", False, "EXOTEL_FLOW_URL is empty"))
    elif not m:
        out.append(
            Check(
                "flow_url", False, "expected http://my.exotel.com/{sid}/exoml/start_voice/{app_id}"
            )
        )
    elif m["sid"] != settings.exotel_account_sid:
        out.append(Check("flow_url", False, f"flow sid {m['sid']!r} != account sid"))
    else:
        out.append(Check("flow_url", True, f"app_id {m['app']}"))

    public = settings.public_base_url
    if re.search(r"//(localhost|127\.|0\.0\.0\.0)", public):
        out.append(Check("public_url", False, f"{public} is localhost; Exotel cannot reach it"))
    else:
        try:
            r = await client.get(f"{public.rstrip('/')}/healthz", timeout=5)
            ok = r.status_code == 200
            out.append(
                Check("public_url", ok, "reachable" if ok else f"/healthz http {r.status_code}")
            )
        except httpx.HTTPError as e:
            out.append(Check("public_url", False, f"unreachable: {e!r}"))

    if missing and any(
        env in missing for env in ("EXOTEL_ACCOUNT_SID", "EXOTEL_API_KEY", "EXOTEL_API_TOKEN")
    ):
        return out
    base = f"https://{settings.exotel_api_host}/v1/Accounts/{settings.exotel_account_sid}"
    auth = (settings.exotel_api_key, settings.exotel_api_token)
    try:
        r = await client.get(f"{base}/Balance.json", auth=auth, timeout=10)
        out.append(Check("auth", r.status_code == 200, "ok" if r.status_code == 200 else _error(r)))
        if settings.exotel_exophone:
            r = await client.get(
                f"{base}/Numbers/{settings.exotel_exophone}.json", auth=auth, timeout=10
            )
            ok = r.status_code == 200
            out.append(Check("exophone", ok, "ok" if ok else _error(r)))
    except httpx.HTTPError as e:
        out.append(Check("auth", False, f"exotel unreachable: {e!r}"))
    return out


async def _main() -> int:
    settings = Settings()
    async with httpx.AsyncClient() as client:
        results = await run_checks(settings, client=client)
    for r in results:
        print(f"{'ok ' if r.ok else 'FAIL'}  {r.name:<11} {r.detail}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
