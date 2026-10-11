-- Рассылки участникам и заметки сотрудников об участниках.
--
-- Рассылка — запись в broadcasts и по уведомлению 'announcement' каждому
-- получателю в общем outbox (user_notifications): отправка в Telegram — тем
-- же фоновым диспетчером, на сайте — в ленте уведомлений. Сегменты
-- (решение владельца студии 2026-10-10): все участники бота; с занятиями
-- на балансе (можно — без записи N дней); записанные на занятие; ходили
-- на направление за N дней.
--
-- Заметки — служебные пометки об участнике (уровень, ограничения по
-- здоровью, договорённости). Участник их не видит.

CREATE TABLE IF NOT EXISTS broadcasts (
    id BIGSERIAL PRIMARY KEY,
    author_telegram_id BIGINT NOT NULL,
    body TEXT NOT NULL CHECK (char_length(btrim(body)) BETWEEN 1 AND 2000),
    segment TEXT NOT NULL
        CHECK (segment IN ('all', 'with_credits', 'slot', 'direction')),
    segment_params JSONB NOT NULL DEFAULT '{}'::JSONB,
    recipients INTEGER NOT NULL DEFAULT 0 CHECK (recipients >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS broadcasts_created_idx ON broadcasts (created_at DESC);

CREATE TABLE IF NOT EXISTS client_notes (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES bot_users(telegram_id) ON DELETE CASCADE,
    author_telegram_id BIGINT NOT NULL,
    body TEXT NOT NULL CHECK (char_length(btrim(body)) BETWEEN 1 AND 1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS client_notes_user_created_idx
    ON client_notes (telegram_id, created_at DESC);

-- Сбои доставки уведомлений — для раздела администратора.
CREATE INDEX IF NOT EXISTS user_notifications_failed_idx
    ON user_notifications (created_at DESC)
    WHERE delivery_status IN ('failed', 'skipped');

ALTER TABLE user_notifications DROP CONSTRAINT IF EXISTS user_notifications_kind_check;
ALTER TABLE user_notifications
    ADD CONSTRAINT user_notifications_kind_check
    CHECK (kind IN (
        'booking_confirmed', 'booking_cancelled', 'slot_cancelled',
        'slot_rescheduled', 'lesson_reminder', 'low_balance',
        'package_granted', 'package_revoked', 'credits_adjusted',
        'package_expiring', 'credits_expired', 'waitlist_seat',
        'announcement'
    ));
