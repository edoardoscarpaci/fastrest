"""
varco_fastapi.admin
=====================
The reliability admin surface (Plan 009, Phase 10 / R6) — REST browse/query/
redrive/prune over a DLQ and an audit log.

Public API::

    from varco_fastapi.admin import build_audit_router, build_dlq_router, mount_reliability_admin
"""

from __future__ import annotations

from varco_fastapi.admin.audit_router import build_audit_router
from varco_fastapi.admin.dlq_router import build_dlq_router
from varco_fastapi.admin.mount import mount_reliability_admin

__all__ = ["build_audit_router", "build_dlq_router", "mount_reliability_admin"]
