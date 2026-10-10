ALTER TABLE bot_users
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE bot_users
    ADD COLUMN IF NOT EXISTS lesson_credits INTEGER NOT NULL DEFAULT 0
        CHECK (lesson_credits >= 0);

CREATE INDEX IF NOT EXISTS bot_users_last_seen_at_idx
    ON bot_users (last_seen_at DESC);

CREATE TABLE IF NOT EXISTS lesson_payments (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL
        REFERENCES bot_users(telegram_id) ON DELETE CASCADE,
    package_key TEXT NOT NULL
        CHECK (package_key IN ('single', 'pack_4', 'pack_8')),
    lessons INTEGER NOT NULL CHECK (lessons > 0),
    amount_minor BIGINT NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL DEFAULT 'RUB' CHECK (currency = 'RUB'),
    provider_payment_id TEXT NOT NULL UNIQUE,
    confirmation_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'succeeded', 'canceled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS lesson_payments_user_created_idx
    ON lesson_payments (telegram_id, created_at DESC);
