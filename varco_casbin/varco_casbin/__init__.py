"""
varco_casbin
============
Casbin policy-engine authorization backend for varco — ACL / RBAC / ABAC with
dynamic, persisted policies and a ready-made REST management router.

Layer map::

    varco_core.auth.PolicyEngine / PolicyManagement   (backend-agnostic seam)
        ↑ implemented by
    varco_casbin.CasbinPolicyEngine                   ← THIS PACKAGE
        ↑ configured by
    varco_casbin.CasbinSettings
        ↑ discovered by
    varco_casbin.di.bootstrap(container)

    varco_core.auth.AbstractAuthorizer
        ↑ bridged (opt-in) by
    varco_core.auth.PolicyEngineAuthorizer            via di.enable_policy_authorizer

    varco_casbin.CasbinPolicyRouter  (requires the [fastapi] extra)
        → REST administration of policies / role assignments

Usage (standalone)::

    from varco_casbin import CasbinPolicyEngine, CasbinSettings
    from varco_core.auth import EnforcementRequest

    async with CasbinPolicyEngine(CasbinSettings(model_preset="rbac")) as engine:
        await engine.add_role_for_user("alice", "admin")
        await engine.add_policy("admin", "*", "*")
        allowed = await engine.enforce(
            EnforcementRequest(subject="alice", object="posts", action="read")
        )

Usage (Providify DI)::

    from providify import DIContainer
    from varco_casbin.di import bootstrap, enable_policy_authorizer
    from varco_core.auth import PolicyEngine

    container = bootstrap(DIContainer())
    enable_policy_authorizer(container)        # opt-in service-layer authorization
    engine = await container.aget(PolicyEngine)
"""

from __future__ import annotations

from varco_casbin.adapter import build_adapter
from varco_casbin.config import CasbinSettings
from varco_casbin.engine import CasbinPolicyEngine

__all__ = [
    # ── Engine + configuration ─────────────────────────────────────────────────
    "CasbinPolicyEngine",
    "CasbinSettings",
    # ── Persistence ────────────────────────────────────────────────────────────
    "build_adapter",
]

# The REST router lives behind the optional [fastapi] extra.  Export it lazily
# so importing varco_casbin does not require varco_fastapi / fastapi installed.
try:  # pragma: no cover - import-guard branch
    from varco_casbin.router import build_policy_router  # noqa: F401

    __all__.append("build_policy_router")
except ImportError:
    # varco_fastapi / fastapi not installed — the management router is
    # simply unavailable; the engine and DI still work.
    pass

# The Beanie adapter lives behind the optional [beanie] extra.  Export lazily
# so importing varco_casbin does not require beanie / motor installed.
try:  # pragma: no cover - import-guard branch
    from varco_casbin.beanie_adapter import (
        BeanieAdapter,
        CasbinRuleDocument,
    )  # noqa: F401

    __all__ += ["BeanieAdapter", "CasbinRuleDocument"]
except ImportError:
    # beanie / motor not installed — the Beanie adapter is unavailable;
    # the engine, DI, and other adapters still work normally.
    pass
