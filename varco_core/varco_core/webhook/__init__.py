"""
varco_core.webhook
====================
Outbound webhooks (Plan 031 / D4, §D-D4-home) — subscription registry,
signing, SSRF-hardened delivery, and the dispatcher. Everything portable
lives here; ``varco_sa``/``varco_beanie`` hold repositories,
``varco_fastapi.webhook`` holds only the admin mount.

⚠️ This module (and its submodules) must stay import-cheap and free of any
hard HTTP client dependency at module scope — see
``varco_core.webhook.transport`` and
``varco_core/tests/test_webhook_no_hard_client_deps.py``.
"""

from __future__ import annotations

__all__: list[str] = []
