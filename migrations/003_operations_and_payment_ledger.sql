ALTER TABLE lesson_payments
    ADD COLUMN IF NOT EXISTS package_title TEXT NOT NULL DEFAULT 'Пакет занятий';

ALTER TABLE lesson_payments
    ADD COLUMN IF NOT EXISTS idempotence_key UUID;

ALTER TABLE lesson_payments
    ADD COLUMN IF NOT EXISTS refund_idempotence_key UUID;

ALTER TABLE lesson_payments
    ADD COLUMN IF NOT EXISTS provider_refund_id TEXT;

ALTER TABLE lesson_payments
    ADD COLUMN IF NOT EXISTS refund_reason TEXT;

ALTER TABLE lesson_payments
    ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMPTZ;

ALTER TABLE lesson_payments
    DROP CONSTRAINT IF EXISTS lesson_payments_status_check;

ALTER TABLE lesson_payments
    ADD CONSTRAINT lesson_payments_status_check
    CHECK (status IN ('pending', 'succeeded', 'canceled', 'refund_pending', 'refunded'));

CREATE UNIQUE INDEX IF NOT EXISTS lesson_payments_idempotence_key_idx
    ON lesson_payments (idempotence_key)
    WHERE idempotence_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS lesson_payments_refund_idempotence_key_idx
    ON lesson_payments (refund_idempotence_key)
    WHERE refund_idempotence_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS lesson_payment_attempts (
    idempotence_key UUID PRIMARY KEY,
    telegram_id BIGINT NOT NULL
        REFERENCES bot_users(telegram_id) ON DELETE CASCADE,
    package_key TEXT NOT NULL,
    package_title TEXT NOT NULL,
    lessons INTEGER NOT NULL CHECK (lessons > 0),
    amount_minor BIGINT NOT NULL CHECK (amount_minor > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider_payment_id TEXT UNIQUE,
    confirmation_url TEXT,
    status TEXT NOT NULL DEFAULT 'creating'
        CHECK (status IN ('creating', 'pending', 'completed', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS lesson_payment_attempts_active_user_idx
    ON lesson_payment_attempts (telegram_id, package_key)
    WHERE status IN ('creating', 'pending');

CREATE TABLE IF NOT EXISTS lesson_credit_ledger (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL
        REFERENCES bot_users(telegram_id) ON DELETE CASCADE,
    payment_id BIGINT
        REFERENCES lesson_payments(id) ON DELETE SET NULL,
    entry_type TEXT NOT NULL
        CHECK (entry_type IN (
            'purchase', 'refund_reservation', 'refund', 'refund_release',
            'lesson_use', 'adjustment'
        )),
    delta INTEGER NOT NULL CHECK (delta <> 0),
    reference_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS lesson_credit_ledger_purchase_once_idx
    ON lesson_credit_ledger (payment_id)
    WHERE payment_id IS NOT NULL
      AND entry_type = 'purchase';

CREATE INDEX IF NOT EXISTS lesson_credit_ledger_user_created_idx
    ON lesson_credit_ledger (telegram_id, created_at DESC);

CREATE TABLE IF NOT EXISTS lesson_payment_events (
    id BIGSERIAL PRIMARY KEY,
    payment_id BIGINT NOT NULL
        REFERENCES lesson_payments(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL
        CHECK (event_type IN (
            'created', 'succeeded', 'canceled', 'refund_requested',
            'refunded', 'refund_failed'
        )),
    actor_telegram_id BIGINT,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE lesson_payment_events
    ADD COLUMN IF NOT EXISTS provider_refund_id TEXT;

CREATE INDEX IF NOT EXISTS lesson_payment_events_payment_created_idx
    ON lesson_payment_events (payment_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS lesson_payment_events_created_once_idx
    ON lesson_payment_events (payment_id)
    WHERE event_type = 'created';

CREATE TABLE IF NOT EXISTS bot_runtime_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
