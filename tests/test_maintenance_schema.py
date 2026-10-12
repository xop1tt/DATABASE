"""Схема технических работ сайта (миграция 021): окно, события, этапы рассылки.

Ограничения окна частично проверены в test_migrate.py; здесь — массив
предупреждений, каскады и ключи идемпотентности рассылки.
"""

import unittest
from datetime import datetime, timedelta, timezone

from pg_support import PostgresTestCase, requires_postgres

INSERT = """
    INSERT INTO site_maintenance (
        status, title, description, starts_at, warning_minutes, created_by
    )
    VALUES ('scheduled', 'Работы', 'Описание', $1, $2, 1)
    RETURNING id
"""


@requires_postgres
class MaintenanceSchemaTests(PostgresTestCase):
    def setUp(self):
        self.starts = datetime.now(timezone.utc) + timedelta(hours=2)

    async def test_warning_minutes_array_is_validated(self):
        valid = ([], [1], [1440, 60, 10], [10080], [1, 2, 3, 4, 5, 6])
        for minutes in valid:
            async with self.connection.transaction():
                maintenance_id = await self.connection.fetchval(
                    INSERT, self.starts, minutes
                )
                await self.connection.execute(
                    "DELETE FROM site_maintenance WHERE id = $1", maintenance_id
                )
        invalid = (
            [0],
            [-5],
            [10081],
            [1, 2, 3, 4, 5, 6, 7],  # не больше шести предупреждений
            [60, None],
        )
        for minutes in invalid:
            await self.assertRejected(
                self.asyncpg.CheckViolationError,
                "INSERT INTO site_maintenance (status, title, description, "
                "starts_at, warning_minutes, created_by) "
                "VALUES ('scheduled', 't', 'd', $1, $2, 1)",
                self.starts,
                minutes,
            )

    async def test_defaults_and_text_bounds(self):
        maintenance_id = await self.connection.fetchval(
            "INSERT INTO site_maintenance (status, title, description, starts_at, "
            "created_by) VALUES ('scheduled', 'T', 'D', $1, 1) RETURNING id",
            self.starts,
        )
        row = await self.connection.fetchrow(
            "SELECT * FROM site_maintenance WHERE id = $1", maintenance_id
        )
        self.assertEqual(list(row["warning_minutes"]), [1440, 60, 10])
        self.assertTrue(row["notify_users"])
        self.assertEqual((row["audience"], row["revision"]), ("all", 1))
        for title, description in (
            ("", "d"),
            ("   ", "d"),
            ("t" * 121, "d"),
            ("t", ""),
            ("t", "d" * 1001),
        ):
            await self.assertRejected(
                self.asyncpg.CheckViolationError,
                "INSERT INTO site_maintenance (status, title, description, "
                "starts_at, created_by, finished_at) "
                "VALUES ('cancelled', $1, $2, $3, 1, $3)",
                title,
                description,
                self.starts,
            )

    async def test_events_and_dispatches_follow_their_window(self):
        maintenance_id = await self.connection.fetchval(INSERT, self.starts, [60])
        await self.connection.execute(
            "INSERT INTO site_maintenance_events (maintenance_id, event) "
            "VALUES ($1, 'created')",
            maintenance_id,
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO site_maintenance_events (maintenance_id, event) "
            "VALUES ($1, 'exploded')",
            maintenance_id,
        )
        await self.assertRejected(
            self.asyncpg.ForeignKeyViolationError,
            "INSERT INTO site_maintenance_events (maintenance_id, event) "
            "VALUES (999999, 'created')",
        )
        stage = (
            "INSERT INTO site_maintenance_dispatches (maintenance_id, revision, stage) "
            "VALUES ($1, $2, 'started')"
        )
        await self.connection.execute(stage, maintenance_id, 1)
        # Та же стадия в новой редакции разрешена, в той же — нет.
        await self.connection.execute(stage, maintenance_id, 2)
        await self.assertRejected(
            self.asyncpg.UniqueViolationError, stage, maintenance_id, 2
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO site_maintenance_dispatches (maintenance_id, revision, "
            "stage, recipients) VALUES ($1, 1, 'x', -1)",
            maintenance_id,
        )
        await self.connection.execute(
            "DELETE FROM site_maintenance WHERE id = $1", maintenance_id
        )
        for table in ("site_maintenance_events", "site_maintenance_dispatches"):
            self.assertEqual(
                await self.connection.fetchval("SELECT COUNT(*) FROM " + table), 0
            )

    async def test_only_one_open_window_but_history_is_unbounded(self):
        first = await self.connection.fetchval(INSERT, self.starts, [60])
        await self.assertRejected(
            self.asyncpg.UniqueViolationError,
            "INSERT INTO site_maintenance (status, title, description, starts_at, "
            "created_by) VALUES ('scheduled', 'T', 'D', $1, 1)",
            self.starts,
        )
        await self.connection.execute(
            "UPDATE site_maintenance SET status = 'completed', finished_at = NOW() "
            "WHERE id = $1",
            first,
        )
        for _ in range(3):
            await self.connection.execute(
                "INSERT INTO site_maintenance (status, title, description, starts_at, "
                "created_by, finished_at) VALUES ('cancelled', 'T', 'D', $1, 1, NOW())",
                self.starts,
            )
        await self.connection.execute(INSERT, self.starts, [60])


if __name__ == "__main__":
    unittest.main()
