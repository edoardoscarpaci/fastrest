"""
Plan 027 / Step 11 — failing-first tests for the four HTTP-client adapters in
``varco_core.tls.clients`` (§D-T4-adapters).

``varco_core.tls.clients`` does not exist yet — every test fails with
``ModuleNotFoundError`` until Phase 3 lands ``clients.py`` (Step 12) plus the delegating
``TrustStore``/``ReloadingTrustStore`` methods (Steps 13-14). The four client libraries
themselves are available in this environment via the root `clients` dependency group
(Step 9) — this file is a unit-level, introspection-based approximation; the real proof is
``test_tls_clients_integration.py``'s loopback handshake (Steps 15-16).
"""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest
from tls_fixtures import PkiBundle


@pytest.fixture
def trust_store(pki_bundle: PkiBundle):
    from varco_core.tls.store import TrustStore

    return TrustStore(ca_cert=pki_bundle.ca_cert_path, include_system_cas=False)


# ── httpx ─────────────────────────────────────────────────────────────────


def test_to_httpx_verify_returns_the_stores_context(trust_store) -> None:
    from varco_core.tls.clients import to_httpx_verify

    verify = to_httpx_verify(trust_store)

    assert isinstance(verify, ssl.SSLContext)


def test_to_httpx_verify_reads_reloading_store_context_at_call_time(
    pki_bundle: PkiBundle, tmp_path: Path
) -> None:
    from varco_core.tls.clients import to_httpx_verify
    from varco_core.tls.store import TrustStore

    spec = TrustStore(ca_cert=pki_bundle.ca_cert_path, include_system_cas=False)

    class _FakeReloadingStore:
        def __init__(self) -> None:
            self.context = spec.build_ssl_context()

    fake = _FakeReloadingStore()
    first = to_httpx_verify(fake)

    replacement_ctx = ssl.create_default_context()
    fake.context = replacement_ctx
    second = to_httpx_verify(fake)

    assert first is not second
    assert second is replacement_ctx


# ── aiohttp ───────────────────────────────────────────────────────────────


async def test_to_aiohttp_connector_carries_the_stores_context(trust_store) -> None:
    from varco_core.tls.clients import to_aiohttp_connector

    connector = await to_aiohttp_connector(trust_store)
    try:
        assert isinstance(connector._ssl, ssl.SSLContext)
    finally:
        await connector.close()


# ── urllib3 ───────────────────────────────────────────────────────────────


def test_to_urllib3_poolmanager_carries_the_stores_context(trust_store) -> None:
    from varco_core.tls.clients import to_urllib3_poolmanager

    pool_manager = to_urllib3_poolmanager(trust_store)

    assert isinstance(
        pool_manager.connection_pool_kw.get("ssl_context"),
        ssl.SSLContext,
    )


# ── requests ──────────────────────────────────────────────────────────────


def test_to_requests_adapter_carries_the_stores_context(trust_store) -> None:
    from varco_core.tls.clients import to_requests_adapter

    adapter = to_requests_adapter(trust_store)

    # The adapter subclass must expose the context it was built with somehow (attribute
    # name is an implementation detail of clients.py; we look for the first SSLContext
    # attribute found on the instance).
    found = [value for value in vars(adapter).values() if isinstance(value, ssl.SSLContext)]
    assert found, f"no ssl.SSLContext attribute found on adapter instance: {vars(adapter)!r}"


# ── missing dependency ───────────────────────────────────────────────────


def test_missing_httpx_raises_missing_client_dependency_error_naming_pip_package(
    trust_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from varco_core.tls.clients import MissingClientDependencyError, to_httpx_verify

    real_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "httpx" or name.startswith("httpx."):
            raise ImportError("simulated missing httpx")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    with pytest.raises(MissingClientDependencyError, match="httpx"):
        to_httpx_verify(trust_store)


def test_missing_aiohttp_raises_missing_client_dependency_error_naming_pip_package(
    trust_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from varco_core.tls.clients import MissingClientDependencyError, to_aiohttp_connector

    real_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "aiohttp" or name.startswith("aiohttp."):
            raise ImportError("simulated missing aiohttp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    with pytest.raises(MissingClientDependencyError, match="aiohttp"):
        import asyncio

        asyncio.run(to_aiohttp_connector(trust_store))


def test_missing_urllib3_raises_missing_client_dependency_error_naming_pip_package(
    trust_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from varco_core.tls.clients import MissingClientDependencyError, to_urllib3_poolmanager

    real_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "urllib3" or name.startswith("urllib3."):
            raise ImportError("simulated missing urllib3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    with pytest.raises(MissingClientDependencyError, match="urllib3"):
        to_urllib3_poolmanager(trust_store)


def test_missing_requests_raises_missing_client_dependency_error_naming_pip_package(
    trust_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from varco_core.tls.clients import MissingClientDependencyError, to_requests_adapter

    real_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "requests" or name.startswith("requests."):
            raise ImportError("simulated missing requests")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    with pytest.raises(MissingClientDependencyError, match="requests"):
        to_requests_adapter(trust_store)
