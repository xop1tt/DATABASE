-- Абонементы: выдача студией и привязка движений ledger к источнику.
--
-- Купленный абонемент — это по-прежнему успешный lesson_payments (вторая
-- система баланса не вводится). Абонемент, выданный администратором без
-- оплаты (например, оплата наличными), хранится в lesson_package_grants —
-- это не платёж, поэтому он не попадает в lesson_payments, сверку ЮKassa и
-- возвраты. Баланс по-прежнему меняет только _change_credits, а остаток
-- каждого абонемента — сумма ledger по его payment_id / grant_id.

CREATE TABLE IF NOT EXISTS lesson_package_grants (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL
        REFERENCES bot_users(telegram_id) ON DELETE RESTRICT,
    package_key TEXT NOT NULL CHECK (char_length(package_key) BETWEEN 1 AND 64),
    package_title TEXT NOT NULL
        CHECK (char_length(package_title) BETWEEN 1 AND 100),
    lessons INTEGER NOT NULL CHECK (lessons BETWEEN 1 AND 100),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'revoked')),
    granted_by BIGINT NOT NULL,
    reason TEXT NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 1 AND 300),
    idempotence_key UUID NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    revoked_by BIGINT,
    revoke_reason TEXT
        CHECK (revoke_reason IS NULL OR char_length(btrim(revoke_reason)) BETWEEN 1 AND 300),
    CHECK ((status = 'revoked') = (revoked_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS lesson_package_grants_user_created_idx
    ON lesson_package_grants (telegram_id, created_at DESC);

-- Источник занятия для движений ledger: платёж (уже был payment_id) или
-- выданный абонемент. И бронь, к которой относится списание/возврат, —
-- чтобы история показывала занятие, а возврат шёл в тот же абонемент.
ALTER TABLE lesson_credit_ledger
    ADD COLUMN IF NOT EXISTS grant_id BIGINT
        REFERENCES lesson_package_grants(id) ON DELETE RESTRICT;

ALTER TABLE lesson_credit_ledger
    ADD COLUMN IF NOT EXISTS booking_id BIGINT
        REFERENCES lesson_bookings(id) ON DELETE SET NULL;

ALTER TABLE lesson_credit_ledger
    DROP CONSTRAINT IF EXISTS lesson_credit_ledger_single_source_check;

ALTER TABLE lesson_credit_ledger
    ADD CONSTRAINT lesson_credit_ledger_single_source_check
    CHECK (payment_id IS NULL OR grant_id IS NULL);

ALTER TABLE lesson_credit_ledger
    DROP CONSTRAINT IF EXISTS lesson_credit_ledger_entry_type_check;

ALTER TABLE lesson_credit_ledger
    ADD CONSTRAINT lesson_credit_ledger_entry_type_check
    CHECK (entry_type IN (
        'purchase', 'refund_reservation', 'refund', 'refund_release',
        'lesson_use', 'adjustment', 'admin_adjustment',
        'slot_cancellation', 'package_grant', 'package_revoke'
    ));

-- Возврат за отмену студией и операции с абонементом выполняет
-- администратор: без актора на уровне БД они невозможны.
ALTER TABLE lesson_credit_ledger
    DROP CONSTRAINT IF EXISTS lesson_credit_ledger_admin_actor_check;

ALTER TABLE lesson_credit_ledger
    ADD CONSTRAINT lesson_credit_ledger_admin_actor_check
    CHECK (
        entry_type NOT IN ('slot_cancellation', 'package_grant', 'package_revoke')
        OR actor_telegram_id IS NOT NULL
    );

ALTER TABLE lesson_credit_ledger
    DROP CONSTRAINT IF EXISTS lesson_credit_ledger_package_grant_link_check;

ALTER TABLE lesson_credit_ledger
    ADD CONSTRAINT lesson_credit_ledger_package_grant_link_check
    CHECK (
        entry_type NOT IN ('package_grant', 'package_revoke')
        OR (grant_id IS NOT NULL AND reason IS NOT NULL)
    );

-- Занятия выданного абонемента начисляются ровно один раз.
CREATE UNIQUE INDEX IF NOT EXISTS lesson_credit_ledger_grant_once_idx
    ON lesson_credit_ledger (grant_id)
    WHERE grant_id IS NOT NULL
      AND entry_type = 'package_grant';

CREATE INDEX IF NOT EXISTS lesson_credit_ledger_payment_idx
    ON lesson_credit_ledger (payment_id)
    WHERE payment_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS lesson_credit_ledger_grant_idx
    ON lesson_credit_ledger (grant_id)
    WHERE grant_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS lesson_credit_ledger_booking_idx
    ON lesson_credit_ledger (booking_id)
    WHERE booking_id IS NOT NULL;

-- Существующие списания/возвраты по записи несут id брони в reference_key
-- ('slot_booking:<booking_id>:use:…' / ':refund:…'). Заполняем booking_id,
-- только если такая бронь существует; CASE гарантирует, что приведение к
-- BIGINT выполняется лишь для строк нужного формата.
WITH parsed AS (
    SELECT id,
           CASE
               WHEN reference_key ~ '^slot_booking:[0-9]{1,18}:'
               THEN split_part(reference_key, ':', 2)::BIGINT
           END AS parsed_booking_id
    FROM lesson_credit_ledger
    WHERE booking_id IS NULL
      AND reference_key LIKE 'slot_booking:%'
)
UPDATE lesson_credit_ledger AS ledger
SET booking_id = parsed.parsed_booking_id
FROM parsed
JOIN lesson_bookings AS booking ON booking.id = parsed.parsed_booking_id
WHERE ledger.id = parsed.id;
