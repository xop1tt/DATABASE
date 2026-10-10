# Flamenco Studio — база данных

Схема PostgreSQL Flamenco Studio: миграции и их применение. Это единственное
место, где меняется схема. Бот, веб-API (репозиторий `TELEGRAM-BOT`)
и сайт (через API) работают с одной базой, но миграции не применяют.

```text
DATABASE (этот репозиторий)
   │  push в main → GitHub Actions: тесты на PostgreSQL 17 → migrate.py
   ▼
Supabase PostgreSQL, схема flamenco  ◄── бот и API (проверяют версию схемы)
```

## Состав

| Путь | Назначение |
|---|---|
| `migrations/NNN_описание.sql` | миграции; применяются по возрастанию номера, каждая один раз |
| `migrate.py` | применение и проверка миграций (`apply`, `status`, `check`) |
| `tests/` | проверки миграций и `migrate.py`, в том числе на настоящей PostgreSQL |
| `certs/supabase-root-2021-ca.crt` | публичный CA Supabase для TLS `verify-full` |
| `docker-compose.backup.yml` | зашифрованные резервные копии на своём сервере (см. ниже) |
| `.github/workflows/migrate.yml` | тесты на каждый push и PR; применение к Supabase из `main` |
| `requirements.txt` | asyncpg с хэшами (те же версии, что у бота) |
| `env.example` | переменные `migrate.py` и резервного копирования |
| `pyproject.toml` | настройки ruff (как у бота) |

Применённые версии хранятся в таблице `schema_migrations`. Все миграции,
ожидающие применения, выполняются одной транзакцией под advisory-блокировкой:
при ошибке схема остаётся прежней, параллельные запуски ждут друг друга.

## Миграции

- `001_initial_schema.sql` — профили и заявки на занятия.
- `002_runtime_and_payments.sql` — отметка активности, баланс занятий и платежи.
- `003_operations_and_payment_ledger.sql` — устойчивые попытки оплаты, аудит
  движений занятий, возвраты и настройки запланированного рестарта.
- `004_class_slots_and_support.sql` — расписание, атомарные записи на занятия и
  сохранённые обращения в поддержку.
- `005`–`009` — веб-аккаунты и сессии сайта, защита вместимости слотов и индекс
  сверки платежей.
- `010_credit_ledger_audit.sql` — актор и причина в ledger занятий, тип
  `admin_adjustment`; платежи, попытки оплаты и ledger больше не удаляются
  каскадно вместе с профилем (`ON DELETE RESTRICT`).
- `011_slot_lifecycle.sql` — терминальный статус слота `cancelled`, причина и
  момент отмены, момент переноса, журнал изменений слота
  `lesson_slot_events`, индексы для «Моих занятий» и напоминаний.
- `012_packages_and_ledger_links.sql` — выданные студией абонементы
  `lesson_package_grants`; в ledger — ссылки на абонемент (`grant_id`) и бронь
  (`booking_id`, заполнена и для существующих строк), типы
  `slot_cancellation`, `package_grant`, `package_revoke` с обязательным
  актором.
- `013_user_notifications.sql` — outbox уведомлений участникам и их
  настройки в `bot_users` (`notify_reminders`, `notify_low_balance`).
- `014_telegram_connect.sql` — одноразовые запросы входа/привязки на сайте
  через бота (в БД — только SHA-256 токенов).
- `015_web_admins.sql` — флаг администратора сайта у веб-аккаунта
  (`users.is_admin`; с миграции 016 не используется).
- `016_staff_roles.sql` — роли сотрудников `staff_members` (`admin`,
  `teacher`) — единственный источник прав в боте и на сайте; существующие
  администраторы перенесены. Преподаватель занятия
  `lesson_slots.teacher_telegram_id`, журнал действий сотрудников
  `staff_audit_log`. Колонки `bot_users.is_admin` и `users.is_admin` остаются,
  пока работает предыдущая версия бота.
- `017_catalog_and_settings.sql` — направления `class_formats`, абонементы
  `lesson_packages` (цена, срок действия) и правила записи `studio_settings`
  вместо констант кода; CHECK на ключи заменены внешними ключами.

## Новая миграция

1. Создайте `migrations/NNN_кратко.sql` со следующим номером. Применённые
   миграции не меняйте — исправление оформляется новой миграцией.
2. Пишите без имени схемы (`CREATE TABLE bot_users`, не
   `public.bot_users`): схему задаёт `search_path` роли (в Supabase —
   `flamenco`, в тестах — временная схема).
3. Сохраняйте внешние ключи, индексы и ограничения; учитывайте данные,
   которые уже есть в базе.
4. Проверьте локально (ниже) и откройте PR — CI применит все миграции к
   пустой PostgreSQL 17.
5. В боте поднимите `REQUIRED_SCHEMA_VERSION`
   (`src/flamenco_bot/database/repository.py`), если новому коду нужна новая
   схема.

**Порядок выкладки: сначала база, потом бот.** Бот с новой
`REQUIRED_SCHEMA_VERSION` не запустится на старой схеме — Render оставит
работать предыдущую версию, данные не пострадают. Изменения схемы делайте
совместимыми с текущей версией бота (добавлять столбцы и таблицы, а не
удалять и переименовывать за один шаг).

## Локальный запуск

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt

# локальная база (Postgres.app/Homebrew)
DATABASE_URL=postgresql://flamenco_bot:flamenco_bot@localhost:5432/flamenco_bot \
DATABASE_SSL_MODE=disable .venv/bin/python migrate.py

# тесты; с PostgreSQL — на отдельной тестовой базе
TEST_DATABASE_URL=postgresql://localhost:5432/flamenco_test \
.venv/bin/python -m unittest discover -s tests -v
```

`launch.py` в общей папке проекта применяет миграции к локальной базе сам
перед запуском бота и API.

## Supabase

- Роль `flamenco_app` — владелец схемы `flamenco`, её `search_path =
  flamenco`; служебные схемы Supabase и публичный Data API (`public`) ей не
  нужны.
- Подключение — session pooler (IPv4, порт **5432**), TLS `verify-full` с
  `certs/supabase-root-2021-ca.crt`.
- GitHub-интеграция Supabase (Branching) платная и не используется: миграции
  применяет workflow этого репозитория.

Настройка один раз: GitHub → репозиторий → **Settings → Environments →
New environment** `production` → **Add environment secret** `DATABASE_URL`:

```text
postgresql://flamenco_app.<PROJECT_REF>:<ПАРОЛЬ>@aws-1-eu-central-1.pooler.supabase.com:5432/postgres
```

Состояние production без изменений: `Actions → Migrations → Run workflow`
применит только недостающие миграции; `python migrate.py status` с тем же
`DATABASE_URL` покажет версии.

## Резервные копии

У бесплатного Supabase нет автоматических резервных копий. Раз в неделю и
перед рискованными миграциями выгружайте схему `flamenco` (нужен Docker;
версия `pg_dump` — не ниже версии PostgreSQL проекта):

```bash
read -rs PGPASSWORD && export PGPASSWORD   # пароль flamenco_app
docker run --rm -e PGPASSWORD \
  -v "$HOME/flamenco-backups:/backups" \
  -v "$PWD/certs/supabase-root-2021-ca.crt:/ca.crt:ro" \
  postgres:17-alpine pg_dump \
  "host=aws-1-eu-central-1.pooler.supabase.com port=5432 dbname=postgres user=flamenco_app.<PROJECT_REF> sslmode=verify-full sslrootcert=/ca.crt" \
  --schema=flamenco --format=custom --file="/backups/flamenco-$(date +%Y%m%d).dump"
```

Дамп содержит персональные данные: храните его вне облачных папок, на
зашифрованном диске. Восстановление проверяйте в отдельную пустую базу
(`pg_restore --no-owner --no-acl`), не поверх рабочей.

### На своём сервере

[`docker-compose.backup.yml`](./docker-compose.backup.yml) запускает
контейнер `postgres:16-alpine`: при старте и затем каждые 24 часа он создаёт
`pg_dump` в формате custom, шифрует архив AES-256-CBC с PBKDF2 и сохраняет его в
Docker volume `encrypted_backups`. Для контроля целостности рядом хранится
HMAC-SHA-256 с тем же ключом. Архивы старше `BACKUP_RETENTION_DAYS` (30)
удаляются. Volume находится на Docker-хосте: отдельно обеспечьте копирование в
независимое хранилище.

1. Заполните в `.env` этой папки переменные `BACKUP_*` (список — в начале
   `docker-compose.backup.yml`). `BACKUP_DOCKER_NETWORK` — существующая
   Docker-сеть, через которую контейнер достигнет PostgreSQL; `BACKUP_PGHOST` —
   hostname базы в этой сети. По умолчанию `BACKUP_PGSSLMODE=verify-full`; для
   частного CA задайте `BACKUP_PGSSLROOTCERT` и смонтируйте сертификат.
2. Сгенерируйте длинный случайный `BACKUP_ENCRYPTION_KEY` и храните его отдельно
   от сервера и volume: без ключа архивы не восстановить.
3. Запустите:

   ```bash
   docker compose --env-file .env -f docker-compose.backup.yml up -d
   docker compose --env-file .env -f docker-compose.backup.yml logs -f postgres-backup
   ```

Восстановление архива в пустую тестовую базу:

```bash
docker compose --env-file .env -f docker-compose.backup.yml exec -T \
  -e BACKUP_RESTORE_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/EMPTY_TEST_DB' \
  postgres-backup sh -o pipefail -ec \
  'archive=/backups/flamenco-YYYYMMDDTHHMMSSZ.dump.enc
   expected=$(cat "$archive.hmac")
   actual=$(openssl dgst -sha256 \
     -hmac "integrity:$BACKUP_ENCRYPTION_KEY" "$archive" | awk "{print \$2}")
   test "$actual" = "$expected"
   openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
    -pass env:BACKUP_ENCRYPTION_KEY \
    -in "$archive" |
   pg_restore --no-owner --no-acl --dbname="$BACKUP_RESTORE_DATABASE_URL"'
```
