import asyncio

from dubhash.audio.frames import FRAME_BYTES, FRAME_SAMPLES, bytes_to_ms, ms_to_bytes
from dubhash.clock import FakeClock
from dubhash.config.pairs import find_pair, load_pairs
from dubhash.config.settings import Settings
from dubhash.events import EventBus
from dubhash.types import CallEvent, direction, other


def test_frame_constants():
    assert FRAME_SAMPLES == 320
    assert FRAME_BYTES == 640
    assert ms_to_bytes(100) == 3200
    assert bytes_to_ms(3200) == 100


def test_sides():
    assert other("a") == "b" and other("b") == "a"
    assert direction("a") == "ab" and direction("b") == "ba"


def test_fake_clock():
    c = FakeClock()
    c.advance(550)
    assert c.now_ms() == 550


def test_pairs_load():
    pairs = load_pairs(Settings().pairs_file)
    hk = pairs["hi-kn"]
    assert hk.a.lang == "hi-IN" and hk.b.tts == "bulbul"
    assert hk.eou_ms == 550 and hk.barge_in_min_ms == 1200 and hk.duck_db == -14
    assert hk.side("b").voice("f") == "bulbul:kn-female-1"
    assert find_pair(pairs, "hi-IN", "kn-IN") is hk


def test_all_bulbul_test_pair_for_live_bench():
    pairs = load_pairs(Settings().pairs_file)
    p = pairs["hi-kn-direct"]
    assert (p.a.lang, p.b.lang) == ("hi-IN", "kn-IN")
    assert p.a.tts == p.b.tts == "bulbul"
    assert find_pair(pairs, "hi-IN", "kn-IN") is pairs["hi-kn"]


def test_settings_keys_optional():
    s = Settings(_env_file=None)
    assert s.deepgram_api_key == ""


async def test_event_bus_filters_by_call():
    bus = EventBus()
    got: list[CallEvent] = []

    async def reader():
        async for ev in bus.subscribe("c_1"):
            got.append(ev)
            if len(got) == 2:
                return

    t = asyncio.create_task(reader())
    await asyncio.sleep(0)
    bus.publish(CallEvent("c_1", 0, "call", {"status": "ringing"}))
    bus.publish(CallEvent("c_2", 0, "call", {}))
    bus.publish(CallEvent("c_1", 5, "state", {"to": "IDLE"}))
    await asyncio.wait_for(t, 1)
    assert [e.ts_ms for e in got] == [0, 5]


def test_relative_pairs_file_resolves_from_any_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for rel in ("config/pairs.yaml", "dubhash/config/pairs.yaml"):
        s = Settings(_env_file=None, pairs_file=rel)
        assert s.pairs_file.exists(), rel


def test_exotel_host_empty_or_with_scheme_normalises():
    assert Settings(_env_file=None, exotel_api_host="").exotel_api_host == "api.exotel.com"
    h = Settings(_env_file=None, exotel_api_host="https://api.in.exotel.com/").exotel_api_host
    assert h == "api.in.exotel.com"


async def test_fake_clock_sleep_wakes_on_advance():
    c = FakeClock()
    woke: list[int] = []

    async def sleeper(ms: int) -> None:
        await c.sleep_ms(ms)
        woke.append(c.now_ms())

    tasks = [asyncio.create_task(sleeper(ms)) for ms in (300, 100)]
    await asyncio.sleep(0)
    c.advance(99)
    await asyncio.sleep(0)
    assert woke == []
    c.advance(1)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert woke == [100]
    c.set(400)
    await asyncio.gather(*tasks)
    assert woke == [100, 400]


async def test_fake_clock_sleep_cancel_is_clean():
    c = FakeClock()
    t = asyncio.create_task(c.sleep_ms(50))
    await asyncio.sleep(0)
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)
    c.advance(100)  # must not raise on the cancelled waiter


async def test_fake_clock_sleep_zero_returns():
    c = FakeClock()
    await c.sleep_ms(0)
