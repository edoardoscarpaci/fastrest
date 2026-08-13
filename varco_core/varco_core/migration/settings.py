"""
varco_core.migration.settings
==============================
``MigrationSettings`` — the ``VARCO_MIGRATE_*`` env-driven configuration for
every migration posture (``off``/``check``/``upgrade``).

DESIGN: frozen dataclass with ``from_env()``, not pydantic ``BaseSettings``
    ✅ Matches ``SAConfig``/``BeanieSettings`` — a frozen dataclass is the
       established shape for injectable settings objects in this repo.
    ✅ Avoids the ``@Singleton``-on-``BaseSettings`` pitfall documented in
       CLAUDE.md (providify cannot inject pydantic's ``**values`` ctor) —
       registering this via a ``@Provider`` needs no special handling.
    ✅ ``env=`` is injectable so tests never mutate ``os.environ`` — and so
       ``build_service(prefix, factory, env={...})`` (composite deployments)
       can pass a scoped mapping.
    ❌ No automatic env-var validation/coercion helpers that pydantic gives
       for free — ``from_env()`` does its own parsing.

Thread safety:  ✅ Frozen — safe to share across coroutines/threads.
Async safety:   ✅ No I/O.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

_LEGAL_MODES = ("off", "check", "upgrade")
_LEGAL_ON_FAILURE = ("fail", "warn")


@dataclass(frozen=True)
class MigrationSettings:
    """
    Env-driven migration posture configuration.

    Args:
        mode:        ``"off"`` (default, nothing runs) / ``"check"`` (fail
                     startup if the schema is behind, never writes DDL) /
                     ``"upgrade"`` (acquire lock, upgrade heads, release).
                     Env: ``VARCO_MIGRATE_MODE``.
        on_failure:  ``"fail"`` (default, raise) / ``"warn"`` (log ERROR and
                     continue — a deliberately dangerous escape hatch).
                     Env: ``VARCO_MIGRATE_ON_FAILURE``.
        lock_key:    Distributed lock key. Env: ``VARCO_MIGRATE_LOCK_KEY``.
        lock_timeout: Seconds to wait for the lock before re-checking
                     ``pending()``. Env: ``VARCO_MIGRATE_LOCK_TIMEOUT``.
        timeout:     Overall seconds budget for the migration run itself.
                     Env: ``VARCO_MIGRATE_TIMEOUT``.
        target:      Revision target for ``upgrade()``. Env:
                     ``VARCO_MIGRATE_TARGET_REV``.
        dry_run:     Render without applying. Env: ``VARCO_MIGRATE_DRY_RUN``.
    """

    mode: Literal["off", "check", "upgrade"] = "off"
    on_failure: Literal["fail", "warn"] = "fail"
    lock_key: str = "varco:migrate"
    lock_timeout: float = 30.0
    timeout: float = 300.0
    target: str = "heads"
    dry_run: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> MigrationSettings:
        """
        Build ``MigrationSettings`` from environment variables.

        Args:
            env: Mapping to read from. ``None`` reads the real
                 ``os.environ``. Tests and composite deployments pass a
                 scoped mapping instead of mutating the process environ.

        Returns:
            A ``MigrationSettings`` reflecting the given environment, with
            documented defaults for anything unset.

        Raises:
            ValueError: ``VARCO_MIGRATE_MODE`` or ``VARCO_MIGRATE_ON_FAILURE``
                is set to a value outside their legal sets.
        """
        source = env if env is not None else os.environ

        mode = source.get("VARCO_MIGRATE_MODE", "off")
        if mode not in _LEGAL_MODES:
            raise ValueError(
                f"Invalid VARCO_MIGRATE_MODE={mode!r}. "
                f"Legal values are: {', '.join(_LEGAL_MODES)}."
            )

        on_failure = source.get("VARCO_MIGRATE_ON_FAILURE", "fail")
        if on_failure not in _LEGAL_ON_FAILURE:
            raise ValueError(
                f"Invalid VARCO_MIGRATE_ON_FAILURE={on_failure!r}. "
                f"Legal values are: {', '.join(_LEGAL_ON_FAILURE)}."
            )

        return cls(
            mode=mode,  # type: ignore[arg-type]
            on_failure=on_failure,  # type: ignore[arg-type]
            lock_key=source.get("VARCO_MIGRATE_LOCK_KEY", "varco:migrate"),
            lock_timeout=float(source.get("VARCO_MIGRATE_LOCK_TIMEOUT", "30.0")),
            timeout=float(source.get("VARCO_MIGRATE_TIMEOUT", "300.0")),
            target=source.get("VARCO_MIGRATE_TARGET_REV", "heads"),
            dry_run=source.get("VARCO_MIGRATE_DRY_RUN", "false").lower()
            in ("1", "true", "yes"),
        )


__all__ = ["MigrationSettings"]
