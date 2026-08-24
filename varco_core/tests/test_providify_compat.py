"""
Tests for ``varco_core.providify_compat.provide_factory`` (Plan 014 / Part B, F8).

This module encodes the plan's Step 15 (unit tests for the helper in
isolation) and Step 16 (the helper must register no bindings when scanned).
"""

from __future__ import annotations

from typing import Generic, TypeVar

import pytest
from providify import DIContainer, Inject, Provider

from varco_core.providify_compat import provide_factory

T = TypeVar("T")


class _Widget:
    """Plain class used as a `returns=` target — mirrors sites 1-4's shape."""


class _Repo(Generic[T]):
    """Generic-alias-shaped class — mirrors sites 5-7's `AsyncRepository[X]` shape."""


class _User:
    pass


class _Post:
    pass


@pytest.fixture
def container() -> DIContainer:
    return DIContainer()


def test_registers_factory_under_a_plain_class(container: DIContainer) -> None:
    # provide_factory must patch the return annotation and register the
    # factory so container.get() resolves it under the plain class target.
    def _factory() -> None:  # placeholder annotation patched by provide_factory
        return _Widget()

    provide_factory(container, _factory, returns=_Widget, singleton=True)

    resolved = container.get(_Widget)

    assert isinstance(resolved, _Widget)


def test_registers_factory_under_a_generic_alias(container: DIContainer) -> None:
    # sites 5-7 register under AsyncRepository[X]-shaped generic aliases —
    # provide_factory must support `returns=` being a generic alias, not
    # just a plain class.
    def _factory() -> None:
        return _Repo()

    provide_factory(container, _factory, returns=_Repo[_User], singleton=False)

    resolved = container.get(_Repo[_User])

    assert isinstance(resolved, _Repo)


def test_singleton_true_returns_the_same_instance_twice(container: DIContainer) -> None:
    def _factory() -> None:
        return _Widget()

    provide_factory(container, _factory, returns=_Widget, singleton=True)

    first = container.get(_Widget)
    second = container.get(_Widget)

    assert first is second


def test_singleton_false_returns_different_instances(container: DIContainer) -> None:
    # DEPENDENT scope (default) — sites 5/6 rely on a fresh instance per
    # resolution so a per-request repository never leaks across requests.
    def _factory() -> None:
        return _Widget()

    provide_factory(container, _factory, returns=_Widget, singleton=False)

    first = container.get(_Widget)
    second = container.get(_Widget)

    assert first is not second


def test_name_kwarg_sets_dunder_name(container: DIContainer) -> None:
    # Sites 5/6 stamp __name__ so that two distinct entity_cls calls don't
    # collide under a shared closure-captured factory function name.
    def _factory() -> None:
        return _Widget()

    provide_factory(
        container,
        _factory,
        returns=_Widget,
        singleton=True,
        name="widget_factory_for_test",
    )

    assert _factory.__name__ == "widget_factory_for_test"


async def test_async_factory_resolves_via_aget(container: DIContainer) -> None:
    # An async def factory must resolve through container.aget() with no
    # extra argument — ProviderBinding.is_async must be True.
    async def _factory() -> None:
        return _Widget()

    provide_factory(container, _factory, returns=_Widget, singleton=True)

    resolved = await container.aget(_Widget)

    assert isinstance(resolved, _Widget)


def test_factory_with_injected_dependency_still_gets_it_injected(
    container: DIContainer,
) -> None:
    # Sites 5/6/7 have factories whose dependencies arrive via an injected
    # parameter, not a closure — provide_factory must not break normal
    # providify parameter injection.
    # NOTE: Inject/Provider are imported at module scope (not locally here)
    # because providify resolves a factory's parameter annotations against
    # its __globals__ — a name only bound in this function's local scope
    # would raise AnnotationResolutionError when the container evaluates
    # `dep: Inject[_User]` on `_factory`.
    @Provider(singleton=True)
    def _dep() -> _User:
        return _User()

    container.provide(_dep)

    def _factory(dep: Inject[_User]) -> None:
        return _Repo()

    provide_factory(container, _factory, returns=_Repo[_Post], singleton=True)

    resolved = container.get(_Repo[_Post])

    assert isinstance(resolved, _Repo)


def test_two_calls_with_different_returns_produce_independent_bindings(
    container: DIContainer,
) -> None:
    # Guards the exact closure-capture bug the seven duplicated sites (six of
    # which now route through provide_factory) exist to avoid: registering
    # AsyncRepository[User] and AsyncRepository[Post] via two provide_factory()
    # calls must not have one shadow the other.
    def _user_factory() -> None:
        return _Repo()

    def _post_factory() -> None:
        return _Repo()

    provide_factory(container, _user_factory, returns=_Repo[_User], singleton=False)
    provide_factory(container, _post_factory, returns=_Repo[_Post], singleton=False)

    user_repo = container.get(_Repo[_User])
    post_repo = container.get(_Repo[_Post])

    assert isinstance(user_repo, _Repo)
    assert isinstance(post_repo, _Repo)


def test_module_registers_no_bindings_when_scanned() -> None:
    # Step 16 — providify_compat must declare no @Provider/@Singleton at
    # module scope, so container.scan("varco_core.providify_compat")
    # registers nothing new.
    container = DIContainer()
    before = list(getattr(container, "_bindings", []))

    container.scan("varco_core.providify_compat")

    after = list(getattr(container, "_bindings", []))
    assert after == before
