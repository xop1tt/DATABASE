"""Командная строка migrate.py: apply / status / check и подключение из env.

Часть тестов — без БД (подмена подключения), часть — против тестового
PostgreSQL через DATABASE_URL с временной схемой (search_path в URL).
"""

import asyncio
import contextlib
import io
import os
import time
import unittest
from pathlib import Path
from unittest import mock

import migrate
from pg_support import DATABASE_URL, requires_postgres


class FakeConnection:
    def __init__(self, applied=()):
        self.applied = set(applied)
        self.closed = False

    async def fetchval(self, query, *args):
        return "schema_migrations" if self.applied else None

    async def fetch(self, query, *args):
        return [{"version": version} for version in self.applied]

    async def close(self):
        self.closed = True


def run_cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = migrate.main(list(argv))
    return code, out.getvalue()


class StatusAndCheckTests(unittest.TestCase):
    def _run(self, command, applied):
        connection = FakeConnection(applied)
        with mock.patch.object(
            migrate, "connect_from_env", mock.AsyncMock(return_value=connection)
        ):
            code, output = run_cli(command)
        self.assertTrue(connection.closed)
        return code, output

    def test_status_lists_applied_and_pending(self):
        code, output = self._run("status", {"001", "002"})
        self.assertEqual(code, 0)
        self.assertIn("Применены: 001, 002", output)
        self.assertIn("003", output.split("Ожидают:")[1])

    def test_status_orders_versions_numerically(self):
        _, output = self._run("status", {"010", "002", "001"})
        self.assertIn("Применены: 001, 002, 010", output)

    def test_check_fails_when_migrations_are_pending(self):
        code, _ = self._run("check", {"001"})
        self.assertEqual(code, 1)

    def test_check_passes_when_schema_is_current(self):
        everything = {v for v, _ in migrate.migration_files()}
        code, output = self._run("check", everything)
        self.assertEqual(code, 0)
        self.assertIn("Ожидают: —", output)

    def test_status_on_empty_database_reports_nothing_applied(self):
        code, output = self._run("status", set())
        self.assertEqual(code, 0)
        self.assertIn("Применены: —", output)

    def test_connection_is_closed_even_when_the_command_fails(self):
        connection = FakeConnection()
        with (
            mock.patch.object(
                migrate, "connect_from_env", mock.AsyncMock(return_value=connection)
            ),
            mock.patch.object(
                migrate,
                "apply_migrations",
                mock.AsyncMock(side_effect=RuntimeError("x")),
            ),
        ):
            with self.assertRaises(RuntimeError):
                run_cli("apply")
        self.assertTrue(connection.closed)

    def test_unknown_command_is_rejected_by_the_parser(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            migrate.main(["drop"])

    def test_default_command_applies(self):
        connection = FakeConnection()
        applied = mock.AsyncMock(return_value=["001"])
        with (
            mock.patch.object(
                migrate, "connect_from_env", mock.AsyncMock(return_value=connection)
            ),
            mock.patch.object(migrate, "apply_migrations", applied),
        ):
            code, output = run_cli()
        self.assertEqual(code, 0)
        applied.assert_awaited_once()
        self.assertIn("Применена миграция 001", output)
        self.assertIn("Готово", output)

    def test_apply_with_nothing_new_says_schema_is_current(self):
        connection = FakeConnection()
        with (
            mock.patch.object(
                migrate, "connect_from_env", mock.AsyncMock(return_value=connection)
            ),
            mock.patch.object(
                migrate, "apply_migrations", mock.AsyncMock(return_value=[])
            ),
        ):
            _, output = run_cli("apply")
        self.assertIn("Схема актуальна", output)


class ConnectFromEnvTests(unittest.TestCase):
    def _connect(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            return asyncio.run(migrate.connect_from_env())

    def test_missing_database_url_stops_with_a_message(self):
        for env in ({}, {"DATABASE_URL": "   "}):
            with self.assertRaises(SystemExit) as raised:
                self._connect(env)
            self.assertIn("DATABASE_URL", str(raised.exception))

    def test_connects_with_tls_verification_by_default(self):
        import ssl

        fake = mock.AsyncMock(return_value="connection")
        with mock.patch("asyncpg.connect", fake):
            result = self._connect(
                {
                    "DATABASE_URL": "postgresql://u@db.example.net/app",
                    "DATABASE_SSL_CA": str(
                        Path(migrate.__file__).parent
                        / "certs"
                        / "supabase-root-2021-ca.crt"
                    ),
                }
            )
        self.assertEqual(result, "connection")
        args, kwargs = fake.call_args
        self.assertEqual(args[0], "postgresql://u@db.example.net/app")
        self.assertEqual(kwargs["ssl"].verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(kwargs["command_timeout"], 60)

    def test_plain_connection_is_refused_for_remote_hosts(self):
        with self.assertRaisesRegex(ValueError, "localhost"):
            self._connect(
                {
                    "DATABASE_URL": "postgresql://u@db.example.net/app",
                    "DATABASE_SSL_MODE": "disable",
                }
            )

    def test_plain_connection_is_allowed_for_localhost(self):
        fake = mock.AsyncMock(return_value="connection")
        with mock.patch("asyncpg.connect", fake):
            self._connect(
                {
                    "DATABASE_URL": "postgresql://u@localhost/app",
                    "DATABASE_SSL_MODE": " DISABLE ",
                }
            )
        self.assertIs(fake.call_args.kwargs["ssl"], False)


class SslSettingEdgeTests(unittest.TestCase):
    def test_ipv6_loopback_counts_as_localhost(self):
        self.assertIs(
            migrate.ssl_setting("postgresql://u@[::1]/db", "disable", None), False
        )

    def test_localhost_name_is_case_insensitive(self):
        self.assertIs(
            migrate.ssl_setting("postgresql://u@LocalHost/db", "disable", None), False
        )

    def test_lookalike_host_is_not_localhost(self):
        for url in (
            "postgresql://u@localhost.evil.net/db",
            "postgresql://u@127.0.0.1.evil.net/db",
            "postgresql://u@10.0.0.5/db",
            "postgresql:///db",
        ):
            with self.assertRaises(ValueError, msg=url):
                migrate.ssl_setting(url, "disable", None)

    def test_verify_full_without_ca_uses_system_roots(self):
        import ssl

        context = migrate.ssl_setting(
            "postgresql://u@db.example.net/db", "verify-full", None
        )
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


@requires_postgres
class CliAgainstPostgresTests(unittest.TestCase):
    """Реальный запуск `python migrate.py` через main() на пустой схеме."""

    def setUp(self):
        import asyncpg

        self.schema = "test_cli_{}_{}".format(os.getpid(), time.time_ns())

        async def create():
            connection = await asyncpg.connect(DATABASE_URL)
            await connection.execute('CREATE SCHEMA "{}"'.format(self.schema))
            await connection.close()

        asyncio.run(create())
        self.addCleanup(self._drop)
        separator = "&" if "?" in DATABASE_URL else "?"
        url = "{}{}search_path={}".format(DATABASE_URL, separator, self.schema)
        patcher = mock.patch.dict(
            os.environ,
            {"DATABASE_URL": url, "DATABASE_SSL_MODE": "disable"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _drop(self):
        import asyncpg

        async def drop():
            connection = await asyncpg.connect(DATABASE_URL)
            await connection.execute(
                'DROP SCHEMA IF EXISTS "{}" CASCADE'.format(self.schema)
            )
            await connection.close()

        asyncio.run(drop())

    def test_check_apply_check_cycle(self):
        total = len(migrate.migration_files())
        code, _ = run_cli("check")
        self.assertEqual(code, 1)

        code, output = run_cli("apply")
        self.assertEqual(code, 0)
        self.assertEqual(output.count("Применена миграция"), total)

        code, output = run_cli("check")
        self.assertEqual(code, 0)
        self.assertIn("Ожидают: —", output)

        code, output = run_cli("apply")
        self.assertEqual(code, 0)
        self.assertIn("Схема актуальна", output)
        self.assertNotIn("Применена миграция", output)

    def test_status_before_and_after_apply(self):
        _, output = run_cli("status")
        self.assertIn("Применены: —", output)
        run_cli("apply")
        _, output = run_cli("status")
        self.assertIn("Ожидают: —", output)


if __name__ == "__main__":
    unittest.main()
