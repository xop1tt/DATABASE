-- Лист ожидания на заполненные занятия (решения владельца студии 2026-10-10).
--
-- Встать в очередь можно на открытое будущее занятие без свободных мест и
-- только с занятием на балансе. Когда место освобождается (отмена записи,
-- больше вместимость, отмена записи администратором), всем в очереди
-- приходит уведомление 'waitlist_seat' — кто первым запишется, тот и
-- получит место; автоматической записи нет. После записи на занятие
-- участник из очереди удаляется.

CREATE TABLE IF NOT EXISTS lesson_waitlist (
    slot_id BIGINT NOT NULL REFERENCES lesson_slots(id) ON DELETE CASCADE,
    telegram_id BIGINT NOT NULL REFERENCES bot_users(telegram_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (slot_id, telegram_id)
);

CREATE INDEX IF NOT EXISTS lesson_waitlist_user_idx
    ON lesson_waitlist (telegram_id, created_at DESC);

ALTER TABLE user_notifications DROP CONSTRAINT IF EXISTS user_notifications_kind_check;
ALTER TABLE user_notifications
    ADD CONSTRAINT user_notifications_kind_check
    CHECK (kind IN (
        'booking_confirmed', 'booking_cancelled', 'slot_cancelled',
        'slot_rescheduled', 'lesson_reminder', 'low_balance',
        'package_granted', 'package_revoked', 'credits_adjusted',
        'package_expiring', 'credits_expired', 'waitlist_seat'
    ));
