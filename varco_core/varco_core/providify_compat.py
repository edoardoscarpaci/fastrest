"""
varco_core.providify_compat
============================
A compat shim for one specific providify limitation: a closure defined
under ``from __future__ import annotations`` (PEP 563) has its return
annotation stored as the *string* ``"None"``/whatever the closure's local
placeholder was — not the real target type. providify needs the real
type to derive the binding interface, so every call site that registers a
dynamically-computed factory (a generic alias like ``AsyncRepository[User]``,
or a class only known at call time) has historically patched
``factory.__annotations__["return"]`` by hand, immediately before
registering it.

That patch-then-register shape was independently reimplemented seven times
across four packages (audit ``001-audit-di-wiring.md``, finding F8 — the
audit itself only names 5; two more were found during Plan 014's inventory),
each with its own copy-pasted ``DESIGN:`` block — some correct, one (in
``varco_fastapi.di``) factually wrong about *why* the ordering matters.
``provide_factory()`` is the one place six of those seven shapes now live.
The seventh, ``varco_beanie.di``'s ``_make_repo_provider()``, is a
deliberate, documented exception: it stays a container-less builder because
``varco_beanie/tests/test_beanie_di.py`` imports it directly and asserts on
its patched, *unregistered* ``__annotations__``/``__name__`` — see that
function's own docstring and Plan 014's Step 21/22 for the full reasoning.

Ordering — why patch-before-``provide()`` is the only real constraint
-----------------------------------------------------------------------
``@Provider``'s decorator body (``providify/decorator/scope.py:538-566``)
only calls ``_set_provider_metadata(fn, ProviderMetadata(...))`` and
returns ``fn`` unchanged — it never reads ``__annotations__``. The return
annotation is read exactly once, later, when the binding is actually
constructed: ``ProviderBinding.__init__`` (``providify/binding.py:496-505``),
which ``container.provide()`` calls (``providify/container.py:672``). So the
annotation must be patched at some point *before* ``container.provide()``
runs — decorating before or after the patch makes no difference, because
decoration never inspects the annotation at all.

Compat shim, not a DI entry point
----------------------------------
This module is named ``providify_compat`` — not ``varco_core.di`` — on
purpose: it is a workaround for a specific third-party limitation, intended
to be **deleted** in one place the day providify resolves closure
annotations under PEP 563 natively. Naming it ``di`` would advertise a
package-level DI entry point (``bootstrap()``, bindings) it deliberately
does not have. For the same reason it is **not** re-exported from
``varco_core/__init__.py`` — a symbol whose whole purpose is to be
deletable should not enter the top-level public namespace.

It declares no ``@Provider``/``@Singleton`` at module scope, so
``container.scan("varco_core")`` (or a direct
``container.scan("varco_core.providify_compat")``) registers nothing new
from it — see ``varco_core/tests/test_providify_compat.py::
test_module_registers_no_bindings_when_scanned``.

Callers
-------
- ``varco_ws.di`` — ``_ws_factory`` / ``_sse_factory`` (plain-class targets)
- ``varco_fastapi.router.mcp`` / ``varco_fastapi.router.skill`` —
  ``_mcp_adapter_factory`` / ``_skill_adapter_factory`` (plain-class targets)
- ``varco_sa.di`` — per-entity ``AsyncRepository[D]`` provider
  (generic-alias target, DEPENDENT scope)
- ``varco_fastapi.di`` — ``bind_clients()``'s per-router client provider
  (generic-alias target)

``varco_beanie.di`` is deliberately **not** a caller — see the section above.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from providify import Provider

if TYPE_CHECKING:
    from providify import DIContainer


def provide_factory(
    container: DIContainer,
    factory: Callable[..., Any],
    *,
    returns: Any,
    singleton: bool = False,
    name: str | None = None,
) -> None:
    """
    Patch *factory*'s return annotation, stamp ``@Provider``, and register it.

    Replaces the hand-rolled::

        @Provider(singleton=True)
        def _factory() -> SomeType:      # placeholder annotation
            ...
        _factory.__annotations__["return"] = SomeType
        container.provide(_factory)

    with one call. Behaviour, in this exact order:

    1. ``factory.__annotations__["return"] = returns`` — patches the
       annotation providify will read at registration time.
    2. ``if name is not None: factory.__name__ = name`` — gives the closure a
       descriptive name for ``describe()``/debugging output; without this,
       every DEPENDENT-scoped factory sharing one closure name (e.g.
       ``_repo_factory``) is indistinguishable in diagnostics.
    3. ``container.provide(Provider(singleton=singleton)(factory))`` —
       decorates and registers. ``Provider()``'s decorator body never reads
       ``__annotations__`` (see the module docstring), so doing this last is
       safe — the annotation is only actually consulted once
       ``container.provide()`` builds the ``ProviderBinding``.

    Args:
        container: The ``DIContainer`` (or ``DIContainer``-shaped object —
            duck-typed to allow test doubles with a ``provide()`` method) to
            register the factory into.
        factory:   The callable to register. May be a plain ``def`` or an
            ``async def`` — ``@Provider`` detects a coroutine function itself
            (``providify/decorator/scope.py:549-551``), so no separate
            ``async_=`` flag is needed (see ``plans/014-refactor-di-settings
            -and-provider-helper.md``'s Part-B alternatives for why one
            proposed signature carrying ``async_=`` was rejected).
        returns:   The interface *factory* should be registered under —
            typically a plain class or a parameterised generic alias
            (e.g. ``AsyncRepository[User]``).
        singleton: When ``True``, the container caches one instance and
            returns it on every subsequent resolution (``Scope.SINGLETON``).
            Defaults to ``False`` (``Scope.DEPENDENT`` — a fresh instance per
            resolution), matching the majority of the sites this replaces
            (the per-entity repository providers).
        name:      When given, sets ``factory.__name__`` before registration.
            Useful when the same closure shape is built in a loop (e.g. one
            factory per entity class) and each iteration's factory should be
            individually identifiable in ``describe()`` output or tracebacks.

    Returns:
        None — the factory is registered as a side effect on *container*.

    Raises:
        Whatever ``container.provide()`` itself raises (e.g. a
        provider-registration error) — this function adds no additional
        validation of its own.

    Edge cases:
        - Calling this twice with two different ``returns=`` values for the
          same *factory* object produces two independent bindings — the
          second call's annotation patch happens after the first call's
          ``container.provide()`` already captured the annotation at that
          point in time, so neither shadows the other (this is exactly the
          closure-capture bug the six original call sites existed to avoid).
        - Calling this twice with the *same* ``(factory, returns)`` pair
          registers two separate provider bindings at equal priority —
          ``container.provide()``'s own documented tie-break (first
          registered wins) applies; no deduplication is added here.
        - An ``async def`` *factory* produces a binding whose
          ``is_async`` is ``True`` — it must be resolved via
          ``await container.aget(...)``, not the synchronous
          ``container.get(...)``, same as any other async provider.

    Thread safety:  ✅ Registration is expected at bootstrap (single-threaded).
    Async safety:   ✅ No I/O — only metadata mutation and a synchronous
                       ``container.provide()`` call.

    DESIGN: one shared helper over six independent hand-rolled closures
        ✅ One place to change if providify's annotation-resolution behaviour
           for closures ever changes, or if the patch shape needs to evolve.
        ✅ No ``async_=`` parameter needed — ``@Provider`` already detects
           ``inspect.iscoroutinefunction(fn)`` itself at decoration time and
           ``ProviderBinding.__init__`` re-detects it at registration
           (``providify/binding.py:510-513``); an explicit flag would be
           dead weight that could disagree with the function it describes.
        ❌ Still invisible to mypy/pyright — annotation patching is
           inherently a runtime-only mechanism, unchanged from every one of
           the six sites this replaces.
    """
    factory.__annotations__["return"] = returns
    if name is not None:
        factory.__name__ = name
    container.provide(Provider(singleton=singleton)(factory))


__all__ = ["provide_factory"]
