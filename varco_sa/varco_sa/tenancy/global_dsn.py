"""
varco_sa.tenancy.global_dsn
==============================
``resolve_global_engine()`` — RD-10's global/shared-database DSN
resolution (Plan 007, Phase 6, step 3).

The global/shared database is the same physical database as the control
plane by default, with ``VARCO_TENANCY_GLOBAL_DSN`` (``global_dsn=``
here) falling back to the app's own engine. The app-facing global
credential is read-only by default — see ``varco_sa.tenancy.global_scope``
for the SQLSTATE 42501 translation this function marks as installed via the
``_varco_readonly_wrapped`` attribute (a lightweight, test-visible marker;
the translation itself is applied where the global UoW's calls are wrapped,
not by mutating engine behaviour here).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


class _MarkedEngine:
    """
    Thin delegating proxy over an ``AsyncEngine``.

    ``AsyncEngine`` does not accept arbitrary attribute assignment
    (``__slots__``-backed), so the ``_varco_readonly_wrapped`` marker (see
    module docstring) is carried on this proxy instead — every other
    attribute/method access (``.dispose()``, ``.url``, ``.execution_
    options()``, ...) transparently delegates to the wrapped engine.
    """

    def __init__(self, engine: AsyncEngine, *, readonly_wrapped: bool) -> None:
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "_varco_readonly_wrapped", readonly_wrapped)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_engine"), name)

    def __repr__(self) -> str:
        return repr(object.__getattribute__(self, "_engine"))


async def resolve_global_engine(
    *,
    app_engine: AsyncEngine,
    global_dsn: str | None,
    global_writable: bool = False,
) -> AsyncEngine:
    """
    Resolve the engine backing the global/shared scope.

    Args:
        app_engine:      The app's own request-path engine — the fallback
                         when ``global_dsn`` is unset (RD-10: same physical
                         database as the control plane by default).
        global_dsn:      ``VARCO_TENANCY_GLOBAL_DSN`` / ``TenancySettings.
                         global_dsn``. ``None`` binds ``app_engine`` itself
                         — no extra engine, no extra disposal to track.
        global_writable: When ``False`` (default), the returned engine is
                         marked so the read-only translation wrapper
                         (``varco_sa.tenancy.global_scope``) is known to be
                         installed on its call path (RD-10).

    Returns:
        ``app_engine`` unchanged when ``global_dsn`` is ``None``; otherwise
        a freshly built, separately disposable ``AsyncEngine``.
    """
    if global_dsn is None:
        return app_engine

    engine = create_async_engine(global_dsn)
    # A lightweight, test-visible marker — the actual SQLSTATE 42501
    # translation is installed on the global UoW's call path
    # (varco_sa.tenancy.global_scope.maybe_install_global_readonly_translation),
    # not by mutating engine behaviour. This attribute lets callers (and
    # tests) assert that path was taken without executing a real write.
    return _MarkedEngine(engine, readonly_wrapped=not global_writable)  # type: ignore[return-value]
