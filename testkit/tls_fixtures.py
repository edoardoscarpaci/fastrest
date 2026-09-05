"""
Plan 027 / Step 1 — session-scoped PKI fixtures for the mTLS-hardening test suites
(``test_tls_mtls.py``, ``test_tls_pkcs12.py``, ``test_tls_clients.py``,
``test_tls_clients_integration.py``).

Builds a proper two-level chain (self-signed CA -> CA-signed server leaf -> CA-signed client
leaf) with ``cryptography`` — deliberately *not* reusing ``testkit/_tls_test_certs.py``'s
self-signed-only ``mint_leaf()``, because mTLS assertions here need a server leaf whose issuer
is the CA (so a real ``CERT_REQUIRED`` handshake can succeed) and a client leaf a peer can
verify against the same CA.

Everything is written once per test session under ``tmp_path_factory`` (a dedicated
``tls-pki`` base temp dir) — these are read-only fixtures; no test is expected to mutate the
files in place (a test needing a *fresh*, mutable copy should copy them into its own
``tmp_path``).
"""

from __future__ import annotations

import datetime
import ipaddress
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


@dataclass(frozen=True)
class MintedIdentity:
    """A CA-signed leaf identity, as PEM bytes plus the parsed cryptography objects."""

    cert: x509.Certificate
    key: rsa.RSAPrivateKey
    cert_pem: bytes
    key_pem_unencrypted: bytes


@dataclass(frozen=True)
class PkiBundle:
    """
    Everything the mTLS/PKCS#12/client-adapter suites need, minted once per session.

    Attributes:
        ca_cert_path: PEM path of the self-signed CA certificate (trust anchor).
        ca_cert_pem: Raw PEM bytes of the CA certificate.
        server_cert_path / server_key_path: CA-signed server leaf, SAN localhost/127.0.0.1.
        client_cert_path: CA-signed client leaf certificate.
        client_key_unencrypted_path: Client leaf private key, unencrypted PEM.
        client_key_encrypted_path: Same private key, ``BestAvailableEncryption``-protected PEM.
        client_key_password: The password protecting ``client_key_encrypted_path``.
        client_pkcs12_path: A PKCS#12 bundle of the client identity (leaf + key + CA chain).
        client_pkcs12_password: The password protecting the PKCS#12 bundle.
        client_pkcs12_no_password_path: The same bundle, built with password=None.
    """

    ca_cert_path: Path
    ca_cert_pem: bytes
    server_cert_path: Path
    server_key_path: Path
    client_cert_path: Path
    client_key_unencrypted_path: Path
    client_key_encrypted_path: Path
    client_key_password: bytes
    client_pkcs12_path: Path
    client_pkcs12_password: bytes
    client_pkcs12_no_password_path: Path


def _mint_ca(common_name: str) -> MintedIdentity:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return MintedIdentity(
        cert=cert,
        key=key,
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        key_pem_unencrypted=key_pem,
    )


def _mint_leaf(
    common_name: str,
    *,
    ca: MintedIdentity,
    san_localhost: bool,
) -> MintedIdentity:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if san_localhost:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
    cert = builder.sign(ca.key, hashes.SHA256())
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return MintedIdentity(
        cert=cert,
        key=key,
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        key_pem_unencrypted=key_pem,
    )


@pytest.fixture(scope="session")
def pki_bundle(tmp_path_factory: pytest.TempPathFactory) -> PkiBundle:
    """
    Session-scoped PKI fixture: self-signed CA, CA-signed server leaf (SAN localhost/
    127.0.0.1), CA-signed client leaf with the client key in both unencrypted and
    ``BestAvailableEncryption`` PEM form, plus a PKCS#12 bundle of the client identity.

    Written under a dedicated ``tls-pki`` base temp dir via ``tmp_path_factory`` (Step 1).
    """
    base = tmp_path_factory.mktemp("tls-pki")

    ca = _mint_ca("varco-plan027-ca")
    server = _mint_leaf("localhost", ca=ca, san_localhost=True)
    client = _mint_leaf("varco-plan027-client", ca=ca, san_localhost=False)

    ca_cert_path = base / "ca.pem"
    ca_cert_path.write_bytes(ca.cert_pem)

    server_cert_path = base / "server.pem"
    server_cert_path.write_bytes(server.cert_pem)
    server_key_path = base / "server.key"
    server_key_path.write_bytes(server.key_pem_unencrypted)

    client_cert_path = base / "client.pem"
    client_cert_path.write_bytes(client.cert_pem)

    client_key_unencrypted_path = base / "client-unencrypted.key"
    client_key_unencrypted_path.write_bytes(client.key_pem_unencrypted)

    client_key_password = b"correct-horse-battery-staple"
    encrypted_key_pem = client.key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.BestAvailableEncryption(client_key_password),
    )
    client_key_encrypted_path = base / "client-encrypted.key"
    client_key_encrypted_path.write_bytes(encrypted_key_pem)

    client_pkcs12_password = b"pkcs12-secret"
    p12_bytes = pkcs12.serialize_key_and_certificates(
        name=b"varco-plan027-client",
        key=client.key,
        cert=client.cert,
        cas=[ca.cert],
        encryption_algorithm=serialization.BestAvailableEncryption(client_pkcs12_password),
    )
    client_pkcs12_path = base / "client.p12"
    client_pkcs12_path.write_bytes(p12_bytes)

    p12_no_password_bytes = pkcs12.serialize_key_and_certificates(
        name=b"varco-plan027-client",
        key=client.key,
        cert=client.cert,
        cas=[ca.cert],
        encryption_algorithm=serialization.NoEncryption(),
    )
    client_pkcs12_no_password_path = base / "client-no-password.p12"
    client_pkcs12_no_password_path.write_bytes(p12_no_password_bytes)

    return PkiBundle(
        ca_cert_path=ca_cert_path,
        ca_cert_pem=ca.cert_pem,
        server_cert_path=server_cert_path,
        server_key_path=server_key_path,
        client_cert_path=client_cert_path,
        client_key_unencrypted_path=client_key_unencrypted_path,
        client_key_encrypted_path=client_key_encrypted_path,
        client_key_password=client_key_password,
        client_pkcs12_path=client_pkcs12_path,
        client_pkcs12_password=client_pkcs12_password,
        client_pkcs12_no_password_path=client_pkcs12_no_password_path,
    )
