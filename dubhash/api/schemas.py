"""Pydantic models for the platform API (docs/tech-spec/api.md) and its error shape:

    {"error": {"code": "unsupported_pair", "message": "...", "call_id": null}}

Routes raise `ApiError`; `api_error_handler` renders it. Request bodies are validated by
hand (`CreateCallRequest.model_validate`) so a bad body is a 400 with our codes rather
than FastAPI's default 422.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError, field_validator

from dubhash.interp.phrases import verticals

E164 = r"^\+[1-9]\d{7,14}$"
INDIAN_MOBILE = r"^\+91\d{10}$"
BCP47 = r"^[a-z]{2,3}-[A-Z]{2}$"
LANG_OR_AUTO = r"^([a-z]{2,3}-[A-Z]{2}|auto)$"  # "auto": detected on the call

ErrorCode = Literal[
    "invalid_number",
    "invalid_body",
    "unauthorized",
    "call_not_found",
    "call_ended",
    "unsupported_pair",
    "capacity",
    "provider_error",
    "provider_unavailable",
]


def _check_vertical(v: str | None) -> str | None:
    if v is not None and v not in verticals():
        raise ValueError(f"vertical must be one of {verticals()}")
    return v


class LegSpec(BaseModel):
    number: str = Field(pattern=INDIAN_MOBILE)
    lang: str = Field(pattern=LANG_OR_AUTO)
    # a prior for lang "auto" (e.g. the platform's profile language); ignored otherwise,
    # and ignored when it is not a supported language
    lang_hint: str | None = Field(default=None, pattern=BCP47)


class CreateCallRequest(BaseModel):
    a: LegSpec
    b: LegSpec
    provider: Literal["exotel", "plivo"] = "exotel"
    caller_id: str = Field(pattern=E164)
    mode: Literal["interpret", "bridge"] = "interpret"
    escape_dtmf: str = Field(default="0", pattern=r"^[0-9]$")
    webhook: AnyHttpUrl | None = None
    record: bool = False
    # phrase pack the call prefills (config/phrases/<vertical>.yaml, L10); default delivery
    vertical: str | None = None

    @field_validator("vertical")
    @classmethod
    def _vertical(cls, v: str | None) -> str | None:
        return _check_vertical(v)


class CreateCallResponse(BaseModel):
    call_id: str
    status: Literal["ringing"] = "ringing"


class HangupResponse(BaseModel):
    call_id: str
    status: str


class LoopbackCallRequest(BaseModel):
    a_lang: str = Field(pattern=LANG_OR_AUTO)
    b_lang: str = Field(pattern=LANG_OR_AUTO)
    a_lang_hint: str | None = Field(default=None, pattern=BCP47)  # for a_lang "auto"
    b_lang_hint: str | None = Field(default=None, pattern=BCP47)  # for b_lang "auto"
    mode: Literal["interpret", "bridge"] = "interpret"
    record: bool = False  # keep src_text/tgt_text in the turn log (dev and live tools)
    pair: str | None = None  # pairs.yaml key, e.g. "hi-kn-direct"; default: by languages
    # default: settings.loopback_opening_line ("short")
    opening_line: Literal["short", "full", "off"] | None = None
    vertical: str | None = None  # phrase pack (L10); default delivery

    @field_validator("vertical")
    @classmethod
    def _vertical(cls, v: str | None) -> str | None:
        return _check_vertical(v)


class LoopbackCallResponse(BaseModel):
    call_id: str
    a_url: str
    b_url: str


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    call_id: str | None = None


class ErrorBody(BaseModel):
    error: ErrorDetail


class ApiError(Exception):
    def __init__(
        self, status: int, code: ErrorCode, message: str, call_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.call_id = call_id

    def body(self) -> dict[str, Any]:
        detail = ErrorDetail(code=self.code, message=self.message, call_id=self.call_id)
        return ErrorBody(error=detail).model_dump()


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
    return JSONResponse(exc.body(), status_code=exc.status, headers=headers)


_NUMBER_FIELDS = {("a", "number"), ("b", "number"), ("caller_id",)}


def error_code_for(err: ValidationError) -> ErrorCode:
    """invalid_number when every failure is a malformed phone number, else invalid_body."""
    errors = err.errors()
    if errors and all(
        tuple(e["loc"]) in _NUMBER_FIELDS and e["type"] == "string_pattern_mismatch" for e in errors
    ):
        return "invalid_number"
    return "invalid_body"


def validation_error(err: ValidationError) -> ApiError:
    first = err.errors()[0] if err.errors() else {"loc": (), "msg": "invalid body"}
    where = ".".join(str(p) for p in first["loc"]) or "body"
    return ApiError(400, error_code_for(err), f"{where}: {first['msg']}")
