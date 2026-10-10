-- Срок действия занятий на балансе (решения владельца студии 2026-10-10).
--
-- Каждое занятие на балансе принадлежит «партии» — оплате (lesson_payments)
-- или выдаче студией (lesson_package_grants), и у партии есть expires_at:
-- дата покупки/выдачи + срок абонемента (lesson_packages.validity_days).
-- Ручные начисления «± баланс» тоже становятся выдачей со сроком
-- studio_settings.manual_credit_validity_days. Неиспользованный остаток
-- истёкшей партии сгорает: строка ledger 'expiration' (бот/API, фоновой
-- задачей). Бессрочны только занятия, которые студия вернула за отменённое
-- ею занятие уже после конца срока их партии (ledger без партии).
--
-- Для уже существующих покупок и выдач срок считается от даты внедрения
-- (этой миграции), как решил владелец студии.

ALTER TABLE lesson_payments
    ADD COLUMN IF NOT EXISTS validity_days INTEGER
        CHECK (validity_days IS NULL OR validity_days BETWEEN 1 AND 3650);
ALTER TABLE lesson_payments ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
-- Остаток истёкшей партии уже списан (или его не было).
ALTER TABLE lesson_payments ADD COLUMN IF NOT EXISTS expiry_processed_at TIMESTAMPTZ;

ALTER TABLE lesson_package_grants ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE lesson_package_grants
    ADD COLUMN IF NOT EXISTS expiry_processed_at TIMESTAMPTZ;

ALTER TABLE studio_settings
    ADD COLUMN IF NOT EXISTS manual_credit_validity_days INTEGER NOT NULL DEFAULT 60
        CHECK (manual_credit_validity_days BETWEEN 1 AND 3650);
ALTER TABLE studio_settings
    ADD COLUMN IF NOT EXISTS expiry_warning_days INTEGER NOT NULL DEFAULT 7
        CHECK (expiry_warning_days BETWEEN 0 AND 60);

ALTER TABLE lesson_credit_ledger
    DROP CONSTRAINT IF EXISTS lesson_credit_ledger_entry_type_check;
ALTER TABLE lesson_credit_ledger
    ADD CONSTRAINT lesson_credit_ledger_entry_type_check
    CHECK (entry_type IN (
        'purchase', 'refund_reservation', 'refund', 'refund_release',
        'lesson_use', 'adjustment', 'admin_adjustment',
        'slot_cancellation', 'package_grant', 'package_revoke', 'expiration'
    ));

ALTER TABLE user_notifications DROP CONSTRAINT IF EXISTS user_notifications_kind_check;
ALTER TABLE user_notifications
    ADD CONSTRAINT user_notifications_kind_check
    CHECK (kind IN (
        'booking_confirmed', 'booking_cancelled', 'slot_cancelled',
        'slot_rescheduled', 'lesson_reminder', 'low_balance',
        'package_granted', 'package_revoked', 'credits_adjusted',
        'package_expiring', 'credits_expired'
    ));

-- Партии к проверке сгорания: срок наступил, остаток ещё не обработан.
CREATE INDEX IF NOT EXISTS lesson_payments_expiry_due_idx
    ON lesson_payments (expires_at)
    WHERE expires_at IS NOT NULL AND expiry_processed_at IS NULL;
CREATE INDEX IF NOT EXISTS lesson_package_grants_expiry_due_idx
    ON lesson_package_grants (expires_at)
    WHERE expires_at IS NOT NULL AND expiry_processed_at IS NULL;

-- ---------- существующие данные ----------

-- Срок оплаты — срок её абонемента на момент внедрения (у ещё не
-- оплаченных он начнётся с даты оплаты).
UPDATE lesson_payments AS payment
SET validity_days = package.validity_days
FROM lesson_packages AS package
WHERE package.key = payment.package_key AND payment.validity_days IS NULL;

UPDATE lesson_payments
SET expires_at = NOW() + make_interval(days => validity_days)
WHERE status = 'succeeded' AND validity_days IS NOT NULL AND expires_at IS NULL;

-- Выдачи: срок абонемента из каталога, иначе — срок ручных начислений.
UPDATE lesson_package_grants AS grant_row
SET expires_at = NOW() + make_interval(days => COALESCE(
    (SELECT package.validity_days FROM lesson_packages AS package
     WHERE package.key = grant_row.package_key),
    (SELECT manual_credit_validity_days FROM studio_settings WHERE id)
))
WHERE grant_row.status = 'active' AND grant_row.expires_at IS NULL;

-- Занятия «вне абонементов» (ручные начисления и баланс до ledger) —
-- в выдачу «Занятия без абонемента» со сроком ручных начислений. Баланс не
-- меняется: +N к новой выдаче и −N без партии (две строки ledger), сумма
-- ledger та же. granted_by = 0 и актор 0 — системное действие миграции.
-- Больше 100 занятий за раз выдача не вмещает: такой остаток (на практике
-- не встречается) остаётся бессрочным.
WITH lots AS (
    SELECT payment.telegram_id, GREATEST(SUM(ledger.delta), 0) AS remaining
    FROM lesson_payments AS payment
    JOIN lesson_credit_ledger AS ledger ON ledger.payment_id = payment.id
    WHERE payment.status = 'succeeded'
    GROUP BY payment.id, payment.telegram_id
    UNION ALL
    SELECT grant_row.telegram_id, GREATEST(SUM(ledger.delta), 0)
    FROM lesson_package_grants AS grant_row
    JOIN lesson_credit_ledger AS ledger ON ledger.grant_id = grant_row.id
    WHERE grant_row.status = 'active'
    GROUP BY grant_row.id, grant_row.telegram_id
),
pool AS (
    SELECT profile.telegram_id,
           LEAST(
               profile.lesson_credits - COALESCE(
                   (SELECT SUM(lots.remaining) FROM lots
                    WHERE lots.telegram_id = profile.telegram_id),
                   0
               ),
               100
           )::INTEGER AS lessons
    FROM bot_users AS profile
),
created AS (
    INSERT INTO lesson_package_grants (
        telegram_id, package_key, package_title, lessons, granted_by, reason,
        idempotence_key, expires_at
    )
    SELECT pool.telegram_id, 'manual', 'Занятия без абонемента', pool.lessons, 0,
           'Срок действия для занятий вне абонементов (внедрение сроков)',
           gen_random_uuid(),
           NOW() + make_interval(
               days => (SELECT manual_credit_validity_days FROM studio_settings WHERE id)
           )
    FROM pool
    WHERE pool.lessons > 0
    RETURNING id, telegram_id, lessons, reason
),
granted AS (
    INSERT INTO lesson_credit_ledger (
        telegram_id, grant_id, entry_type, delta, reference_key,
        actor_telegram_id, reason
    )
    SELECT created.telegram_id, created.id, 'package_grant', created.lessons,
           'migration-018:grant:' || created.id, 0, created.reason
    FROM created
    RETURNING id
)
INSERT INTO lesson_credit_ledger (
    telegram_id, entry_type, delta, reference_key, actor_telegram_id, reason
)
SELECT created.telegram_id, 'adjustment', -created.lessons,
       'migration-018:pool:' || created.id, 0, created.reason
FROM created;
