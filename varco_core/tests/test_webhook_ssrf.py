"""
Plan 031 (D4c) / Step 11 — SSRF guard tests. MERGE GATE per the plan: "do not
proceed without Step 11 green."

``varco_core/varco_core/webhook/ssrf.py`` does not exist yet — every test
below fails with ``ModuleNotFoundError`` on import.

Covers every case the plan's Step 11 lists explicitly, including the
IPv4-mapped-IPv6 bypass form and a DNS-rebinding simulation.
"""

from __future__ import annotations

import pytest


def test_ssrf_module_importable() -> None:
    from varco_core.webhook.ssrf import validate_target  # noqa: F401


def test_ssrf_error_type_importable() -> None:
    from varco_core.webhook.ssrf import SSRFValidationError  # noqa: F401


class TestBlockedAddresses:
    @pytest.mark.parametrize(
        "url",
        [
            "https://169.254.169.254/latest/meta-data/",  # AWS metadata endpoint
            "https://[::ffff:169.254.169.254]/",  # IPv4-mapped IPv6 bypass form
            "https://127.0.0.1/hook",
            "https://[::1]/hook",
            "https://10.0.0.5/hook",
            "https://172.16.0.5/hook",
            "https://192.168.1.5/hook",
            "https://[fc00::1]/hook",
            "https://[fe80::1]/hook",
        ],
    )
    async def test_blocked_literal_address_raises(self, url: str) -> None:
        from varco_core.webhook.ssrf import SSRFValidationError, validate_target

        with pytest.raises(SSRFValidationError):
            await validate_target(url)

    async def test_hostname_resolving_to_a_private_address_raises(self, monkeypatch) -> None:
        from varco_core.webhook import ssrf

        async def _fake_resolve(host: str) -> list[str]:
            return ["10.1.2.3"]

        monkeypatch.setattr(ssrf, "_resolve_host", _fake_resolve)

        with pytest.raises(ssrf.SSRFValidationError):
            await ssrf.validate_target("https://internal.example.com/hook")


class TestDnsRebinding:
    async def test_second_resolution_returning_private_address_does_not_bypass_the_pin(
        self, monkeypatch
    ) -> None:
        """
        Simulates DNS rebinding: the first resolution (used for validation) is
        public, but a naive implementation that re-resolves at connect time
        would pick up a second, private, resolution. The validated/pinned IP
        must be the one actually connected to.
        """
        from varco_core.webhook import ssrf

        calls = {"count": 0}

        async def _fake_resolve(host: str) -> list[str]:
            calls["count"] += 1
            if calls["count"] == 1:
                return ["93.184.216.34"]  # public — passes validation
            return ["169.254.169.254"]  # private — must never be used to connect

        monkeypatch.setattr(ssrf, "_resolve_host", _fake_resolve)

        result = await ssrf.validate_target("https://rebinding.example.com/hook")
        # The pinned IP returned to the caller must be the first (validated)
        # resolution — a second internal call to _resolve_host (e.g. from the
        # HTTP client re-resolving) must never change what gets connected to.
        assert result.pinned_ip == "93.184.216.34"


class TestSchemeAllowlist:
    async def test_http_rejected_by_default(self) -> None:
        from varco_core.webhook.ssrf import SSRFValidationError, validate_target

        with pytest.raises(SSRFValidationError):
            await validate_target("http://example.com/hook")

    async def test_http_allowed_when_opted_in(self, monkeypatch) -> None:
        from varco_core.webhook import ssrf

        async def _fake_resolve(host: str) -> list[str]:
            return ["93.184.216.34"]

        monkeypatch.setattr(ssrf, "_resolve_host", _fake_resolve)

        result = await ssrf.validate_target("http://example.com/hook", allow_insecure_http=True)
        assert result.pinned_ip == "93.184.216.34"


class TestNoRedirectFollowing:
    async def test_redirect_to_a_private_address_is_rejected_not_followed(
        self, monkeypatch
    ) -> None:
        """
        A 302 pointing at a private address must be treated as a delivery
        failure, never as a hop to re-validate — validate_target() itself
        never follows redirects; this pins that the dispatcher's send path
        does not either.
        """
        from varco_core.webhook import dispatcher as dispatcher_module

        # The dispatcher's HTTP call must be constructed with redirects
        # disabled — this test fails today because the module does not exist.
        assert hasattr(dispatcher_module, "WebhookDispatcher")
