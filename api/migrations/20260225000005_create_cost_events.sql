-- migrate:up
CREATE TABLE cost_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL CHECK (event_type IN ('stt', 'llm', 'tts', 'telephony')),
    provider    TEXT NOT NULL,
    amount_usd  NUMERIC(10, 6) NOT NULL,
    units       REAL NOT NULL DEFAULT 0,
    unit_type   TEXT NOT NULL DEFAULT 'seconds' CHECK (unit_type IN ('seconds', 'tokens', 'characters')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_cost_events_session_id ON cost_events(session_id);
CREATE INDEX idx_cost_events_type ON cost_events(event_type);
CREATE INDEX idx_cost_events_created_at ON cost_events(created_at DESC);

-- migrate:down
DROP TABLE IF EXISTS cost_events;
