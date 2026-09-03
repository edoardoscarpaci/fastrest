"""
Characterization for **P22-PROVIDER-PREDESTROY** — resolved (Plan 024 / C2).

`container.ashutdown()` does not run the ``@PreDestroy`` hook of an instance
produced by a ``@Provider``. providify's ``_adispose()`` dispatches on binding
*kind*: a ``ProviderBinding`` runs its ``@Disposes`` disposer (if any) and
returns, so ``binding.pre_destroy`` is only ever consulted for a
``ClassBinding``.

**Resolution (2026-09-02, Plan 024)**: providify 2.0.1 shipped 2026-09-01 and
settles this as **intentional**, not a bug — the Jakarta CDI producer-method
rule providify follows (`providify/SKILL.md:287`) says a producer method's
output is torn down only by an explicit disposer, never by a lifecycle hook
declared on the produced class. providify 2.0.1 adds `IssueKind.
UNREACHABLE_PRE_DESTROY` (a ``WARNING``) to *detect* this shape, and corrects
its docstrings to state the behaviour explicitly
(`providify/README.md:945-949`). No upstream code change closes the gap — and
none was asked for; varco's own report (`design/upstream-gaps/providify-provider-predestroy.md`
§5) already proposed the fix that ships: adopt ``@Disposes``, upstream's
*supported* teardown mechanism for provider output. varco adopted it at all
nine affected sites (Plan 024 §D-C2-audit).

Why this file exists **in addition to**
``varco_redis/tests/test_redis_cache_lifespan_shutdown_integration.py``, which
already characterizes the same settled contract against a real Redis
container: that test needs Docker and ``-m integration``, so it runs nightly
at best and never in ``make test``. This file reproduces the contract with
**no broker, no varco types and no I/O at all** — just providify, a
``@Configuration``, and a class with a ``@PreDestroy``. It is the minimal
reproduction, and doubles as the runnable attachment to
``design/upstream-gaps/providify-provider-predestroy.md``.

Three tests:

* ``test_class_bound_predestroy_runs`` — the **control**: a ``ClassBinding``'s
  ``@PreDestroy`` runs. Isolates binding *kind* as the variable that matters.
* ``test_provider_bound_predestroy_does_not_run`` — characterizes the settled
  upstream contract: a ``@Provider``-produced instance's ``@PreDestroy`` is
  **never** invoked, by design. This assertion is now expected to hold
  forever (not an xfail) — a future providify release changing this would be
  a breaking behavioural change to their own documented contract, not a "fix".
* ``test_provider_bound_disposes_runs`` — the varco-side answer:
  ``@Disposes`` on the same ``@Configuration`` **does** run, proving the
  adopted teardown mechanism actually works for this exact shape.

See BACKLOG.md's C2 row and `plans/024-3-0-1-cleanup.md` §D-C2.
"""

from __future__ import annotations

from providify import Configuration, DIContainer, Disposes, PreDestroy, Provider, Singleton


class _Resource:
    """
    A stand-in for ``RedisCache``/``MemcachedCache``: holds a "pool", closes it
    in a ``@PreDestroy``.

    Deliberately not a varco type — the characterized contract is providify's
    teardown dispatch, so reproducing it must not depend on anything varco
    does.
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
    only variable distinguishing this from the provider-bound characterization
    below.
    """
    container = DIContainer()
    container.register(Singleton(_Resource))

    resource = await container.aget(_Resource)
    assert resource.closed is False

    await container.ashutdown()

    assert resource.closed is True, "a ClassBinding's @PreDestroy must run"


async def test_provider_bound_predestroy_does_not_run() -> None:
    """
    Characterizes the settled upstream contract: identical class, identical
    hook, bound through a ``@Provider`` instead — and the hook never runs.

    This is **intentional**, per providify 2.0.1's own documentation
    (Jakarta CDI producer-method rule) — not a bug to be fixed, so this
    assertion is not an xfail. `container.validate()` surfaces this shape as
    a ``WARNING``-severity `IssueKind.UNREACHABLE_PRE_DESTROY` issue (see
    `varco_redis/tests/test_redis_cache_disposes.py` for the Tier-A
    mechanism-level assertion against that detector); it never raises and
    never appears in `report.errors`.
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

    assert resource.closed is False, (
        "a @Provider-produced instance's @PreDestroy is never invoked by "
        "design (Jakarta CDI producer-method rule) — providify 2.0.1 "
        "documents this explicitly rather than fixing it"
    )


async def test_provider_bound_disposes_runs() -> None:
    """
    The adopted fix: ``@Disposes`` on the producing ``@Configuration`` DOES
    run for a ``@Provider``-produced instance — this is the mechanism varco
    now uses at all nine Plan-024-audited sites instead of relying on
    ``@PreDestroy``.
    """

    @Configuration
    class _Module:
        @Provider(singleton=True)
        async def resource(self) -> _Resource:
            return _Resource()

        @Disposes(_Resource)
        async def close_resource(self, resource: _Resource) -> None:
            await resource.stop()

    container = DIContainer()
    await container.ainstall(_Module)

    resource = await container.aget(_Resource)
    assert resource.closed is False

    await container.ashutdown()

    assert resource.closed is True, (
        "@Disposes must run for a @Provider-produced instance — this is "
        "the supported teardown mechanism providify documents for exactly "
        "this shape"
    )
