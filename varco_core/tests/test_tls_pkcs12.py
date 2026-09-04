"""
Plan 027 / Steps 5 + 8 — failing-first tests for PKCS#12 ingestion on
``varco_core.tls.store.TrustStore`` (§D-T6-pkcs12).

``varco_core.tls.pkcs12`` does not exist yet, and ``TrustStore`` does not yet carry
``pkcs12_file``/``pkcs12_password``/``pkcs12_trust_ca`` — every unit test here fails with
``ModuleNotFoundError``/``TypeError`` until Phase 1 lands. The integration test at the bottom
(Step 8) additionally needs either Phase 3's ``to_httpx_verify()`` or a raw ``ssl`` socket —
this file uses the raw-socket form per the plan's own fallback instruction, so it does not
depend on Phase 3's landing order.
"""

from __future__ import annotations

import socket
import ssl
import threading

import pytest
from tls_fixtures import PkiBundle

# ── unit: loading a PKCS#12 bundle ───────────────────────────────────────────


def test_pkcs12_bundle_produces_a_working_client_context(pki_bundle: PkiBundle) -> None:
    from varco_core.tls.store import TrustStore

    store = TrustStore(
        pkcs12_file=pki_bundle.client_pkcs12_path,
        pkcs12_password=pki_bundle.client_pkcs12_password,
        ca_cert=pki_bundle.ca_cert_path,
        include_system_cas=False,
    )

    ctx = store.build_ssl_context()

    assert isinstance(ctx, ssl.SSLContext)


def test_pkcs12_bundle_matches_standalone_pem_path_ca_certs(pki_bundle: PkiBundle) -> None:
    # Both paths should leave the context trusting exactly the same CA set (the pkcs12 path
    # only affects the client identity, not the trust anchors here since pkcs12_trust_ca
    # defaults to False and ca_cert is supplied identically in both stores).
    from varco_core.tls.store import TrustStore

    pem_store = TrustStore(
        client_cert=pki_bundle.client_cert_path,
        client_key=pki_bundle.client_key_unencrypted_path,
        ca_cert=pki_bundle.ca_cert_path,
        include_system_cas=False,
    )
    p12_store = TrustStore(
        pkcs12_file=pki_bundle.client_pkcs12_path,
        pkcs12_password=pki_bundle.client_pkcs12_password,
        ca_cert=pki_bundle.ca_cert_path,
        include_system_cas=False,
    )

    pem_ctx = pem_store.build_ssl_context()
    p12_ctx = p12_store.build_ssl_context()

    assert pem_ctx.get_ca_certs() == p12_ctx.get_ca_certs()


def test_pkcs12_wrong_password_raises_pkcs12_load_error(pki_bundle: PkiBundle) -> None:
    from varco_core.tls.pkcs12 import Pkcs12LoadError
    from varco_core.tls.store import TrustStore

    store = TrustStore(
        pkcs12_file=pki_bundle.client_pkcs12_path,
        pkcs12_password=b"definitely-not-the-password",
        include_system_cas=False,
    )

    with pytest.raises(Pkcs12LoadError):
        store.build_ssl_context()


def test_pkcs12_file_with_client_cert_is_mutually_exclusive(pki_bundle: PkiBundle) -> None:
    from varco_core.tls.store import TrustStore

    with pytest.raises(ValueError, match="pkcs12_file"):
        TrustStore(
            pkcs12_file=pki_bundle.client_pkcs12_path,
            client_cert=pki_bundle.client_cert_path,
            client_key=pki_bundle.client_key_unencrypted_path,
        )


def test_pkcs12_empty_password_and_none_password_both_accepted(pki_bundle: PkiBundle) -> None:
    from varco_core.tls.store import TrustStore

    # Loaded with password=None (matches how the bundle was serialised: NoEncryption()).
    store_none = TrustStore(
        pkcs12_file=pki_bundle.client_pkcs12_no_password_path,
        pkcs12_password=None,
        include_system_cas=False,
    )
    ctx_none = store_none.build_ssl_context()
    assert isinstance(ctx_none, ssl.SSLContext)

    # Loaded with password=b"" — cryptography distinguishes b"" from None; both must work
    # against a bundle serialised with no encryption.
    store_empty = TrustStore(
        pkcs12_file=pki_bundle.client_pkcs12_no_password_path,
        pkcs12_password=b"",
        include_system_cas=False,
    )
    ctx_empty = store_empty.build_ssl_context()
    assert isinstance(ctx_empty, ssl.SSLContext)


def test_pkcs12_trust_ca_true_adds_bundle_ca_to_trust(pki_bundle: PkiBundle) -> None:
    from varco_core.tls.store import TrustStore

    store = TrustStore(
        pkcs12_file=pki_bundle.client_pkcs12_path,
        pkcs12_password=pki_bundle.client_pkcs12_password,
        pkcs12_trust_ca=True,
        include_system_cas=False,
    )

    ctx = store.build_ssl_context()

    assert len(ctx.get_ca_certs()) >= 1


def test_pkcs12_trust_ca_default_false_does_not_add_bundle_ca(pki_bundle: PkiBundle) -> None:
    from varco_core.tls.store import TrustStore

    store = TrustStore(
        pkcs12_file=pki_bundle.client_pkcs12_path,
        pkcs12_password=pki_bundle.client_pkcs12_password,
        include_system_cas=False,
        # No ca_cert, no pkcs12_trust_ca -> nothing trusted at all.
    )

    ctx = store.build_ssl_context()

    assert ctx.get_ca_certs() == []


# ── unit: temp material never survives the call, success or failure ─────────


def test_pkcs12_temp_material_absent_after_successful_build(
    pki_bundle: PkiBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    from varco_core.tls import pkcs12 as pkcs12_module
    from varco_core.tls.store import TrustStore

    captured_dirs: list[Path] = []
    original = pkcs12_module.materialize_chain

    def _spy(*args: object, **kwargs: object):
        cm = original(*args, **kwargs)  # type: ignore[arg-type]
        return _CapturingContextManager(cm, captured_dirs)

    monkeypatch.setattr(pkcs12_module, "materialize_chain", _spy)

    store = TrustStore(
        pkcs12_file=pki_bundle.client_pkcs12_path,
        pkcs12_password=pki_bundle.client_pkcs12_password,
        include_system_cas=False,
    )
    store.build_ssl_context()

    assert captured_dirs, "materialize_chain() was never invoked"
    for d in captured_dirs:
        assert not d.exists(), f"temp dir {d} still exists after a successful build"


def test_pkcs12_temp_material_absent_after_failed_build(
    pki_bundle: PkiBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    from varco_core.tls import pkcs12 as pkcs12_module
    from varco_core.tls.pkcs12 import Pkcs12LoadError
    from varco_core.tls.store import TrustStore

    captured_dirs: list[Path] = []
    original = pkcs12_module.materialize_chain

    def _spy(*args: object, **kwargs: object):
        cm = original(*args, **kwargs)  # type: ignore[arg-type]
        return _CapturingContextManager(cm, captured_dirs)

    monkeypatch.setattr(pkcs12_module, "materialize_chain", _spy)

    store = TrustStore(
        pkcs12_file=pki_bundle.client_pkcs12_path,
        pkcs12_password=b"wrong-password",
        include_system_cas=False,
    )
    with pytest.raises(Pkcs12LoadError):
        store.build_ssl_context()

    assert captured_dirs, "materialize_chain() was never invoked"
    for d in captured_dirs:
        assert not d.exists(), f"temp dir {d} still exists after a failed build"


class _CapturingContextManager:
    """Wraps a context manager, recording the directory of the yielded chain-file path."""

    def __init__(self, inner: object, sink: list) -> None:
        self._inner = inner
        self._sink = sink

    def __enter__(self):
        value = self._inner.__enter__()  # type: ignore[attr-defined]
        path = value.chain_path if hasattr(value, "chain_path") else value
        self._sink.append(path.parent)
        return value

    def __exit__(self, *exc_info: object) -> object:
        return self._inner.__exit__(*exc_info)  # type: ignore[attr-defined]


# ── integration: real loopback mutual-TLS handshake via a raw ssl socket ────

pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
def test_pkcs12_identity_completes_a_real_loopback_mtls_handshake(pki_bundle: PkiBundle) -> None:
    """
    No Docker needed: binds a loopback TCP port and performs a real mutual-TLS handshake
    using stdlib ``ssl`` sockets — the server requires and verifies a client certificate
    built from the PKCS#12 bundle via ``TrustStore.build_ssl_context()``.
    """
    from varco_core.tls.store import TrustStore

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(
        certfile=str(pki_bundle.server_cert_path), keyfile=str(pki_bundle.server_key_path)
    )
    server_ctx.load_verify_locations(cafile=str(pki_bundle.ca_cert_path))
    server_ctx.verify_mode = ssl.CERT_REQUIRED

    client_store = TrustStore(
        pkcs12_file=pki_bundle.client_pkcs12_path,
        pkcs12_password=pki_bundle.client_pkcs12_password,
        ca_cert=pki_bundle.ca_cert_path,
        include_system_cas=False,
    )
    client_ctx = client_store.build_ssl_context()

    raw_server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_server_sock.bind(("127.0.0.1", 0))
    raw_server_sock.listen(1)
    port = raw_server_sock.getsockname()[1]

    observed_subject: list[object] = []
    server_error: list[BaseException] = []

    def _serve_one() -> None:
        try:
            with server_ctx.wrap_socket(raw_server_sock, server_side=True) as tls_sock:
                conn, _addr = tls_sock.accept()
                with conn:
                    observed_subject.append(conn.getpeercert())
        except BaseException as exc:  # noqa: BLE001 — surfaced to the test thread below
            server_error.append(exc)

    thread = threading.Thread(target=_serve_one, daemon=True)
    thread.start()

    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw_client_sock:
        with client_ctx.wrap_socket(raw_client_sock, server_hostname="localhost"):
            pass

    thread.join(timeout=5)

    assert not server_error, f"server side raised: {server_error}"
    assert observed_subject, "server never observed a client certificate"
