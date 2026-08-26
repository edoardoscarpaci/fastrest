"""
Red-mode tests for Plan 011 Phase 3, step 39 — RD-1's T1 proof.

Plan line (step 39): "TimezoneSettings() -> enabled is False;
current_timezone() is None; no header or query param is read; to_user_tz()
is the identity with no ambient zone; every existing
datetime.now(timezone.utc) call site is untouched."
"""

from __future__ import annotations

from datetime import UTC, datetime

from varco_core.context.request import current_timezone
from varco_core.tz.settings import TimezoneSettings


def test_timezone_settings_default_is_disabled() -> None:
    settings = TimezoneSettings()
    assert settings.enabled is False
    assert settings.default_timezone == "UTC"


def test_current_timezone_is_none_with_no_active_scope() -> None:
    assert current_timezone() is None


async def test_to_user_tz_is_identity_with_no_ambient_zone() -> None:
    from varco_core.tz.resolve import to_user_tz

    instant = datetime.now(UTC)
    assert to_user_tz(instant) == instant
