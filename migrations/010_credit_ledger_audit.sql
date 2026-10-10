-- Ledger занятий: аудит ручных корректировок и защита финансовой истории.

-- Кто и почему изменил баланс. Заполняется для административной корректировки;
-- остальные операции (оплата, запись, возврат) остаются без актора, как раньше.
-- actor_telegram_id без внешнего ключа — как в lesson_payment_events: запись
-- аудита не должна исчезать или блокировать удаление профиля администратора.
ALTER TABLE lesson_credit_ledger
    ADD COLUMN IF NOT EXISTS actor_telegram_id BIGINT;

ALTER TABLE lesson_credit_ledger
    ADD COLUMN IF NOT EXISTS reason TEXT;

-- Отдельный тип для ручной корректировки: прежний 'adjustment' остаётся только
-- у возврата занятия при отмене записи пользователем и исторических строк.
ALTER TABLE lesson_credit_ledger
    DROP CONSTRAINT IF EXISTS lesson_credit_ledger_entry_type_check;

ALTER TABLE lesson_credit_ledger
    ADD CONSTRAINT lesson_credit_ledger_entry_type_check
    CHECK (entry_type IN (
        'purchase', 'refund_reservation', 'refund', 'refund_release',
        'lesson_use', 'adjustment', 'admin_adjustment'
    ));

-- Ручная корректировка без администратора и причины на уровне БД невозможна.
ALTER TABLE lesson_credit_ledger
    DROP CONSTRAINT IF EXISTS lesson_credit_ledger_admin_adjustment_audit_check;

ALTER TABLE lesson_credit_ledger
    ADD CONSTRAINT lesson_credit_ledger_admin_adjustment_audit_check
    CHECK (
        entry_type <> 'admin_adjustment'
        OR (
            actor_telegram_id IS NOT NULL
            AND reason IS NOT NULL
            AND char_length(btrim(reason)) BETWEEN 1 AND 300
        )
    );

-- Удаление профиля больше не стирает платежи, попытки оплаты и ledger.
-- Раньше ON DELETE CASCADE молча уничтожал финансовую историю; теперь DELETE
-- профиля с такой историей завершается ошибкой внешнего ключа. Как именно
-- хранить или обезличивать эти данные — решение владельца студии
-- (см. docs/operations.md); миграция лишь делает небезопасный вариант
-- невозможным по умолчанию.
DO $$
DECLARE
    fk RECORD;
BEGIN
    FOR fk IN
        SELECT conrelid::regclass AS table_name, conname
        FROM pg_constraint
        WHERE contype = 'f'
          AND confrelid = 'bot_users'::regclass
          AND confdeltype = 'c'
          AND conrelid IN (
              'lesson_payments'::regclass,
              'lesson_payment_attempts'::regclass,
              'lesson_credit_ledger'::regclass
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE %s DROP CONSTRAINT %I', fk.table_name, fk.conname
        );
        EXECUTE format(
            'ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (telegram_id) '
            'REFERENCES bot_users(telegram_id) ON DELETE RESTRICT',
            fk.table_name,
            fk.conname
        );
    END LOOP;
END
$$;
