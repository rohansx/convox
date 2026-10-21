"""Provider sockets are opened when the call is created, not on the first utterance (L7):
STT per source side, TTS per target side, the MT HTTP connection. Again at activation
(both phones joined), since a call can ring for longer than an idle socket lives."""

from __future__ import annotations

import asyncio

import httpx

from dubhash.interp.mt_cache import CachedMT
from dubhash.providers.fakes import FakeMT, FakeSTT, FakeTTS
from dubhash.providers.sarvam_mt import SarvamMT
from dubhash.session.providers import Providers, prewarm
from tests.session.test_call_session import PAIR, attach_both, make, providers


class WarmSTT(FakeSTT):
    def __init__(self) -> None:
        super().__init__()
        self.warmed: list[str] = []
        self.closed = 0

    async def prewarm(self, lang: str) -> None:
        self.warmed.append(lang)

    async def aclose(self) -> None:
        self.closed += 1


class WarmTTS(FakeTTS):
    def __init__(self) -> None:
        super().__init__()
        self.warmed: list[str] = []

    async def prewarm(self, voice: str) -> None:
        self.warmed.append(voice)


class WarmMT(FakeMT):
    def __init__(self) -> None:
        super().__init__()
        self.warmed = 0

    async def prewarm(self) -> None:
        self.warmed += 1


def warm_providers() -> Providers:
    p = providers(stt={"a": WarmSTT(), "b": WarmSTT()}, tts={"a": WarmTTS(), "b": WarmTTS()})
    p.mt = CachedMT(WarmMT())  # (an empty CachedMT is falsy: providers() would drop it)
    return p


async def spin(n: int = 10) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def test_prewarm_opens_every_provider_of_the_pair():
    p = warm_providers()
    await prewarm(p, PAIR)
    for s in ("a", "b"):
        assert p.stt[s].warmed == [PAIR.side(s).lang]  # type: ignore[attr-defined]
        assert p.tts[s].warmed == [PAIR.side(s).voice_f]  # type: ignore[attr-defined]
    assert p.mt.inner.warmed == 1  # type: ignore[attr-defined]


async def test_prewarm_failures_are_logged_not_raised(caplog):
    p = warm_providers()

    async def boom(lang: str) -> None:
        raise ConnectionError("sarvam down")

    p.stt["a"].prewarm = boom  # type: ignore[attr-defined]
    await prewarm(p, PAIR)
    assert p.stt["b"].warmed  # type: ignore[attr-defined]
    assert any("provider.prewarm_failed" in r.getMessage() for r in caplog.records)


async def test_providers_without_prewarm_are_skipped():
    await prewarm(providers(), PAIR)  # plain fakes: nothing to do, no error


async def test_session_prewarms_on_creation_and_again_at_activation():
    p = warm_providers()
    session, legs, clock, bus = make(prov=p)
    session.start_prewarm()
    await spin()
    assert p.stt["a"].warmed == [PAIR.a.lang]  # type: ignore[attr-defined]
    await attach_both(session, legs)
    await spin()
    assert p.stt["a"].warmed == [PAIR.a.lang, PAIR.a.lang]  # type: ignore[attr-defined]
    await session.hangup()


async def test_a_call_that_never_connects_closes_its_prewarmed_sockets():
    p = warm_providers()
    session, legs, clock, bus = make(prov=p)
    session.start_prewarm()
    await spin()
    await session.attach_leg("a", legs["a"])
    await session.hangup("no_answer")
    assert p.stt["a"].closed == 1 and p.stt["b"].closed == 1  # type: ignore[attr-defined]


async def test_the_session_factory_prewarms():
    from types import SimpleNamespace

    from dubhash.api.deps import get_session_factory
    from dubhash.config.settings import Settings
    from dubhash.events import EventBus

    settings = Settings(_env_file=None, dubhash_env="test")  # type: ignore[call-arg]
    state = SimpleNamespace(settings=settings, bus=EventBus(), writer=None)
    factory = get_session_factory(SimpleNamespace(app=SimpleNamespace(state=state)))  # type: ignore[arg-type]
    session = factory("c_x", PAIR)
    assert session.prewarm_task is not None
    await session.hangup()


# --- Sarvam MT: a warm HTTPS connection that outlives the pauses of a conversation -----


async def test_sarvam_mt_prewarm_opens_the_connection():
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(f"{req.method} {req.url.path}")
        return httpx.Response(404)

    mt = SarvamMT("k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await mt.prewarm()
    assert seen == ["HEAD /"]


async def test_sarvam_mt_prewarm_never_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    mt = SarvamMT("k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await mt.prewarm()


def test_sarvam_mt_keeps_idle_connections_for_a_minute():
    """httpx drops an idle keep-alive connection after 5 s by default, so most turns of a
    real conversation paid a fresh TLS handshake to Sarvam (~150-300 ms)."""
    mt = SarvamMT("k")
    pool = mt._client._transport._pool  # type: ignore[attr-defined]
    assert pool._keepalive_expiry >= 60
