import pytest
from httpx import ASGITransport, AsyncClient

from convox.app import create_app
from convox.model.scenario import Say, Scenario
from convox.model.target import Target
from convox.sim.runner import RunOptions


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def fast_options():
    """Skip realistic speech pacing so the suite runs in seconds, not minutes."""
    return RunOptions(realtime=False)


def ws_target(url: str) -> Target:
    return Target(name="reference", kind="websocket", config={"url": url})


def refill_scenario(**overrides) -> Scenario:
    """The canonical happy path against the reference agent."""
    data = {
        "name": "refill_happy_path",
        "caller": {
            "facts": {"phone": "+91 98765 43210", "medication": "Metformin 500mg"},
            "opening": "Hi, I need to refill my prescription.",
            "script": [
                Say(text="My number is nine eight seven six five four three two one zero"),
                Say(text="It's Metformin, 500 milligram"),
                Say(text="No, that's all. Thanks."),
            ],
        },
        "assert": [],
        "max_turns": 10,
        "timeout_s": 20,
    }
    data.update(overrides)
    return Scenario.model_validate(data)
