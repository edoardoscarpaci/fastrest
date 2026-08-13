"""
varco_fastapi.migrate
=======================
``MigrationLifecycle`` — the auto-on-startup migration component (Plan 006
Phase 4, the headline feature). Structurally satisfies
``varco_fastapi.lifespan.AbstractLifecycle`` (no inheritance needed) and is
registered **first** in ``VarcoLifespan``'s component list — before the
event bus, before the outbox relay, before the job runner — so nothing
touches a table that does not exist yet.

``varco_fastapi`` never imports ``varco_sa``, ``varco_beanie``, or
``alembic`` — only ``varco_core.migration.AbstractMigrator``. The concrete
migrator is constructed by the application and passed in, exactly like
``AbstractEventBus``.

DESIGN: the D4 algorithm lives here, not in AbstractMigrator implementations
    ✅ ``AlembicMigrator``/``BeanieMigrator`` both already fold "lock timeout
       → re-check pending → skipped_locked" into their own ``upgrade()`` —
       this class's re-check is a SECOND, outer layer that decides what to
       DO with a ``skipped_locked=True`` report (serve vs. raise) and how
       ``on_failure`` interacts with it — that decision is app-startup
       policy, not migrator-internal mechanics.
    ✅ ``asyncio.timeout(settings.timeout)`` wraps the WHOLE run (all
       migrators, sequentially) — one budget for "how long may migrations
       hold up startup", not per-migrator.

Thread safety:  N/A — runs once during ASGI lifespan startup.
Async safety:   ✅ ``start()``/``stop()`` are ``async def``.
"""

from __future__ import annotations

import asyncio
import logging

from varco_core.migration.base import AbstractMigrator
from varco_core.migration.errors import MigrationLockTimeout
from varco_core.migration.settings import MigrationSettings

logger = logging.getLogger(__name__)


class MigrationLifecycle:
    """
    Runs one or more ``AbstractMigrator``s during ASGI startup, under the
    posture configured by ``MigrationSettings.mode``.

    Args:
        *migrators: One or more ``AbstractMigrator`` instances, run
                    sequentially in the given order (a composite/dual-backend
                    service's Postgres and Mongo migrators, for example).
        settings:   ``None`` → ``MigrationSettings.from_env()``.
    """

    def __init__(
        self,
        *migrators: AbstractMigrator,
        settings: MigrationSettings | None = None,
    ) -> None:
        self._migrators = migrators
        self._settings = settings or MigrationSettings.from_env()

    async def start(self) -> None:
        """
        Run every migrator sequentially under the configured mode.

        Raises:
            PendingMigrationsError: ``mode="check"`` and a migrator has
                pending revisions.
            MigrationLockTimeout: ``mode="upgrade"``, a migrator reported
                ``skipped_locked=True``, and its subsequent ``plan()`` is
                still non-empty.
            Exception: Any migrator-raised exception, when
                ``on_failure="fail"`` (the default).
        """
        if self._settings.mode == "off":
            return

        try:
            async with asyncio.timeout(self._settings.timeout):
                for migrator in self._migrators:
                    await self._run_one(migrator)
        except TimeoutError as exc:
            message = (
                f"Migration timed out after {self._settings.timeout}s "
                f"(VARCO_MIGRATE_TIMEOUT)."
            )
            if self._settings.on_failure == "warn":
                logger.error(message)
                return
            raise TimeoutError(message) from exc
        except Exception:
            if self._settings.on_failure == "warn":
                logger.exception(
                    "Migration failed and on_failure='warn' — continuing to "
                    "serve traffic against a schema that may be incorrect."
                )
                return
            raise

    async def _run_one(self, migrator: AbstractMigrator) -> None:
        if self._settings.mode == "check":
            await migrator.check()
            return

        # mode == "upgrade"
        report = await migrator.upgrade(
            self._settings.target, dry_run=self._settings.dry_run
        )

        if report.skipped_locked:
            replanned = await migrator.plan()
            if replanned.is_empty:
                logger.info(
                    "Migration lock was held by another instance; schema "
                    "is now current — proceeding."
                )
                return
            raise MigrationLockTimeout(
                self._settings.lock_key, self._settings.lock_timeout
            )

        if report.applied:
            applied_ids = ", ".join(rev.id for rev in report.applied)
            logger.info("Applied %d migration(s): %s", len(report.applied), applied_ids)

    async def stop(self) -> None:
        """
        Close every migrator. Never raises — matches
        ``VarcoLifespan._stop_all``'s log-and-swallow contract. Idempotent.
        """
        for migrator in self._migrators:
            try:
                await migrator.close()
            except Exception:
                logger.exception("Error closing migrator %r during shutdown.", migrator)


__all__ = ["MigrationLifecycle"]
