CREATE TABLE IF NOT EXISTS lesson_slots (
    id BIGSERIAL PRIMARY KEY,
    class_key TEXT NOT NULL
        CHECK (class_key IN ('beginner', 'intermediate', 'individual')),
    starts_at TIMESTAMPTZ NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity BETWEEN 1 AND 100),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed')),
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS lesson_slots_open_starts_idx
    ON lesson_slots (class_key, starts_at)
    WHERE status = 'open';

CREATE TABLE IF NOT EXISTS lesson_bookings (
    id BIGSERIAL PRIMARY KEY,
    slot_id BIGINT NOT NULL
        REFERENCES lesson_slots(id) ON DELETE RESTRICT,
    telegram_id BIGINT NOT NULL
        REFERENCES bot_users(telegram_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('confirmed', 'completed', 'cancelled')),
    booked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (slot_id, telegram_id)
);

CREATE INDEX IF NOT EXISTS lesson_bookings_slot_status_idx
    ON lesson_bookings (slot_id, status);

CREATE TABLE IF NOT EXISTS support_tickets (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL
        REFERENCES bot_users(telegram_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS support_tickets_one_open_per_user_idx
    ON support_tickets (telegram_id)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS support_tickets_open_updated_idx
    ON support_tickets (updated_at DESC)
    WHERE status = 'open';

CREATE TABLE IF NOT EXISTS support_messages (
    id BIGSERIAL PRIMARY KEY,
    ticket_id BIGINT NOT NULL
        REFERENCES support_tickets(id) ON DELETE CASCADE,
    sender_telegram_id BIGINT NOT NULL,
    sender_role TEXT NOT NULL CHECK (sender_role IN ('user', 'admin')),
    body TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 2000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS support_messages_ticket_created_idx
    ON support_messages (ticket_id, created_at);
