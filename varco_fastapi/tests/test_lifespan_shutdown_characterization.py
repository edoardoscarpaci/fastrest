"""
Characterization test — providify 2.0.0's aggregated ``ShutdownError`` shape
(Plan 016 / RL-3b, Design §RL-3b, Step 26).

This test locks the CURRENT, already-shipped upstream shape of
``DIContainer.ashutdown()`` for a **future** adoption inside
``VarcoLifespan`` — it does not drive any varco production-code change.

``VarcoLifespan`` (``varco_fastapi/varco_fastapi/lifespan.py:205-212``)
deliberately does **not** call ``container.shutdown()``/``ashutdown()``
today: it calls ``component.stop()`` per registered lifecycle component and
``_stop_all()`` logs errors but never raises. Adopting ``ashutdown()`` is
explicitly deferred to Phase 4 (RL-8) because it would newly fire every
``@PreDestroy`` hook in the container, including ones on singletons that are
not registered lifecycle components today (see BACKLOG.md's Plan 016 /
RL-3b row). This file exists purely to de-risk that future adoption by
pinning the exception shape now.

NOTE: this test may legitimately pass on arrival — it characterizes
already-shipped providify 2.0.0 behaviour (verified directly against the
installed providify source: ``exceptions.py``'s ``ShutdownError``/
``ShutdownFailure``, and ``container.py``'s ``ashutdown()``), not a
varco-side change this plan is driving. A green result here is the correct
and expected outcome, unlike the other Phase D "new file" steps which must
start red.

ADOPTED — Plan 022 / Phase 4 (RL-8a), Step 24. This file is the de-risking it
was written for: Plan 022's measurement
(``design/api-freeze-and-standards/measurements/predestroy-vs-lifespan.md``)
found **6 orphaned ``@PreDestroy`` singletons out of 10**, so §D-8a2's "adopt"
branch fired. ``VarcoLifespan`` gains a ``shutdown=`` hook that
``create_varco_app()`` fills with ``lambda: container.ashutdown()``, and per
§D-8a2(c) the aggregated ``ShutdownError`` pinned below is logged at ERROR —
one line per ``ShutdownFailure`` — rather than re-raised out of the ASGI
shutdown. The adoption's own tests live in ``test_lifespan_shutdown.py``; this
file keeps characterizing the *upstream* shape they depend on, so an upstream
providify change surfaces here rather than as a confusing failure there.

Thread safety:  N/A (unit test)
Async safety:   ✅ ``ashutdown()`` is awaited directly.
"""

from __future__ import annotations

from providify import DIContainer, PreDestroy, Singleton
from providify.exceptions import ShutdownError, ShutdownFailure


@Singleton
class _EarliestCreatedFailingComponent:
    """Registered/created FIRST — the earliest-created failing singleton."""

    @PreDestroy
    def close(self) -> None:
        raise RuntimeError("earliest-created component failed to tear down")


@Singleton
class _LaterCreatedFailingComponent:
    """Registered/created SECOND — a later-created failing singleton."""

    @PreDestroy
    def close(self) -> None:
        raise RuntimeError("later-created component failed to tear down")


async def test_ashutdown_raises_single_aggregated_shutdown_error() -> None:
    """
    Two singletons whose @PreDestroy hooks both raise must produce exactly
    ONE ShutdownError from ashutdown(), not two separate exceptions and not
    a stop-at-first-failure crash.
    """
    container = DIContainer()
    container.bind(_EarliestCreatedFailingComponent, _EarliestCreatedFailingComponent)
    container.bind(_LaterCreatedFailingComponent, _LaterCreatedFailingComponent)

    # Construct in this order so "earliest-created" is unambiguous.
    container.get(_EarliestCreatedFailingComponent)
    container.get(_LaterCreatedFailingComponent)

    raised: ShutdownError | None = None
    try:
        await container.ashutdown()
    except ShutdownError as exc:
        raised = exc

    assert raised is not None, "ashutdown() must raise ShutdownError, not swallow failures"


async def test_ashutdown_error_carries_exactly_two_shutdown_failures() -> None:
    """
    exc.failures must have exactly two ShutdownFailure entries — one per
    raising @PreDestroy hook — each carrying .owner and .exception, so a
    caller can inspect every failure rather than only the first.
    """
    container = DIContainer()
    container.bind(_EarliestCreatedFailingComponent, _EarliestCreatedFailingComponent)
    container.bind(_LaterCreatedFailingComponent, _LaterCreatedFailingComponent)
    container.get(_EarliestCreatedFailingComponent)
    container.get(_LaterCreatedFailingComponent)

    try:
        await container.ashutdown()
        raise AssertionError("expected ShutdownError")
    except ShutdownError as exc:
        assert len(exc.failures) == 2
        for failure in exc.failures:
            assert isinstance(failure, ShutdownFailure)
            assert isinstance(failure.owner, str) and failure.owner
            assert isinstance(failure.exception, BaseException)


async def test_ashutdown_error_cause_chains_to_earliest_created_component() -> None:
    """
    exc.__cause__ must chain to the exception of the EARLIEST-created
    failing component (here, _EarliestCreatedFailingComponent) — not simply
    the first one encountered during the reverse-creation-order teardown
    walk, per ShutdownError's own docstring reasoning: the earliest-created
    component is typically the most foundational one, so its failure is
    often the true root cause of failures further downstream.
    """
    container = DIContainer()
    container.bind(_EarliestCreatedFailingComponent, _EarliestCreatedFailingComponent)
    container.bind(_LaterCreatedFailingComponent, _LaterCreatedFailingComponent)
    container.get(_EarliestCreatedFailingComponent)
    container.get(_LaterCreatedFailingComponent)

    try:
        await container.ashutdown()
        raise AssertionError("expected ShutdownError")
    except ShutdownError as exc:
        assert exc.__cause__ is not None
        assert isinstance(exc.__cause__, RuntimeError)
        assert "earliest-created component failed to tear down" in str(exc.__cause__)


async def test_ashutdown_clears_singleton_cache_even_after_failures() -> None:
    """
    Edge case (plan's Edge cases table): "all hooks still run, caches still
    clear" even when every hook raised. Proven indirectly — resolving the
    same singleton binding again after ashutdown() must produce a NEW
    instance (the old cached one was evicted), rather than reusing the
    (now torn-down) singleton or raising because of the earlier failure.
    """
    container = DIContainer()
    container.bind(_EarliestCreatedFailingComponent, _EarliestCreatedFailingComponent)

    first = container.get(_EarliestCreatedFailingComponent)

    try:
        await container.ashutdown()
    except ShutdownError:
        pass

    second = container.get(_EarliestCreatedFailingComponent)

    assert first is not second, (
        "singleton cache must be cleared after ashutdown(), even though its @PreDestroy hook raised"
    )
