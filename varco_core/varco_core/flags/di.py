"""
varco_core.flags.di
=====================
Providify DI integration for ``varco_core.flags`` (Plan 032 / D7).

Mirrors ``varco_casbin.di``'s ``enable_policy_authorizer`` precedent:
``NullFeatureFlags`` is bound by default (a scanned ``@Singleton`` at the
lowest priority — see ``null.py``), and ``enable_feature_flags(container)``
is the only way to swap in a real backend. This is deliberately **not** a
scanned ``@Configuration`` — ``scan`` auto-activates those, which would
silently change flag-resolution behaviour for any app that merely imports
``varco_core.flags`` (or scans ``varco_core`` recursively).

Usage::

    from providify import DIContainer
    from varco_core.flags.di import enable_feature_flags
    from varco_core.flags import AbstractFeatureFlags

    container = DIContainer()
    container.scan("varco_core.flags", recursive=True)  # NullFeatureFlags bound
    enable_feature_flags(container)                      # opt-in: InMemoryFeatureFlags

    flags = await container.aget(AbstractFeatureFlags)   # InMemoryFeatureFlags
"""

from __future__ import annotations

from typing import Any

from providify import Provider

from varco_core.flags.base import AbstractFeatureFlags
from varco_core.flags.memory import InMemoryFeatureFlags

__all__ = ["enable_feature_flags"]


@Provider(singleton=True)
def _provide_in_memory_feature_flags() -> AbstractFeatureFlags:
    """
    Module-level provider binding ``InMemoryFeatureFlags`` as the app
    ``AbstractFeatureFlags``.

    Module-level so ``scan`` does NOT auto-register it (scan only picks up
    ``@Singleton``/``@Configuration``) — it activates only when
    ``enable_feature_flags`` passes it to ``container.provide``.

    Returns:
        An empty ``InMemoryFeatureFlags()`` — every flag falls through to
        the caller's own default until the application populates it via its
        own wiring (this provider exists to demonstrate the opt-in seam,
        not to ship a pre-populated flag set).
    """
    return InMemoryFeatureFlags()


def enable_feature_flags(container: Any) -> Any:
    """
    Opt in to ``InMemoryFeatureFlags`` as the application's
    ``AbstractFeatureFlags``, shadowing the always-off ``NullFeatureFlags``
    default.

    Args:
        container: The ``DIContainer`` already scanned via
            ``container.scan("varco_core.flags", recursive=True)``.

    Returns:
        The same container, for chaining.

    Edge cases:
        - Calling before scanning is safe — ``container.provide`` does not
          require the scan to have happened first, but ``NullFeatureFlags``
          simply will not exist as a competing binding yet.

    Example::

        container = DIContainer()
        container.scan("varco_core.flags", recursive=True)
        enable_feature_flags(container)
    """
    container.provide(_provide_in_memory_feature_flags)
    return container
