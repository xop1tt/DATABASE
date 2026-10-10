-- Вход на сайт и привязка Telegram через бота (deep link), без SMS.
--
-- Сайт создаёт запрос и получает ссылку t.me/<бот>?start=c_<token>;
-- браузер при этом получает отдельный секрет в httpOnly-cookie. Участник
-- открывает ссылку и подтверждает действие кнопкой в чате с ботом — бот
-- записывает свой (проверенный Telegram) telegram_id. Сессию сайт выдаёт
-- только браузеру с секретом из cookie, один раз. В БД — только SHA-256
-- токена и секрета, как у web_sessions.

CREATE TABLE IF NOT EXISTS telegram_connect_requests (
    id BIGSERIAL PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    browser_secret_hash TEXT NOT NULL UNIQUE,
    purpose TEXT NOT NULL CHECK (purpose IN ('login', 'link')),
    web_user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    telegram_id BIGINT REFERENCES bot_users(telegram_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'consumed', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    confirmed_at TIMESTAMPTZ,
    consumed_at TIMESTAMPTZ,
    CHECK ((purpose = 'link') = (web_user_id IS NOT NULL)),
    CHECK (status IN ('pending', 'rejected') OR telegram_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS telegram_connect_requests_expires_idx
    ON telegram_connect_requests (expires_at);
