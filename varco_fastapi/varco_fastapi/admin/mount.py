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

# Tracks which FastAPI app instances already had the admin surface mounted
# — refuses a second mount rather than silently duplicating routes. Same
# pattern as varco_fastapi.tenancy.mount._MOUNTED_APPS.
_MOUNTED_APPS: set[int] = set()


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
        ValueError: ``acknowledge_bundled_admin`` is not ``True``, or this
            app was already mounted once (a router was actually included on
            a previous call — see Edge cases).

    Edge cases:
        - ``server_auth=None`` → routes mount unauthenticated and one
          WARNING is logged at mount time naming the risk.
        - Mounting with neither ``audit_repo`` nor ``dlq`` given mounts
          nothing and does **not** poison the app for a later real mount —
          the app id is only recorded when at least one router was actually
          included. This is a deliberate deviation from
          ``mount_tenant_admin()``, which has no "mount nothing" case.
        - The double-mount guard is keyed by ``id(app)`` — an app instance
          that has been garbage-collected releases its id, which a new,
          unrelated ``FastAPI`` instance could reuse (same caveat as
          ``mount_tenant_admin()``'s ``_MOUNTED_APPS``).
    """
    if not acknowledge_bundled_admin:
        raise ValueError(
            "mount_reliability_admin() requires acknowledge_bundled_admin=True. "
            "This surface can replay messages onto the bus and delete audit "
            "records — at least as privileged as the tenant control plane "
            "(Plan 007 RD-9). Pass it only after confirming a standalone "
            "deployment genuinely isn't justified."
        )

    if id(app) in _MOUNTED_APPS:
        raise ValueError(
            "mount_reliability_admin() was already called for this app — "
            "refusing to mount a second time (would duplicate routes)."
        )

    if server_auth is None:
        _logger.warning(
            "mount_reliability_admin(): server_auth=None — the reliability "
            "admin surface (%s) is mounting UNAUTHENTICATED.",
            prefix,
        )

    mounted_any = False

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
        mounted_any = True

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
        mounted_any = True

    # Only poison the app id when something was actually mounted — a
    # no-op call (neither audit_repo nor dlq given) must not refuse a
    # later, legitimate mount on the same app (see Edge cases above).
    if mounted_any:
        _MOUNTED_APPS.add(id(app))


__all__ = ["mount_reliability_admin"]
