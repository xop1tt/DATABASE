-- Роли сотрудников студии — единственный источник прав.
--
-- Раньше права жили в трёх местах: bot_users.is_admin (бот), users.is_admin
-- (сайт, вход по email без Telegram) и переменная окружения ADMINS (адресаты
-- системных уведомлений). Теперь роль хранится здесь по Telegram ID — тому
-- же ключу, по которому ведутся журналы расписания, поддержки и баланса.
-- Администратор сайта — аккаунт с привязанным Telegram, у которого есть роль.
--
--   admin   — всё: расписание, участники, финансы, настройки, сотрудники;
--   teacher — только свои занятия (lesson_slots.teacher_telegram_id), их
--             участники и поддержка; без финансов и баланса.
--
-- Существующие администраторы бота и сайта (с привязанным Telegram)
-- переносятся сюда. Колонки bot_users.is_admin и users.is_admin больше не
-- читаются новой версией бота, но остаются, пока работает предыдущая
-- (база выкладывается раньше бота); их удалит отдельная миграция.

CREATE TABLE IF NOT EXISTS staff_members (
    telegram_id BIGINT PRIMARY KEY
        REFERENCES bot_users(telegram_id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK (role IN ('admin', 'teacher')),
    -- Имя преподавателя в публичном расписании; NULL — имя профиля бота.
    display_name TEXT
        CHECK (display_name IS NULL OR char_length(btrim(display_name)) BETWEEN 1 AND 64),
    -- Кто назначил; NULL — миграция или серверная команда manage.py.
    granted_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO staff_members (telegram_id, role)
SELECT telegram_id, 'admin' FROM bot_users WHERE is_admin
ON CONFLICT (telegram_id) DO NOTHING;

INSERT INTO staff_members (telegram_id, role)
SELECT account.telegram_id, 'admin'
FROM users AS account
WHERE account.is_admin AND account.telegram_id IS NOT NULL
ON CONFLICT (telegram_id) DO NOTHING;

-- Преподаватель занятия. Удаление сотрудника не удаляет занятия — у них
-- просто не остаётся преподавателя.
ALTER TABLE lesson_slots
    ADD COLUMN IF NOT EXISTS teacher_telegram_id BIGINT
        REFERENCES staff_members(telegram_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS lesson_slots_teacher_starts_idx
    ON lesson_slots (teacher_telegram_id, starts_at)
    WHERE teacher_telegram_id IS NOT NULL;

-- Журнал действий сотрудников, которые не оставляют следа в других
-- журналах (lesson_slot_events, lesson_payment_events, lesson_credit_ledger,
-- lesson_package_grants): правка профиля участника, сотрудники, настройки,
-- рассылки, заметки. Раньше такие действия писались только в файл
-- admin_actions_log, который на Render теряется при перезапуске.
CREATE TABLE IF NOT EXISTS staff_audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor_telegram_id BIGINT NOT NULL,
    action TEXT NOT NULL CHECK (char_length(action) BETWEEN 1 AND 64),
    target_telegram_id BIGINT,
    details JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS staff_audit_log_created_idx
    ON staff_audit_log (created_at DESC);

CREATE INDEX IF NOT EXISTS staff_audit_log_target_idx
    ON staff_audit_log (target_telegram_id, created_at DESC)
    WHERE target_telegram_id IS NOT NULL;
