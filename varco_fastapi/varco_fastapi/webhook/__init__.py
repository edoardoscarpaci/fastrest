"""
varco_fastapi.webhook
========================
Outbound webhook admin mount (Plan 031 / D4d, §D-D4-admin,
§D-D4-home) — the only FastAPI-specific piece of Plan 031; everything
portable lives in ``varco_core.webhook``.
"""

from __future__ import annotations

from varco_fastapi.webhook.mount import mount_webhook_admin
from varco_fastapi.webhook.router import build_webhook_router

__all__ = ["mount_webhook_admin", "build_webhook_router"]
