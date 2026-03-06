-- migrate:up
CREATE TABLE provider_configs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL,
    category    TEXT NOT NULL CHECK (category IN ('stt', 'llm', 'tts', 'telephony')),
    credentials JSONB NOT NULL DEFAULT '{}',
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, provider, category)
);

CREATE INDEX idx_provider_configs_user ON provider_configs(user_id);

-- migrate:down
DROP TABLE IF EXISTS provider_configs;
