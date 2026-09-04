"""
Plan 026 / Step 16 — failing-first tests for the ``varco_fastapi.auth.TrustStore`` deprecation
subclass (§D-T3-oq1).

These assert against 3.0 behaviour changing to a subclass of ``varco_core.tls.TrustStore``.
Every test below fails today because either (a) ``varco_core.tls`` does not exist yet
(``ModuleNotFoundError``), or (b) the legacy ``varco_fastapi.auth.TrustStore`` is not yet a
subclass of it and does not yet warn — until Step 17 lands the rewritten
``varco_fastapi/varco_fastapi/auth/trust_store.py``.
"""

from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

import pytest


def test_constructing_legacy_trust_store_emits_exactly_one_deprecation_warning(
    tmp_path: Path,
) -> None:
    from varco_fastapi.auth.trust_store import TrustStore

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        TrustStore(ca_cert=tmp_path / "ca.pem")

    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warnings) == 1
    assert "varco_core.tls.TrustStore" in str(deprecation_warnings[0].message)


def test_importing_varco_fastapi_emits_no_deprecation_warning() -> None:
    # The DeprecationWarning is emitted at construction, not at import — merely having
    # "from varco_fastapi import TrustStore" at module top (varco_fastapi/__init__.py) must
    # not warn. Runs in a subprocess so no earlier test's imports mask a warning at import time.
    script = (
        "import warnings\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    import varco_fastapi  # noqa: F401\n"
        "    from varco_fastapi.auth.trust_store import TrustStore  # noqa: F401\n"
        "deprecation = [w for w in caught if issubclass(w.category, DeprecationWarning)]\n"
        "assert deprecation == [], deprecation\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_legacy_trust_store_is_a_subclass_of_core_trust_store(tmp_path: Path) -> None:
    from varco_core.tls.store import TrustStore as CoreTrustStore
    from varco_fastapi.auth.trust_store import TrustStore as LegacyTrustStore

    assert issubclass(LegacyTrustStore, CoreTrustStore)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        instance = LegacyTrustStore(ca_cert=tmp_path / "ca.pem")
    assert isinstance(instance, CoreTrustStore)


def test_legacy_type_pins_recursive_false_and_narrow_patterns(tmp_path: Path) -> None:
    from varco_fastapi.auth.trust_store import TrustStore as LegacyTrustStore

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        instance = LegacyTrustStore()

    assert instance.recursive is False
    assert instance.cert_patterns == ("*.pem", "*.crt")


def test_core_type_stays_recursive_true_and_wide_patterns() -> None:
    from varco_core.tls.discovery import CERT_FILE_PATTERNS
    from varco_core.tls.store import TrustStore as CoreTrustStore

    instance = CoreTrustStore()

    assert instance.recursive is True
    assert instance.cert_patterns == CERT_FILE_PATTERNS


def test_legacy_build_ssl_context_byte_equivalent_to_3_0_for_mixed_cert_folder(
    tmp_path: Path,
) -> None:
    # 3.0 behaviour frozen: ca.crt/ca.cer/sub/deep.pem must NOT widen what a legacy TrustStore
    # trusts, even though the underlying folder now contains files the new core type would
    # pick up (ca.crt) or warn about (ca.cer) or find recursively (sub/deep.pem).
    from _tls_test_certs import mint_ca, write_pem

    folder = tmp_path / "certs"
    folder.mkdir()
    ca_pem = mint_ca("ca-pem")
    ca_crt = mint_ca("ca-crt")
    ca_cer = mint_ca("ca-cer")
    deep_pem = mint_ca("ca-deep")
    write_pem(folder / "ca.pem", ca_pem)
    write_pem(folder / "ca.crt", ca_crt, suffix=".crt")
    write_pem(folder / "ca.cer", ca_cer, suffix=".cer")
    (folder / "sub").mkdir()
    write_pem(folder / "sub" / "deep.pem", deep_pem)

    from varco_fastapi.auth.trust_store import TrustStore as LegacyTrustStore

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        legacy = LegacyTrustStore(ca_folders=folder, include_system_cas=False)
    ctx = legacy.build_ssl_context()

    # 3.0 loaded *.pem + *.crt, non-recursive -> ca.pem + ca.crt only (2 certs).
    assert len(ctx.get_ca_certs()) == 2


def test_legacy_partial_mtls_raises_at_build_ssl_context_not_at_construction(
    tmp_path: Path,
) -> None:
    # Behaviour frozen: 3.0's TrustStore defers the mTLS pairing check to build_ssl_context(),
    # unlike the core type's eager __post_init__ check (Step 6).
    from varco_fastapi.auth.trust_store import TrustStore as LegacyTrustStore

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        instance = LegacyTrustStore(client_cert=tmp_path / "client.crt")  # no client_key

    with pytest.raises(ValueError, match="client_cert"):
        instance.build_ssl_context()
