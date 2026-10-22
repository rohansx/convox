import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from convox.api.auth import parse_api_keys, require_platform_key
from convox.api.schemas import ApiError, CreateCallRequest, api_error_handler, error_code_for
from convox.config.settings import Settings

BODY = {
    "a": {"number": "+919812345678", "lang": "hi-IN"},
    "b": {"number": "+919876543210", "lang": "kn-IN"},
    "caller_id": "+918047123456",
}


def test_defaults_per_api_md():
    req = CreateCallRequest.model_validate(BODY)
    assert req.provider == "exotel"
    assert req.mode == "interpret"
    assert req.escape_dtmf == "0"
    assert req.webhook is None
    assert req.record is False


@pytest.mark.parametrize(
    "patch",
    [
        {"mode": "listen"},
        {"provider": "twilio"},
        {"escape_dtmf": "12"},
        {"escape_dtmf": "x"},
        {"a": {"number": "+919812345678", "lang": "hindi"}},
        {"webhook": "not a url"},
    ],
)
def test_invalid_body(patch):
    with pytest.raises(ValidationError) as ei:
        CreateCallRequest.model_validate({**BODY, **patch})
    assert error_code_for(ei.value) == "invalid_body"


@pytest.mark.parametrize("number", ["9812345678", "+1415555123", "+91981234567", "+44 20 7946"])
def test_invalid_number(number):
    with pytest.raises(ValidationError) as ei:
        CreateCallRequest.model_validate({**BODY, "a": {"number": number, "lang": "hi-IN"}})
    assert error_code_for(ei.value) == "invalid_number"


def test_missing_field_is_invalid_body():
    body = {k: v for k, v in BODY.items() if k != "caller_id"}
    with pytest.raises(ValidationError) as ei:
        CreateCallRequest.model_validate(body)
    assert error_code_for(ei.value) == "invalid_body"


def test_parse_api_keys():
    assert parse_api_keys("dev:change-me, p2:abc:def ,bad,") == {
        "dev": "change-me",
        "p2": "abc:def",
    }


def _app(env: str, keys: str = "swiggy:sk_1,zomato:sk_2") -> TestClient:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None, convox_env=env, platform_api_keys=keys)
    app.add_exception_handler(ApiError, api_error_handler)

    from typing import Annotated

    from fastapi import Depends

    @app.get("/who")
    def who(pid: Annotated[str, Depends(require_platform_key)]) -> dict:
        return {"platform": pid}

    return TestClient(app)


def test_bearer_key_identifies_platform():
    c = _app("dev")
    r = c.get("/who", headers={"Authorization": "Bearer sk_2"})
    assert r.status_code == 200 and r.json() == {"platform": "zomato"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": "Basic " + base64.b64encode(b"x:sk_1").decode()},
    ],
)
def test_missing_or_bad_key_is_401(headers):
    r = _app("dev").get("/who", headers=headers)
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized", "message": r.json()["error"]["message"],
                                  "call_id": None}}  # fmt: skip


def test_env_test_allows_missing_key_but_not_bad_key():
    c = _app("test")
    assert c.get("/who").json() == {"platform": "test"}
    assert c.get("/who", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_prod_ignores_default_keys():
    c = _app("prod", keys="dev:change-me")
    assert c.get("/who", headers={"Authorization": "Bearer change-me"}).status_code == 401
