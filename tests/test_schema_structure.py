"""Структура схемы после всех миграций: таблицы, ключи, индексы, ограничения.

Проверяется реальный каталог PostgreSQL (information_schema / pg_catalog), а не
текст SQL-файлов: тест упадёт, если миграция не создаст объект или создаст не
тот.
"""

import unittest

from pg_support import PostgresTestCase, requires_postgres

EXPECTED_TABLES = {
    "bot_users",
    "broadcasts",
    "bot_runtime_settings",
    "class_formats",
    "client_notes",
    "lesson_bookings",
    "lesson_credit_ledger",
    "lesson_package_grants",
    "lesson_packages",
    "lesson_payment_attempts",
    "lesson_payment_events",
    "lesson_payments",
    "lesson_requests",
    "lesson_slot_events",
    "lesson_slots",
    "lesson_waitlist",
    "schema_migrations",
    "site_maintenance",
    "site_maintenance_dispatches",
    "site_maintenance_events",
    "staff_audit_log",
    "staff_members",
    "studio_settings",
    "support_messages",
    "support_tickets",
    "telegram_connect_requests",
    "user_notifications",
    "users",
    "web_sessions",
}

# (таблица, колонка) -> ссылка и действие при удалении родителя.
# a = NO ACTION, r = RESTRICT, c = CASCADE, n = SET NULL
EXPECTED_FOREIGN_KEYS = {
    ("lesson_requests", "telegram_id"): ("bot_users", "c"),
    ("lesson_bookings", "slot_id"): ("lesson_slots", "r"),
    ("lesson_bookings", "telegram_id"): ("bot_users", "c"),
    ("support_tickets", "telegram_id"): ("bot_users", "c"),
    ("support_messages", "ticket_id"): ("support_tickets", "c"),
    ("lesson_waitlist", "slot_id"): ("lesson_slots", "c"),
    ("lesson_waitlist", "telegram_id"): ("bot_users", "c"),
    ("user_notifications", "telegram_id"): ("bot_users", "c"),
    ("client_notes", "telegram_id"): ("bot_users", "c"),
    ("web_sessions", "user_id"): ("users", "c"),
    ("users", "telegram_id"): ("bot_users", "n"),
    ("lesson_slot_events", "slot_id"): ("lesson_slots", "c"),
    ("lesson_payment_events", "payment_id"): ("lesson_payments", "c"),
    # Финансовая история не должна исчезать вместе с пользователем.
    ("lesson_payments", "telegram_id"): ("bot_users", "r"),
    ("lesson_payment_attempts", "telegram_id"): ("bot_users", "r"),
    ("lesson_credit_ledger", "telegram_id"): ("bot_users", "r"),
    ("lesson_package_grants", "telegram_id"): ("bot_users", "r"),
    ("staff_members", "telegram_id"): ("bot_users", "r"),
    ("lesson_credit_ledger", "grant_id"): ("lesson_package_grants", "r"),
    ("lesson_credit_ledger", "payment_id"): ("lesson_payments", "n"),
    ("lesson_credit_ledger", "booking_id"): ("lesson_bookings", "n"),
    ("lesson_slots", "class_key"): ("class_formats", "r"),
    ("lesson_slots", "teacher_telegram_id"): ("staff_members", "n"),
    ("lesson_payments", "package_key"): ("lesson_packages", "r"),
}

# Индексы, на которых держатся идемпотентность и защита от дублей.
EXPECTED_INDEXES = {
    "lesson_payments_idempotence_key_idx",
    "lesson_payments_refund_idempotence_key_idx",
    "lesson_payment_attempts_active_user_idx",
    "lesson_credit_ledger_purchase_once_idx",
    "lesson_credit_ledger_grant_once_idx",
    "lesson_payment_events_created_once_idx",
    "support_tickets_one_open_per_user_idx",
    "users_email_unique_idx",
    "site_maintenance_single_open_idx",
    "user_notifications_due_idx",
    "lesson_credit_ledger_user_created_idx",
    "lesson_bookings_slot_status_idx",
}


@requires_postgres
class SchemaStructureTests(PostgresTestCase):
    async def _tables(self):
        records = await self.connection.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
        )
        return {record["table_name"] for record in records}

    async def test_all_expected_tables_exist(self):
        self.assertEqual(await self._tables(), EXPECTED_TABLES)

    async def test_every_table_has_a_primary_key(self):
        records = await self.connection.fetch(
            """
            SELECT table_name FROM information_schema.tables AS t
            WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
              AND NOT EXISTS (
                  SELECT 1 FROM information_schema.table_constraints AS c
                  WHERE c.table_schema = t.table_schema
                    AND c.table_name = t.table_name
                    AND c.constraint_type = 'PRIMARY KEY'
              )
            """
        )
        self.assertEqual([r["table_name"] for r in records], [])

    async def test_foreign_keys_have_expected_delete_behaviour(self):
        records = await self.connection.fetch(
            """
            SELECT c.conrelid::regclass::text AS child, a.attname AS column,
                   c.confrelid::regclass::text AS parent, c.confdeltype::text AS action
            FROM pg_constraint AS c
            JOIN pg_attribute AS a
              ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.contype = 'f' AND c.connamespace = current_schema()::regnamespace
            """
        )
        actual = {
            (r["child"], r["column"]): (r["parent"], r["action"]) for r in records
        }
        for key, expected in EXPECTED_FOREIGN_KEYS.items():
            self.assertEqual(actual.get(key), expected, key)

    async def test_expected_indexes_exist(self):
        records = await self.connection.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
        )
        names = {r["indexname"] for r in records}
        self.assertEqual(EXPECTED_INDEXES - names, set())

    async def test_every_foreign_key_column_is_indexed_or_keyed(self):
        """Дочерние колонки внешних ключей входят в какой-либо индекс
        (иначе удаление родителя и JOIN-ы превращаются в полный просмотр)."""
        records = await self.connection.fetch(
            """
            SELECT c.conrelid::regclass::text AS child, a.attname AS column
            FROM pg_constraint AS c
            JOIN pg_attribute AS a
              ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
            WHERE c.contype = 'f' AND c.connamespace = current_schema()::regnamespace
              AND NOT EXISTS (
                  SELECT 1 FROM pg_index AS i
                  WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1]
              )
            """
        )
        unindexed = sorted((r["child"], r["column"]) for r in records)
        # Известные исключения: таблицы-справочники и редкие запросы, где
        # ключ не участвует в выборках. Новые «голые» внешние ключи — повод
        # осознанно добавить индекс или дополнить этот список.
        known = {
            ("lesson_slots", "class_key"),
            ("lesson_payments", "package_key"),
            ("lesson_credit_ledger", "payment_id"),  # частичный индекс есть
            ("staff_members", "telegram_id"),
            # Устаревшая таблица заявок и короткоживущие запросы подключения
            # Telegram: строк мало, индекс не нужен (проверено 2026-10).
            ("lesson_requests", "telegram_id"),
            ("telegram_connect_requests", "telegram_id"),
            ("telegram_connect_requests", "web_user_id"),
        }
        unexpected = [pair for pair in unindexed if pair not in known]
        self.assertEqual(unexpected, [])

    async def test_critical_columns_are_not_null(self):
        records = await self.connection.fetch(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = current_schema() AND is_nullable = 'YES'
            """
        )
        nullable = {(r["table_name"], r["column_name"]) for r in records}
        for pair in (
            ("bot_users", "telegram_id"),
            ("bot_users", "user_name"),
            ("bot_users", "lesson_credits"),
            ("lesson_credit_ledger", "telegram_id"),
            ("lesson_credit_ledger", "delta"),
            ("lesson_credit_ledger", "entry_type"),
            ("lesson_credit_ledger", "created_at"),
            ("lesson_payments", "amount_minor"),
            ("lesson_payments", "status"),
            ("lesson_bookings", "slot_id"),
            ("lesson_bookings", "status"),
            ("lesson_slots", "capacity"),
            ("lesson_slots", "starts_at"),
        ):
            self.assertNotIn(pair, nullable)

    async def test_timestamp_columns_keep_the_time_zone(self):
        """Все моменты времени хранятся как timestamptz: naive timestamp
        сместил бы время занятий при смене пояса сервера."""
        records = await self.connection.fetch(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND data_type = 'timestamp without time zone'
            """
        )
        self.assertEqual([tuple(r) for r in records], [])

    async def test_defaults_of_new_user(self):
        await self.add_user(10)
        row = await self.connection.fetchrow("SELECT * FROM bot_users")
        self.assertEqual(row["lesson_credits"], 0)
        self.assertFalse(row["is_admin"])
        self.assertTrue(row["notify_reminders"])
        self.assertTrue(row["notify_low_balance"])
        self.assertIsNotNone(row["registered_at"])
        self.assertIsNotNone(row["last_seen_at"])

    async def test_seed_catalog_and_settings(self):
        formats = await self.connection.fetch(
            "SELECT key FROM class_formats ORDER BY sort_order"
        )
        self.assertEqual(
            [r["key"] for r in formats], ["beginner", "intermediate", "individual"]
        )
        packages = {
            r["key"]: (r["lessons"], r["price_minor"], r["validity_days"])
            for r in await self.connection.fetch("SELECT * FROM lesson_packages")
        }
        self.assertEqual(
            packages,
            {
                "single": (1, 100000, 30),
                "pack_4": (4, 360000, 30),
                "pack_8": (8, 680000, 60),
            },
        )
        settings = await self.connection.fetchrow("SELECT * FROM studio_settings")
        self.assertEqual(settings["cancellation_deadline_hours"], 24)
        self.assertEqual(settings["rebook_cooldown_hours"], 12)
        self.assertEqual(settings["manual_credit_validity_days"], 60)
        self.assertEqual(settings["expiry_warning_days"], 7)

    async def test_studio_settings_is_a_singleton(self):
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO studio_settings (id) VALUES (FALSE)",
        )
        await self.assertRejected(
            self.asyncpg.UniqueViolationError,
            "INSERT INTO studio_settings (id) VALUES (TRUE)",
        )

    async def test_schema_migrations_lists_every_file(self):
        import migrate

        recorded = await migrate.applied_versions(self.connection)
        self.assertEqual(recorded, {v for v, _ in migrate.migration_files()})


if __name__ == "__main__":
    unittest.main()
