"""Миграции и их запуск (migrate.py).

Тесты с PostgreSQL требуют TEST_DATABASE_URL (отдельная тестовая база):
каждый тест создаёт свою временную схему и удаляет её после себя.
"""

import os
import shutil
import ssl
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import migrate  # noqa: E402

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
CA = ROOT / "certs" / "supabase-root-2021-ca.crt"


class MigrationFilesTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)

    def _write(self, *names):
        for name in names:
            (self.directory / name).write_text("SELECT 1;", encoding="utf-8")

    def test_versions_are_ordered_numerically(self):
        self._write("010_ten.sql", "002_two.sql", "001_one.sql")
        versions = [v for v, _ in migrate.migration_files(self.directory)]
        self.assertEqual(versions, ["001", "002", "010"])

    def test_name_without_numeric_version_is_rejected(self):
        self._write("001_one.sql", "initial.sql")
        with self.assertRaisesRegex(ValueError, "числовой версии"):
            migrate.migration_files(self.directory)

    def test_duplicate_version_is_rejected(self):
        self._write("003_a.sql", "003_b.sql")
        with self.assertRaisesRegex(ValueError, "Две миграции"):
            migrate.migration_files(self.directory)

    def test_repository_migrations_are_valid(self):
        versions = [v for v, _ in migrate.migration_files()]
        self.assertEqual(versions[0], "001")
        self.assertEqual(len(versions), len(set(versions)))


class MigrationContentTests(unittest.TestCase):
    def _read(self, name):
        return (migrate.MIGRATIONS_DIRECTORY / name).read_text(encoding="utf-8")

    def test_initial_schema_protects_profile_identity_and_request_states(self):
        migration = self._read("001_initial_schema.sql")
        self.assertIn("telegram_id BIGINT PRIMARY KEY", migration)
        self.assertIn("CHECK (kind IN ('booking', 'purchase'))", migration)
        self.assertIn("REFERENCES bot_users", migration)

    def test_runtime_payment_migration_tracks_activity_and_paid_credits(self):
        migration = self._read("002_runtime_and_payments.sql")
        self.assertIn("last_seen_at", migration)
        self.assertIn("lesson_credits", migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS lesson_payments", migration)


class SslSettingTests(unittest.TestCase):
    def test_disable_only_for_localhost(self):
        self.assertIs(
            migrate.ssl_setting("postgresql://u@localhost/db", "disable", None), False
        )
        self.assertIs(
            migrate.ssl_setting("postgresql://u@127.0.0.1/db", "disable", None), False
        )
        with self.assertRaisesRegex(ValueError, "localhost"):
            migrate.ssl_setting("postgresql://u@db.example.net/db", "disable", None)

    def test_verify_full_checks_hostname_with_private_ca(self):
        context = migrate.ssl_setting(
            "postgresql://u@pooler.example.net/db", "verify-full", str(CA)
        )
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertFalse(context.verify_flags & getattr(ssl, "VERIFY_X509_STRICT", 0))

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            migrate.ssl_setting("postgresql://u@localhost/db", "require", None)


@unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL не задан")
class ApplyMigrationsPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg

        self.schema = "test_migrate_{}_{}".format(os.getpid(), time.time_ns())
        self.admin = await asyncpg.connect(DATABASE_URL)
        await self.admin.execute('CREATE SCHEMA "{}"'.format(self.schema))
        self.connection = await asyncpg.connect(
            DATABASE_URL, server_settings={"search_path": self.schema}
        )

    async def asyncTearDown(self):
        await self.connection.close()
        await self.admin.execute(
            'DROP SCHEMA IF EXISTS "{}" CASCADE'.format(self.schema)
        )
        await self.admin.close()

    async def test_all_migrations_apply_once(self):
        expected = [v for v, _ in migrate.migration_files()]
        self.assertEqual(await migrate.apply_migrations(self.connection), expected)
        self.assertEqual(await migrate.apply_migrations(self.connection), [])
        self.assertEqual(await migrate.applied_versions(self.connection), set(expected))
        table = await self.connection.fetchval("SELECT to_regclass('bot_users')::text")
        self.assertEqual(table, "bot_users")

    async def test_existing_schema_gets_only_new_migrations(self):
        files = migrate.migration_files()
        before = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, before)
        for _, path in files[:5]:
            shutil.copy(path, before / path.name)
        await migrate.apply_migrations(self.connection, before)

        applied = await migrate.apply_migrations(self.connection)

        self.assertEqual(applied, [v for v, _ in files[5:]])

    async def test_failed_migration_rolls_back_everything(self):
        broken = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, broken)
        shutil.copy(migrate.migration_files()[0][1], broken)
        (broken / "999_broken.sql").write_text("SELECT * FROM missing_table;")

        import asyncpg

        with self.assertRaises(asyncpg.UndefinedTableError):
            await migrate.apply_migrations(self.connection, broken)
        self.assertEqual(await migrate.applied_versions(self.connection), set())
        self.assertIsNone(
            await self.connection.fetchval("SELECT to_regclass('bot_users')")
        )


if __name__ == "__main__":
    unittest.main()
