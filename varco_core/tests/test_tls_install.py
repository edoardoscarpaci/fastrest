"""
Plan 027 / Step 17 — failing-first tests for ``varco_core.tls.install.install_process_trust``
and ``RestoreHandle`` (§D-T4-install).

``varco_core.tls.install`` does not exist yet — every test fails with
``ModuleNotFoundError`` until Phase 4 lands ``install.py`` (Step 18).
"""

from __future__ import annotations

import ssl

import pytest
from tls_fixtures import PkiBundle


@pytest.fixture
def trust_store(pki_bundle: PkiBundle):
    from varco_core.tls.store import TrustStore

    return TrustStore(ca_cert=pki_bundle.ca_cert_path, include_system_cas=False)


def test_without_acknowledgement_raises_value_error_and_does_not_mutate(trust_store) -> None:
    from varco_core.tls.install import install_process_trust

    original_hook = ssl._create_default_https_context  # noqa: SLF001 — asserting non-mutation

    with pytest.raises(ValueError, match="acknowledge_global_mutation"):
        install_process_trust(trust_store)

    assert ssl._create_default_https_context is original_hook  # noqa: SLF001


def test_with_acknowledgement_new_contexts_see_the_stores_context(trust_store) -> None:
    from varco_core.tls.install import install_process_trust

    handle = install_process_trust(trust_store, acknowledge_global_mutation=True)
    try:
        ctx = ssl._create_default_https_context()  # noqa: SLF001 — exercising the patched hook
        assert isinstance(ctx, ssl.SSLContext)
    finally:
        handle.restore()


def test_restore_handle_restore_puts_back_the_exact_original_hook(trust_store) -> None:
    from varco_core.tls.install import install_process_trust

    original_hook = ssl._create_default_https_context  # noqa: SLF001

    handle = install_process_trust(trust_store, acknowledge_global_mutation=True)
    assert ssl._create_default_https_context is not original_hook  # noqa: SLF001

    handle.restore()

    assert ssl._create_default_https_context is original_hook  # noqa: SLF001


def test_restore_handle_usable_as_a_context_manager(trust_store) -> None:
    from varco_core.tls.install import install_process_trust

    original_hook = ssl._create_default_https_context  # noqa: SLF001

    with install_process_trust(trust_store, acknowledge_global_mutation=True) as handle:
        assert ssl._create_default_https_context is not original_hook  # noqa: SLF001
        assert handle is not None

    assert ssl._create_default_https_context is original_hook  # noqa: SLF001


def test_two_restore_handles_nest_correctly(trust_store) -> None:
    from varco_core.tls.install import install_process_trust

    original_hook = ssl._create_default_https_context  # noqa: SLF001

    handle_a = install_process_trust(trust_store, acknowledge_global_mutation=True)
    hook_after_a = ssl._create_default_https_context  # noqa: SLF001

    handle_b = install_process_trust(trust_store, acknowledge_global_mutation=True)
    hook_after_b = ssl._create_default_https_context  # noqa: SLF001
    assert hook_after_b is not hook_after_a

    # Release in LIFO order (the supported usage): b then a restores the original.
    handle_b.restore()
    assert ssl._create_default_https_context is hook_after_a  # noqa: SLF001

    handle_a.restore()
    assert ssl._create_default_https_context is original_hook  # noqa: SLF001


def test_varco_never_calls_install_process_trust_itself() -> None:
    # Mechanical form of "varco never calls it" (Step 20's rg check, mirrored here so a
    # regression is caught by the test suite too, not only by a commit-time grep).
    import subprocess

    result = subprocess.run(
        [
            "grep",
            "-rn",
            "install_process_trust(",
            "varco_core/varco_core",
            "varco_fastapi/varco_fastapi",
            "varco_sa/varco_sa",
            "varco_beanie/varco_beanie",
            "varco_kafka/varco_kafka",
            "varco_redis/varco_redis",
            "varco_nats/varco_nats",
            "varco_memcached/varco_memcached",
            "varco_ws/varco_ws",
            "varco_casbin/varco_casbin",
        ],
        capture_output=True,
        text=True,
        cwd="/home/edoardo/projects/varco",
        check=False,
    )
    hits = [line for line in result.stdout.splitlines() if "def install_process_trust" not in line]
    assert not hits, f"install_process_trust() is called from library code: {hits}"
