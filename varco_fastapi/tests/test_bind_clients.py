"""
Regression tests for ``varco_fastapi.di.bind_clients()``.

What was broken
---------------
``bind_clients()`` was dead API — it always raised and registered nothing.  The
internal ``_factory`` closure was a plain function, never ``@Provider``-decorated:

1. ``container.bind(client_alias, _factory)`` raised inside providify's
   ``_is_generic_subtype`` (``issubclass()`` on a function).
2. The ``except Exception:`` fallback ``container.provide(_factory)`` raised
   ``ProviderBindingNotDecoratedError`` — still not decorated.
3. The last-resort ``container.bind(client_cls, client_cls)`` raised
   ``ClassBindingNotDecoratedError`` — the client class carries no
   ``@Component``/``@Singleton`` either.

Three nested ``except Exception`` blocks turned a precise failure into a
confusing one from the last branch, and would have silently swallowed a real
registration failure had any branch ever succeeded.

Current contract
----------------
``_factory`` gets its return annotation patched to ``AsyncVarcoClient[Router]``
*before* being decorated with ``@Provider(singleton=True)``, then registered via
a single un-guarded ``container.provide()`` call — a registration failure now
surfaces immediately, naming its real cause.
"""

from __future__ import annotations

import pytest
from providify import DIContainer
from varco_fastapi.client.base import AsyncVarcoClient
from varco_fastapi.di import bind_clients
from varco_fastapi.router.presets import GenericRouter


class _OrderRouter(GenericRouter):
    _prefix = "/orders"


class _UserRouter(GenericRouter):
    _prefix = "/users"


class _OrderClient(AsyncVarcoClient[_OrderRouter]):
    def __init__(self) -> None:
        super().__init__(base_url="http://orders.test")


class _UserClient(AsyncVarcoClient[_UserRouter]):
    def __init__(self) -> None:
        super().__init__(base_url="http://users.test")


class TestBindClientsRegisters:
    def test_regression_bind_clients_no_longer_raises(self) -> None:
        """
        User-visible symptom: ``bind_clients(container, OrderClient)`` aborted
        bootstrap with ``ClassBindingNotDecoratedError`` and registered nothing.
        """
        container = DIContainer()

        bind_clients(container, _OrderClient)

        assert any("AsyncVarcoClient" in str(b.interface) for b in container._bindings)

    def test_client_resolves_under_its_router_alias(self) -> None:
        """``Inject[VarcoClient[OrderRouter]]`` must resolve to the concrete client."""
        container = DIContainer()
        bind_clients(container, _OrderClient)

        assert isinstance(container.get(AsyncVarcoClient[_OrderRouter]), _OrderClient)

    def test_client_is_a_singleton(self) -> None:
        """The docstring promises a singleton — a bare @Provider would not be."""
        container = DIContainer()
        bind_clients(container, _OrderClient)

        assert container.get(AsyncVarcoClient[_OrderRouter]) is container.get(
            AsyncVarcoClient[_OrderRouter]
        )

    def test_multiple_clients_do_not_collide(self) -> None:
        """
        Guards the closure late-binding trap: each ``_factory`` must capture its
        own client class, so two clients registered in one call resolve to
        different concrete types.
        """
        container = DIContainer()
        bind_clients(container, _OrderClient, _UserClient)

        assert isinstance(container.get(AsyncVarcoClient[_OrderRouter]), _OrderClient)
        assert isinstance(container.get(AsyncVarcoClient[_UserRouter]), _UserClient)


class TestBindClientsFailsLoudly:
    def test_client_without_router_class_raises_typeerror(self) -> None:
        """A helpful error, not a swallowed one."""

        class _Bare:
            pass

        container = DIContainer()

        with pytest.raises(TypeError, match="has no _router_class"):
            bind_clients(container, _Bare)

    def test_registration_failure_is_not_swallowed(self) -> None:
        """
        The three nested ``except Exception`` blocks are gone: a genuine
        ``provide()`` failure must propagate rather than silently leaving the
        container half-wired.
        """

        class _Exploding(DIContainer):
            def provide(self, *args: object, **kwargs: object) -> None:
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            bind_clients(_Exploding(), _OrderClient)
