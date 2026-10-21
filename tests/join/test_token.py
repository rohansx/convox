"""Join tokens: a phone joins one side of one call without the console password."""

from dubhash.config.settings import Settings
from dubhash.join import tokens


def settings(**kw) -> Settings:
    base = {"dubhash_env": "test", "console_password": "test-console-pw"}
    return Settings(_env_file=None, **{**base, **kw})


def test_sign_and_verify():
    s = settings()
    tok = tokens.make_join_token(s, "c_abc", "a", now=1000)
    assert tokens.verify_join_token(s, tok, "c_abc", "a", now=1001)


def test_expires_after_about_two_hours():
    s = settings()
    tok = tokens.make_join_token(s, "c_abc", "b", now=1000)
    assert tokens.JOIN_TTL_S == 2 * 3600
    assert tokens.verify_join_token(s, tok, "c_abc", "b", now=1000 + tokens.JOIN_TTL_S - 1)
    assert not tokens.verify_join_token(s, tok, "c_abc", "b", now=1000 + tokens.JOIN_TTL_S + 1)


def test_wrong_side_or_call_or_key_fails():
    s = settings()
    tok = tokens.make_join_token(s, "c_abc", "a", now=1000)
    assert not tokens.verify_join_token(s, tok, "c_abc", "b", now=1001)
    assert not tokens.verify_join_token(s, tok, "c_xyz", "a", now=1001)
    other = settings(console_password="other")
    assert not tokens.verify_join_token(other, tok, "c_abc", "a", now=1001)


def test_garbage_and_tampered_tokens_fail():
    s = settings()
    tok = tokens.make_join_token(s, "c_abc", "a", now=1000)
    exp, _, sig = tok.partition(".")
    later = f"{int(exp) + 3600}.{sig}"  # a stretched expiry breaks the signature
    for bad in ("", "garbage", ".", f"{exp}.", f"x{exp}.{sig}", later, None):
        assert not tokens.verify_join_token(s, bad, "c_abc", "a", now=1001)


def test_ward_tokens_do_not_open_a_call():
    """Same key, different purpose: a ward token is not a join token."""
    from dubhash.ward import routes

    s = settings()
    ward = routes.make_ws_token(s, now=1000)
    assert not tokens.verify_join_token(s, ward, "c_abc", "a", now=1001)


def test_prod_with_default_password_never_verifies():
    s = settings(dubhash_env="prod", console_password="change-me")
    tok = tokens.make_join_token(s, "c_abc", "a", now=1000)
    assert not tokens.verify_join_token(s, tok, "c_abc", "a", now=1001)
