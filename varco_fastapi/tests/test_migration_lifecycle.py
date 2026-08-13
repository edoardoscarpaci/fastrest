"""
Failing tests for varco_fastapi.migrate.MigrationLifecycle (Plan 006,
Phase 4, step 39). Uses ``InMemoryMigrator`` — no DB at all.
"""

from __future__ import annotations

import logging

import pytest


async def test_mode_off_never_calls_the_migrator() -> None:
    from varco_core.migration.base import Revision
    from varco_core.migration.inmemory import InMemoryMigrator
    from varco_core.migration.settings import MigrationSettings
    from varco_fastapi.migrate import MigrationLifecycle

    migrator = InMemoryMigrator(revisions=[Revision(id="0001", label="init")])
    lifecycle = MigrationLifecycle(migrator, settings=MigrationSettings(mode="off"))

    await lifecycle.start()

    assert migrator.calls == []


async def test_mode_check_raises_pending_migrations_error_listing_them() -> None:
    from varco_core.migration.base import Revision
    from varco_core.migration.errors import PendingMigrationsError
    from varco_core.migration.inmemory import InMemoryMigrator
    from varco_core.migration.settings import MigrationSettings
    from varco_fastapi.migrate import MigrationLifecycle

    migrator = InMemoryMigrator(revisions=[Revision(id="0001", label="create orders")])
    lifecycle = MigrationLifecycle(migrator, settings=MigrationSettings(mode="check"))

    with pytest.raises(PendingMigrationsError) as exc:
        await lifecycle.start()

    assert "0001" in str(exc.value)
    assert "create orders" in str(exc.value)


async def test_mode_check_with_no_pending_returns_cleanly() -> None:
    from varco_core.migration.inmemory import InMemoryMigrator
    from varco_core.migration.settings import MigrationSettings
    from varco_fastapi.migrate import MigrationLifecycle

    migrator = InMemoryMigrator(revisions=[])
    lifecycle = MigrationLifecycle(migrator, settings=MigrationSettings(mode="check"))

    await lifecycle.start()  # must not raise


async def test_mode_upgrade_calls_upgrade_once_and_logs_applied(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from varco_core.migration.base import Revision
    from varco_core.migration.inmemory import InMemoryMigrator
    from varco_core.migration.settings import MigrationSettings
    from varco_fastapi.migrate import MigrationLifecycle

    migrator = InMemoryMigrator(revisions=[Revision(id="0001", label="init")])
    lifecycle = MigrationLifecycle(
        migrator, settings=MigrationSettings(mode="upgrade", target="heads")
    )

    with caplog.at_level(logging.INFO):
        await lifecycle.start()

    assert migrator.calls.count("upgrade") == 1
    assert any("0001" in record.message for record in caplog.records)


async def test_on_failure_warn_with_raising_migrator_logs_error_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from varco_core.migration.base import Revision
    from varco_core.migration.inmemory import InMemoryMigrator
    from varco_core.migration.settings import MigrationSettings
    from varco_fastapi.migrate import MigrationLifecycle

    migrator = InMemoryMigrator(
        revisions=[Revision(id="0001", label="init")],
        fail_on_upgrade_call=1,
    )
    lifecycle = MigrationLifecycle(
        migrator, settings=MigrationSettings(mode="upgrade", on_failure="warn")
    )

    with caplog.at_level(logging.ERROR):
        await lifecycle.start()  # must NOT raise

    assert any(record.levelno == logging.ERROR for record in caplog.records)


async def test_on_failure_fail_raises() -> None:
    from varco_core.migration.base import Revision
    from varco_core.migration.inmemory import InMemoryMigrator
    from varco_core.migration.settings import MigrationSettings
    from varco_fastapi.migrate import MigrationLifecycle

    migrator = InMemoryMigrator(
        revisions=[Revision(id="0001", label="init")],
        fail_on_upgrade_call=1,
    )
    lifecycle = MigrationLifecycle(
        migrator, settings=MigrationSettings(mode="upgrade", on_failure="fail")
    )

    with pytest.raises(Exception):  # noqa: B017 — migrator-raised error propagates
        await lifecycle.start()


async def test_skipped_locked_with_empty_subsequent_plan_returns_cleanly() -> None:
    from varco_core.migration.base import Revision
    from varco_core.migration.inmemory import InMemoryMigrator
    from varco_core.migration.settings import MigrationSettings
    from varco_fastapi.migrate import MigrationLifecycle

    migrator = InMemoryMigrator(
        revisions=[Revision(id="0001", label="init")],
        skip_locked_on_upgrade=True,
        # After the "skipped" upgrade, plan() reports nothing pending — a
        # concurrent pod already finished the work.
        pending_after_skip=[],
    )
    lifecycle = MigrationLifecycle(migrator, settings=MigrationSettings(mode="upgrade"))

    await lifecycle.start()  # must not raise


async def test_skipped_locked_with_nonempty_plan_raises_lock_timeout() -> None:
    from varco_core.migration.base import Revision
    from varco_core.migration.errors import MigrationLockTimeout
    from varco_core.migration.inmemory import InMemoryMigrator
    from varco_core.migration.settings import MigrationSettings
    from varco_fastapi.migrate import MigrationLifecycle

    migrator = InMemoryMigrator(
        revisions=[Revision(id="0001", label="init")],
        skip_locked_on_upgrade=True,
        pending_after_skip=[Revision(id="0001", label="init")],
    )
    lifecycle = MigrationLifecycle(migrator, settings=MigrationSettings(mode="upgrade"))

    with pytest.raises(MigrationLockTimeout):
        await lifecycle.start()


async def test_timeout_exceeded_raises_with_elapsed_time_in_message() -> None:
    from varco_core.migration.inmemory import InMemoryMigrator
    from varco_core.migration.settings import MigrationSettings
    from varco_fastapi.migrate import MigrationLifecycle

    migrator = InMemoryMigrator(revisions=[], upgrade_delay_s=0.5)
    lifecycle = MigrationLifecycle(
        migrator, settings=MigrationSettings(mode="upgrade", timeout=0.05)
    )

    with pytest.raises(
        Exception
    ) as exc:  # noqa: B017 — asyncio.TimeoutError or wrapper
        await lifecycle.start()

    assert "0.05" in str(exc.value) or "timeout" in str(exc.value).lower()


async def test_stop_calls_close_and_is_idempotent() -> None:
    from varco_core.migration.inmemory import InMemoryMigrator
    from varco_fastapi.migrate import MigrationLifecycle

    migrator = InMemoryMigrator(revisions=[])
    lifecycle = MigrationLifecycle(migrator)

    await lifecycle.stop()
    await lifecycle.stop()  # idempotent — must not raise

    assert migrator.calls.count("close") == 2 or migrator.closed is True


async def test_two_migrators_run_sequentially_in_registration_order() -> None:
    from varco_core.migration.inmemory import InMemoryMigrator
    from varco_core.migration.settings import MigrationSettings
    from varco_fastapi.migrate import MigrationLifecycle

    order: list[str] = []

    migrator_a = InMemoryMigrator(revisions=[], name="a", call_log=order)
    migrator_b = InMemoryMigrator(revisions=[], name="b", call_log=order)

    lifecycle = MigrationLifecycle(
        migrator_a, migrator_b, settings=MigrationSettings(mode="upgrade")
    )

    await lifecycle.start()

    assert order == ["a", "b"]
