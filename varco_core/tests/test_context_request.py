"""
Red-mode tests for Plan 011 Phase 0, step 5 —
``varco_core.context.request.RequestContext`` and the ``request_context()``
merge-on-nest contract (D-6).
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from varco_core.context.request import (
    RequestContext,
    current_locale,
    current_request_context,
    current_timezone,
    request_context,
)


def test_current_request_context_returns_empty_instance_never_none() -> None:
    # D-6 / RD-1: with nothing configured, current_request_context() must be
    # an empty RequestContext, not None — callers should never null-check it.
    ctx = current_request_context()
    assert isinstance(ctx, RequestContext)
    assert ctx.locale is None
    assert ctx.timezone is None


def test_current_locale_and_timezone_are_none_with_no_active_scope() -> None:
    assert current_locale() is None
    assert current_timezone() is None


def test_request_context_sets_locale_visible_via_helpers() -> None:
    with request_context(locale="fr"):
        assert current_locale() == "fr"
        assert current_request_context().locale == "fr"
    assert current_locale() is None


def test_request_context_merges_with_enclosing_context_not_replaces() -> None:
    # D-6's named hazard: setting a locale in an inner scope must not blank
    # an already-resolved timezone from an outer scope.
    tz = ZoneInfo("America/New_York")
    with request_context(timezone=tz):
        with request_context(locale="de"):
            merged = current_request_context()
            assert merged.locale == "de"
            assert merged.timezone == tz
        # Exiting the inner scope restores the outer (timezone-only) context.
        assert current_request_context().locale is None
        assert current_request_context().timezone == tz


def test_request_context_is_frozen() -> None:
    import dataclasses

    ctx = RequestContext()
    assert dataclasses.is_dataclass(ctx)
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        ctx.locale = "fr"  # type: ignore[misc]


def test_request_context_extras_default_to_empty_mapping() -> None:
    ctx = RequestContext()
    assert dict(ctx.extras) == {}
