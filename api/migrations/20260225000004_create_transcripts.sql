-- migrate:up
CREATE TABLE transcripts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_index      INTEGER NOT NULL,
    speaker         TEXT NOT NULL CHECK (speaker IN ('user', 'agent')),
    text            TEXT NOT NULL,
    timestamp_ms    BIGINT NOT NULL,
    stt_provider    TEXT,
    stt_confidence  REAL,
    stt_cost_usd    NUMERIC(10, 6) DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_transcripts_session_id ON transcripts(session_id);
CREATE INDEX idx_transcripts_turn ON transcripts(session_id, turn_index);

-- migrate:down
DROP TABLE IF EXISTS transcripts;
