from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import structlog

from convox.compliance.base import ComplianceModule
from convox.config import settings
from convox.database.postgres import get_pool

logger = structlog.get_logger()

# Default consent validity: 1 year per DPDP Act
CONSENT_VALIDITY_DAYS = 365


class DPDPModule(ComplianceModule):
    """Digital Personal Data Protection (DPDP) Act 2023 compliance module.

    Handles:
    - Consent verification before call processing
    - Consent recording with audio reference
    - Data retention enforcement
    - Audit logging for all data access
    """

    module_id = "dpdp"

    def __init__(self) -> None:
        self.enabled = settings.dpdp_enabled

    @staticmethod
    def hash_phone(phone: str) -> str:
        """One-way hash of phone number for pseudonymization."""
        return hashlib.sha256(phone.encode()).hexdigest()

    async def on_call_start(self, session_id: str, caller: str) -> bool:
        """Check if caller has valid consent. Returns False to block the call."""
        if not self.enabled:
            return True

        phone_hash = self.hash_phone(caller)
        pool = get_pool()

        # Check for valid, non-expired consent
        consent = await pool.fetchrow(
            """SELECT id, consent_given, expiry FROM consent_events
               WHERE phone_number_hash = $1 AND consent_given = true
               AND (expiry IS NULL OR expiry > now())
               ORDER BY created_at DESC LIMIT 1""",
            phone_hash,
        )

        if not consent:
            logger.warning(
                "dpdp_consent_missing",
                session_id=session_id,
                phone_hash=phone_hash[:12],
            )
            await self.log_audit(
                "system", "consent_check_failed", "session", session_id,
                {"reason": "no_valid_consent", "phone_hash": phone_hash[:12]},
            )
            return False

        await self.log_audit(
            "system", "consent_verified", "session", session_id,
            {"consent_id": str(consent["id"])},
        )
        return True

    async def on_call_end(self, session_id: str) -> None:
        """Post-call compliance tasks: audit log entry."""
        if not self.enabled:
            return

        await self.log_audit(
            "system", "call_completed", "session", session_id, {},
        )

    async def record_consent(
        self,
        session_id: uuid.UUID | None,
        phone: str,
        purpose: str,
        consent_given: bool,
        language: str = "en",
        audio_ref: str | None = None,
    ) -> dict:
        """Record a consent event per DPDP Act requirements."""
        pool = get_pool()
        phone_hash = self.hash_phone(phone)
        expiry = datetime.now(UTC) + timedelta(days=CONSENT_VALIDITY_DAYS) if consent_given else None

        row = await pool.fetchrow(
            """INSERT INTO consent_events
               (session_id, phone_number_hash, purpose, consent_given, language, audio_ref, expiry)
               VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *""",
            session_id, phone_hash, purpose, consent_given, language, audio_ref, expiry,
        )

        logger.info(
            "dpdp_consent_recorded",
            consent_given=consent_given,
            purpose=purpose,
            phone_hash=phone_hash[:12],
        )

        await self.log_audit(
            "system",
            "consent_recorded",
            "consent_event",
            str(row["id"]),
            {"consent_given": consent_given, "purpose": purpose},
        )

        return {
            "id": str(row["id"]),
            "consent_given": consent_given,
            "purpose": purpose,
            "expiry": str(expiry) if expiry else None,
        }

    async def check_consent(self, phone: str) -> dict:
        """Check current consent status for a phone number."""
        pool = get_pool()
        phone_hash = self.hash_phone(phone)

        row = await pool.fetchrow(
            """SELECT id, consent_given, purpose, expiry, created_at
               FROM consent_events
               WHERE phone_number_hash = $1
               ORDER BY created_at DESC LIMIT 1""",
            phone_hash,
        )

        if not row:
            return {"has_consent": False, "reason": "no_record"}

        if not row["consent_given"]:
            return {"has_consent": False, "reason": "consent_denied"}

        if row["expiry"] and row["expiry"] < datetime.now(UTC):
            return {"has_consent": False, "reason": "consent_expired"}

        return {
            "has_consent": True,
            "consent_id": str(row["id"]),
            "purpose": row["purpose"],
            "expiry": str(row["expiry"]) if row["expiry"] else None,
            "granted_at": str(row["created_at"]),
        }

    async def request_data_deletion(self, phone: str) -> dict:
        """Handle data deletion request per DPDP Act right to erasure."""
        pool = get_pool()
        phone_hash = self.hash_phone(phone)

        # Revoke all consents
        await pool.execute(
            """UPDATE consent_events SET consent_given = false
               WHERE phone_number_hash = $1 AND consent_given = true""",
            phone_hash,
        )

        await self.log_audit(
            "system", "data_deletion_requested", "user_data", phone_hash[:12],
            {"action": "consents_revoked"},
        )

        logger.info("dpdp_deletion_requested", phone_hash=phone_hash[:12])

        return {"status": "consents_revoked", "phone_hash": phone_hash[:12]}

    @staticmethod
    async def log_audit(
        actor_type: str,
        action: str,
        resource_type: str,
        resource_id: str | None,
        details: dict,
    ) -> None:
        """Write an entry to the audit log."""
        pool = get_pool()
        import json

        await pool.execute(
            """INSERT INTO audit_log (actor_type, actor_id, action, resource_type, resource_id, details)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
            actor_type, "system", action, resource_type, resource_id or "",
            json.dumps(details),
        )
