CREATE TABLE IF NOT EXISTS bot_users (
    telegram_id BIGINT PRIMARY KEY CHECK (telegram_id > 0),
    phone TEXT,
    user_name TEXT NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_admin BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS lesson_requests (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES bot_users(telegram_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('booking', 'purchase')),
    details TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'completed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS lesson_requests_pending_created_idx
    ON lesson_requests (created_at DESC) WHERE status = 'pending';
