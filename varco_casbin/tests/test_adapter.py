"""
Unit tests for varco_casbin.adapter.build_adapter
=================================================
Covers the adapter-selection factory: memory/file/sqlalchemy plus the
fail-fast validation for missing parameters and unknown adapters.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from varco_casbin.adapter import build_adapter
from varco_casbin.config import CasbinSettings


def test_memory_returns_none() -> None:
    """The in-memory adapter is represented by None (policies live in RAM)."""
    assert build_adapter(CasbinSettings(adapter="memory")) is None


def test_file_adapter_built() -> None:
    """The file adapter is constructed for a given policy_path."""
    tmp = Path(tempfile.mkdtemp()) / "p.csv"
    tmp.write_text("", encoding="utf-8")
    adapter = build_adapter(CasbinSettings(adapter="file", policy_path=str(tmp)))
    assert adapter is not None


def test_file_adapter_requires_policy_path() -> None:
    """adapter='file' without policy_path fails fast."""
    with pytest.raises(ValueError, match="POLICY_PATH"):
        build_adapter(CasbinSettings(adapter="file"))


def test_sqlalchemy_requires_db_url() -> None:
    """adapter='sqlalchemy' without db_url fails fast."""
    with pytest.raises(ValueError, match="DB_URL"):
        build_adapter(CasbinSettings(adapter="sqlalchemy"))


def test_sqlalchemy_adapter_built() -> None:
    """The sqlalchemy adapter is constructed for a given db_url."""
    adapter = build_adapter(
        CasbinSettings(adapter="sqlalchemy", db_url="sqlite+aiosqlite:///:memory:")
    )
    assert adapter is not None
