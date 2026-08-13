"""
varco_sa.tenancy.router
=========================
``SASchemaRouter`` — schema-per-tenant routing via SQLAlchemy's
``schema_translate_map`` (Plan 007, Phase 3, step 1-2).

DESIGN: ``schema_translate_map`` over ``SET LOCAL search_path`` (chosen)
    See the plan's "DESIGN: schema_translate_map over SET LOCAL search_path"
    section for the full rationale. Summary: fails closed (an unrouted
    query errors rather than silently reading another tenant's rows),
    pooler-safe unconditionally (nothing written to session state), zero
    extra round trips, and one compiled-SQL cache entry shared across every
    tenant (the map's *keys* — ``{"tenant": ...}`` — participate in the
    compile cache, not its values).

    ``mechanism="search_path"`` is the documented, deliberately kept escape
    hatch — ✅ covers raw ``text()`` SQL the translate map cannot reach;
    ❌ fail-open on a forgotten call, a round trip per transaction. Even in
    this mode, the statement is ``set_config(..., true)`` (transaction-
    scoped, pooler-safe) — **never** a bare session-scoped ``SET`` (the same
    defect class as ``SAAdvisoryLock``'s U-16 session-scoped release).

Identifier validation is the **only** injection defence for a schema name —
schema names cannot be bound parameters in DDL/``search_path``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

# The symbolic schema token every routed table's generated ORM class carries
# (see varco_sa.factory.SAModelFactory.build(isolation="schema")) — resolved
# per-session via schema_translate_map={"tenant": "<real schema>"}.
SYMBOLIC_SCHEMA_TOKEN = "tenant"

_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SASchemaRouter:
    """
    Resolves a tenant id to a real Postgres schema name and binds sessions
    to it.

    Args:
        schema_template: ``{tenant_id}``-templated schema name (mirrors
                          ``TenancySettings.schema_template``).
        mechanism:        ``"translate_map"`` (default, recommended) or
                          ``"search_path"`` (documented escape hatch for raw
                          SQL that ``schema_translate_map`` cannot reach).
    """

    def __init__(
        self,
        *,
        schema_template: str = "t_{tenant_id}",
        mechanism: Literal["translate_map", "search_path"] = "translate_map",
    ) -> None:
        self._schema_template = schema_template
        self._mechanism = mechanism

    def schema_name_for(self, tenant_id: str) -> str:
        """
        Render the real schema name for ``tenant_id``.

        Raises:
            ValueError: The rendered name fails ``^[A-Za-z_][A-Za-z0-9_]*$``
                — schema names cannot be bound parameters, so identifier
                validation is the only injection defence available.
        """
        name = self._schema_template.format(tenant_id=tenant_id)
        if not _VALID_IDENTIFIER.match(name):
            raise ValueError(
                f"Schema name {name!r} (rendered from tenant_id={tenant_id!r}) "
                "is not a valid SQL identifier "
                "(must match ^[A-Za-z_][A-Za-z0-9_]*$). Schema names cannot "
                "be bound parameters, so this validation is the only "
                "injection defence — reject rather than quote-and-hope."
            )
        return name

    def session_factory_for(
        self, engine: "AsyncEngine", tenant_id: str
    ) -> "async_sessionmaker[AsyncSession]":
        """
        Build an ``async_sessionmaker`` bound to ``engine`` with
        ``schema_translate_map={"tenant": "<real schema>"}`` execution
        options applied.

        Every session created by the returned factory resolves the
        ``SYMBOLIC_SCHEMA_TOKEN`` ("tenant") to ``tenant_id``'s real schema;
        global tables and framework tables (which carry no symbolic token)
        resolve to the untranslated default schema.

        Edge cases:
            - Raw ``text()`` SQL is **not** translated — it must
              self-qualify (documented loudly in the multitenancy docs).
        """
        schema_name = self.schema_name_for(tenant_id)
        translated_engine = engine.execution_options(
            schema_translate_map={SYMBOLIC_SCHEMA_TOKEN: schema_name}
        )
        return async_sessionmaker(bind=translated_engine, expire_on_commit=False)

    def search_path_statement(self, tenant_id: str) -> sa.TextClause:
        """
        Build the ``mechanism="search_path"`` escape-hatch statement.

        Always ``set_config(..., true)`` (transaction-scoped via
        ``is_local=true``) — **never** a bare ``SET`` (session-scoped, and
        therefore unsafe under a transaction-mode pooler; the same defect
        class as ``SAAdvisoryLock``'s session-scoped release).
        """
        schema_name = self.schema_name_for(tenant_id)
        return sa.text(
            "SELECT set_config('search_path', :schema_name, true)"
        ).bindparams(schema_name=schema_name)

    @property
    def mechanism(self) -> str:
        return self._mechanism
