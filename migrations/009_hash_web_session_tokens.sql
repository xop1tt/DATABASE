-- В web_sessions хранится SHA-256 (hex) от токена из cookie, а не сам токен:
-- утечка чтения БД (дамп, доступ к таблице) больше не даёт войти на сайт
-- чужой сессией. Cookie по-прежнему несёт исходный токен; хеш считает
-- репозиторий (`hash_session_token`) при создании, поиске и удалении.
--
-- Существующие сессии не сбрасываются: хеш от сохранённого токена равен
-- хешу от того же токена в cookie (sha256() встроен в PostgreSQL 11+).
ALTER TABLE web_sessions RENAME COLUMN token TO token_hash;

UPDATE web_sessions
SET token_hash = encode(sha256(convert_to(token_hash, 'UTF8')), 'hex');
