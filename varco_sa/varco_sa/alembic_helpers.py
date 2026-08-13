"""
varco_sa.alembic_helpers
=============================
Utilities for wiring ``varco_sa`` into an Alembic migration environment.

Two helpers simplify the ``alembic/env.py`` integration:

``get_target_metadata(*domain_classes, base)``
    Derives the ``MetaData`` from the SA ORM classes generated for the
    given domain models.  Pass the result to ``target_metadata`` in
    ``env.py`` so Alembic can compare it against the live database.

``print_create_ddl(*domain_classes, dialect)``
    Render CREATE TABLE DDL as a plain string.  Useful for reviewing
    what Alembic will generate before running ``alembic upgrade head``,
    or for documenting the schema in code review.

Typical ``alembic/env.py`` wiring::

    from varco_sa.alembic_helpers import get_target_metadata
    from myapp.domain import Post, User

    # Ensure ORM classes are generated before Alembic inspects metadata.
    # provider.register() triggers SAModelFactory.build() for each class.
    from myapp.di import provider  # noqa: F401

    target_metadata = get_target_metadata(Post, User)

    def run_migrations_online() -> None:
        ...
        context.configure(target_metadata=target_metadata, ...)

DESIGN: helpers over a custom AlembicEnv class
    ✅ Minimal footprint — just two functions; no base class to inherit.
    ✅ Flexible — callers decide which domain classes to include.
    ✅ Compatible with existing ``env.py`` setups — drop-in additions.
    ❌ Callers must ensure ``provider.register(...)`` has been called before
       ``get_target_metadata()`` — order dependency at import time.
       The module docstring example shows the correct import order.

Thread safety:  ✅ Both helpers are pure functions after registry is populated.
Async safety:   ✅ Synchronous — Alembic's env.py runs outside the event loop.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from sqlalchemy import MetaData, create_mock_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.schema import CreateTable

if TYPE_CHECKING:
    # DomainModel import is guarded to avoid pulling in the full core at
    # import time — the helpers work with any registered domain class.
    from varco_core.model import DomainModel


# ── get_target_metadata ───────────────────────────────────────────────────────


def get_target_metadata(
    *domain_classes: type[DomainModel],
    base: type[DeclarativeBase] | None = None,
    include_framework: bool = False,
) -> MetaData:
    """
    Return the ``MetaData`` covering the given domain classes.

    Resolves the SA ORM class for each domain class via ``SAModelRegistry``
    and collects the underlying ``Table`` objects into a fresh ``MetaData``
    instance.  Optionally accepts an explicit ``base`` whose full metadata is
    included as well (useful when some tables are hand-crafted, not generated).

    Args:
        *domain_classes: Domain model classes whose generated ORM tables should
                         be included.  Each must have been registered with
                         ``provider.register()`` before this call.
        base:            Optional explicit ``DeclarativeBase`` subclass.
                         When provided, ``base.metadata`` is also included —
                         every table defined on any class that inherits from
                         ``base`` appears in the result.
        include_framework: When ``True``, merge ``varco_sa.metadata.framework_metadata()``
                         (outbox, inbox, jobs, sagas, conversation, dedup,
                         audit, dlq, encryption keys) into the result — the
                         single-branch escape hatch documented in Plan 006's
                         D3 (an app that does not want the packaged ``varco``
                         Alembic branch). Defaults to ``False`` because
                         Phase 2's ``varco`` branch owns those tables by
                         default — including them here as well would make
                         app-side autogenerate try to re-create them.

    Returns:
        A ``MetaData`` object containing all relevant ``Table`` definitions.
        Pass this as ``target_metadata`` in Alembic's ``env.py``.

    Raises:
        KeyError: A ``domain_class`` was never registered with
                  ``SAModelRegistry`` (i.e. ``provider.register()`` was
                  never called for it).

    Edge cases:
        - Passing zero ``domain_classes`` with no ``base`` returns an empty
          ``MetaData`` — Alembic will generate no migrations.
        - Duplicate tables (same class via both ``domain_classes`` and ``base``)
          are deduplicated by ``MetaData`` automatically — no error.
        - If ``provider.register()`` is called lazily (e.g. on first request),
          it must be triggered before this function is called.  The typical
          pattern is to import the provider at the top of ``env.py``.

    Example::

        # alembic/env.py
        from varco_sa.alembic_helpers import get_target_metadata
        from myapp.domain import Post, User
        from myapp.di import provider   # triggers SAModelFactory.build()

        target_metadata = get_target_metadata(Post, User)

    Thread safety:  ✅ Reads only from the already-populated SAModelRegistry.
    """
    # Import here — avoids a top-level circular import when alembic_helpers
    # is imported before the SA models have been built.
    from varco_sa.factory import SAModelRegistry

    # Build a fresh MetaData to collect tables into
    combined = MetaData()

    # Include tables from all registered domain classes
    for domain_cls in domain_classes:
        # Will raise KeyError with a descriptive message if not registered
        orm_cls = SAModelRegistry.get(domain_cls)
        # Copy the Table object into the combined MetaData.
        # tometadata() returns a Table bound to the target MetaData — safe
        # to call multiple times on the same Table (idempotent per MetaData).
        orm_cls.__table__.to_metadata(combined)

    # Optionally include all tables from a hand-crafted DeclarativeBase
    if base is not None:
        for table in base.metadata.tables.values():
            # tometadata() deduplicates — safe even if the same table appeared
            # via domain_classes above
            table.to_metadata(combined)

    if include_framework:
        from varco_sa.metadata import framework_metadata

        for table in framework_metadata().tables.values():
            table.to_metadata(combined)

    return combined


# ── print_create_ddl ──────────────────────────────────────────────────────────


def print_create_ddl(
    *domain_classes: type[DomainModel],
    dialect: str = "postgresql",
) -> str:
    """
    Return the CREATE TABLE DDL for the given domain classes as a string.

    Renders DDL statements using SQLAlchemy's ``CreateTable`` compiler for
    the requested dialect.  No database connection is required — the DDL is
    generated entirely from in-memory ``Table`` metadata.

    Useful for:
    - Reviewing what Alembic will generate before running migrations.
    - Documenting the expected schema in code review.
    - Integration tests that assert on column definitions.

    Args:
        *domain_classes: Domain model classes to render.  Each must be
                         registered with ``SAModelRegistry``.
        dialect:         SQLAlchemy dialect name for DDL rendering.
                         Common values: ``"postgresql"``, ``"sqlite"``,
                         ``"mysql"``.  Defaults to ``"postgresql"``.

    Returns:
        Multi-line string with one CREATE TABLE statement per domain class,
        separated by blank lines.  Statements end with a semicolon.

    Raises:
        KeyError: A domain class was never registered with ``SAModelRegistry``.
        ArgumentError: ``dialect`` is not a recognised SQLAlchemy dialect name.

    Edge cases:
        - Column defaults (``server_default``) are included in the DDL —
          the output may differ from a minimal hand-written migration.
        - PostgreSQL-specific types (``UUID``, ``JSONB``) may render
          differently when ``dialect="sqlite"`` — review for correctness.
        - This function creates an in-memory SQLite engine solely for DDL
          compilation — no database file is created or modified.

    Example::

        from varco_sa.alembic_helpers import print_create_ddl
        from myapp.domain import Post, User
        from myapp.di import provider   # triggers SAModelFactory.build()

        print(print_create_ddl(Post, User, dialect="postgresql"))
        # CREATE TABLE posts (
        #     id UUID NOT NULL,
        #     ...
        # );

    Thread safety:  ✅ Pure function — creates a temporary mock engine.
    Async safety:   ✅ Synchronous — uses ``create_mock_engine``, not async.
    """
    from varco_sa.factory import SAModelRegistry

    # Use a throwaway "mock" engine solely for DDL compilation. No actual
    # connection is ever opened — the mock engine's executor callback is
    # invoked with each compiled statement instead of running it.
    #
    # DESIGN: ``sqlalchemy.create_mock_engine`` instead of
    #   ``create_engine(..., strategy="mock", executor=...)``
    #   ✅ ``strategy=``/``executor=`` on ``create_engine`` were removed in
    #      SQLAlchemy 1.4+ — ``create_mock_engine`` is the current spelling
    #      (source correction 1).
    #   ✅ Still requires no live database connection — pure offline DDL
    #      rendering, matching Alembic's own offline-mode recipe.
    #   ❌ The executor callback receives compiled ``ClauseElement``/SQL
    #      objects, not necessarily plain strings — we ``str()`` them.
    buf = io.StringIO()

    def _capture(sql: object, *_args: object, **_kwargs: object) -> None:
        # Append each DDL statement to the string buffer
        buf.write(str(sql).strip())
        buf.write(";\n\n")

    # Build a mock engine for the requested dialect
    engine = create_mock_engine(f"{dialect}://", _capture)

    for domain_cls in domain_classes:
        orm_cls = SAModelRegistry.get(domain_cls)
        # engine.execute() is what actually invokes the executor callback —
        # merely calling CreateTable(...).compile() never triggers _capture
        # (the second half of source correction 1's bug).
        engine.execute(CreateTable(orm_cls.__table__))

    return buf.getvalue().strip()


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "get_target_metadata",
    "print_create_ddl",
]
