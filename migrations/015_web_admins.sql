-- Администраторы сайта.
--
-- Права администратора в боте — bot_users.is_admin (по Telegram ID). Сайту
-- нужен ещё и администратор, который входит по email/паролю и может не иметь
-- привязанного Telegram, поэтому флаг есть и у веб-аккаунта. Итоговое право
-- на сайте: users.is_admin ИЛИ bot_users.is_admin привязанного Telegram
-- (один человек — одни права, см. _WEB_USER_SELECT в repository.py).
--
-- Флаг выставляется только с сервера командой
-- `python -m flamenco_bot.api.manage create-admin` — через API его изменить
-- нельзя.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;
