import httpx

from dubhash.config.settings import Settings
from tools.exotel_check import run_checks


def _settings(**kw) -> Settings:
    base = dict(
        _env_file=None,
        exotel_account_sid="rohan28",
        exotel_api_key="k",
        exotel_api_token="t",
        exotel_exophone="08047000000",
        exotel_flow_url="http://my.exotel.com/rohan28/exoml/start_voice/7",
        public_base_url="https://dub.example.com",
        edge_wss_base="wss://dub.example.com",
    )
    base.update(kw)
    return Settings(**base)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_all_good():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/Numbers/08047000000.json"):
            return httpx.Response(200, json={"Numbers": {"PhoneNumber": "08047000000"}})
        if req.url.path.endswith("/Balance.json"):
            return httpx.Response(200, json={"Balance": {"Amount": "150"}})
        if req.url.host == "dub.example.com" and req.url.path == "/healthz":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    results = await run_checks(_settings(), client=_client(handler))
    assert all(r.ok for r in results), results


async def test_reports_missing_values_without_calling_exotel():
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={})

    results = await run_checks(
        _settings(exotel_api_key="", exotel_flow_url=""), client=_client(handler)
    )
    by = {r.name: r for r in results}
    assert not by["env"].ok
    assert "EXOTEL_API_KEY" in by["env"].detail and "EXOTEL_FLOW_URL" in by["env"].detail
    assert not any("exotel" in str(c.url) for c in calls)


async def test_bad_credentials_and_localhost_flagged():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"RestException": {"Message": "Authentication failed"}})

    results = await run_checks(
        _settings(public_base_url="https://localhost:8443", edge_wss_base="wss://localhost:8443"),
        client=_client(handler),
    )
    by = {r.name: r for r in results}
    assert not by["auth"].ok and "Authentication failed" in by["auth"].detail
    assert not by["public_url"].ok and "localhost" in by["public_url"].detail


async def test_flow_url_must_match_account_sid():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    results = await run_checks(
        _settings(exotel_flow_url="http://my.exotel.com/other/exoml/start_voice/7"),
        client=_client(handler),
    )
    by = {r.name: r for r in results}
    assert not by["flow_url"].ok
