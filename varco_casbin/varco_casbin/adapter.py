"""
varco_casbin.adapter
====================
Casbin persistence-adapter factory.

The adapter is *where policy rules live*.  ``build_adapter`` turns a
``CasbinSettings.adapter`` selector into a concrete Casbin adapter object that
``CasbinPolicyEngine`` hands to ``casbin.AsyncEnforcer``:

    "memory"     → None (policies live only in the enforcer's RAM — dev/tests)
    "file"       → casbin FileAdapter (CSV on disk — single process)
    "sqlalchemy" → casbin_async_sqlalchemy_adapter.Adapter (durable, dynamic CRUD)
    "beanie"     → varco_casbin.BeanieAdapter (durable, MongoDB-backed via Beanie)

DESIGN: a factory function rather than per-adapter @Singletons
    ✅ One injection point — the engine asks the factory for whatever the
       settings selected; no conditional DI wiring at the application level.
    ✅ Optional dependencies (the async SQLAlchemy adapter) are imported
       lazily, so ``pip install varco-casbin`` works without them.
    ❌ The factory must be kept in sync with the ``CasbinSettings.adapter``
       Literal — a new option needs editing in two places.  Acceptable.

Thread safety:  ✅ Pure factory — returns a fresh adapter per call.
Async safety:   ✅ No awaits; adapter I/O happens later inside the engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from varco_casbin.config import CasbinSettings


def build_adapter(settings: CasbinSettings) -> Any | None:
    """
    Build the Casbin persistence adapter selected by ``settings.adapter``.

    Args:
        settings: The Casbin configuration.  ``settings.adapter`` chooses the
                  backend; ``policy_path`` / ``db_url`` parameterize it.

    Returns:
        A Casbin adapter instance, or ``None`` for the in-memory mode (Casbin
        treats a ``None`` adapter as "keep policies in RAM only").

    Raises:
        ValueError:   ``adapter`` is unknown, or a required parameter
                      (``policy_path`` for file, ``db_url`` / ``db_name`` for
                      beanie / sqlalchemy) is missing — fail fast rather than
                      silently degrade.
        ImportError:  ``adapter="sqlalchemy"`` but the optional
                      ``varco-casbin[sqlalchemy]`` extra is not installed; or
                      ``adapter="beanie"`` but ``varco-casbin[beanie]`` is not.

    Edge cases:
        - ``"memory"`` returns ``None`` — a process restart loses all policy.
        - ``"file"`` is single-process only; concurrent writers can corrupt
          the CSV.  Use ``"sqlalchemy"`` or ``"beanie"`` for dynamic persisted
          CRUD with multi-process safety.
    """
    kind = settings.adapter

    # In-memory: no adapter object — the enforcer holds policy in RAM.
    if kind == "memory":
        return None

    # File CSV adapter — single-process durability.  Must be the *async* file
    # adapter: AsyncEnforcer rejects a sync Adapter ("Invalid parameters").
    if kind == "file":
        if not settings.policy_path:
            raise ValueError(
                "CasbinSettings.adapter='file' requires VARCO_CASBIN_POLICY_PATH "
                "to point at a CSV policy file."
            )
        from casbin.persist.adapters.asyncio import AsyncFileAdapter

        return AsyncFileAdapter(settings.policy_path)

    # Durable async SQLAlchemy adapter — the dynamic persisted CRUD store.
    if kind == "sqlalchemy":
        if not settings.db_url:
            raise ValueError(
                "CasbinSettings.adapter='sqlalchemy' requires VARCO_CASBIN_DB_URL "
                "(e.g. 'postgresql+asyncpg://localhost/app')."
            )
        try:
            # Lazy import — only needed when the sqlalchemy extra is selected.
            from casbin_async_sqlalchemy_adapter import Adapter
        except ImportError as exc:  # pragma: no cover - import-guard branch
            raise ImportError(
                "adapter='sqlalchemy' needs the optional dependency. "
                "Install it with: pip install 'varco-casbin[sqlalchemy]'."
            ) from exc

        return Adapter(settings.db_url)

    # Durable async MongoDB/Beanie adapter — the dynamic persisted CRUD store
    # for Beanie-backed applications that want to avoid a SQLAlchemy dependency.
    if kind == "beanie":
        if not settings.db_url:
            raise ValueError(
                "CasbinSettings.adapter='beanie' requires VARCO_CASBIN_DB_URL "
                "(e.g. 'mongodb://localhost:27017')."
            )
        if not settings.db_name:
            raise ValueError(
                "CasbinSettings.adapter='beanie' requires VARCO_CASBIN_DB_NAME "
                "(the target MongoDB database name, e.g. 'myapp')."
            )
        try:
            # Lazy import — only needed when the beanie extra is selected.
            from varco_casbin.beanie_adapter import BeanieAdapter  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - import-guard branch
            raise ImportError(
                "adapter='beanie' needs the optional dependency. "
                "Install it with: pip install 'varco-casbin[beanie]'."
            ) from exc

        return BeanieAdapter(db_url=settings.db_url, db_name=settings.db_name)

    # Unreachable while the Literal and this branch stay in sync, but guards
    # against a future settings change that forgets to update the factory.
    raise ValueError(
        f"Unknown Casbin adapter {kind!r}. "
        f"Valid options: 'memory', 'file', 'sqlalchemy', 'beanie'."
    )


__all__ = ["build_adapter"]
