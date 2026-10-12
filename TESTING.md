# Тестирование схемы БД

Репозиторий — SQL-миграции и `migrate.py`, поэтому тесты проверяют **настоящую
схему в PostgreSQL**, а не текст SQL: что создалось, какие ограничения держат
данные, что происходит с существующими строками при обновлении схемы.
SQLite не подходит (частичные индексы, `CHECK`-выражения, триггеры plpgsql,
`JSONB`, `gen_random_uuid`).

## Запуск

```bash
python -m pip install --require-hashes -r requirements.txt    # asyncpg
python -m pip install coverage==7.16.2 ruff==0.17.0            # только для проверок

# Одноразовая БД (не production и не Supabase!). Каждый тест создаёт свою
# схему test_*_<pid>_<ns> и удаляет её после себя.
docker run --rm -d -p 55432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=test postgres:17
export TEST_DATABASE_URL=postgresql://test:test@127.0.0.1:55432/test

python -m unittest discover -s tests -v                        # все тесты
python -m coverage run --branch --source=. --omit='tests/*' \
  -m unittest discover -s tests && python -m coverage report --show-missing
ruff check . && ruff format --check .
```

Без `TEST_DATABASE_URL` выполняются 31 тест без БД (имена файлов, SSL, CLI с
подменой подключения, правила файлов миграций), остальные 74 **пропускаются с
причиной** `TEST_DATABASE_URL не задан`. С базой — 105 тестов, 0 пропусков,
покрытие `migrate.py`: 98.8% строк, 96.4% ветвей (непокрыт только вызов
`sys.exit(main())` под `if __name__ == "__main__"`).

## Файлы

| Файл | Что проверяет |
|---|---|
| `tests/test_migrate.py` | порядок версий, SSL-режимы, применение «с нуля», дозапуск, откат неудачной миграции, ограничения 021 |
| `tests/test_schema_structure.py` | итоговый каталог: все таблицы, PK, внешние ключи и их `ON DELETE` (финансовая история — `RESTRICT`), индексы идемпотентности, `NOT NULL`, `timestamptz`, начальные данные, `studio_settings` как singleton |
| `tests/test_data_integrity.py` | отрицательные сценарии `CHECK`/`UNIQUE`/`FK` для пользователей, веб-аккаунтов, занятий, **бронирований (триггер вместимости)**, платежей, **ledger**, абонементов, поддержки, уведомлений, каталога, персонала, рассылок; атомарность и конкурентные списания |
| `tests/test_migration_upgrades.py` | обновление **с данными**: хэширование токенов сессий (009), привязка ledger к броням (012), сотрудники из админов (016), сроки действия и «ручные» выдачи (018) с сохранением балансов; правила файлов миграций |
| `tests/test_maintenance_schema.py` | миграция 021: массив предупреждений, каскады событий и этапов рассылки, единственное открытое окно |
| `tests/test_migrate_cli.py` | `status/check/apply`, `connect_from_env`, TLS; цикл `check → apply → check` на реальной схеме |
| `tests/pg_support.py` | общая основа: временная схема, помощники данных |

## Правила

* Миграции пишутся **без имени схемы**; схему выбирает `search_path` (в тестах —
  временная). Тест `test_migrations_do_not_hardcode_a_schema_name` это страхует.
* Применённые миграции не редактируются. Новая миграция = новый файл `NNN_*.sql` +
  тест, если она меняет ограничения или переносит данные: добавьте проверку в
  `test_data_integrity.py` (ограничение) и/или `test_migration_upgrades.py`
  (данные «до/после»).
* Тесты не должны зависеть друг от друга — у каждого своя схема.
* Не подключайте тесты к production: `TEST_DATABASE_URL` — только одноразовая БД.

## Известные ограничения и особенности схемы (зафиксированы тестами)

* **Триггер вместимости не защищает от гонки сам по себе**: две параллельные
  транзакции без блокировки занятия обе вставляют бронь последнего места
  (триггер видит только закоммиченные строки).
  `test_capacity_trigger_alone_does_not_serialize_concurrent_inserts` фиксирует это;
  вместимость держит приложение (`SELECT … FOR UPDATE` по занятию, проверено
  тестами бота). Если защиту перенесут в БД, тест надо обновить.
* `users.telegram_id` — `ON DELETE SET NULL`, но `CHECK (email IS NOT NULL OR
  telegram_id IS NOT NULL)`: удалить `bot_users` у веб-аккаунта **без email**
  нельзя (БД отклонит). Зафиксировано как поведение.
* Нет индексов на `lesson_requests.telegram_id` и `telegram_connect_requests.*`
  (таблицы малы) — в явном списке исключений теста.
* Нет триггера, связывающего `bot_users.lesson_credits` и сумму ledger: их
  согласованность держит приложение (`_change_credits`) и проверяет «Сверка
  баланса»; в БД — только `CHECK (lesson_credits >= 0)`.
* `downgrade` в проекте не предусмотрен (миграции только вперёд) — не тестируется.
* Локально версия PostgreSQL может быть выше CI (17); критично только то, что
  схема применяется на 17 (так в CI и Supabase).
