"""
varco_core.auth
==================
Authorization primitives, default permissive authorizer, and the pluggable
policy-engine seam (ACL / RBAC / ABAC via ``varco_casbin``, ``varco_opa``).
"""

from varco_core.auth.base import (
    AbstractAuthorizer,
    Action,
    AuthContext,
    Resource,
    ResourceGrant,
)
from varco_core.auth.authorizer import BaseAuthorizer
from varco_core.auth.policy import (
    EnforcementRequest,
    PolicyEngine,
    PolicyEngineAuthorizer,
    PolicyManagement,
    RequestMapper,
    attributes_of,
    attributes_of_context,
)

__all__ = [
    # ── Static, token-derived primitives ───────────────────────────────────────
    "AbstractAuthorizer",
    "Action",
    "AuthContext",
    "Resource",
    "ResourceGrant",
    "BaseAuthorizer",
    # ── Dynamic policy-engine seam ─────────────────────────────────────────────
    "EnforcementRequest",
    "PolicyEngine",
    "PolicyManagement",
    "RequestMapper",
    "PolicyEngineAuthorizer",
    "attributes_of",
    "attributes_of_context",
]
