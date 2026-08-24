"""
tests.test_lifecycle_component_discovery
==========================================
Plan 014 / audit finding F2 — characterization tests for
``varco_fastapi.app._collect_lifecycle_components`` /
``_try_resolve_component`` written against **unmodified** production code
(Phase 1 of Plan 014).

These tests originally pinned today's behaviour, including two
deliberately-silent defects (marked ⟳). Plan 014 Phase 2 (step 14) inverted
those two assertions once ``_try_resolve_component`` gained tiered logging;
step 15 added the ``warn_if_missing``/kill-switch tests below them.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from varco_core.event.base import AbstractEventBus
from varco_core.job.base import AbstractJobRunner


# ── helpers ───────────────────────────────────────────────────────────────────


def _fake_container(*, resolvable: dict[type, object] | None = None) -> MagicMock:
    """
    Build a ``MagicMock`` standing in for a ``DIContainer``, modeled on the
    pattern in ``varco_fastapi/tests/milestone_f/test_app_factory.py:329-378``.

    ``scan()`` is a no-op; ``is_resolvable(cls)``/``get(cls)`` are driven by
    the ``resolvable`` mapping — a class present in the mapping resolves to
    its mapped instance, anything else is "not bound" (``is_resolvable`` ->
    ``False``, ``get`` -> ``LookupError``).

    Args:
        resolvable: Mapping of type -> instance to resolve. Defaults to
            empty (nothing resolvable).

    Returns:
        A ``MagicMock`` with ``scan``/``is_resolvable``/``get`` stubbed.
    """
    resolvable = resolvable or {}
    container = MagicMock()
    container.scan = MagicMock()

    def _is_resolvable(cls: type) -> bool:
        return cls in resolvable

    def _get(cls: type) -> object:
        if cls in resolvable:
            return resolvable[cls]
        raise LookupError(f"not bound: {cls!r}")

    container.is_resolvable = MagicMock(side_effect=_is_resolvable)
    container.get = MagicMock(side_effect=_get)
    return container


# ── happy path ───────────────────────────────────────────────────────────────


def test_collect_lifecycle_includes_event_bus_when_resolvable() -> None:
    """
    No existing test covers the ``AbstractEventBus`` branch of
    ``_collect_lifecycle_components`` — only the two ``varco_ws`` branches
    are covered in ``milestone_f/test_app_factory.py``. Pin the happy path.
    """
    from varco_fastapi.app import _collect_lifecycle_components

    bus_instance = object()
    container = _fake_container(resolvable={AbstractEventBus: bus_instance})

    components = _collect_lifecycle_components(container)

    assert bus_instance in components


# ── container is None / nothing resolvable ─────────────────────────────────


def test_collect_lifecycle_returns_empty_list_for_none_container() -> None:
    """``container is None`` short-circuits to ``[]`` with no attribute access."""
    from varco_fastapi.app import _collect_lifecycle_components

    assert _collect_lifecycle_components(None) == []


def test_collect_lifecycle_returns_empty_list_when_nothing_resolvable() -> None:
    """A container where every binding is missing returns ``[]``, never raises."""
    from varco_fastapi.app import _collect_lifecycle_components

    container = _fake_container(resolvable={})

    components = _collect_lifecycle_components(container)

    assert components == []


# ── failure isolation ────────────────────────────────────────────────────────


def test_collect_lifecycle_isolates_failures_between_components() -> None:
    """
    ``container.scan`` raising ``ModuleNotFoundError`` for the ``varco_ws.*``
    components, and ``container.get`` raising ``RuntimeError`` for
    ``AbstractJobRunner``, must both leave ``AbstractEventBus`` collected and
    must not raise out of ``_collect_lifecycle_components``.

    This assertion must remain green after Plan 014 step 13 — it is the
    "control flow unchanged" contract F2 promises.
    """
    from varco_fastapi.app import _collect_lifecycle_components

    bus_instance = object()
    container = MagicMock()

    def _scan(module: str, *args: object, **kwargs: object) -> None:
        if module.startswith("varco_ws"):
            raise ModuleNotFoundError(module)

    def _is_resolvable(cls: type) -> bool:
        return cls in (AbstractEventBus, AbstractJobRunner)

    def _get(cls: type) -> object:
        if cls is AbstractEventBus:
            return bus_instance
        if cls is AbstractJobRunner:
            raise RuntimeError("construction failed")
        raise LookupError(f"not bound: {cls!r}")

    container.scan = MagicMock(side_effect=_scan)
    container.is_resolvable = MagicMock(side_effect=_is_resolvable)
    container.get = MagicMock(side_effect=_get)

    components = _collect_lifecycle_components(container)  # must not raise

    assert components == [bus_instance]


# ── tiered logging (Plan 014 step 14 inverted the two ⟳ tests below) ───────


def test_missing_event_bus_binding_emits_exactly_one_warning(
    caplog: object,
) -> None:
    """
    Plan 014 step 5, inverted by step 14.

    ``AbstractEventBus`` being importable-but-not-resolvable now emits
    exactly one WARNING naming ``AbstractEventBus`` — the exact silence the
    audit (F2) flagged ("you forgot ``redis_bootstrap(container)``") now
    produces a signal.
    """
    from varco_fastapi.app import _collect_lifecycle_components

    container = _fake_container(resolvable={})

    with caplog.at_level(logging.DEBUG):  # type: ignore[attr-defined]
        components = _collect_lifecycle_components(container)

    assert components == []
    matching = [
        record
        for record in caplog.records  # type: ignore[attr-defined]
        if "AbstractEventBus" in record.getMessage()
    ]
    assert len(matching) == 1
    assert matching[0].levelno == logging.WARNING


def test_get_raising_non_lookup_exception_emits_exactly_one_warning(
    caplog: object,
) -> None:
    """
    Plan 014 step 6, inverted by step 14.

    ``container.get()`` raising a non-``Lookup`` exception (e.g. a
    construction failure such as a socket connect error) now emits exactly
    one WARNING (with ``exc_info=True``) naming ``AbstractEventBus``.
    """
    from varco_fastapi.app import _collect_lifecycle_components

    container = MagicMock()
    container.scan = MagicMock()

    def _is_resolvable(cls: type) -> bool:
        return cls is AbstractEventBus

    def _get(cls: type) -> object:
        raise RuntimeError("construction failed")

    container.is_resolvable = MagicMock(side_effect=_is_resolvable)
    container.get = MagicMock(side_effect=_get)

    with caplog.at_level(logging.DEBUG):  # type: ignore[attr-defined]
        components = _collect_lifecycle_components(container)

    assert components == []
    matching = [
        record
        for record in caplog.records  # type: ignore[attr-defined]
        if "AbstractEventBus" in record.getMessage()
    ]
    assert len(matching) == 1
    assert matching[0].levelno == logging.WARNING
    assert matching[0].exc_info is not None


# ── warn_if_missing / kill switch (Plan 014 step 15) ────────────────────────


def test_missing_websocket_binding_emits_no_warning_while_event_bus_does(
    caplog: object,
) -> None:
    """
    Plan 014 step 15 — the two ``varco_ws`` push adapters pass
    ``warn_if_missing=False`` so an app that never uses ``varco_ws`` gets no
    WARNING for them, while ``AbstractEventBus`` still warns.
    """
    from varco_core.event.base import AbstractEventBus as _Bus
    from varco_fastapi.app import _collect_lifecycle_components

    container = _fake_container(resolvable={})

    with caplog.at_level(logging.DEBUG):  # type: ignore[attr-defined]
        _collect_lifecycle_components(container)

    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING  # type: ignore[attr-defined]
    ]
    assert any(_Bus.__name__ in r.getMessage() for r in warnings)
    assert not any("WebSocketEventBus" in r.getMessage() for r in warnings)
    assert not any("SSEEventBus" in r.getMessage() for r in warnings)


def test_kill_switch_demotes_missing_binding_warning_to_debug(
    caplog: object, monkeypatch: object
) -> None:
    """
    Plan 014 step 15 — ``VARCO_LIFECYCLE_DISCOVERY_WARN=false`` demotes the
    ``AbstractEventBus`` missing-binding signal from WARNING to DEBUG.
    """
    from varco_fastapi.app import _collect_lifecycle_components

    monkeypatch.setenv("VARCO_LIFECYCLE_DISCOVERY_WARN", "false")  # type: ignore[attr-defined]

    container = _fake_container(resolvable={})

    with caplog.at_level(logging.DEBUG):  # type: ignore[attr-defined]
        _collect_lifecycle_components(container)

    matching = [
        record
        for record in caplog.records  # type: ignore[attr-defined]
        if "AbstractEventBus" in record.getMessage()
    ]
    assert len(matching) == 1
    assert matching[0].levelno == logging.DEBUG
