-- Жизненный цикл занятия: отмена студией, перенос, журнал изменений слота.
--
-- Отмена студией — отдельный терминальный статус слота 'cancelled' (а не
-- статус брони): бронь при этом становится обычной 'cancelled', а причину
-- (студия, а не участник) видно по слоту и по ledger-записи
-- 'slot_cancellation' (миграция 012). Перенос — изменение starts_at того же
-- слота: брони и баланс не трогаются, факт переноса пишется в
-- lesson_slot_events и lesson_slots.rescheduled_at.

ALTER TABLE lesson_slots
    DROP CONSTRAINT IF EXISTS lesson_slots_status_check;

ALTER TABLE lesson_slots
    ADD CONSTRAINT lesson_slots_status_check
    CHECK (status IN ('open', 'closed', 'cancelled'));

ALTER TABLE lesson_slots
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

ALTER TABLE lesson_slots
    ADD COLUMN IF NOT EXISTS cancelled_by BIGINT;

ALTER TABLE lesson_slots
    ADD COLUMN IF NOT EXISTS cancel_reason TEXT;

-- Момент последнего переноса студией. Участник, записавшийся до переноса,
-- может отменить запись до нового начала без ограничения «за 24 часа»
-- (см. booking_cancellation_deadline в database/repository.py).
ALTER TABLE lesson_slots
    ADD COLUMN IF NOT EXISTS rescheduled_at TIMESTAMPTZ;

ALTER TABLE lesson_slots
    DROP CONSTRAINT IF EXISTS lesson_slots_cancelled_state_check;

ALTER TABLE lesson_slots
    ADD CONSTRAINT lesson_slots_cancelled_state_check
    CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL));

ALTER TABLE lesson_slots
    DROP CONSTRAINT IF EXISTS lesson_slots_cancel_reason_check;

ALTER TABLE lesson_slots
    ADD CONSTRAINT lesson_slots_cancel_reason_check
    CHECK (cancel_reason IS NULL OR char_length(cancel_reason) BETWEEN 1 AND 300);

-- Журнал изменений слота: аудит для администратора и источник
-- «перенесено с …» в истории участника. actor_telegram_id без внешнего
-- ключа — как в lesson_payment_events: запись аудита не должна мешать
-- удалению профиля администратора.
CREATE TABLE IF NOT EXISTS lesson_slot_events (
    id BIGSERIAL PRIMARY KEY,
    slot_id BIGINT NOT NULL REFERENCES lesson_slots(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL
        CHECK (event_type IN (
            'created', 'rescheduled', 'capacity_changed', 'closed',
            'reopened', 'cancelled'
        )),
    actor_telegram_id BIGINT,
    old_starts_at TIMESTAMPTZ,
    new_starts_at TIMESTAMPTZ,
    old_capacity INTEGER,
    new_capacity INTEGER,
    reason TEXT CHECK (reason IS NULL OR char_length(reason) BETWEEN 1 AND 300),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        event_type <> 'rescheduled'
        OR (old_starts_at IS NOT NULL AND new_starts_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS lesson_slot_events_slot_created_idx
    ON lesson_slot_events (slot_id, created_at);

CREATE INDEX IF NOT EXISTS lesson_slot_events_created_idx
    ON lesson_slot_events (created_at DESC);

-- «Мои занятия», история и напоминания ищут брони по участнику; раньше
-- такого индекса не было (только (slot_id, telegram_id) и (slot_id, status)).
CREATE INDEX IF NOT EXISTS lesson_bookings_user_idx
    ON lesson_bookings (telegram_id);

-- Напоминания и админ-расписание выбирают слоты по времени начала
-- независимо от статуса.
CREATE INDEX IF NOT EXISTS lesson_slots_starts_idx
    ON lesson_slots (starts_at);
