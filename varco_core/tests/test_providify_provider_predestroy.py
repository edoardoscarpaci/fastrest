"""
Characterization + strict xfail for **P22-PROVIDER-PREDESTROY**.

`container.ashutdown()` does not run the ``@PreDestroy`` hook of an instance
produced by a ``@Provider``. providify's ``_adispose()`` dispatches on binding
*kind*: a ``ProviderBinding`` runs its ``@Disposes`` disposer and returns, so
``binding.pre_destroy`` is only ever consulted for a ``ClassBinding``.

Why this file exists **in addition to**
``varco_redis/tests/test_redis_cache_lifespan_shutdown_integration.py``, which
already pins the same gap: that test needs Docker and ``-m integration``, so it
runs nightly at best and never in ``make test``. The gap it guards is the reason
two varco caches (``RedisCache``, ``MemcachedCache``) still leak a started
connection pool after the Plan 022 / RL-8a adoption — that is worth an
early-warning signal on **every** unit run, not a nightly one.

This file therefore reproduces the gap with **no broker, no varco types and no
I/O at all** — just providify, a `@Configuration`, and a class with a
`@PreDestroy`. It is the minimal reproduction, and doubles as the runnable
attachment to ``design/upstream-gaps/providify-provider-predestroy.md``.

Two tests, deliberately paired:

* ``test_class_bound_predestroy_runs`` — the **control**, and it passes today.
  Without it, an xfail below could be caused by anything (teardown not running
  at all, the hook never registering); with it, the difference is isolated to
  the one variable that matters: how the instance was bound.
* ``test_provider_bound_predestroy_runs`` — ``strict=True`` xfail. Per CLAUDE.md's
  conformance rule, a genuine upstream contract gap becomes a strict xfail plus a
  register entry, never an in-place workaround. ``strict=True`` means this test
  **fails loudly** the day the gap closes, so the fix cannot land unnoticed and
  untested — which is exactly the guarantee it was asked for.

See BACKLOG.md's "Findings from Plan 022 (Phase 4 / RL-8a)" row.
"""

from __future__ import annotations

import pytest
from providify import Configuration, DIContainer, PreDestroy, Provider


class _Resource:
    """
    A stand-in for ``RedisCache``/``MemcachedCache``: holds a "pool", closes it
    in a ``@PreDestroy``.

    Deliberately not a varco type — the gap is in providify's teardown dispatch,
    so reproducing it must not depend on anything varco does.
    """

    def __init__(self) -> None:
        self.closed = False

    @PreDestroy
    async def stop(self) -> None:
        """Release the resource. Idempotent, like every varco ``stop()``."""
        self.closed = True


async def test_class_bound_predestroy_runs() -> None:
    """
    Control: bound as a class, the hook runs. Isolates the binding *kind* as the
    only variable distinguishing this from the xfail below.
    """
    from providify import Singleton

    container = DIContainer()
    container.register(Singleton(_Resource))

    resource = await container.aget(_Resource)
    assert resource.closed is False

    await container.ashutdown()

    assert resource.closed is True, "a ClassBinding's @PreDestroy must run"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG (P22-PROVIDER-PREDESTROY): container.ashutdown() never consults "
        "@PreDestroy for a @Provider-produced instance — providify's "
        "_adispose() returns early for a ProviderBinding whose @Disposes is "
        "unset. Two varco caches leak a started connection pool because of it. "
        "See design/upstream-gaps/providify-provider-predestroy.md."
    ),
)
async def test_provider_bound_predestroy_runs() -> None:
    """
    The gap: identical class, identical hook, bound through a ``@Provider``
    instead — and the hook silently never runs.

    Nothing warns. There is no exception, no log line, and no
    ``container.validate()`` issue kind that covers "this class's @PreDestroy is
    unreachable given how it is bound", so the only way to discover it is to
    observe the resource still open after shutdown.
    """

    @Configuration
    class _Module:
        @Provider(singleton=True)
        async def resource(self) -> _Resource:
            return _Resource()

    container = DIContainer()
    await container.ainstall(_Module)

    resource = await container.aget(_Resource)
    assert resource.closed is False

    await container.ashutdown()

    assert resource.closed is True, (
        "a @Provider-produced instance's @PreDestroy must run — "
        "@PreDestroy's own docstring says 'called on shutdown or scope "
        "teardown' with no binding-kind caveat"
    )
