"""
Failing tests for the global-DSN resolution (Plan 007, Phase 6, step 3 — RD-10).
"""

from __future__ import annotations


async def test_no_global_dsn_binds_the_apps_own_engine() -> None:
    from varco_sa.tenancy.global_dsn import resolve_global_engine
    from sqlalchemy.ext.asyncio import create_async_engine

    app_engine = create_async_engine("sqlite+aiosqlite://")

    resolved = await resolve_global_engine(app_engine=app_engine, global_dsn=None)

    assert resolved is app_engine


async def test_global_dsn_set_builds_and_disposes_a_separate_engine() -> None:
    from varco_sa.tenancy.global_dsn import resolve_global_engine
    from sqlalchemy.ext.asyncio import create_async_engine

    app_engine = create_async_engine("sqlite+aiosqlite://")

    resolved = await resolve_global_engine(
        app_engine=app_engine, global_dsn="sqlite+aiosqlite://"
    )

    assert resolved is not app_engine
    await resolved.dispose()


async def test_resolved_global_credential_is_readonly_by_default() -> None:
    from varco_sa.tenancy.global_dsn import resolve_global_engine
    from sqlalchemy.ext.asyncio import create_async_engine

    app_engine = create_async_engine("sqlite+aiosqlite://")

    resolved = await resolve_global_engine(
        app_engine=app_engine, global_dsn="sqlite+aiosqlite://", global_writable=False
    )

    assert getattr(resolved, "_varco_readonly_wrapped", False) is True


async def test_global_writable_true_omits_translation_wrapper() -> None:
    from varco_sa.tenancy.global_dsn import resolve_global_engine
    from sqlalchemy.ext.asyncio import create_async_engine

    app_engine = create_async_engine("sqlite+aiosqlite://")

    resolved = await resolve_global_engine(
        app_engine=app_engine, global_dsn="sqlite+aiosqlite://", global_writable=True
    )

    assert getattr(resolved, "_varco_readonly_wrapped", False) is False
