"""
Plan 027 / Steps 15-16 — integration tests for the four HTTP-client adapters against a real
loopback TLS server, plus the `ReloadingTrustStore` MUTATE rotation end-to-end proof.

No Docker is needed for anything in this module — every server here is a plain stdlib
``ssl``-wrapped loopback TCP/HTTP server bound to ``127.0.0.1`` on an ephemeral port.

``varco_core.tls.clients`` does not exist yet — every test fails with
``ModuleNotFoundError`` until Phase 3 (clients.py + delegating methods) lands. The rotation
test additionally needs Plan 026's ``ReloadingTrustStore`` MUTATE branch, which already
exists — only the httpx-adapter half of that test is new/red here.
"""

from __future__ import annotations

import http.server
import shutil
import ssl
import threading
from pathlib import Path

import pytest
from tls_fixtures import PkiBundle

pytestmark = pytest.mark.integration


class _OkHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:  # silence stderr noise
        pass

    def do_GET(self) -> None:  # noqa: N802 — stdlib API name
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_tls_server(
    server_ctx: ssl.SSLContext,
) -> tuple[http.server.HTTPServer, int, threading.Thread]:
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _OkHandler)
    httpd.socket = server_ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port, thread


@pytest.fixture
def tls_server(pki_bundle: PkiBundle):
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(
        certfile=str(pki_bundle.server_cert_path), keyfile=str(pki_bundle.server_key_path)
    )
    httpd, port, thread = _start_tls_server(server_ctx)
    try:
        yield port
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def trust_store(pki_bundle: PkiBundle):
    from varco_core.tls.store import TrustStore

    return TrustStore(ca_cert=pki_bundle.ca_cert_path, include_system_cas=False)


def test_httpx_fetches_successfully_via_adapter(tls_server: int, trust_store) -> None:
    import httpx
    from varco_core.tls.clients import to_httpx_verify

    with httpx.Client(verify=to_httpx_verify(trust_store)) as client:
        resp = client.get(f"https://localhost:{tls_server}/")
        assert resp.status_code == 200


def test_httpx_fails_without_the_adapters_trust(tls_server: int) -> None:
    import httpx

    with httpx.Client(verify=True) as client, pytest.raises(httpx.TransportError):
        client.get(f"https://localhost:{tls_server}/", timeout=5)


async def test_aiohttp_fetches_successfully_via_adapter(tls_server: int, trust_store) -> None:
    import aiohttp
    from varco_core.tls.clients import to_aiohttp_connector

    connector = await to_aiohttp_connector(trust_store)
    async with (
        aiohttp.ClientSession(connector=connector) as session,
        session.get(f"https://localhost:{tls_server}/") as resp,
    ):
        assert resp.status == 200


async def test_aiohttp_fails_without_the_adapters_trust(tls_server: int) -> None:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.ClientError):
            async with session.get(
                f"https://localhost:{tls_server}/", timeout=aiohttp.ClientTimeout(total=5)
            ):
                pass


def test_urllib3_fetches_successfully_via_adapter(tls_server: int, trust_store) -> None:
    from varco_core.tls.clients import to_urllib3_poolmanager

    pool_manager = to_urllib3_poolmanager(trust_store)
    resp = pool_manager.request("GET", f"https://localhost:{tls_server}/")
    assert resp.status == 200


def test_urllib3_fails_without_the_adapters_trust() -> None:
    import urllib3

    pool_manager = urllib3.PoolManager()
    with pytest.raises(urllib3.exceptions.HTTPError):
        pool_manager.request("GET", "https://localhost:1/", timeout=1, retries=False)


def test_requests_fetches_successfully_via_adapter(tls_server: int, trust_store) -> None:
    import requests
    from varco_core.tls.clients import to_requests_adapter

    session = requests.Session()
    adapter = to_requests_adapter(trust_store)
    session.mount("https://", adapter)
    resp = session.get(f"https://localhost:{tls_server}/")
    assert resp.status_code == 200


def test_requests_fails_without_the_adapters_trust(tls_server: int) -> None:
    import requests

    with pytest.raises(requests.exceptions.SSLError):
        requests.get(f"https://localhost:{tls_server}/", timeout=5)


# ── ReloadingTrustStore MUTATE rotation, end-to-end through the httpx adapter ─────────


async def test_reloading_trust_store_mutate_rotation_reaches_httpx_without_rebuild(
    pki_bundle: PkiBundle, tmp_path: Path
) -> None:
    """
    Add a new server's CA to the watched folder (MUTATE branch) and prove a *second* request,
    through the SAME httpx client object (no rebuild), against a server using that new CA
    succeeds — the end-to-end proof of the whole cycle's premise.
    """
    import httpx
    from varco_core.tls.clients import to_httpx_verify
    from varco_core.tls.reload import ReloadingTrustStore
    from varco_core.tls.store import TrustStore
    from varco_core.watch import WatchTarget, default_watcher

    ca_folder = tmp_path / "ca_folder"
    ca_folder.mkdir()
    shutil.copy(pki_bundle.ca_cert_path, ca_folder / "ca.pem")

    spec = TrustStore(ca_folders=ca_folder, include_system_cas=False)
    # A fast, explicit watcher -- same pattern as every Plan 026 reload test
    # (test_tls_reloading_store.py), and required here: ReloadingTrustStore's default
    # StatPollWatcher interval is 5.0s, which would otherwise race the wait budget below.
    watcher = default_watcher(
        [WatchTarget(root=ca_folder, patterns=spec.cert_patterns, recursive=spec.recursive)],
        interval=0.05,
    )
    reloading = ReloadingTrustStore(spec, watcher=watcher)
    await reloading.start()
    try:
        client = httpx.Client(verify=to_httpx_verify(reloading))

        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(
            certfile=str(pki_bundle.server_cert_path), keyfile=str(pki_bundle.server_key_path)
        )
        httpd, port, thread = _start_tls_server(server_ctx)
        try:
            resp = client.get(f"https://localhost:{port}/")
            assert resp.status_code == 200
        finally:
            httpd.shutdown()
            thread.join(timeout=5)

        # Mint and add a second CA + server leaf, rotate the watched folder, and prove the
        # SAME client picks up the new trust with no rebuild.
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        ca2_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca2_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "varco-plan027-ca-2")])
        now = datetime.datetime.now(datetime.UTC)
        ca2_cert = (
            x509.CertificateBuilder()
            .subject_name(ca2_name)
            .issuer_name(ca2_name)
            .public_key(ca2_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(ca2_key, hashes.SHA256())
        )
        server2_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server2_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        import ipaddress

        server2_cert = (
            x509.CertificateBuilder()
            .subject_name(server2_name)
            .issuer_name(ca2_name)
            .public_key(server2_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
            .sign(ca2_key, hashes.SHA256())
        )

        (ca_folder / "ca2.pem").write_bytes(ca2_cert.public_bytes(serialization.Encoding.PEM))

        # Give the watcher a moment to settle the ADDED batch and mutate the live context.
        import asyncio

        for _ in range(50):
            await asyncio.sleep(0.1)
            if reloading.context.get_ca_certs() and len(reloading.context.get_ca_certs()) >= 2:
                break
        else:
            pytest.fail(
                "ReloadingTrustStore did not observe the added CA within the wait budget "
                f"(fast watcher interval=0.05s); ca_certs={reloading.context.get_ca_certs()!r}"
            )

        server2_key_pem = server2_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        server2_cert_path = tmp_path / "server2.pem"
        server2_key_path = tmp_path / "server2.key"
        server2_cert_path.write_bytes(server2_cert.public_bytes(serialization.Encoding.PEM))
        server2_key_path.write_bytes(server2_key_pem)
        server2_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server2_ctx.load_cert_chain(certfile=str(server2_cert_path), keyfile=str(server2_key_path))

        httpd2, port2, thread2 = _start_tls_server(server2_ctx)
        try:
            resp2 = client.get(f"https://localhost:{port2}/")
            assert resp2.status_code == 200
        finally:
            httpd2.shutdown()
            thread2.join(timeout=5)

        client.close()
    finally:
        await reloading.stop()
