"""Общая основа тестов с PostgreSQL.

Каждый тест получает свою временную схему (search_path указывает на неё),
поэтому тесты независимы и не трогают чужие данные. Нужна отдельная тестовая
база: TEST_DATABASE_URL (в CI — сервисный контейнер, локально — любой
одноразовый PostgreSQL). Production-URL сюда подставлять нельзя.
"""

import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import migrate  # noqa: E402


def _guard_test_database(url):
    """Защита от случайного подключения тестов к рабочей базе: они очищают и
    создают схемы, поэтому адрес должен быть локальным (или явно разрешён
    ALLOW_REMOTE_TEST_DATABASE=1) и не совпадать с DATABASE_URL."""
    if not url:
        return url
    from urllib.parse import urlparse

    if url == os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "TEST_DATABASE_URL совпадает с DATABASE_URL — тесты остановлены"
        )
    host = (urlparse(url).hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1") and not os.getenv(
        "ALLOW_REMOTE_TEST_DATABASE"
    ):
        raise RuntimeError(
            "TEST_DATABASE_URL указывает не на локальный сервер ({}): тесты "
            "остановлены. Нужна одноразовая БД; для удалённой тестовой БД "
            "задайте ALLOW_REMOTE_TEST_DATABASE=1.".format(host)
        )
    return url


DATABASE_URL = _guard_test_database(os.getenv("TEST_DATABASE_URL"))

requires_postgres = unittest.skipUnless(DATABASE_URL, "TEST_DATABASE_URL не задан")


class PostgresTestCase(unittest.IsolatedAsyncioTestCase):
    """Пустая временная схема и подключение с её search_path."""

    apply_all = True

    async def asyncSetUp(self):
        import asyncpg

        self.asyncpg = asyncpg
        self.schema = "test_db_{}_{}".format(os.getpid(), time.time_ns())
        self.admin = await asyncpg.connect(DATABASE_URL)
        await self.admin.execute('CREATE SCHEMA "{}"'.format(self.schema))
        self.connections = []
        self.connection = await self.connect()
        if self.apply_all:
            await migrate.apply_migrations(self.connection)

    async def connect(self):
        connection = await self.asyncpg.connect(
            DATABASE_URL, server_settings={"search_path": self.schema}
        )
        self.connections.append(connection)
        return connection

    async def asyncTearDown(self):
        for connection in self.connections:
            await connection.close()
        await self.admin.execute(
            'DROP SCHEMA IF EXISTS "{}" CASCADE'.format(self.schema)
        )
        await self.admin.close()

    # --- помощники для данных ---

    async def add_user(self, telegram_id, name="Тест", credits=0):
        await self.connection.execute(
            "INSERT INTO bot_users (telegram_id, user_name, lesson_credits) "
            "VALUES ($1, $2, $3)",
            telegram_id,
            name,
            credits,
        )
        return telegram_id

    async def add_slot(self, capacity=2, class_key="beginner", hours=48):
        return await self.connection.fetchval(
            "INSERT INTO lesson_slots (class_key, starts_at, capacity, created_by) "
            "VALUES ($1, NOW() + make_interval(hours => $2), $3, 1) RETURNING id",
            class_key,
            hours,
            capacity,
        )

    async def add_payment(self, telegram_id, key="pay-1", package_key="single"):
        return await self.connection.fetchval(
            "INSERT INTO lesson_payments (telegram_id, package_key, lessons, "
            "amount_minor, provider_payment_id, confirmation_url) "
            "VALUES ($1, $2, 1, 100000, $3, 'https://pay.example/c') RETURNING id",
            telegram_id,
            package_key,
            key,
        )

    async def assertRejected(self, error, query, *args):
        """Запрос отклонён БД, а транзакция теста остаётся пригодной."""
        async with self.connection.transaction():
            with self.assertRaises(error):
                await self.connection.execute(query, *args)
