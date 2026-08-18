"""
varco_fastapi.admin.mount
============================
``mount_reliability_admin`` — the single way to expose the DLQ/audit admin
surfaces on a running app (Plan 009, Phase 10 / R6).

Mirrors Plan 007 RD-9 (the tenant control plane) verbatim: this surface can
*replay* messages onto the bus and *delete* audit records — at least as
privileged as the tenant admin — so it requires an explicit
``acknowledge_bundled_admin=True`` and there is deliberately **no**
env var that mounts it.

Thread safety:  N/A — mounting happens once at startup.
Async safety:   N/A — synchronous FastAPI route registration.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI
    from varco_core.event.dlq import AbstractDeadLetterQueue
    from varco_core.event.redrive import DlqRedriver
    from varco_core.service.audit import AuditRepository

_logger = logging.getLogger(__name__)


def mount_reliability_admin(
    app: FastAPI,
    *,
    audit_repo: AuditRepository | None = None,
    dlq: AbstractDeadLetterQueue | None = None,
    redriver: DlqRedriver | None = None,
    acknowledge_bundled_admin: bool = False,
    server_auth: Any | None = None,
    admin_role: str = "reliability-admin",
    prefix: str = "/reliability",
    dependencies: Sequence[Any] | None = None,
) -> None:
    """
    Mount the audit/DLQ admin routers under ``prefix``.

    Args:
        app:         The FastAPI app to mount onto.
        audit_repo:  Mounts ``{prefix}/audit/*`` when given.
        dlq:         Mounts ``{prefix}/dlq/*`` when given.
        redriver:    Passed through to ``build_dlq_router`` — enables the
                     redrive routes.
        acknowledge_bundled_admin: Required ``True`` or this raises
                     ``ValueError`` (RD-9) — bundling admin-adjacent
                     privilege into the app pod's own environment is a
                     deliberate, friction-gated choice, not a default.
        server_auth: Auth strategy forwarded to both routers.
        admin_role:  Documented role requirement.
        prefix:      URL prefix for the whole admin surface.
        dependencies: Extra FastAPI dependencies (e.g. an IP allowlist,
                     an mTLS check) applied to every mounted route.

    Raises:
        ValueError: ``acknowledge_bundled_admin`` is not ``True``.

    Edge cases:
        - ``server_auth=None`` → routes mount unauthenticated and one
          WARNING is logged at mount time naming the risk.
    """
    if not acknowledge_bundled_admin:
        raise ValueError(
            "mount_reliability_admin() requires acknowledge_bundled_admin=True. "
            "This surface can replay messages onto the bus and delete audit "
            "records — at least as privileged as the tenant control plane "
            "(Plan 007 RD-9). Pass it only after confirming a standalone "
            "deployment genuinely isn't justified."
        )

    if server_auth is None:
        _logger.warning(
            "mount_reliability_admin(): server_auth=None — the reliability "
            "admin surface (%s) is mounting UNAUTHENTICATED.",
            prefix,
        )

    if audit_repo is not None:
        from varco_fastapi.admin.audit_router import build_audit_router

        app.include_router(
            build_audit_router(
                audit_repo,
                server_auth=server_auth,
                admin_role=admin_role,
                prefix=f"{prefix}/audit",
            ),
            dependencies=list(dependencies) if dependencies else None,
        )

    if dlq is not None:
        from varco_fastapi.admin.dlq_router import build_dlq_router

        app.include_router(
            build_dlq_router(
                dlq,
                redriver=redriver,
                server_auth=server_auth,
                admin_role=admin_role,
                prefix=f"{prefix}/dlq",
            ),
            dependencies=list(dependencies) if dependencies else None,
        )


__all__ = ["mount_reliability_admin"]
