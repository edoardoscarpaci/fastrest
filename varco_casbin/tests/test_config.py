"""
Unit tests for varco_casbin.config.CasbinSettings
=================================================
Covers model resolution (preset / file / inline text), env-var loading,
and the validation edge cases (unknown preset, missing file).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from varco_casbin.config import CasbinSettings


def test_defaults() -> None:
    """Out-of-the-box: in-memory RBAC, auto-save on, admin role 'admin'."""
    s = CasbinSettings()
    assert s.model_preset == "rbac"
    assert s.adapter == "memory"
    assert s.auto_save is True
    assert s.admin_role == "admin"


def test_resolve_preset_models() -> None:
    """Every bundled preset resolves to non-empty model text."""
    for preset in ("acl", "rbac", "rbac_domains", "abac"):
        text = CasbinSettings(model_preset=preset).resolve_model_text()
        assert "[request_definition]" in text


def test_inline_text_wins_over_preset() -> None:
    """model_text takes precedence over model_preset."""
    custom = "[request_definition]\nr = sub, obj, act\n"
    s = CasbinSettings(model_text=custom, model_preset="abac")
    assert s.resolve_model_text() == custom


def test_model_path_read_from_disk() -> None:
    """model_path is read from disk when model_text is unset."""
    tmp = Path(tempfile.mkdtemp()) / "m.conf"
    tmp.write_text("[request_definition]\nr = sub, obj, act\n", encoding="utf-8")
    s = CasbinSettings(model_path=str(tmp))
    assert "r = sub, obj, act" in s.resolve_model_text()


def test_unknown_preset_raises() -> None:
    """An unknown preset fails fast and lists the valid options."""
    with pytest.raises(ValueError, match="Unknown Casbin model preset"):
        CasbinSettings(model_preset="nope").resolve_model_text()


def test_missing_model_path_raises() -> None:
    """A non-existent model_path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        CasbinSettings(model_path="/no/such/model.conf").resolve_model_text()


def test_env_var_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    """VARCO_CASBIN_* env vars populate settings via from_env()."""
    monkeypatch.setenv("VARCO_CASBIN_MODEL_PRESET", "abac")
    monkeypatch.setenv("VARCO_CASBIN_ADMIN_ROLE", "superuser")
    s = CasbinSettings.from_env()
    assert s.model_preset == "abac"
    assert s.admin_role == "superuser"
