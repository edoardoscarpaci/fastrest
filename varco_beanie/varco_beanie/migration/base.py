"""
varco_beanie.migration.base
============================
``Migration`` — the hand-written, ordered migration unit for MongoDB — and
``MigrationRegistry``, which collects and orders them.

DESIGN: hand-written up()/down() scripts, not a document-shape differ
    ✅ MongoDB is schemaless — there is nothing to diff except indexes
       (``IndexReconciler`` handles those separately). Explicit
       hand-written migrations are the honest surface (Plan 006 Non-goals).
    ✅ ``down()`` is concrete-but-raising by default — most Mongo migrations
       are genuinely one-way (a backfill, a document reshape); a migration
       author opts INTO reversibility by overriding ``down()``, rather than
       every migration being forced to implement a no-op.
    ❌ No autogenerate — every migration is hand-written Python.

Thread safety:  ✅ ``MigrationRegistry`` is populated at import/wiring time,
                   read-only afterward — same pattern as ``SAModelRegistry``.
Async safety:   ✅ ``up()``/``down()`` are ``async def``.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, ClassVar

from varco_core.migration.errors import IrreversibleMigrationError


class Migration:
    """
    A single hand-written MongoDB migration.

    Args (as class attributes, set by subclasses):
        version: Sortable version string, e.g. ``"20260812_001"``. Must be
                 unique across every migration in a ``MigrationRegistry``.
        name:    Human-readable migration name.

    DESIGN: a plain base class, not ``abc.ABC``/``@abstractmethod``
        ✅ ``up()`` raising ``NotImplementedError`` by default (rather than
           an enforced ``@abstractmethod``) allows a subclass to attach its
           implementation after class-body execution (e.g. a scaffolding
           tool or a dynamically-generated migration module assigning
           ``MyMigration.up = some_function``) — ``ABCMeta`` freezes
           ``__abstractmethods__`` at class-creation time and does not
           recompute it on later attribute assignment, which would make
           that pattern raise ``TypeError`` at instantiation.
        ❌ A subclass that forgets to override ``up()`` only fails at
           ``upgrade()`` time (``NotImplementedError``), not at import time
           the way ``@abstractmethod`` would.
    """

    version: ClassVar[str]
    name: ClassVar[str]

    async def up(self, db: Any) -> None:
        """Apply this migration against ``db`` (an ``AsyncIOMotorDatabase`` or compatible)."""
        raise NotImplementedError(f"{type(self).__name__} must implement up(db).")

    async def down(self, db: Any) -> None:
        """
        Reverse this migration. Concrete-but-raising by default.

        Raises:
            IrreversibleMigrationError: Unless overridden, every migration
                is one-way — the honest default for hand-written Mongo
                migrations (backfills, document reshapes) that usually have
                no clean inverse.
        """
        raise IrreversibleMigrationError(
            f"Migration {self.version!r} ({self.name!r}) has no down() — "
            "it is irreversible. Override down() to make it reversible."
        )


class MigrationRegistry:
    """
    Collects and orders ``Migration`` subclasses.

    DESIGN: uniqueness/sortability validated in register(), not at apply time
        ✅ A duplicate ``version`` is a developer error caught at import
           time (when ``register()``/``discover()`` runs), not silently
           discovered mid-``upgrade()`` on some future deploy.

    Thread safety:  ⚠️ Populate at startup/wiring time only.
    """

    def __init__(self) -> None:
        self._by_version: dict[str, type[Migration]] = {}

    def register(self, *migrations: type[Migration]) -> None:
        """
        Register one or more ``Migration`` subclasses.

        Args:
            *migrations: ``Migration`` subclasses (not instances).

        Raises:
            ValueError: A ``version`` is already registered — either a
                duplicate within this call or against a previously
                registered migration.
        """
        for migration_cls in migrations:
            version = migration_cls.version
            if version in self._by_version:
                existing = self._by_version[version]
                if existing is not migration_cls:
                    raise ValueError(
                        f"Duplicate migration version {version!r}: "
                        f"{existing.__name__!r} and {migration_cls.__name__!r} "
                        "both claim it."
                    )
                continue
            self._by_version[version] = migration_cls

    def discover(self, package: str) -> None:
        """
        Walk ``package`` and register every ``Migration`` subclass found.

        Mirrors ``container.scan()``'s import-walk pattern.

        Args:
            package: Dotted package name to walk (e.g. ``"myapp.migrations"``).
        """
        pkg = importlib.import_module(package)
        pkg_path = getattr(pkg, "__path__", None)
        if pkg_path is None:
            return

        for _finder, name, _is_pkg in pkgutil.walk_packages(
            pkg_path, prefix=f"{package}."
        ):
            module = importlib.import_module(name)
            for attr in vars(module).values():
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Migration)
                    and attr is not Migration
                    and getattr(attr, "version", None) is not None
                ):
                    self.register(attr)

    def ordered(self) -> tuple[type[Migration], ...]:
        """Return every registered ``Migration`` subclass, sorted by ``version``."""
        return tuple(self._by_version[version] for version in sorted(self._by_version))


__all__ = ["Migration", "MigrationRegistry"]
