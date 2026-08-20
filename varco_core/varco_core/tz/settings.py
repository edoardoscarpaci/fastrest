"""
varco_core.tz.settings
=========================
``TimezoneSettings`` — off by default (Plan 011 RD-1's T1 row).
"""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from varco_core.config import VarcoSettings
from varco_core.tz.zones import validate_iana_zone

__all__ = ["TimezoneSettings"]


class TimezoneSettings(VarcoSettings):
    """
    Attributes:
        enabled: Master switch. ``False`` (default) — no resolution, no
            header/query-param read, ``current_timezone()`` stays ``None``.
        default_timezone: Fallback IANA zone — the last step of the T1
            precedence chain. Validated **at startup**
            (``model_validator``): a missing tzdata database raises a
            legible error naming ``pip install tzdata`` — a startup
            failure, never a per-request one.
        query_param: The query parameter name for the explicit override.
        header: The header name for the explicit override (brief 004 §B3
            names ``X-Timezone`` exactly).
    """

    model_config = SettingsConfigDict(env_prefix="VARCO_TZ_")

    enabled: bool = False
    default_timezone: str = "UTC"
    query_param: str = "tz"
    header: str = "X-Timezone"

    @model_validator(mode="after")
    def _validate_default_timezone(self) -> "TimezoneSettings":
        if validate_iana_zone(self.default_timezone) is None:
            raise ValueError(
                f"TimezoneSettings.default_timezone={self.default_timezone!r} "
                "could not be resolved via the system/PyPI tzdata database. "
                "If this is a slim container image (python:*-slim, "
                "distroless, Alpine), install the optional extra: "
                "`pip install tzdata` (or `pip install varco-core[tz]`)."
            )
        return self
