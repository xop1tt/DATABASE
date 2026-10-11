-- Техническое обслуживание сайта: окна работ, журнал событий и факты
-- постановки рассылок о них.
--
-- Окно — запись site_maintenance: заголовок и описание для страницы
-- обслуживания, начало и предполагаемое окончание (TIMESTAMPTZ, UTC),
-- моменты предупреждений участникам. Статусы: scheduled → active →
-- completed, scheduled → cancelled. Незавершённое окно (scheduled или
-- active) одновременно только одно — так «текущее состояние» однозначно.
-- Окно с наступившим началом считается активным сразу (сайт и API
-- вычисляют это по starts_at), фоновая задача лишь фиксирует переход и
-- ставит уведомления. Завершается окно только действием администратора:
-- наступление ends_at обслуживание не выключает.
--
-- Уведомления идут через общий outbox user_notifications. Строка в
-- site_maintenance_dispatches — факт постановки одного этапа рассылки
-- (предупреждение за N минут для конкретной редакции времени, начало,
-- перенос, отмена, завершение): первичный ключ не даёт поставить этап
-- дважды при перезапусках и параллельной работе бота и API, а для
-- пропущенного (запоздавшего) предупреждения хранится причина.

CREATE TABLE IF NOT EXISTS site_maintenance (
    id BIGSERIAL PRIMARY KEY,
    status TEXT NOT NULL
        CHECK (status IN ('scheduled', 'active', 'completed', 'cancelled')),
    title TEXT NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 120),
    description TEXT NOT NULL
        CHECK (char_length(btrim(description)) BETWEEN 1 AND 1000),
    starts_at TIMESTAMPTZ NOT NULL,
    -- Предполагаемое окончание; NULL — неизвестно.
    ends_at TIMESTAMPTZ CHECK (ends_at IS NULL OR ends_at > starts_at),
    -- За сколько минут до начала предупреждать; пустой массив — без
    -- предупреждений.
    warning_minutes INTEGER[] NOT NULL DEFAULT '{1440,60,10}'
        CHECK (
            cardinality(warning_minutes) <= 6
            AND array_position(warning_minutes, NULL) IS NULL
            AND 0 < ALL (warning_minutes)
            AND 10080 >= ALL (warning_minutes)
        ),
    -- FALSE — окно без уведомлений в Telegram.
    notify_users BOOLEAN NOT NULL DEFAULT TRUE,
    -- 'staff' — тестовый режим: уведомления получают только сотрудники.
    audience TEXT NOT NULL DEFAULT 'all' CHECK (audience IN ('all', 'staff')),
    -- Растёт при каждом изменении времени: предупреждения прежней редакции
    -- устаревают, новые ставятся заново.
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status <> 'active' OR started_at IS NOT NULL),
    CHECK (status NOT IN ('completed', 'cancelled') OR finished_at IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS site_maintenance_single_open_idx
    ON site_maintenance ((TRUE))
    WHERE status IN ('scheduled', 'active');

CREATE INDEX IF NOT EXISTS site_maintenance_created_idx
    ON site_maintenance (created_at DESC);

CREATE TABLE IF NOT EXISTS site_maintenance_events (
    id BIGSERIAL PRIMARY KEY,
    maintenance_id BIGINT NOT NULL
        REFERENCES site_maintenance(id) ON DELETE CASCADE,
    event TEXT NOT NULL
        CHECK (event IN (
            'created', 'updated', 'rescheduled', 'started', 'completed',
            'cancelled', 'notifications_queued', 'warning_skipped'
        )),
    -- NULL — действие системы (автоматический старт, рассылка).
    actor_telegram_id BIGINT,
    details JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS site_maintenance_events_window_idx
    ON site_maintenance_events (maintenance_id, id);

CREATE TABLE IF NOT EXISTS site_maintenance_dispatches (
    maintenance_id BIGINT NOT NULL
        REFERENCES site_maintenance(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    -- 'warning:<минуты>', 'started', 'rescheduled', 'cancelled', 'completed'
    stage TEXT NOT NULL CHECK (char_length(stage) BETWEEN 1 AND 40),
    recipients INTEGER NOT NULL DEFAULT 0 CHECK (recipients >= 0),
    -- Не NULL — этап пропущен (например, 'late': время предупреждения
    -- прошло до того, как его можно было отправить вовремя).
    skipped_reason TEXT CHECK (skipped_reason IS NULL OR char_length(skipped_reason) <= 40),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (maintenance_id, revision, stage)
);

ALTER TABLE user_notifications DROP CONSTRAINT IF EXISTS user_notifications_kind_check;
ALTER TABLE user_notifications
    ADD CONSTRAINT user_notifications_kind_check
    CHECK (kind IN (
        'booking_confirmed', 'booking_cancelled', 'slot_cancelled',
        'slot_rescheduled', 'lesson_reminder', 'low_balance',
        'package_granted', 'package_revoked', 'credits_adjusted',
        'package_expiring', 'credits_expired', 'waitlist_seat',
        'announcement', 'maintenance_warning', 'maintenance_started',
        'maintenance_rescheduled', 'maintenance_cancelled',
        'maintenance_completed'
    ));

-- Кому уже ушли уведомления об окне (перенос, отмена, завершение — только
-- им) и статистика доставки по окну.
CREATE INDEX IF NOT EXISTS user_notifications_maintenance_idx
    ON user_notifications ((payload ->> 'maintenance_id'), kind)
    WHERE kind LIKE 'maintenance\_%';
