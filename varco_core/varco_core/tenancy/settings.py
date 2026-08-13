"""
varco_core.tenancy.settings
============================
``TenantIsolation`` / ``TenantScope`` / ``TenantStatus`` enums and the
env-driven ``TenancySettings`` (Plan 007, Phase 1, step 1-2).

DESIGN: three enum values, not six — RLS is additive
    ✅ ``TenantIsolation`` names *how strongly* tenants are isolated; RLS is
       a hardening flag (``enforce_rls: bool``) on ``SHARED``, not a fourth
       enum value — keeps the enum backend-neutral (avoids doubling again
       for ``TenantScope``).
    ❌ Two independent boolean-ish axes are visible only through the enum
       *and* a flag rather than one flat value. Accepted — see the plan's
       "Alternatives considered" section for the rejected six-value form.

DESIGN: frozen dataclass with ``from_env()``, mirroring ``MigrationSettings``
    ✅ Matches the established shape for injectable settings objects in this
       repo (``SAConfig``, ``BeanieSettings``, ``MigrationSettings``).
    ✅ Avoids the ``@Singleton``-on-pydantic-``BaseSettings`` pitfall — this
       is a plain frozen dataclass, safe to register via ``@Provider``.
    ✅ ``env=`` is injectable so tests never mutate ``os.environ``.
    ❌ No automatic env-var validation/coercion helpers pydantic gives for
       free — ``from_env()`` does its own parsing, same trade-off as
       ``MigrationSettings``.

RD-9: **no** ``VARCO_TENANCY_MOUNT_ADMIN`` env var exists or is ever read.
    The privileged admin surface can only be mounted via an explicit,
    acknowledged code call (``mount_tenant_admin(..., acknowledge_bundled_
    admin=True)``, Phase 5) — never by environment alone. This module does
    not recognise any such key, by design; asserted directly in
    ``test_tenancy_settings.py::test_from_env_ignores_mount_admin_env_var_entirely``.

Thread safety:  ✅ Frozen — safe to share across coroutines/threads.
Async safety:   ✅ No I/O.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

_LEGAL_ISOLATION = ("shared", "schema", "database")


class TenantIsolation(StrEnum):
    """How strongly tenants are isolated at the storage layer."""

    SHARED = "shared"  # one schema/db/collection + discriminator
    SCHEMA = "schema"  # one Postgres schema per tenant   (Postgres only)
    DATABASE = "database"  # one logical database per tenant  (Postgres + Mongo)


class TenantScope(StrEnum):
    """Orthogonal axis: whether an entity is per-tenant or globally shared."""

    TENANT = "tenant"  # default — routed per tenant under SCHEMA/DATABASE
    GLOBAL = "global"  # one shared copy; every tenant reads it


class TenantStatus(StrEnum):
    """Lifecycle status of a tenant in the catalog (Plan 007, Phase 4)."""

    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEPROVISIONING = "deprovisioning"
    DELETED = "deleted"


@dataclass(frozen=True)
class TenancySettings:
    """
    Env-driven multitenancy configuration.

    With every default, the deployment is byte-identical to today's
    behaviour: no pool, no extra engine/client, no symbolic schema, no
    control-plane surface constructed.

    Args:
        isolation: ``TenantIsolation`` — storage isolation strategy.
                   Env: ``VARCO_TENANCY_ISOLATION``.
        enforce_rls: Hardening flag on ``SHARED`` — assert Postgres RLS is
                   enabled on every routed table. Env:
                   ``VARCO_TENANCY_ENFORCE_RLS``.
        schema_template: ``{tenant_id}``-templated schema name for
                   ``SCHEMA``. Env: ``VARCO_TENANCY_SCHEMA_TEMPLATE``.
        db_template: ``{tenant_id}``-templated database name for
                   ``DATABASE``. Env: ``VARCO_TENANCY_DB_TEMPLATE``.
        max_entries: Soft cap on the bounded per-tenant resource pool.
                   Env: ``VARCO_TENANCY_MAX_ENTRIES``.
        idle_ttl_s: Sweeper idle threshold, seconds. Env:
                   ``VARCO_TENANCY_IDLE_TTL``.
        catalog_ttl_s: ``CachedTenantCatalog`` TTL backstop, seconds. Env:
                   ``VARCO_TENANCY_CATALOG_TTL``.
        fanout_framework_tables: Enable ``TenantFanoutSupervisor`` (RD-8).
                   Env: ``VARCO_TENANCY_FANOUT_FRAMEWORK_TABLES``.
        global_dsn: Optional DSN for the global/shared database (RD-10).
                   Falls back to the app's own DSN when unset. Env:
                   ``VARCO_TENANCY_GLOBAL_DSN``.
        global_writable: Opt-in to a writable global credential (RD-10).
                   Env: ``VARCO_TENANCY_GLOBAL_WRITABLE``.

    Edge cases:
        - No key corresponding to "mount the admin surface" is recognised
          anywhere in this module (RD-9) — asserted by a dedicated test.
    """

    isolation: TenantIsolation = TenantIsolation.SHARED
    enforce_rls: bool = False
    schema_template: str = "t_{tenant_id}"
    db_template: str = "db_{tenant_id}"
    max_entries: int = 50
    idle_ttl_s: float = 300.0
    catalog_ttl_s: float = 60.0
    fanout_framework_tables: bool = False
    global_dsn: str | None = None
    global_writable: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TenancySettings:
        """
        Build ``TenancySettings`` from environment variables.

        Args:
            env: Mapping to read from. ``None`` reads the real
                 ``os.environ``. Tests pass a scoped mapping instead of
                 mutating the process environ.

        Returns:
            A ``TenancySettings`` reflecting the given environment, with
            documented defaults for anything unset.

        Raises:
            ValueError: ``VARCO_TENANCY_ISOLATION`` set to a value outside
                the legal set.
        """
        source = env if env is not None else os.environ

        isolation = source.get("VARCO_TENANCY_ISOLATION", TenantIsolation.SHARED.value)
        if isolation not in _LEGAL_ISOLATION:
            raise ValueError(
                f"Invalid VARCO_TENANCY_ISOLATION={isolation!r}. "
                f"Legal values are: {', '.join(_LEGAL_ISOLATION)}."
            )

        def _bool(key: str, default: bool) -> bool:
            raw = source.get(key)
            if raw is None:
                return default
            return raw.strip().lower() in ("1", "true", "yes", "on")

        defaults = cls()

        return cls(
            isolation=TenantIsolation(isolation),
            enforce_rls=_bool("VARCO_TENANCY_ENFORCE_RLS", defaults.enforce_rls),
            schema_template=source.get(
                "VARCO_TENANCY_SCHEMA_TEMPLATE", defaults.schema_template
            ),
            db_template=source.get("VARCO_TENANCY_DB_TEMPLATE", defaults.db_template),
            max_entries=int(
                source.get("VARCO_TENANCY_MAX_ENTRIES", defaults.max_entries)
            ),
            idle_ttl_s=float(source.get("VARCO_TENANCY_IDLE_TTL", defaults.idle_ttl_s)),
            catalog_ttl_s=float(
                source.get("VARCO_TENANCY_CATALOG_TTL", defaults.catalog_ttl_s)
            ),
            fanout_framework_tables=_bool(
                "VARCO_TENANCY_FANOUT_FRAMEWORK_TABLES",
                defaults.fanout_framework_tables,
            ),
            global_dsn=source.get("VARCO_TENANCY_GLOBAL_DSN", defaults.global_dsn),
            global_writable=_bool(
                "VARCO_TENANCY_GLOBAL_WRITABLE", defaults.global_writable
            ),
        )
