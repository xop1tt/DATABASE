-- Веб-аккаунты сайта: вход по email/паролю или по привязанному Telegram.
--
-- Таблица не затрагивает bot_users и бизнес-данные бота (платежи, записи,
-- поддержку): она ссылается на bot_users.telegram_id, а не владеет им.
-- Привязанный Telegram можно сменить в ЛК (UPDATE users.telegram_id) без
-- изменения истории в bot_users/lesson_*: баланс и записи в ЛК показываются
-- по тому telegram_id, который привязан сейчас.
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT,
    password_hash TEXT,
    telegram_id BIGINT UNIQUE REFERENCES bot_users(telegram_id) ON DELETE SET NULL,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (email IS NOT NULL OR telegram_id IS NOT NULL),
    CHECK ((email IS NULL) = (password_hash IS NULL))
);

-- Регистр email не должен влиять на уникальность и поиск при входе.
CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique_idx
    ON users (LOWER(email))
    WHERE email IS NOT NULL;

CREATE INDEX IF NOT EXISTS users_telegram_id_idx ON users (telegram_id);
