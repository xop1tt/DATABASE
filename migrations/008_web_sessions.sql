-- Серверные веб-сессии вместо самоподписанного JWT в cookie.
--
-- Раньше logout только удалял cookie у клиента — токен оставался валиден
-- до истечения 7 дней, отозвать его раньше было нечем (см. аудит
-- безопасности). Теперь cookie несёт непрозрачный токен, а факт входа и
-- срок жизни проверяются здесь; logout удаляет строку — сессия отзывается
-- немедленно.
CREATE TABLE IF NOT EXISTS web_sessions (
    token TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS web_sessions_user_id_idx ON web_sessions (user_id);
