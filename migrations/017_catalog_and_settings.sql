-- Направления, абонементы и правила записи — в базе, а не в коде.
--
-- Раньше направления (class_catalog.py), абонементы с ценами
-- (payments/catalog.py) и правила записи (константы в repository.py) жили в
-- коде бота, а ключи были зашиты в CHECK-ограничения: чтобы поменять цену
-- или добавить направление, нужны были миграция и деплой. Теперь их меняет
-- администратор в админке сайта; бот и API читают их отсюда.
--
-- Ключи (key) неизменяемы: на них ссылаются занятия, платежи и выдачи.
-- Ненужное направление или абонемент не удаляется, а скрывается
-- (is_active = FALSE): прошлые занятия и покупки сохраняют название.

CREATE TABLE IF NOT EXISTS class_formats (
    key TEXT PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,31}$'),
    title TEXT NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 100),
    short_title TEXT NOT NULL
        CHECK (char_length(btrim(short_title)) BETWEEN 1 AND 32),
    description TEXT NOT NULL DEFAULT '' CHECK (char_length(description) <= 1000),
    level TEXT NOT NULL DEFAULT '' CHECK (char_length(level) <= 100),
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO class_formats (key, title, short_title, description, level, sort_order)
VALUES
    (
        'beginner', 'Фламенко для начинающих', 'Начинающие',
        'Группа для тех, кто никогда не танцевал фламенко или делает первые '
        'шаги. Базовая техника, ритм, работа с телом — без предварительной '
        'подготовки.',
        'Для начинающих, без подготовки', 10
    ),
    (
        'intermediate', 'Продолжающая группа', 'Продолжающие',
        'Для тех, кто уже освоил основы и хочет двигаться дальше: усложнённые '
        'связки, работа с партнёром по классу, более быстрый темп.',
        'Для продолжающих', 20
    ),
    (
        'individual', 'Индивидуальное занятие', 'Индивидуально',
        'Персональный разбор техники и подготовка к выступлению в удобное '
        'время, в своём темпе.',
        'Индивидуально, любой уровень', 30
    )
ON CONFLICT (key) DO NOTHING;

ALTER TABLE lesson_slots DROP CONSTRAINT IF EXISTS lesson_slots_class_key_check;
ALTER TABLE lesson_slots DROP CONSTRAINT IF EXISTS lesson_slots_class_key_fkey;
ALTER TABLE lesson_slots
    ADD CONSTRAINT lesson_slots_class_key_fkey
    FOREIGN KEY (class_key) REFERENCES class_formats(key) ON DELETE RESTRICT;

-- validity_days — срок действия с даты покупки или выдачи; NULL — бессрочно.
CREATE TABLE IF NOT EXISTS lesson_packages (
    key TEXT PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,31}$'),
    title TEXT NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 100),
    lessons INTEGER NOT NULL CHECK (lessons BETWEEN 1 AND 100),
    price_minor BIGINT NOT NULL CHECK (price_minor > 0),
    validity_days INTEGER CHECK (validity_days IS NULL OR validity_days BETWEEN 1 AND 3650),
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Цены — те, что были в payments/catalog.py; сроки — решение владельца
-- студии (2026-10-10).
INSERT INTO lesson_packages (key, title, lessons, price_minor, validity_days, sort_order)
VALUES
    ('single', 'Разовое занятие', 1, 100000, 30, 10),
    ('pack_4', 'Абонемент на 4 занятия', 4, 360000, 30, 20),
    ('pack_8', 'Абонемент на 8 занятий', 8, 680000, 60, 30)
ON CONFLICT (key) DO NOTHING;

ALTER TABLE lesson_payments DROP CONSTRAINT IF EXISTS lesson_payments_package_key_check;
ALTER TABLE lesson_payments DROP CONSTRAINT IF EXISTS lesson_payments_package_key_fkey;
ALTER TABLE lesson_payments
    ADD CONSTRAINT lesson_payments_package_key_fkey
    FOREIGN KEY (package_key) REFERENCES lesson_packages(key) ON DELETE RESTRICT;

-- Правила записи студии — одна строка. Значения по умолчанию — прежние
-- константы кода (подтверждены владельцем продукта).
CREATE TABLE IF NOT EXISTS studio_settings (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    -- Отменить запись можно не позднее чем за столько часов до начала.
    cancellation_deadline_hours INTEGER NOT NULL DEFAULT 24
        CHECK (cancellation_deadline_hours BETWEEN 0 AND 168),
    -- После своей отмены повторно записаться на то же занятие — через.
    rebook_cooldown_hours INTEGER NOT NULL DEFAULT 12
        CHECK (rebook_cooldown_hours BETWEEN 0 AND 168),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by BIGINT
);

INSERT INTO studio_settings (id) VALUES (TRUE) ON CONFLICT (id) DO NOTHING;
