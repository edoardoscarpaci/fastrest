"""
Plan 027 / Step 17 — failing-first tests for ``varco_core.tls.install.install_process_trust``
and ``RestoreHandle`` (§D-T4-install).

``varco_core.tls.install`` does not exist yet — every test fails with
``ModuleNotFoundError`` until Phase 4 lands ``install.py`` (Step 18).
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
    """
    Mechanical form of "varco never calls it" (Step 20's rg check, mirrored here so a
    regression is caught by the test suite too, not only by a commit-time grep).

    The scan is pure Python rather than a ``grep`` subprocess: the workspace root is
    derived from ``__file__`` (this file lives at ``<root>/varco_core/tests/``) so the
    test passes from any checkout location and in CI, and no external binary is needed.

    Edge cases:
        - The definition site in ``varco_core/tls/install.py`` is expected and excluded.
        - A file that is not valid UTF-8 is read with ``errors="ignore"`` rather than
          failing the test — a binary blob under a package dir is not library code.
    """
    # <root>/varco_core/tests/test_tls_install.py -> parents[2] is the workspace root.
    root = Path(__file__).resolve().parents[2]

    # Derived, not hand-listed, so an eleventh package is covered the day it is added
    # (same RL-18 discipline as scripts/api_surface.py deriving from scripts/packages.sh):
    # a distribution's source dir is the same-named directory inside its package dir.
    package_dirs = sorted(
        d for d in root.glob("varco_*/varco_*") if d.is_dir() and d.name == d.parent.name
    )
    assert package_dirs, f"no varco_*/varco_* source dirs found under {root}"

    hits: list[str] = []
    for package_dir in package_dirs:
        for path in package_dir.rglob("*.py"):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
            ):
                if "install_process_trust(" not in line:
                    continue
                if "def install_process_trust" in line:  # the definition itself
                    continue
                hits.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")

    assert not hits, f"install_process_trust() is called from library code: {hits}"
