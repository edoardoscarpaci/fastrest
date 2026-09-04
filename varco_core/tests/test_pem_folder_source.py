"""
Plan 026 / Step 5 — ``PemFolderSource`` cert-glob unification tests.

Note: no ``test_pem_folder_source.py`` existed prior to this plan (the plan's Step 5 says
"extend"; this file is created fresh here and covers exactly the Step 5 scope — the widened,
opt-in ``patterns=`` behaviour — not general PemFolderSource coverage).

``PemFolderSource.__init__`` does not accept a ``patterns=`` keyword yet, so every test that
passes it fails with ``TypeError: __init__() got an unexpected keyword argument 'patterns'``
until Step 4 lands.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from varco_core.authority.sources.pem_folder import PemFolderSource


async def test_crt_and_cer_files_are_ignored_by_default(tmp_path: Path) -> None:
    # Widening PemFolderSource is dangerous — this folder holds JWT signing keys, not certs
    # (§D-T7). Default behaviour (only *.pem) must be unchanged.
    (tmp_path / "kid-a.pem").write_bytes(_RSA_PEM)
    (tmp_path / "kid-b.crt").write_text("not a key")
    (tmp_path / "kid-c.cer").write_text("not a key")

    source = PemFolderSource(tmp_path, algorithm="RS256")

    keyset = await source.load()

    assert [k.kid for k in keyset.keys] == ["kid-a"]


async def test_crt_and_cer_files_produce_a_warning_by_default(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "kid-a.pem").write_bytes(_RSA_PEM)
    (tmp_path / "kid-b.crt").write_text("not a key")

    source = PemFolderSource(tmp_path, algorithm="RS256")

    with caplog.at_level(logging.WARNING):
        await source.load()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("kid-b.crt" in r.getMessage() for r in warnings)


async def test_patterns_kwarg_opts_into_wider_cert_file_patterns(tmp_path: Path) -> None:
    # Explicit opt-in with CERT_FILE_PATTERNS widens what this folder scans — but this folder
    # holds JWT keys, so a *.crt entry must still fail key-loading, not be silently skipped.
    from varco_core.authority.exceptions import KeyLoadError
    from varco_core.tls.discovery import CERT_FILE_PATTERNS

    (tmp_path / "kid-a.pem").write_bytes(_RSA_PEM)
    (tmp_path / "kid-b.crt").write_text("not a valid key")

    source = PemFolderSource(tmp_path, algorithm="RS256", patterns=CERT_FILE_PATTERNS)

    with pytest.raises(KeyLoadError):
        await source.load()


def test_has_changes_and_scan_use_the_same_patterns(tmp_path: Path) -> None:
    # _has_changes and _scan must agree on enumeration or refresh() can silently miss a
    # newly-added file that _scan would have picked up (§D-T7 Step 4 comment).
    from varco_core.tls.discovery import CERT_FILE_PATTERNS

    source = PemFolderSource(tmp_path, algorithm="RS256", patterns=CERT_FILE_PATTERNS)

    assert source._patterns == CERT_FILE_PATTERNS  # noqa: SLF001 — internal invariant under test


def _make_rsa_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


_RSA_PEM = _make_rsa_pem()
