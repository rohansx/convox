"""Join tokens: let a phone open one side of one loopback call without the console password.

Same scheme as ward mode's websocket token (convox/ward/routes.py): `<exp>.<sig>`, where
sig is a truncated HMAC-SHA256 keyed on the console password. Here the key has its own
purpose prefix and the signed payload names the call and the side, so a token for side a
does not open side b, and a ward token opens nothing. Expiry is about one stage slot.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from convox.api.deps import DEFAULT_PASSWORDS
from convox.config.settings import Settings

JOIN_TTL_S = 2 * 3600


def _sign(settings: Settings, call_id: str, side: str, exp: str) -> str:
    key = f"join:{settings.console_password}".encode()
    msg = f"{call_id}|{side}|{exp}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def make_join_token(settings: Settings, call_id: str, side: str, now: float | None = None) -> str:
    exp = str(int((time.time() if now is None else now) + JOIN_TTL_S))
    return f"{exp}.{_sign(settings, call_id, side, exp)}"


def verify_join_token(
    settings: Settings, token: str | None, call_id: str, side: str, now: float | None = None
) -> bool:
    if not token:
        return False
    if settings.convox_env == "prod" and settings.console_password in DEFAULT_PASSWORDS:
        return False  # a guessable key would let anyone in, like the console's 503
    exp, _, sig = token.partition(".")
    if not exp.isdigit() or not sig:
        return False
    if int(exp) < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(sig, _sign(settings, call_id, side, exp))
