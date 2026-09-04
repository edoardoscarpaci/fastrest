"""
Plan 027 / Step 2 — failing-first tests for encrypted private keys on
``varco_core.tls.store.TrustStore`` (§D-T6-password).

``TrustStore`` does not yet carry a ``key_password`` field — every test here fails with
``TypeError: __init__() got an unexpected keyword argument 'key_password'`` (or, for the
handshake-shaped tests, ``ssl.SSLError``/``TypeError`` from ``load_cert_chain`` receiving no
``password=``) until Phase 0 lands.
"""

from __future__ import annotations

import ssl

import pytest
from tls_fixtures import PkiBundle


def test_encrypted_client_key_without_password_raises(pki_bundle: PkiBundle) -> None:
    # An encrypted key with no key_password must surface as a clear ssl error at
    # build_ssl_context() time, not hang on a TTY prompt and not succeed silently.
    from varco_core.tls.store import TrustStore

    store = TrustStore(
        client_cert=pki_bundle.client_cert_path,
        client_key=pki_bundle.client_key_encrypted_path,
        ca_cert=pki_bundle.ca_cert_path,
        include_system_cas=False,
    )

    with pytest.raises(ssl.SSLError):
        store.build_ssl_context()


def test_encrypted_client_key_with_str_password_loads(pki_bundle: PkiBundle) -> None:
    from varco_core.tls.store import TrustStore

    store = TrustStore(
        client_cert=pki_bundle.client_cert_path,
        client_key=pki_bundle.client_key_encrypted_path,
        key_password=pki_bundle.client_key_password.decode(),
        ca_cert=pki_bundle.ca_cert_path,
        include_system_cas=False,
    )

    ctx = store.build_ssl_context()

    assert isinstance(ctx, ssl.SSLContext)


def test_encrypted_client_key_with_bytes_password_loads(pki_bundle: PkiBundle) -> None:
    from varco_core.tls.store import TrustStore

    store = TrustStore(
        client_cert=pki_bundle.client_cert_path,
        client_key=pki_bundle.client_key_encrypted_path,
        key_password=pki_bundle.client_key_password,
        ca_cert=pki_bundle.ca_cert_path,
        include_system_cas=False,
    )

    ctx = store.build_ssl_context()

    assert isinstance(ctx, ssl.SSLContext)


def test_encrypted_client_key_with_callable_password_loads_and_is_lazy(
    pki_bundle: PkiBundle,
) -> None:
    # The callable must not be invoked at TrustStore construction time — only when
    # build_ssl_context() actually calls load_cert_chain(..., password=callable).
    from varco_core.tls.store import TrustStore

    calls: list[None] = []

    def _password() -> bytes:
        calls.append(None)
        return pki_bundle.client_key_password

    store = TrustStore(
        client_cert=pki_bundle.client_cert_path,
        client_key=pki_bundle.client_key_encrypted_path,
        key_password=_password,
        ca_cert=pki_bundle.ca_cert_path,
        include_system_cas=False,
    )

    assert calls == []  # not invoked at construction

    ctx = store.build_ssl_context()

    assert isinstance(ctx, ssl.SSLContext)
    assert calls == [None]  # invoked exactly once during build_ssl_context()


def test_repr_does_not_leak_key_password(pki_bundle: PkiBundle) -> None:
    from varco_core.tls.store import TrustStore

    secret = pki_bundle.client_key_password.decode()
    store = TrustStore(
        client_cert=pki_bundle.client_cert_path,
        client_key=pki_bundle.client_key_encrypted_path,
        key_password=secret,
    )

    rendered = repr(store)

    assert secret not in rendered
    assert "password" not in rendered.lower()


def test_key_password_without_client_key_raises_value_error() -> None:
    from varco_core.tls.store import TrustStore

    with pytest.raises(ValueError, match="key_password"):
        TrustStore(key_password="irrelevant")
