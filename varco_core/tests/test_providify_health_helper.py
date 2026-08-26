"""
Unit tests for ``varco_conformance.providify_health.assert_no_structural_di_issues``
(Plan 016 / RL-3a, Design §RL-3a, Step 23).

Reached via this package's ``pythonpath = ["../testkit"]`` pytest ini
setting (``varco_core/pyproject.toml``) — same convention every
``varco_conformance`` consumer already relies on.

Thread safety:  N/A (unit tests)
Async safety:   N/A (``container.validate()`` is synchronous)
"""

from __future__ import annotations

import pytest
from providify import DIContainer, Inject, Singleton
from varco_conformance.providify_health import assert_no_structural_di_issues


class _SomeAppSuppliedInterface:
    """Stand-in for an interface a package legitimately expects the app to bind."""


def test_passes_when_container_only_has_an_app_supplied_missing_binding() -> None:
    """
    A package scanned in isolation legitimately lacks an application
    binding (e.g. AsyncRepository[User], AbstractAuthorizer, app settings)
    — MISSING_BINDING alone must NOT trip the assertion.
    """

    @Singleton
    class NeedsAppInterface:
        def __init__(self, dep: Inject[_SomeAppSuppliedInterface]) -> None:
            self.dep = dep

    container = DIContainer()
    container.bind(NeedsAppInterface, NeedsAppInterface)

    # Must not raise.
    assert_no_structural_di_issues(container)


def test_raises_on_circular_dependency() -> None:
    """
    A genuine structural DI defect (CIRCULAR_DEPENDENCY) must fail loudly —
    this is exactly the class of bug the plan says must never be silently
    tolerated alongside MISSING_BINDING.
    """

    @Singleton
    class A:
        def __init__(self, b: Inject[B]) -> None:
            self.b = b

    @Singleton
    class B:
        def __init__(self, a: Inject[A]) -> None:
            self.a = a

    container = DIContainer()
    container.bind(A, A)
    container.bind(B, B)

    with pytest.raises(AssertionError, match="Circular dependency"):
        assert_no_structural_di_issues(container)


def test_raises_on_ambiguous_binding() -> None:
    """
    Two competing bindings for the same interface at the same priority
    (AMBIGUOUS_BINDING) is a structural defect distinct from an
    app-supplied MISSING_BINDING — must also fail loudly.
    """

    class _Interface:
        pass

    @Singleton
    class _ImplOne(_Interface):
        pass

    @Singleton
    class _ImplTwo(_Interface):
        pass

    @Singleton
    class _Consumer:
        # AMBIGUOUS_BINDING is only reported for an actual injection point —
        # two tied bindings with nothing injecting them never triggers it.
        def __init__(self, dep: Inject[_Interface]) -> None:
            self.dep = dep

    container = DIContainer()
    container.bind(_Interface, _ImplOne)
    container.bind(_Interface, _ImplTwo)
    container.bind(_Consumer, _Consumer)

    with pytest.raises(AssertionError):
        assert_no_structural_di_issues(container)


def test_error_message_lists_every_structural_issue() -> None:
    """
    The assertion message must surface each structural issue's own
    ``.message`` (not a generic "validation failed") so a failing test's
    output is immediately actionable.
    """

    @Singleton
    class A:
        def __init__(self, b: Inject[B]) -> None:
            self.b = b

    @Singleton
    class B:
        def __init__(self, a: Inject[A]) -> None:
            self.a = a

    container = DIContainer()
    container.bind(A, A)
    container.bind(B, B)

    with pytest.raises(AssertionError) as exc_info:
        assert_no_structural_di_issues(container)

    assert "Circular dependency detected" in str(exc_info.value)
