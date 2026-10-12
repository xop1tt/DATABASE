"""Обновление схемы с данными: миграции не теряют и правильно переносят то,
что уже лежит в production-базе.

Схема доводится до версии N, в неё вставляются «старые» данные, затем
применяются оставшиеся миграции, и проверяется результат.
"""

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import migrate
from pg_support import PostgresTestCase, requires_postgres


@requires_postgres
class UpgradeWithDataTests(PostgresTestCase):
    apply_all = False

    async def migrate_to(self, version):
        """Применить миграции до версии включительно (во временной копии)."""
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        for number, path in migrate.migration_files():
            if int(number) <= int(version):
                shutil.copy(path, directory / path.name)
        return await migrate.apply_migrations(self.connection, directory)

    async def migrate_rest(self):
        return await migrate.apply_migrations(self.connection)

    async def test_rows_from_the_first_version_survive_all_migrations(self):
        await self.migrate_to("001")
        await self.connection.execute(
            "INSERT INTO bot_users (telegram_id, user_name, phone) "
            "VALUES (5, 'Анна', '+79990000000')"
        )
        await self.connection.execute(
            "INSERT INTO lesson_requests (telegram_id, kind, details) "
            "VALUES (5, 'booking', 'вечер')"
        )
        await self.migrate_rest()
        row = await self.connection.fetchrow("SELECT * FROM bot_users")
        self.assertEqual((row["user_name"], row["phone"]), ("Анна", "+79990000000"))
        self.assertEqual(row["lesson_credits"], 0)
        self.assertEqual(
            await self.connection.fetchval("SELECT details FROM lesson_requests"),
            "вечер",
        )

    async def test_web_session_tokens_are_hashed_in_place(self):
        await self.migrate_to("008")
        user_id = await self.connection.fetchval(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES ('a@b.c', 'h', 'a') RETURNING id"
        )
        await self.connection.execute(
            "INSERT INTO web_sessions (token, user_id, expires_at) "
            "VALUES ('plain-token', $1, NOW() + INTERVAL '1 day')",
            user_id,
        )
        await self.migrate_rest()
        stored = await self.connection.fetchval("SELECT token_hash FROM web_sessions")
        self.assertEqual(stored, hashlib.sha256(b"plain-token").hexdigest())
        self.assertNotEqual(stored, "plain-token")

    async def test_booking_links_are_backfilled_from_reference_keys(self):
        await self.migrate_to("011")
        await self.connection.execute(
            "INSERT INTO bot_users (telegram_id, user_name, lesson_credits) "
            "VALUES (1, 'a', 0)"
        )
        slot = await self.add_slot()
        booking = await self.connection.fetchval(
            "INSERT INTO lesson_bookings (slot_id, telegram_id) VALUES ($1, 1) "
            "RETURNING id",
            slot,
        )
        await self.connection.execute(
            "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta, "
            "reference_key) VALUES (1, 'lesson_use', -1, $1), "
            "(1, 'adjustment', 1, 'slot_booking:999999:use'), "
            "(1, 'adjustment', 1, 'unrelated')",
            "slot_booking:{}:use".format(booking),
        )
        await self.migrate_rest()
        rows = {
            r["reference_key"]: r["booking_id"]
            for r in await self.connection.fetch(
                "SELECT reference_key, booking_id FROM lesson_credit_ledger"
            )
        }
        self.assertEqual(rows["slot_booking:{}:use".format(booking)], booking)
        # Ссылка на несуществующую бронь и посторонние ключи не привязываются.
        self.assertIsNone(rows["slot_booking:999999:use"])
        self.assertIsNone(rows["unrelated"])

    async def test_existing_admins_become_staff_members(self):
        await self.migrate_to("015")
        await self.connection.execute(
            "INSERT INTO bot_users (telegram_id, user_name, is_admin) VALUES "
            "(1, 'bot admin', TRUE), (2, 'web admin', FALSE), "
            "(3, 'client', FALSE), (4, 'both', TRUE)"
        )
        await self.connection.execute(
            "INSERT INTO users (telegram_id, display_name, is_admin) VALUES "
            "(2, 'web admin', TRUE), (3, 'client', FALSE), (4, 'both', TRUE)"
        )
        await self.migrate_rest()
        staff = {
            r["telegram_id"]: r["role"]
            for r in await self.connection.fetch("SELECT * FROM staff_members")
        }
        self.assertEqual(staff, {1: "admin", 2: "admin", 4: "admin"})

    async def test_credit_expiry_backfill_preserves_every_users_balance(self):
        await self.migrate_to("017")
        await self.connection.execute(
            "INSERT INTO bot_users (telegram_id, user_name, lesson_credits) VALUES "
            "(1, 'без абонемента', 5), (2, 'по платежу', 3), "
            "(3, 'много', 150), (4, 'ноль', 0)"
        )
        payment = await self.connection.fetchval(
            "INSERT INTO lesson_payments (telegram_id, package_key, lessons, "
            "amount_minor, provider_payment_id, confirmation_url, status) "
            "VALUES (2, 'pack_4', 4, 360000, 'p', 'u', 'succeeded') RETURNING id"
        )
        await self.connection.execute(
            "INSERT INTO lesson_credit_ledger (telegram_id, payment_id, "
            "entry_type, delta) VALUES (2, $1, 'purchase', 4)",
            payment,
        )
        await self.connection.execute(
            "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta) "
            "VALUES (2, 'lesson_use', -1)"
        )

        await self.migrate_rest()

        # Платёж получил срок по пакету (pack_4 — 30 дней).
        days = await self.connection.fetchval(
            "SELECT validity_days FROM lesson_payments WHERE id = $1", payment
        )
        self.assertEqual(days, 30)
        left = await self.connection.fetchval(
            "SELECT expires_at - NOW() FROM lesson_payments WHERE id = $1", payment
        )
        self.assertGreater(left.days, 28)
        self.assertLessEqual(left.days, 30)

        grants = {
            r["telegram_id"]: r["lessons"]
            for r in await self.connection.fetch(
                "SELECT telegram_id, lessons FROM lesson_package_grants "
                "WHERE package_key = 'manual'"
            )
        }
        # Остаток вне абонементов получает срок; покрытый платежом — нет;
        # выдача ограничена 100 занятиями (CHECK), ноль не создаёт записей.
        self.assertEqual(grants, {1: 5, 3: 100})

        # Каждая выдача оформлена парой записей ledger с нулевой суммой по
        # пулу: баланс участника в ledger не изменился.
        for telegram_id in grants:
            entries = await self.connection.fetch(
                "SELECT entry_type, delta FROM lesson_credit_ledger "
                "WHERE telegram_id = $1 ORDER BY id",
                telegram_id,
            )
            self.assertEqual(
                sorted((e["entry_type"], e["delta"]) for e in entries),
                sorted(
                    [
                        ("package_grant", grants[telegram_id]),
                        ("adjustment", -grants[telegram_id]),
                    ]
                ),
            )
        # Сами балансы bot_users не тронуты.
        balances = {
            r["telegram_id"]: r["lesson_credits"]
            for r in await self.connection.fetch(
                "SELECT telegram_id, lesson_credits FROM bot_users"
            )
        }
        self.assertEqual(balances, {1: 5, 2: 3, 3: 150, 4: 0})

    async def test_partial_upgrade_is_resumable(self):
        applied_first = await self.migrate_to("010")
        self.assertEqual(applied_first[-1], "010")
        applied_rest = await self.migrate_rest()
        self.assertEqual(applied_rest[0], "011")
        self.assertEqual(await self.migrate_rest(), [])


class MigrationFilesContractTests(unittest.TestCase):
    """Правила репозитория, которые нельзя нарушить незаметно."""

    def test_versions_are_contiguous_without_gaps(self):
        numbers = [int(v) for v, _ in migrate.migration_files()]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def test_file_names_are_snake_case_with_three_digit_version(self):
        import re

        for _, path in migrate.migration_files():
            self.assertRegex(path.name, r"^\d{3}_[a-z0-9_]+\.sql$")
            self.assertTrue(re.fullmatch(r"\d{3}", path.name[:3]))

    def test_migrations_do_not_hardcode_a_schema_name(self):
        for _, path in migrate.migration_files():
            text = path.read_text(encoding="utf-8").lower()
            for forbidden in (
                "flamenco.",
                "public.",
                "create schema",
                "set search_path",
            ):
                self.assertNotIn(forbidden, text, path.name)

    def test_migrations_do_not_manage_transactions_themselves(self):
        """apply_migrations оборачивает всё в одну транзакцию: собственные
        BEGIN/COMMIT внутри файла сломали бы откат."""
        import re

        for _, path in migrate.migration_files():
            text = re.sub(r"--.*", "", path.read_text(encoding="utf-8"))
            self.assertIsNone(
                re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", text, re.I | re.M),
                path.name,
            )

    def test_migrations_contain_no_secrets(self):
        for _, path in migrate.migration_files():
            text = path.read_text(encoding="utf-8").lower()
            for marker in ("password '", "postgresql://", "secret_key", "bearer "):
                self.assertNotIn(marker, text, path.name)


if __name__ == "__main__":
    unittest.main()
