-- Индекс для фоновой сверки платежей, зависших в статусе 'pending'.
--
-- ЮKassa не шлёт вебхук (см. CLAUDE.md/аудит): без периодической проверки
-- "забытый" платёж остаётся pending, если никто не вернулся и не нажал
-- "проверить статус". `list_pending_lesson_payments_older_than` сканирует
-- именно по (status, created_at) — старый индекс (telegram_id, created_at)
-- для этого запроса бесполезен.
CREATE INDEX IF NOT EXISTS lesson_payments_pending_created_idx
    ON lesson_payments (created_at)
    WHERE status = 'pending';
