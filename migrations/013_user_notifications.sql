-- Уведомления участникам: transactional outbox без отдельной очереди.
--
-- Строка уведомления вставляется в той же транзакции, что и изменение
-- состояния (запись, отмена, перенос, возврат), — значит, уведомление есть
-- тогда и только тогда, когда изменение зафиксировано. Отправку в Telegram
-- выполняет фоновый диспетчер уже после commit (runtime/notifications.py):
-- сбой Telegram не откатывает бизнес-операцию, а повтор отправки не
-- повторяет финансовую операцию. dedupe_key не даёт поставить одно и то же
-- уведомление дважды. Та же таблица — лента уведомлений на сайте (read_at).

CREATE TABLE IF NOT EXISTS user_notifications (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL
        REFERENCES bot_users(telegram_id) ON DELETE CASCADE,
    kind TEXT NOT NULL
        CHECK (kind IN (
            'booking_confirmed', 'booking_cancelled', 'slot_cancelled',
            'slot_rescheduled', 'lesson_reminder', 'low_balance',
            'package_granted', 'package_revoked', 'credits_adjusted'
        )),
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    dedupe_key TEXT NOT NULL UNIQUE CHECK (char_length(dedupe_key) <= 200),
    delivery_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (delivery_status IN (
            'pending', 'sending', 'sent', 'failed', 'skipped'
        )),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- После этого момента уведомление не отправляется (например,
    -- напоминание после начала занятия) — помечается 'skipped'.
    expires_at TIMESTAMPTZ,
    last_error TEXT CHECK (last_error IS NULL OR char_length(last_error) <= 300),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    read_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS user_notifications_due_idx
    ON user_notifications (next_attempt_at)
    WHERE delivery_status IN ('pending', 'sending');

CREATE INDEX IF NOT EXISTS user_notifications_user_created_idx
    ON user_notifications (telegram_id, created_at DESC);

-- Настройки уведомлений участника. Критичные уведомления (отмена и перенос
-- занятия студией, изменение баланса) отправляются всегда; отключить можно
-- только напоминания и сообщение о малом остатке занятий.
ALTER TABLE bot_users
    ADD COLUMN IF NOT EXISTS notify_reminders BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE bot_users
    ADD COLUMN IF NOT EXISTS notify_low_balance BOOLEAN NOT NULL DEFAULT TRUE;
