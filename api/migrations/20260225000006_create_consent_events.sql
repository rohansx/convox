-- migrate:up
CREATE TABLE consent_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID REFERENCES sessions(id) ON DELETE SET NULL,
    phone_number_hash   TEXT NOT NULL,
    purpose             TEXT NOT NULL,
    consent_given       BOOLEAN NOT NULL,
    language            TEXT NOT NULL DEFAULT 'en',
    audio_ref           TEXT,
    expiry              TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_consent_events_phone ON consent_events(phone_number_hash);
CREATE INDEX idx_consent_events_session ON consent_events(session_id);

-- migrate:down
DROP TABLE IF EXISTS consent_events;
