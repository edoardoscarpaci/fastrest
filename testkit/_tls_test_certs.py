"""
Shared, in-test certificate minting helpers for Plan 026's TLS test suites.

Not a test module itself (no ``test_`` prefix — pytest does not collect it).
Mints a self-signed CA and leaf certs with ``cryptography`` (already a hard
dependency of ``varco_core``, see ``varco_core/pyproject.toml``).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@dataclass(frozen=True)
class MintedCert:
    """A minted certificate/key pair, as PEM bytes."""

    cert_pem: bytes
    key_pem: bytes


def _self_signed(
    common_name: str,
    *,
    is_ca: bool,
    issuer_key: rsa.RSAPrivateKey | None = None,
    issuer_name: x509.Name | None = None,
) -> MintedCert:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    signer_key = issuer_key or key
    issuer = issuer_name or subject

    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
    )
    if not is_ca:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(_ip())]),
            critical=False,
        )
    cert = builder.sign(signer_key, hashes.SHA256())

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return MintedCert(cert_pem=cert_pem, key_pem=key_pem)


def _ip():  # noqa: ANN202 — small internal helper, no public typing needed
    import ipaddress

    return ipaddress.ip_address("127.0.0.1")


def mint_ca(common_name: str = "varco-test-ca") -> MintedCert:
    """Mint a self-signed CA certificate + key."""
    return _self_signed(common_name, is_ca=True)


def mint_leaf(common_name: str = "leaf") -> MintedCert:
    """Mint a self-signed leaf certificate + key (not chained to any CA)."""
    return _self_signed(common_name, is_ca=False)


def write_pem(path: Path, minted: MintedCert, *, suffix: str = ".pem") -> Path:
    """Write ``minted.cert_pem`` to ``path`` (with ``suffix``) and return the path."""
    out = path if path.suffix else path.with_suffix(suffix)
    out.write_bytes(minted.cert_pem)
    return out
