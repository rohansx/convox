from collections.abc import Callable

import pytest

from convox.interp.contracts import MT
from convox.interp.mt_cache import CachedMT
from convox.providers.fakes import FakeMT

IMPLS: list = [
    pytest.param(lambda: FakeMT(), id="fake"),
    pytest.param(lambda: CachedMT(FakeMT()), id="cached-fake"),
]


@pytest.fixture(params=IMPLS)
def mt(request) -> MT:
    factory: Callable[[], MT] = request.param
    return factory()


async def test_satisfies_protocol(mt: MT):
    assert isinstance(mt, MT)
    assert isinstance(mt.name, str) and mt.name


@pytest.mark.parametrize(
    "text,src,tgt",
    [
        ("bhaiya main gate ke bahar hoon", "hi-IN", "kn-IN"),
        ("naanu gate hattira iddini", "kn-IN", "hi-IN"),
        ("flat 402", "hi-IN", "ta-IN"),
    ],
)
async def test_translate_returns_non_empty_str(mt: MT, text: str, src: str, tgt: str):
    out = await mt.translate(text, src, tgt)
    assert isinstance(out, str) and out.strip()
