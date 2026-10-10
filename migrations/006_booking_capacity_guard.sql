-- Страховка на уровне БД для инварианта "занятых мест не больше вместимости".
--
-- `PostgresRepository.book_class_slot` уже обеспечивает это транзакционно
-- через `SELECT ... FOR UPDATE` на строку слота перед подсчётом занятых мест
-- (см. репозиторий) — этот путь остаётся единственным способом записи и
-- остаётся правильным. Триггер — вторая линия защиты на случай, если в
-- будущем появится код, который изменит `lesson_bookings` в обход этой
-- блокировки (например, прямой UPDATE из админ-инструмента или миграции
-- данных): без него такой код мог бы создать овербукинг незаметно.
CREATE OR REPLACE FUNCTION enforce_lesson_slot_capacity() RETURNS TRIGGER AS $$
DECLARE
    slot_capacity INTEGER;
    confirmed_count INTEGER;
BEGIN
    SELECT capacity INTO slot_capacity
    FROM lesson_slots
    WHERE id = NEW.slot_id
    FOR SHARE;

    SELECT COUNT(*) INTO confirmed_count
    FROM lesson_bookings
    WHERE slot_id = NEW.slot_id AND status = 'confirmed';

    IF confirmed_count > slot_capacity THEN
        RAISE EXCEPTION
            'lesson_slots capacity exceeded for slot_id=%: % confirmed > % capacity',
            NEW.slot_id, confirmed_count, slot_capacity;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS lesson_bookings_capacity_guard ON lesson_bookings;

CREATE TRIGGER lesson_bookings_capacity_guard
    AFTER INSERT OR UPDATE OF status ON lesson_bookings
    FOR EACH ROW
    WHEN (NEW.status = 'confirmed')
    EXECUTE FUNCTION enforce_lesson_slot_capacity();
