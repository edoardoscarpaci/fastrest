"""
Red tests for AB-4 (Plan 022 / Phase 3) — collapse ``BeanieConfig`` into
``BeanieSettings``.

Verdict: ``rename+alias``. ``BeanieSettings`` is unchanged and stays the
canonical, DI-registered token; ``BeanieConfig`` survives as a deprecated
**alias for the same class** (so ``isinstance`` and every existing
``BeanieConfig(...)`` call site keep working), and ``BeanieFastrestApp``
constructs ``BeanieSettings`` directly instead of re-mapping KI-10's
field-for-field bridge.
"""

from __future__ import annotations

import warnings

import pytest
from varco_core.model import DomainModel


class _Widget(DomainModel):
    """Minimal domain model — entity_classes needs a real DomainModel subclass."""

    name: str = "w"


class _FakeMongoClient:
    """Stand-in for AsyncMongoClient — settings construction performs no I/O."""


def _get_alias():
    """Fetch varco_beanie.BeanieConfig with the deprecation warning silenced."""
    import varco_beanie

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return varco_beanie.BeanieConfig


# ── BeanieSettings is unchanged ───────────────────────────────────────────────


def test_beanie_settings_is_still_exported_and_constructible() -> None:
    """AB-4 keeps BeanieSettings — it is the surviving concept, not the deleted one."""
    from varco_beanie import BeanieSettings

    settings = BeanieSettings(
        mongo_client=_FakeMongoClient(),
        db_name="mydb",
        entity_classes=(_Widget,),
    )

    assert settings.db_name == "mydb"
    assert settings.entity_classes == (_Widget,)
    assert settings.transactional is False


def test_beanie_settings_does_not_warn() -> None:
    """Only the alias is deprecated — warning on the survivor would be a false alarm."""
    import varco_beanie

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        varco_beanie.BeanieSettings  # noqa: B018

    assert [w for w in record if issubclass(w.category, DeprecationWarning)] == []


# ── BeanieConfig is a deprecated alias for the SAME class ─────────────────────


def test_beanie_config_is_the_same_object_as_beanie_settings() -> None:
    """A subclass alias would break `isinstance(x, BeanieSettings)` for BeanieConfig instances."""
    from varco_beanie import BeanieSettings

    assert _get_alias() is BeanieSettings


def test_beanie_config_is_still_constructible_with_the_same_fields() -> None:
    """The collapse must not break a single existing non-DI call site."""
    from varco_beanie import BeanieSettings

    config = _get_alias()(
        mongo_client=_FakeMongoClient(),
        db_name="mydb",
        entity_classes=(_Widget,),
        transactional=False,
    )

    assert isinstance(config, BeanieSettings)
    assert config.db_name == "mydb"


def test_accessing_beanie_config_emits_a_deprecation_warning() -> None:
    """An alias that never warns is never migrated off before removal."""
    import varco_beanie

    with pytest.warns(DeprecationWarning) as record:
        varco_beanie.BeanieConfig  # noqa: B018

    message = str(record[0].message)
    assert "BeanieConfig" in message
    assert "BeanieSettings" in message


def test_beanie_config_warning_blames_the_caller() -> None:
    """stacklevel must point at the importing module, not at varco_beanie/__init__.py."""
    import varco_beanie

    with pytest.warns(DeprecationWarning) as record:
        varco_beanie.BeanieConfig  # noqa: B018

    assert record[0].filename == __file__


def test_varco_beanie_still_raises_attribute_error_for_unknown_names() -> None:
    """A module __getattr__ must not swallow genuine typos."""
    import varco_beanie

    with pytest.raises(AttributeError) as exc:
        varco_beanie.NoSuchSymbol  # noqa: B018

    assert "NoSuchSymbol" in str(exc.value)


# ── BeanieFastrestApp constructs BeanieSettings directly ──────────────────────


def test_beanie_fastrest_app_accepts_beanie_settings_directly() -> None:
    """AB-4 retires KI-10's field-for-field remap — the app takes the settings object itself."""
    from varco_beanie.bootstrap import BeanieFastrestApp

    from varco_beanie import BeanieSettings

    settings = BeanieSettings(
        mongo_client=_FakeMongoClient(),
        db_name="mydb",
        entity_classes=(),
    )

    app = BeanieFastrestApp(settings)

    assert app.uow_provider is not None


def test_beanie_fastrest_app_no_longer_remaps_config_fields() -> None:
    """The KI-10 bridge is the carrying cost AB-4 exists to delete."""
    import inspect

    from varco_beanie.bootstrap import BeanieFastrestApp

    source = inspect.getsource(BeanieFastrestApp.__init__)

    assert "config.mongo_client" not in source
    assert "config.db_name" not in source


def test_bootstrap_module_no_longer_defines_its_own_beanie_config_class() -> None:
    """A second class named BeanieConfig would defeat the identity guarantee above."""
    import varco_beanie.bootstrap as bootstrap

    from varco_beanie import BeanieSettings

    defined = bootstrap.__dict__.get("BeanieConfig")

    assert defined is None or defined is BeanieSettings
