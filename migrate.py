#!/usr/bin/env python3
"""Миграции PostgreSQL Flamenco Studio — единственное место, где меняется схема.

    python migrate.py           применить новые миграции
    python migrate.py status    применённые и ожидающие версии
    python migrate.py check     код 1, если есть неприменённые миграции

Подключение — DATABASE_URL от роли-владельца схемы (в Supabase это
flamenco_app: её search_path указывает на схему flamenco). TLS:
DATABASE_SSL_MODE=verify-full (по умолчанию) с CA из DATABASE_SSL_CA
(например, certs/supabase-root-2021-ca.crt) или disable — только для
localhost.

Бот и API миграции не применяют: при старте они проверяют, что версия схемы
не ниже нужной коду (flamenco_bot.database.repository.REQUIRED_SCHEMA_VERSION).
Поэтому порядок выкладки — сначала этот репозиторий, затем бот.

Модуль импортируют и тесты бота (FLAMENCO_DB_DIR), чтобы создавать схему
во временных схемах PostgreSQL той же логикой, что и в production.
"""

import argparse
import asyncio
import ipaddress
import os
import ssl
import sys
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import urlparse

MIGRATIONS_DIRECTORY = Path(__file__).resolve().parent / "migrations"
# Тот же ключ, что раньше использовал бот: параллельные запуски (CI и
# разработчик) ждут друг друга, а не применяют миграцию дважды.
MIGRATION_LOCK_ID = 715_203_401


def migration_files(directory: Path = MIGRATIONS_DIRECTORY) -> list[tuple[str, Path]]:
    """(версия, файл) по возрастанию версии; версия — числовой префикс имени."""
    migrations: dict[str, Path] = {}
    for path in sorted(directory.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if not version.isdigit():
            raise ValueError(
                "Имя миграции должно начинаться с числовой версии: " + path.name
            )
        if version in migrations:
            raise ValueError(
                "Две миграции с версией {}: {} и {}".format(
                    version, migrations[version].name, path.name
                )
            )
        migrations[version] = path
    return sorted(migrations.items(), key=lambda item: int(item[0]))


async def applied_versions(connection: Any) -> set[str]:
    exists = await connection.fetchval("SELECT to_regclass('schema_migrations')")
    if exists is None:
        return set()
    records = await connection.fetch("SELECT version FROM schema_migrations")
    return {record["version"] for record in records}


async def apply_migrations(
    connection: Any, directory: Path = MIGRATIONS_DIRECTORY
) -> list[str]:
    """Применяет неприменённые миграции одной транзакцией; возвращает версии."""
    applied: list[str] = []
    async with connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock($1)", MIGRATION_LOCK_ID)
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        done = await applied_versions(connection)
        for version, path in migration_files(directory):
            if version in done:
                continue
            await connection.execute(path.read_text(encoding="utf-8"))
            await connection.execute(
                "INSERT INTO schema_migrations (version) VALUES ($1)", version
            )
            applied.append(version)
    return applied


def ssl_setting(database_url: str, mode: str, ca_path: Optional[str]) -> Any:
    hostname = urlparse(database_url).hostname or ""
    if mode == "disable":
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname.lower() == "localhost"
        if not loopback:
            raise ValueError("DATABASE_SSL_MODE=disable разрешён только для localhost")
        return False
    if mode != "verify-full":
        raise ValueError("DATABASE_SSL_MODE должен быть verify-full или disable")
    context = ssl.create_default_context(
        cafile=str(Path(ca_path).expanduser()) if ca_path else None
    )
    if ca_path:
        # Python 3.13 включает VERIFY_X509_STRICT, а промежуточный CA пулера
        # Supabase без keyUsage им отклоняется. Цепочка до заданного CA и
        # hostname по-прежнему проверяются, как в libpq verify-full.
        context.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    return context


async def connect_from_env() -> Any:
    import asyncpg

    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_URL не задан")
    mode = os.getenv("DATABASE_SSL_MODE", "verify-full").strip().lower()
    ca_path = os.getenv("DATABASE_SSL_CA", "").strip() or None
    return await asyncpg.connect(
        url, ssl=ssl_setting(url, mode, ca_path), command_timeout=60
    )


async def run(command: str) -> int:
    connection = await connect_from_env()
    try:
        if command == "apply":
            applied = await apply_migrations(connection)
            for version in applied:
                print("Применена миграция", version)
            print("Схема актуальна" if not applied else "Готово")
            return 0
        done = await applied_versions(connection)
        pending = [v for v, _ in migration_files() if v not in done]
        print("Применены:", ", ".join(sorted(done, key=int)) or "—")
        print("Ожидают:", ", ".join(pending) or "—")
        return 1 if command == "check" and pending else 0
    finally:
        await connection.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Миграции PostgreSQL Flamenco Studio")
    parser.add_argument(
        "command", nargs="?", default="apply", choices=("apply", "status", "check")
    )
    args = parser.parse_args(argv)
    return asyncio.run(run(args.command))


if __name__ == "__main__":
    sys.exit(main())
