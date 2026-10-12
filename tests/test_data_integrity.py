"""Целостность данных: ограничения, уникальность, каскады, транзакции.

Финансово значимые таблицы (платежи, ledger, выдачи абонементов) и бронирования
проверяются на отрицательных сценариях: БД обязана отклонить некорректную
запись, а не положиться на код приложения.
"""

import asyncio
import unittest
import uuid

from pg_support import PostgresTestCase, requires_postgres


@requires_postgres
class UsersAndWebAccountsTests(PostgresTestCase):
    async def test_telegram_id_must_be_positive_and_unique(self):
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO bot_users (telegram_id, user_name) VALUES (0, 'x')",
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO bot_users (telegram_id, user_name) VALUES (-5, 'x')",
        )
        await self.add_user(1)
        await self.assertRejected(
            self.asyncpg.UniqueViolationError,
            "INSERT INTO bot_users (telegram_id, user_name) VALUES (1, 'дубль')",
        )

    async def test_user_name_is_required(self):
        await self.assertRejected(
            self.asyncpg.NotNullViolationError,
            "INSERT INTO bot_users (telegram_id) VALUES (1)",
        )

    async def test_lesson_credits_cannot_go_negative(self):
        await self.add_user(1, credits=2)
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "UPDATE bot_users SET lesson_credits = -1 WHERE telegram_id = 1",
        )
        self.assertEqual(
            await self.connection.fetchval(
                "SELECT lesson_credits FROM bot_users WHERE telegram_id = 1"
            ),
            2,
        )

    async def test_web_account_needs_email_or_telegram_and_matching_password(self):
        # Ни почты, ни Telegram.
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO users (display_name) VALUES ('аноним')",
        )
        # Почта без пароля и пароль без почты.
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO users (email, display_name) VALUES ('a@b.c', 'x')",
        )
        await self.add_user(7)
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO users (telegram_id, password_hash, display_name) "
            "VALUES (7, 'hash', 'x')",
        )
        # Допустимо: только Telegram, либо почта вместе с хэшем.
        await self.connection.execute(
            "INSERT INTO users (telegram_id, display_name) VALUES (7, 'tg')"
        )
        await self.connection.execute(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES ('ok@example.com', 'hash', 'mail')"
        )

    async def test_email_is_unique_case_insensitively(self):
        await self.connection.execute(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES ('Ann@Example.com', 'h', 'Ann')"
        )
        await self.assertRejected(
            self.asyncpg.UniqueViolationError,
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES ('ann@example.COM', 'h', 'Ann 2')",
        )

    async def test_telegram_account_is_linked_to_one_web_user_only(self):
        await self.add_user(7)
        await self.connection.execute(
            "INSERT INTO users (telegram_id, display_name) VALUES (7, 'a')"
        )
        await self.assertRejected(
            self.asyncpg.UniqueViolationError,
            "INSERT INTO users (telegram_id, display_name) VALUES (7, 'b')",
        )

    async def test_deleting_bot_user_unlinks_account_that_has_an_email(self):
        await self.add_user(7)
        user_id = await self.connection.fetchval(
            "INSERT INTO users (email, password_hash, telegram_id, display_name) "
            "VALUES ('a@b.c', 'h', 7, 'a') RETURNING id"
        )
        await self.connection.execute(
            "INSERT INTO web_sessions (token_hash, user_id, expires_at) "
            "VALUES ('t', $1, NOW() + INTERVAL '1 day')",
            user_id,
        )
        await self.connection.execute("DELETE FROM bot_users WHERE telegram_id = 7")
        self.assertIsNone(
            await self.connection.fetchval(
                "SELECT telegram_id FROM users WHERE id = $1", user_id
            )
        )
        await self.connection.execute("DELETE FROM users WHERE id = $1", user_id)
        self.assertEqual(
            await self.connection.fetchval("SELECT COUNT(*) FROM web_sessions"), 0
        )

    async def test_deleting_bot_user_of_telegram_only_account_is_rejected(self):
        """Известная особенность схемы: FK users.telegram_id — SET NULL, но
        аккаунт без почты не может остаться без Telegram (CHECK), поэтому
        удаление такого профиля БД отклоняет, а не теряет вход молча."""
        await self.add_user(7)
        await self.connection.execute(
            "INSERT INTO users (telegram_id, display_name) VALUES (7, 'tg')"
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "DELETE FROM bot_users WHERE telegram_id = 7",
        )
        self.assertEqual(
            await self.connection.fetchval("SELECT COUNT(*) FROM bot_users"), 1
        )


@requires_postgres
class BookingTests(PostgresTestCase):
    async def test_slot_constraints(self):
        for capacity in (0, 101, -1):
            await self.assertRejected(
                self.asyncpg.CheckViolationError,
                "INSERT INTO lesson_slots (class_key, starts_at, capacity, created_by) "
                "VALUES ('beginner', NOW(), $1, 1)",
                capacity,
            )
        await self.assertRejected(
            self.asyncpg.ForeignKeyViolationError,
            "INSERT INTO lesson_slots (class_key, starts_at, capacity, created_by) "
            "VALUES ('unknown_class', NOW(), 5, 1)",
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO lesson_slots (class_key, starts_at, capacity, created_by, "
            "status) VALUES ('beginner', NOW(), 5, 1, 'archived')",
        )
        for capacity in (1, 100):
            await self.add_slot(capacity=capacity)

    async def test_cancelled_state_requires_cancellation_time(self):
        slot = await self.add_slot()
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "UPDATE lesson_slots SET status = 'cancelled' WHERE id = $1",
            slot,
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "UPDATE lesson_slots SET cancelled_at = NOW() WHERE id = $1",
            slot,
        )
        await self.connection.execute(
            "UPDATE lesson_slots SET status = 'cancelled', cancelled_at = NOW(), "
            "cancel_reason = 'болезнь' WHERE id = $1",
            slot,
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "UPDATE lesson_slots SET cancel_reason = '' WHERE id = $1",
            slot,
        )

    async def test_user_cannot_book_the_same_slot_twice(self):
        await self.add_user(1)
        slot = await self.add_slot(capacity=5)
        insert = "INSERT INTO lesson_bookings (slot_id, telegram_id) VALUES ($1, 1)"
        await self.connection.execute(insert, slot)
        await self.assertRejected(self.asyncpg.UniqueViolationError, insert, slot)

    async def test_capacity_is_enforced_for_new_bookings(self):
        slot = await self.add_slot(capacity=2)
        for telegram_id in (1, 2, 3):
            await self.add_user(telegram_id)
        insert = "INSERT INTO lesson_bookings (slot_id, telegram_id) VALUES ($1, $2)"
        await self.connection.execute(insert, slot, 1)
        await self.connection.execute(insert, slot, 2)
        with self.assertRaisesRegex(Exception, "capacity exceeded"):
            async with self.connection.transaction():
                await self.connection.execute(insert, slot, 3)
        confirmed = await self.connection.fetchval(
            "SELECT COUNT(*) FROM lesson_bookings WHERE slot_id = $1", slot
        )
        self.assertEqual(confirmed, 2)

    async def test_cancelled_booking_frees_a_seat_and_reconfirming_is_guarded(self):
        slot = await self.add_slot(capacity=1)
        await self.add_user(1)
        await self.add_user(2)
        first = await self.connection.fetchval(
            "INSERT INTO lesson_bookings (slot_id, telegram_id) VALUES ($1, 1) "
            "RETURNING id",
            slot,
        )
        await self.connection.execute(
            "UPDATE lesson_bookings SET status = 'cancelled' WHERE id = $1", first
        )
        await self.connection.execute(
            "INSERT INTO lesson_bookings (slot_id, telegram_id) VALUES ($1, 2)", slot
        )
        # Вернуть отменённую запись в confirmed нельзя: место занято.
        with self.assertRaisesRegex(Exception, "capacity exceeded"):
            async with self.connection.transaction():
                await self.connection.execute(
                    "UPDATE lesson_bookings SET status = 'confirmed' WHERE id = $1",
                    first,
                )
        self.assertEqual(
            await self.connection.fetchval(
                "SELECT status FROM lesson_bookings WHERE id = $1", first
            ),
            "cancelled",
        )

    async def test_completed_bookings_do_not_occupy_seats(self):
        slot = await self.add_slot(capacity=1)
        for telegram_id in (1, 2):
            await self.add_user(telegram_id)
        await self.connection.execute(
            "INSERT INTO lesson_bookings (slot_id, telegram_id, status) "
            "VALUES ($1, 1, 'completed')",
            slot,
        )
        await self.connection.execute(
            "INSERT INTO lesson_bookings (slot_id, telegram_id) VALUES ($1, 2)", slot
        )

    async def test_slot_with_bookings_cannot_be_deleted(self):
        slot = await self.add_slot()
        await self.add_user(1)
        await self.connection.execute(
            "INSERT INTO lesson_bookings (slot_id, telegram_id) VALUES ($1, 1)", slot
        )
        await self.assertRejected(
            self.restrict_errors,
            "DELETE FROM lesson_slots WHERE id = $1",
            slot,
        )

    async def test_slot_events_validate_reschedule_data(self):
        slot = await self.add_slot()
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO lesson_slot_events (slot_id, event_type) "
            "VALUES ($1, 'rescheduled')",
            slot,
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO lesson_slot_events (slot_id, event_type) "
            "VALUES ($1, 'teleported')",
            slot,
        )

    async def test_concurrent_booking_of_last_seat_with_slot_lock_keeps_capacity(
        self,
    ):
        """Так бронирует приложение: блокировка занятия FOR UPDATE, затем вставка.
        Из двух одновременных запросов на последнее место проходит один."""
        slot = await self.add_slot(capacity=1)
        for telegram_id in (1, 2):
            await self.add_user(telegram_id)
        connections = [await self.connect(), await self.connect()]

        async def book(connection, telegram_id):
            try:
                async with connection.transaction():
                    await connection.execute(
                        "SELECT id FROM lesson_slots WHERE id = $1 FOR UPDATE", slot
                    )
                    await connection.execute(
                        "INSERT INTO lesson_bookings (slot_id, telegram_id) "
                        "VALUES ($1, $2)",
                        slot,
                        telegram_id,
                    )
                return True
            except self.asyncpg.PostgresError:
                return False

        results = await asyncio.gather(book(connections[0], 1), book(connections[1], 2))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(
            await self.connection.fetchval(
                "SELECT COUNT(*) FROM lesson_bookings WHERE status = 'confirmed'"
            ),
            1,
        )

    async def test_capacity_trigger_alone_does_not_serialize_concurrent_inserts(self):
        """Характеризация, не пожелание: триггер видит только закоммиченные
        строки, поэтому две одновременные вставки без блокировки занятия обе
        проходят. Вместимость на самом деле держит приложение (SELECT ... FOR
        UPDATE по занятию — тест выше и интеграционные тесты бота). Если
        защиту когда-нибудь перенесут в БД, этот тест надо обновить."""
        slot = await self.add_slot(capacity=1)
        for telegram_id in (1, 2):
            await self.add_user(telegram_id)
        connections = [await self.connect(), await self.connect()]
        both_inserted = asyncio.Event()
        inserted = 0

        async def book(connection, telegram_id):
            nonlocal inserted
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO lesson_bookings (slot_id, telegram_id) "
                    "VALUES ($1, $2)",
                    slot,
                    telegram_id,
                )
                inserted += 1
                if inserted == 2:
                    both_inserted.set()
                await asyncio.wait_for(both_inserted.wait(), 5)

        await asyncio.gather(book(connections[0], 1), book(connections[1], 2))
        self.assertEqual(
            await self.connection.fetchval("SELECT COUNT(*) FROM lesson_bookings"), 2
        )


@requires_postgres
class PaymentsAndLedgerTests(PostgresTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.add_user(1, credits=0)

    async def test_payment_constraints(self):
        base = (
            "INSERT INTO lesson_payments (telegram_id, package_key, lessons, "
            "amount_minor, provider_payment_id, confirmation_url{extra}) "
            "VALUES (1, $1, $2, $3, $4, 'u'{values})"
        )
        insert = base.format(extra="", values="")
        await self.assertRejected(
            self.asyncpg.CheckViolationError, insert, "single", 0, 100, "p1"
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError, insert, "single", 1, 0, "p2"
        )
        await self.assertRejected(
            self.asyncpg.ForeignKeyViolationError, insert, "no_such", 1, 100, "p3"
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            base.format(extra=", currency", values=", 'USD'"),
            "single",
            1,
            100,
            "p4",
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            base.format(extra=", status", values=", 'paid'"),
            "single",
            1,
            100,
            "p5",
        )
        await self.connection.execute(insert, "single", 1, 100, "ok")
        await self.assertRejected(
            self.asyncpg.UniqueViolationError, insert, "single", 1, 100, "ok"
        )

    async def test_payment_idempotence_keys_are_unique_when_set(self):
        key = uuid.uuid4()
        insert = (
            "INSERT INTO lesson_payments (telegram_id, package_key, lessons, "
            "amount_minor, provider_payment_id, confirmation_url, idempotence_key) "
            "VALUES (1, 'single', 1, 100, $1, 'u', $2)"
        )
        await self.connection.execute(insert, "a", key)
        await self.assertRejected(self.asyncpg.UniqueViolationError, insert, "b", key)
        # NULL-ключи не конфликтуют между собой.
        null_insert = insert.replace("$2", "NULL")
        await self.connection.execute(null_insert, "c")
        await self.connection.execute(null_insert, "d")

    async def test_one_active_payment_attempt_per_user_and_package(self):
        insert = (
            "INSERT INTO lesson_payment_attempts (idempotence_key, telegram_id, "
            "package_key, package_title, lessons, amount_minor, status) "
            "VALUES ($1, 1, 'single', 'Разовое', 1, 100, $2)"
        )
        await self.connection.execute(insert, uuid.uuid4(), "creating")
        await self.assertRejected(
            self.asyncpg.UniqueViolationError, insert, uuid.uuid4(), "pending"
        )
        # Завершённые и неудачные попытки не мешают новой.
        await self.connection.execute(
            "UPDATE lesson_payment_attempts SET status = 'failed'"
        )
        await self.connection.execute(insert, uuid.uuid4(), "creating")
        await self.connection.execute(insert, uuid.uuid4(), "completed")

    async def _ledger(self, entry_type="purchase", delta=1, **extra):
        columns = ["telegram_id", "entry_type", "delta"] + list(extra)
        placeholders = ", ".join("${}".format(i + 1) for i in range(len(columns)))
        await self.connection.execute(
            "INSERT INTO lesson_credit_ledger ({}) VALUES ({})".format(
                ", ".join(columns), placeholders
            ),
            1,
            entry_type,
            delta,
            *extra.values(),
        )

    async def test_ledger_rejects_zero_delta_and_unknown_type(self):
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta) "
            "VALUES (1, 'adjustment', 0)",
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta) "
            "VALUES (1, 'gift', 1)",
        )

    async def test_admin_adjustment_requires_actor_and_reason(self):
        insert = (
            "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta, "
            "actor_telegram_id, reason) VALUES (1, 'admin_adjustment', 1, $1, $2)"
        )
        await self.assertRejected(self.asyncpg.CheckViolationError, insert, None, "r")
        await self.assertRejected(self.asyncpg.CheckViolationError, insert, 9, None)
        await self.assertRejected(self.asyncpg.CheckViolationError, insert, 9, "   ")
        await self.assertRejected(
            self.asyncpg.CheckViolationError, insert, 9, "я" * 301
        )
        await self.connection.execute(insert, 9, "компенсация")

    async def test_studio_actions_in_ledger_require_actor(self):
        for entry_type in ("slot_cancellation", "package_grant", "package_revoke"):
            await self.assertRejected(
                self.asyncpg.CheckViolationError,
                "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta) "
                "VALUES (1, $1, 1)",
                entry_type,
            )

    async def test_ledger_reference_key_makes_operations_idempotent(self):
        await self._ledger("lesson_use", -1, reference_key="slot_booking:1:use")
        await self.assertRejected(
            self.asyncpg.UniqueViolationError,
            "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta, "
            "reference_key) VALUES (1, 'lesson_use', -1, 'slot_booking:1:use')",
        )

    async def test_purchase_is_credited_once_per_payment(self):
        payment = await self.add_payment(1)
        await self._ledger("purchase", 1, payment_id=payment)
        await self.assertRejected(
            self.asyncpg.UniqueViolationError,
            "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta, "
            "payment_id) VALUES (1, 'purchase', 1, $1)",
            payment,
        )
        # Возврат по тому же платежу — другая запись и допустим.
        await self._ledger("refund", -1, payment_id=payment)

    async def test_ledger_entry_has_a_single_source(self):
        payment = await self.add_payment(1)
        grant = await self._grant()
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta, "
            "payment_id, grant_id, actor_telegram_id, reason) "
            "VALUES (1, 'package_grant', 1, $1, $2, 9, 'r')",
            payment,
            grant,
        )

    async def _grant(self, key=None, lessons=4):
        return await self.connection.fetchval(
            "INSERT INTO lesson_package_grants (telegram_id, package_key, "
            "package_title, lessons, granted_by, reason, idempotence_key) "
            "VALUES (1, 'pack_4', 'Абонемент', $1, 9, 'наличные', $2) RETURNING id",
            lessons,
            key or uuid.uuid4(),
        )

    async def test_package_grant_is_recorded_once_and_needs_a_grant_link(self):
        grant = await self._grant()
        entry = (
            "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta, "
            "grant_id, actor_telegram_id, reason) "
            "VALUES (1, $1, $2, $3, 9, 'наличные')"
        )
        await self.connection.execute(entry, "package_grant", 4, grant)
        await self.assertRejected(
            self.asyncpg.UniqueViolationError, entry, "package_grant", 4, grant
        )
        await self.connection.execute(entry, "package_revoke", -4, grant)
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta, "
            "actor_telegram_id, reason) VALUES (1, 'package_grant', 4, 9, 'r')",
        )

    async def test_grant_idempotence_key_is_unique_and_values_are_bounded(self):
        key = uuid.uuid4()
        await self._grant(key)
        await self.assertRejected(
            self.asyncpg.UniqueViolationError,
            "INSERT INTO lesson_package_grants (telegram_id, package_key, "
            "package_title, lessons, granted_by, reason, idempotence_key) "
            "VALUES (1, 'pack_4', 'x', 4, 9, 'r', $1)",
            key,
        )
        for lessons in (0, 101):
            await self.assertRejected(
                self.asyncpg.CheckViolationError,
                "INSERT INTO lesson_package_grants (telegram_id, package_key, "
                "package_title, lessons, granted_by, reason, idempotence_key) "
                "VALUES (1, 'pack_4', 'x', $1, 9, 'r', $2)",
                lessons,
                uuid.uuid4(),
            )

    async def test_revoked_grant_requires_revocation_time(self):
        grant = await self._grant()
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "UPDATE lesson_package_grants SET status = 'revoked' WHERE id = $1",
            grant,
        )
        await self.connection.execute(
            "UPDATE lesson_package_grants SET status = 'revoked', revoked_at = NOW(), "
            "revoked_by = 9, revoke_reason = 'ошибка' WHERE id = $1",
            grant,
        )

    async def test_financial_history_survives_attempts_to_delete_the_user(self):
        payment = await self.add_payment(1)
        await self._ledger("purchase", 1, payment_id=payment)
        await self.assertRejected(
            self.restrict_errors,
            "DELETE FROM bot_users WHERE telegram_id = 1",
        )
        self.assertEqual(
            await self.connection.fetchval("SELECT COUNT(*) FROM lesson_credit_ledger"),
            1,
        )
        self.assertEqual(
            await self.connection.fetchval("SELECT COUNT(*) FROM lesson_payments"), 1
        )

    async def test_deleting_a_payment_keeps_ledger_but_unlinks_it(self):
        payment = await self.add_payment(1)
        await self._ledger("purchase", 1, payment_id=payment)
        await self.connection.execute(
            "DELETE FROM lesson_payments WHERE id = $1", payment
        )
        row = await self.connection.fetchrow("SELECT * FROM lesson_credit_ledger")
        self.assertIsNone(row["payment_id"])
        self.assertEqual(row["delta"], 1)

    async def test_failed_transaction_leaves_no_partial_balance_change(self):
        """Баланс и ledger меняются одной транзакцией: при ошибке — ни того,
        ни другого."""
        with self.assertRaises(self.asyncpg.CheckViolationError):
            async with self.connection.transaction():
                await self.connection.execute(
                    "INSERT INTO lesson_credit_ledger (telegram_id, entry_type, delta) "
                    "VALUES (1, 'lesson_use', -1)"
                )
                await self.connection.execute(
                    "UPDATE bot_users SET lesson_credits = lesson_credits - 1 "
                    "WHERE telegram_id = 1"
                )
        self.assertEqual(
            await self.connection.fetchval("SELECT COUNT(*) FROM lesson_credit_ledger"),
            0,
        )
        self.assertEqual(
            await self.connection.fetchval(
                "SELECT lesson_credits FROM bot_users WHERE telegram_id = 1"
            ),
            0,
        )

    async def test_concurrent_balance_debits_cannot_overdraw(self):
        """Два одновременных списания одного занятия: CHECK не даёт уйти
        в минус, второе ждёт блокировку строки и получает отказ."""
        await self.connection.execute(
            "UPDATE bot_users SET lesson_credits = 1 WHERE telegram_id = 1"
        )
        connections = [await self.connect(), await self.connect()]

        async def debit(connection):
            try:
                async with connection.transaction():
                    await connection.execute(
                        "UPDATE bot_users SET lesson_credits = lesson_credits - 1 "
                        "WHERE telegram_id = 1"
                    )
                return True
            except self.asyncpg.CheckViolationError:
                return False

        results = await asyncio.gather(*(debit(c) for c in connections))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(
            await self.connection.fetchval(
                "SELECT lesson_credits FROM bot_users WHERE telegram_id = 1"
            ),
            0,
        )

    async def test_payment_events_are_ordered_and_created_event_is_unique(self):
        payment = await self.add_payment(1)
        await self.connection.execute(
            "INSERT INTO lesson_payment_events (payment_id, event_type) "
            "VALUES ($1, 'created')",
            payment,
        )
        await self.assertRejected(
            self.asyncpg.UniqueViolationError,
            "INSERT INTO lesson_payment_events (payment_id, event_type) "
            "VALUES ($1, 'created')",
            payment,
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO lesson_payment_events (payment_id, event_type) "
            "VALUES ($1, 'hacked')",
            payment,
        )
        await self.connection.execute(
            "INSERT INTO lesson_payment_events (payment_id, event_type) "
            "VALUES ($1, 'succeeded')",
            payment,
        )


@requires_postgres
class SupportNotificationsAndCatalogTests(PostgresTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.add_user(1)

    async def test_one_open_ticket_per_user(self):
        insert = "INSERT INTO support_tickets (telegram_id, status) VALUES (1, $1)"
        await self.connection.execute(insert, "open")
        await self.assertRejected(self.asyncpg.UniqueViolationError, insert, "open")
        await self.connection.execute(insert, "closed")
        await self.connection.execute(insert, "closed")

    async def test_ticket_message_validation_and_cascade(self):
        ticket = await self.connection.fetchval(
            "INSERT INTO support_tickets (telegram_id) VALUES (1) RETURNING id"
        )
        insert = (
            "INSERT INTO support_messages (ticket_id, sender_telegram_id, "
            "sender_role, body) VALUES ($1, 1, $2, $3)"
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError, insert, ticket, "user", ""
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError, insert, ticket, "user", "x" * 2001
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError, insert, ticket, "bot", "hi"
        )
        await self.connection.execute(insert, ticket, "user", "x" * 2000)
        await self.connection.execute(
            "DELETE FROM support_tickets WHERE id = $1", ticket
        )
        self.assertEqual(
            await self.connection.fetchval("SELECT COUNT(*) FROM support_messages"), 0
        )

    async def test_notification_validation_and_deduplication(self):
        insert = (
            "INSERT INTO user_notifications (telegram_id, kind, dedupe_key{extra}) "
            "VALUES (1, $1, $2{values})"
        )
        plain = insert.format(extra="", values="")
        await self.connection.execute(plain, "booking_confirmed", "k1")
        await self.assertRejected(
            self.asyncpg.UniqueViolationError, plain, "booking_confirmed", "k1"
        )
        await self.assertRejected(self.asyncpg.CheckViolationError, plain, "spam", "k2")
        await self.assertRejected(
            self.asyncpg.CheckViolationError, plain, "low_balance", "k" * 201
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            insert.format(extra=", attempts", values=", -1"),
            "low_balance",
            "k3",
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            insert.format(extra=", delivery_status", values=", 'lost'"),
            "low_balance",
            "k4",
        )
        # Поздние виды уведомлений тоже допустимы.
        for index, kind in enumerate(
            ("waitlist_seat", "announcement", "package_expiring", "credits_expired")
        ):
            await self.connection.execute(plain, kind, "late{}".format(index))

    async def test_notification_defaults(self):
        await self.connection.execute(
            "INSERT INTO user_notifications (telegram_id, kind, dedupe_key) "
            "VALUES (1, 'low_balance', 'k')"
        )
        row = await self.connection.fetchrow("SELECT * FROM user_notifications")
        self.assertEqual(row["delivery_status"], "pending")
        self.assertEqual(row["attempts"], 0)
        self.assertEqual(row["payload"], "{}")
        self.assertIsNone(row["sent_at"])

    async def test_waitlist_entry_is_unique_and_removed_with_slot(self):
        slot = await self.add_slot()
        insert = "INSERT INTO lesson_waitlist (slot_id, telegram_id) VALUES ($1, 1)"
        await self.connection.execute(insert, slot)
        await self.assertRejected(self.asyncpg.UniqueViolationError, insert, slot)
        await self.connection.execute("DELETE FROM lesson_slots WHERE id = $1", slot)
        self.assertEqual(
            await self.connection.fetchval("SELECT COUNT(*) FROM lesson_waitlist"), 0
        )

    async def test_catalog_keys_and_prices_are_validated(self):
        package = (
            "INSERT INTO lesson_packages (key, title, lessons, price_minor{extra}) "
            "VALUES ($1, 'T', $2, $3{values})"
        )
        plain = package.format(extra="", values="")
        for key in ("Bad", "1abc", "a", "has space", "x" * 33):
            await self.assertRejected(
                self.asyncpg.CheckViolationError, plain, key, 1, 100
            )
        await self.assertRejected(
            self.asyncpg.CheckViolationError, plain, "ok_key", 0, 100
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError, plain, "ok_key", 1, 0
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            package.format(extra=", validity_days", values=", 0"),
            "ok_key",
            1,
            100,
        )
        await self.connection.execute(plain, "ok_key", 1, 100)

    async def test_package_in_use_cannot_be_deleted(self):
        await self.add_payment(1)
        await self.assertRejected(
            self.restrict_errors,
            "DELETE FROM lesson_packages WHERE key = 'single'",
        )

    async def test_class_format_in_use_cannot_be_deleted(self):
        await self.add_slot(class_key="individual")
        await self.assertRejected(
            self.restrict_errors,
            "DELETE FROM class_formats WHERE key = 'individual'",
        )

    async def test_staff_roles_and_audit_log(self):
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO staff_members (telegram_id, role) VALUES (1, 'owner')",
        )
        await self.connection.execute(
            "INSERT INTO staff_members (telegram_id, role) VALUES (1, 'teacher')"
        )
        await self.assertRejected(
            self.restrict_errors,
            "DELETE FROM bot_users WHERE telegram_id = 1",
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO staff_audit_log (actor_telegram_id, action) VALUES (1, '')",
        )
        await self.connection.execute(
            "INSERT INTO staff_audit_log (actor_telegram_id, action, details) "
            "VALUES (1, 'grant', '{\"slot_id\": 5}')"
        )
        self.assertEqual(
            await self.connection.fetchval(
                "SELECT (details->>'slot_id')::INT FROM staff_audit_log"
            ),
            5,
        )

    async def test_teacher_is_unassigned_when_staff_member_is_removed(self):
        await self.connection.execute(
            "INSERT INTO staff_members (telegram_id, role) VALUES (1, 'teacher')"
        )
        slot = await self.add_slot()
        await self.connection.execute(
            "UPDATE lesson_slots SET teacher_telegram_id = 1 WHERE id = $1", slot
        )
        await self.connection.execute("DELETE FROM staff_members WHERE telegram_id = 1")
        self.assertIsNone(
            await self.connection.fetchval(
                "SELECT teacher_telegram_id FROM lesson_slots WHERE id = $1", slot
            )
        )

    async def test_broadcasts_and_notes_validation(self):
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO broadcasts (author_telegram_id, body, segment) "
            "VALUES (1, 'hi', 'everyone')",
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO broadcasts (author_telegram_id, body, segment) "
            "VALUES (1, '   ', 'all')",
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO broadcasts (author_telegram_id, body, segment, recipients) "
            "VALUES (1, 'hi', 'all', -1)",
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError,
            "INSERT INTO client_notes (telegram_id, author_telegram_id, body) "
            "VALUES (1, 2, '')",
        )
        await self.connection.execute(
            "INSERT INTO client_notes (telegram_id, author_telegram_id, body) "
            "VALUES (1, 2, 'заметка')"
        )
        await self.connection.execute("DELETE FROM bot_users WHERE telegram_id = 1")
        self.assertEqual(
            await self.connection.fetchval("SELECT COUNT(*) FROM client_notes"), 0
        )

    async def test_telegram_connect_request_rules(self):
        insert = (
            "INSERT INTO telegram_connect_requests (token_hash, browser_secret_hash, "
            "purpose, {extra} expires_at) VALUES ($1, $2, $3, {values} NOW())"
        )
        columns = {
            row["column_name"]
            for row in await self.connection.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'telegram_connect_requests'"
            )
        }
        self.assertIn("web_user_id", columns)
        plain = insert.format(extra="", values="")
        # «link» обязан быть привязан к веб-пользователю, «login» — нет.
        await self.assertRejected(
            self.asyncpg.CheckViolationError, plain, "t1", "s1", "link"
        )
        await self.connection.execute(plain, "t2", "s2", "login")
        await self.assertRejected(
            self.asyncpg.UniqueViolationError, plain, "t2", "s3", "login"
        )
        await self.assertRejected(
            self.asyncpg.CheckViolationError, plain, "t4", "s4", "other"
        )


if __name__ == "__main__":
    unittest.main()
