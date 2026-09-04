"""
Plan 026 / Step 1 — failing-first tests for ``varco_core.tls.discovery.iter_cert_files``.

``varco_core.tls`` does not exist yet: every test here fails with ``ModuleNotFoundError``/
``ImportError`` until Step 2 lands ``varco_core/varco_core/tls/discovery.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest


def test_iter_cert_files_filters_by_pattern(tmp_path: Path) -> None:
    # Only files matching the given patterns are returned — a .cer must be excluded here.
    from varco_core.tls.discovery import iter_cert_files

    (tmp_path / "ca.pem").write_text("pem")
    (tmp_path / "ca.crt").write_text("crt")
    (tmp_path / "ca.cer").write_text("cer")
    (tmp_path / "notes.txt").write_text("txt")

    found = list(iter_cert_files(tmp_path, patterns=("*.pem", "*.crt"), recursive=False))

    assert {p.name for p in found} == {"ca.pem", "ca.crt"}


def test_iter_cert_files_deterministic_sort(tmp_path: Path) -> None:
    # Consumers (SSLConfig, TrustStore) rely on load order being stable across runs.
    from varco_core.tls.discovery import iter_cert_files

    for name in ("zeta.pem", "alpha.pem", "mid.pem"):
        (tmp_path / name).write_text("pem")

    found = list(iter_cert_files(tmp_path, patterns=("*.pem",), recursive=False))

    assert found == sorted(found)
    assert [p.name for p in found] == ["alpha.pem", "mid.pem", "zeta.pem"]


def test_iter_cert_files_recursive_true_finds_nested_cert(tmp_path: Path) -> None:
    from varco_core.tls.discovery import iter_cert_files

    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.pem").write_text("pem")

    found = list(iter_cert_files(tmp_path, patterns=("*.pem",), recursive=True))

    assert (sub / "deep.pem") in found


def test_iter_cert_files_recursive_false_skips_nested_cert(tmp_path: Path) -> None:
    from varco_core.tls.discovery import iter_cert_files

    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.pem").write_text("pem")

    found = list(iter_cert_files(tmp_path, patterns=("*.pem",), recursive=False))

    assert found == []


def test_iter_cert_files_dotdot_symlink_layout_enumerates_resolved_files_once(
    tmp_path: Path,
) -> None:
    # Mirrors Kubernetes' ..data symlink-swap ConfigMap/Secret delivery shape (Plan 025 T1) —
    # the generation directory holds the real file, "..data" points at it, and the visible
    # name symlinks through "..data". iter_cert_files must return the resolved file exactly once,
    # not once per traversal path and not the raw "..2026..." generation directory entry.
    from varco_core.tls.discovery import iter_cert_files

    gen_dir = tmp_path / "..2026_01_01_00_00_00.000000000"
    gen_dir.mkdir()
    real_cert = gen_dir / "ca.pem"
    real_cert.write_text("pem")

    data_link = tmp_path / "..data"
    data_link.symlink_to(gen_dir.name)

    visible = tmp_path / "ca.pem"
    visible.symlink_to(Path("..data") / "ca.pem")

    found = list(iter_cert_files(tmp_path, patterns=("*.pem",), recursive=False))

    assert len(found) == 1
    assert found[0].name == "ca.pem"
    # The raw "..2026..." generation directory must never itself be surfaced as a match.
    assert not any(p.name.startswith("..") for p in found)


def test_iter_cert_files_cer_file_warns_once_and_is_not_returned(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # BACKLOG's own complaint: a .cer file is ignored "with no error" — the defect is the
    # silence, so this asserts a WARNING fires, not that the file becomes trusted.
    from varco_core.tls.discovery import iter_cert_files

    (tmp_path / "extra.cer").write_text("cer")

    with caplog.at_level(logging.WARNING):
        found = list(iter_cert_files(tmp_path, patterns=("*.pem", "*.crt"), recursive=False))

    assert found == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "extra.cer" in warnings[0].getMessage()


def test_iter_cert_files_warning_fires_at_most_once_per_root_and_patterns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from varco_core.tls.discovery import iter_cert_files

    (tmp_path / "extra.cer").write_text("cer")

    with caplog.at_level(logging.WARNING):
        list(iter_cert_files(tmp_path, patterns=("*.pem", "*.crt"), recursive=False))
        list(iter_cert_files(tmp_path, patterns=("*.pem", "*.crt"), recursive=False))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_iter_cert_files_nonexistent_root_returns_empty_without_raising(tmp_path: Path) -> None:
    from varco_core.tls.discovery import iter_cert_files

    missing = tmp_path / "does-not-exist"

    found = list(iter_cert_files(missing, patterns=("*.pem",), recursive=False))

    assert found == []


def test_cert_file_patterns_is_the_wider_known_set() -> None:
    from varco_core.tls.discovery import CERT_FILE_PATTERNS

    assert set(CERT_FILE_PATTERNS) == {"*.pem", "*.crt", "*.cer"}
