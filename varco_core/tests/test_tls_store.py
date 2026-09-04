"""
Plan 026 / Step 6 — failing-first tests for ``varco_core.tls.store.TrustStore`` (§D-T3-model).

``varco_core.tls`` does not exist yet — every test fails with ``ModuleNotFoundError`` until
Step 7 lands ``varco_core/varco_core/tls/store.py``.
"""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest
from _tls_test_certs import mint_ca, write_pem

# ── defaults ──────────────────────────────────────────────────────────────────


def test_defaults() -> None:
    from varco_core.tls.store import TrustStore

    store = TrustStore()

    assert store.ca_cert is None
    assert store.ca_folders is None
    assert store.client_cert is None
    assert store.client_key is None
    assert store.include_system_cas is True
    assert store.verify is True
    assert store.check_hostname is True


def test_recursive_defaults_to_true_on_this_type() -> None:
    # Locked "Cert search — recursive by default, on the new type only" (BACKLOG.md:30).
    from varco_core.tls.store import TrustStore

    store = TrustStore()

    assert store.recursive is True


def test_cert_patterns_defaults_to_cert_file_patterns() -> None:
    from varco_core.tls.discovery import CERT_FILE_PATTERNS
    from varco_core.tls.store import TrustStore

    store = TrustStore()

    assert store.cert_patterns == CERT_FILE_PATTERNS


# ── ca_folders normalisation ─────────────────────────────────────────────────


def test_ca_folders_accepts_single_path_and_normalises_to_tuple(tmp_path: Path) -> None:
    from varco_core.tls.store import TrustStore

    store = TrustStore(ca_folders=tmp_path)

    assert store.ca_folders == (tmp_path,)


def test_ca_folders_accepts_sequence_and_normalises_to_tuple(tmp_path: Path) -> None:
    from varco_core.tls.store import TrustStore

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    store = TrustStore(ca_folders=[a, b])

    assert store.ca_folders == (a, b)


def test_ca_folders_empty_sequence_treated_as_none() -> None:
    from varco_core.tls.store import TrustStore

    store = TrustStore(ca_folders=[])

    assert store.ca_folders is None


# ── __post_init__ validation ──────────────────────────────────────────────────


def test_check_hostname_true_with_verify_false_raises() -> None:
    from varco_core.tls.store import TrustStore

    with pytest.raises(ValueError, match="check_hostname"):
        TrustStore(verify=False, check_hostname=True)


def test_exactly_one_of_client_cert_client_key_raises(tmp_path: Path) -> None:
    from varco_core.tls.store import TrustStore

    with pytest.raises(ValueError, match="client_cert"):
        TrustStore(client_cert=tmp_path / "client.crt")

    with pytest.raises(ValueError, match="client_key"):
        TrustStore(client_key=tmp_path / "client.key")


def test_validation_is_eager_at_construction_not_at_build_ssl_context(tmp_path: Path) -> None:
    # Stricter than 3.0's TrustStore, which defers to build_ssl_context() — explicitly
    # required by §D-T3-model so the legacy shim (Step 16/17) does NOT inherit this behaviour.
    from varco_core.tls.store import TrustStore

    with pytest.raises(ValueError):
        TrustStore(client_cert=tmp_path / "client.crt")


# ── ca_cert bytes loading ─────────────────────────────────────────────────────


def test_ca_cert_bytes_loads_via_cadata() -> None:
    from varco_core.tls.store import TrustStore

    ca = mint_ca()
    store = TrustStore(ca_cert=ca.cert_pem, include_system_cas=False)

    ctx = store.build_ssl_context()

    loaded = ctx.get_ca_certs()
    assert len(loaded) == 1


# ── include_system_cas / verify combinations (§D-T3-model build_ssl_context ordering) ──


def test_include_system_cas_false_produces_cert_required_and_check_hostname_true() -> None:
    from varco_core.tls.store import TrustStore

    store = TrustStore(include_system_cas=False)

    ctx = store.build_ssl_context()

    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_verify_false_produces_cert_none_and_check_hostname_false() -> None:
    from varco_core.tls.store import TrustStore

    store = TrustStore(verify=False, check_hostname=False)

    ctx = store.build_ssl_context()

    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_verify_false_wins_over_include_system_cas_true() -> None:
    # Edge case from the plan: verify=False + include_system_cas=True -> verify=False wins.
    from varco_core.tls.store import TrustStore

    store = TrustStore(verify=False, check_hostname=False, include_system_cas=True)

    ctx = store.build_ssl_context()

    assert ctx.verify_mode == ssl.CERT_NONE


# ── differential no-regression test — the phase's proof ─────────────────────


def _legacy_fastapi_trust_store_config(ca_path: Path):
    from varco_fastapi.auth.trust_store import TrustStore as LegacyTrustStore

    return LegacyTrustStore(ca_cert=ca_path)


def _legacy_ssl_config(ca_path: Path):
    from varco_core.connection.ssl import SSLConfig

    return SSLConfig(ca_cert=ca_path)


@pytest.mark.parametrize("legacy_factory", [_legacy_fastapi_trust_store_config, _legacy_ssl_config])
def test_differential_matches_legacy_models_for_single_ca_cert(
    tmp_path: Path, legacy_factory
) -> None:
    # The no-regression proof for the whole phase (Step 6): any config expressible in both
    # old models must build byte-equivalent contexts (get_ca_certs / verify_mode /
    # check_hostname) via the new unified TrustStore.
    from varco_core.tls.store import TrustStore

    ca = mint_ca()
    ca_path = write_pem(tmp_path / "ca.pem", ca)

    legacy = legacy_factory(ca_path)
    legacy_ctx = legacy.build_ssl_context()

    new_store = TrustStore(ca_cert=ca_path)
    new_ctx = new_store.build_ssl_context()

    assert _ca_cert_fingerprints(new_ctx) == _ca_cert_fingerprints(legacy_ctx)
    assert new_ctx.verify_mode == legacy_ctx.verify_mode
    assert new_ctx.check_hostname == legacy_ctx.check_hostname


def test_differential_matches_legacy_ssl_config_verify_false(tmp_path: Path) -> None:
    from varco_core.connection.ssl import SSLConfig
    from varco_core.tls.store import TrustStore

    legacy = SSLConfig(verify=False, check_hostname=False)
    legacy_ctx = legacy.build_ssl_context()

    new_store = TrustStore(verify=False, check_hostname=False)
    new_ctx = new_store.build_ssl_context()

    assert new_ctx.verify_mode == legacy_ctx.verify_mode
    assert new_ctx.check_hostname == legacy_ctx.check_hostname


def test_differential_matches_legacy_fastapi_trust_store_include_system_cas_false(
    tmp_path: Path,
) -> None:
    from varco_core.tls.store import TrustStore
    from varco_fastapi.auth.trust_store import TrustStore as LegacyTrustStore

    ca = mint_ca()
    ca_path = write_pem(tmp_path / "ca.pem", ca)

    legacy = LegacyTrustStore(ca_cert=ca_path, include_system_cas=False)
    legacy_ctx = legacy.build_ssl_context()

    new_store = TrustStore(ca_cert=ca_path, include_system_cas=False)
    new_ctx = new_store.build_ssl_context()

    assert _ca_cert_fingerprints(new_ctx) == _ca_cert_fingerprints(legacy_ctx)
    assert new_ctx.verify_mode == legacy_ctx.verify_mode
    assert new_ctx.check_hostname == legacy_ctx.check_hostname


def _ca_cert_fingerprints(ctx: ssl.SSLContext) -> set[tuple]:
    return {tuple(sorted(cert.items())) for cert in ctx.get_ca_certs()}
