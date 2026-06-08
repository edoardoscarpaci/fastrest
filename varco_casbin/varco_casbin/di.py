"""
varco_casbin.di
===============
Providify DI integration for ``varco_casbin``.

``CasbinPolicyEngine`` carries ``@Singleton`` and is discovered automatically by
``container.scan("varco_casbin", recursive=True)``.  Because it implements both
``PolicyEngine`` and ``PolicyManagement``, scanning binds it to both interfaces
— service code resolves ``PolicyEngine``, the REST router resolves
``PolicyManagement``.

``CasbinSettings`` is a pydantic ``BaseSettings`` and therefore cannot be a
providify ``@Singleton`` (providify would try to inject pydantic's ``**values``
constructor parameter).  ``bootstrap`` registers a clean provider for it instead.

Binding the *authorizer* is deliberately **opt-in**.  ``scan`` auto-registers
``@Singleton`` and ``@Configuration`` objects, so the authorizer is NOT a scanned
``@Configuration`` — it would silently shadow an application's own
``AbstractAuthorizer`` just by importing the package.  Instead call
``enable_policy_authorizer(container)`` explicitly; it registers a module-level
``@Provider`` (which ``scan`` does NOT auto-register) only when you ask for it.

Usage::

    from providify import DIContainer
    from varco_casbin.di import bootstrap, enable_policy_authorizer
    from varco_core.auth import PolicyEngine, PolicyManagement, AbstractAuthorizer

    container = bootstrap(DIContainer())            # engine + settings (no authorizer)
    enable_policy_authorizer(container)             # opt-in: bind the authorizer

    engine = await container.aget(PolicyEngine)     # CasbinPolicyEngine
    authz  = await container.aget(AbstractAuthorizer)  # PolicyEngineAuthorizer
"""

from __future__ import annotations

from typing import Any

from providify import Inject, Provider

from varco_core.auth import (
    AbstractAuthorizer,
    PolicyEngine,
    PolicyEngineAuthorizer,
    RequestMapper,
)
from varco_casbin.config import CasbinSettings


@Provider(singleton=True)
def _provide_casbin_settings() -> CasbinSettings:
    """
    Provider that builds ``CasbinSettings`` from the environment.

    Registered by ``bootstrap`` so the engine's ``Inject[CasbinSettings]``
    resolves to a ready instance.  This is the deterministic alternative to
    ``@Singleton`` on a pydantic ``BaseSettings`` (which providify cannot
    construct — see the note in ``varco_casbin.config``).

    Returns:
        A ``CasbinSettings`` populated from ``VARCO_CASBIN_*`` env vars.
    """
    return CasbinSettings.from_env()


@Provider(singleton=True)
def _provide_policy_authorizer(
    engine: Inject[PolicyEngine],
) -> AbstractAuthorizer:
    """
    Provider that binds ``PolicyEngineAuthorizer`` as the app ``AbstractAuthorizer``.

    Module-level so that ``scan`` does NOT auto-register it (scan only picks up
    ``@Singleton`` / ``@Configuration``).  It becomes active only when
    ``enable_policy_authorizer`` passes it to ``container.provide``.

    Args:
        engine: The scanned ``CasbinPolicyEngine`` (as ``PolicyEngine``).

    Returns:
        A ``PolicyEngineAuthorizer`` using the default ``RequestMapper``.
    """
    # Default mapper mirrors GrantBasedAuthorizer's key convention so token
    # grants and engine policy rules share one resource-key namespace.
    return PolicyEngineAuthorizer(engine, RequestMapper())


def bootstrap(container: Any = None) -> Any:
    """
    Bootstrap ``varco_casbin`` into a ``DIContainer``.

    Scans the package for ``@Singleton`` classes (``CasbinPolicyEngine``), binds
    the engine to both ``PolicyEngine`` and ``PolicyManagement``, and registers
    the ``CasbinSettings`` provider.  Does **not** bind ``AbstractAuthorizer`` —
    call ``enable_policy_authorizer`` for that.

    Args:
        container: An existing ``DIContainer`` to scan into.  When ``None``,
                   ``DIContainer.current()`` is used.

    Returns:
        The ``DIContainer`` after scanning, or ``None`` if providify is not
        installed (mirrors the other backends' graceful-degradation pattern).

    Edge cases:
        - Calling twice is safe — scanning is idempotent and re-registering the
          settings provider resolves to the same value.
        - ``container.ashutdown()`` must be awaited at process exit so the
          engine's ``@PreDestroy`` runs.
    """
    try:
        from providify import DIContainer
    except ImportError:  # pragma: no cover - providify is a hard dep in practice
        return None

    if container is None:
        container = DIContainer.current()

    container.scan("varco_casbin", recursive=True)
    # pydantic BaseSettings cannot be a @Singleton under providify — register a
    # clean provider (scan does not auto-register module-level @Provider funcs).
    container.provide(_provide_casbin_settings)
    return container


def enable_policy_authorizer(container: Any) -> Any:
    """
    Opt in to Casbin-driven service-layer authorization.

    Registers ``PolicyEngineAuthorizer`` as the application ``AbstractAuthorizer``
    at the default priority (``0``), shadowing the permissive ``BaseAuthorizer``
    fallback.  Every existing ``AsyncService`` then enforces Casbin policy with
    no service-code changes.

    DESIGN: an explicit function rather than a scanned @Configuration
        ✅ Truly opt-in — ``scan`` auto-activates ``@Configuration`` objects, so
           a scanned config would shadow an app's own authorizer on import.  A
           module-level ``@Provider`` registered only on request cannot.
        ✅ One obvious call site documents that authorization behaviour changed.
        ❌ One extra call versus "magic on scan".  Intentional — explicit is safer.

    Args:
        container: The ``DIContainer`` already bootstrapped via ``bootstrap``.

    Returns:
        The same container, for chaining.

    Edge cases:
        - If the application already binds an ``AbstractAuthorizer`` at priority
          ``0``, this introduces an ambiguous same-priority binding — bind your
          own authorizer at a higher priority, or do not call this.

    Example::

        container = bootstrap(DIContainer())
        enable_policy_authorizer(container)
    """
    container.provide(_provide_policy_authorizer)
    return container


__all__ = [
    "bootstrap",
    "enable_policy_authorizer",
]
