from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from convox.compliance.dpdp import DPDPModule
from convox.config import settings
from convox.middleware.auth import get_current_user_id

router = APIRouter(prefix="/v1/compliance", tags=["compliance"])


class ConsentRequest(BaseModel):
    phone: str
    purpose: str = "voice_ai_processing"
    consent_given: bool
    language: str = "en"
    session_id: uuid.UUID | None = None
    audio_ref: str | None = None


class ConsentCheckRequest(BaseModel):
    phone: str


class DeletionRequest(BaseModel):
    phone: str


@router.get("/status")
async def compliance_status(
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> dict:
    """Get DPDP compliance module status."""
    return {
        "dpdp_enabled": settings.dpdp_enabled,
        "breach_notify_email": settings.dpdp_breach_notify_email or None,
    }


@router.post("/consent")
async def record_consent(
    body: ConsentRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> dict:
    """Record a consent event for a phone number."""
    dpdp = DPDPModule()
    return await dpdp.record_consent(
        session_id=body.session_id,
        phone=body.phone,
        purpose=body.purpose,
        consent_given=body.consent_given,
        language=body.language,
        audio_ref=body.audio_ref,
    )


@router.post("/consent/check")
async def check_consent(
    body: ConsentCheckRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> dict:
    """Check consent status for a phone number."""
    dpdp = DPDPModule()
    return await dpdp.check_consent(body.phone)


@router.post("/data/delete")
async def request_deletion(
    body: DeletionRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> dict:
    """Request data deletion per DPDP Act right to erasure."""
    dpdp = DPDPModule()
    return await dpdp.request_data_deletion(body.phone)
