"""
``CircuitBreaker`` against a **black-holed** real dependency
(Plan 018 / RT7b, Step 32 — chaos tier).

Mechanism (§RT7-shape): ``docker pause`` / ``unpause``, not stop/start.
A paused container's processes are frozen, so an in-flight request **hangs
with no RST** — strictly the *harder* failure mode. A closed port fails
fast; a black hole is what actually takes production down, and it is what
``@timeout`` + ``CircuitBreaker`` exist for. This is the one thing the
existing ``test_breaker_chaos_integration.py`` (which stops the container,
producing a fast connection-refused) does not cover.

Relationship to §RT7-toxiproxy: ``pause`` cannot express *graded latency*,
bandwidth throttling, or one-directional faults — those are the Toxiproxy
capabilities deliberately deferred to 3.1. What ``pause`` does buy is a
genuine timeout-driven failure, which is all these assertions need.

Container scope (§chaos-fixture): a **module**-scoped
``redis_container_chaos`` declared here, never in ``conftest.py`` — pausing
the session-scoped ``redis_url`` container would freeze it under every other
test in ``varco_redis/tests/``. ``ChaosContainer.paused()`` unpauses in a
``finally``, so a failed assertion never leaves a frozen container behind
for the rest of the module.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator

import pytest
import redis.asyncio as aioredis
from varco_chaos.containers import ChaosContainer
from varco_core.resilience import CircuitBreaker, CircuitBreakerConfig
from varco_core.resilience.circuit_breaker import CircuitOpenError, CircuitState

pytestmark = [pytest.mark.integration, pytest.mark.chaos]

_CALL_TIMEOUT = 1.0
"""Short client timeout so a black-holed call surfaces as a failure quickly
rather than waiting out redis-py's much longer defaults."""

_FAILURE_THRESHOLD = 3
_RECOVERY_TIMEOUT = 3.0

_CHAOS_ENDPOINT: dict[str, tuple[str, int]] = {}


@pytest.fixture(scope="module")
def redis_container_chaos() -> Iterator[ChaosContainer]:
    """
    A Redis container this module is allowed to pause.

    Yields:
        A ``ChaosContainer`` wrapping a Redis server.

    Edge cases:
        - Module-scoped. ``paused()``'s ``finally``-unpause contract is what
          makes that safe; if this module ever leaves the container wedged,
          §chaos-fixture's named fallback is to drop it to ``function``
          scope, never to add cleanup cleverness to ``ChaosContainer``.
    """
    from testcontainers.redis import RedisContainer  # noqa: PLC0415

    with RedisContainer() as container:
        _CHAOS_ENDPOINT["redis"] = (
            container.get_container_host_ip(),
            int(container.get_exposed_port(6379)),
        )
        yield ChaosContainer(container, ready=lambda logs: "Ready to accept connections" in logs)


async def test_circuit_breaker_opens_when_the_dependency_black_holes(
    redis_container_chaos: ChaosContainer,
) -> None:
    """
    A shared breaker opens on a black-holed dependency, then fails fast, then
    recovers HALF_OPEN → CLOSED once the dependency returns.

    Asserts, in order:
      1. Baseline: CLOSED, and a real ``PING`` succeeds.
      2. Inside ``paused()``: ``failure_threshold`` real timeouts drive the
         breaker to OPEN.
      3. Still paused: the next call raises ``CircuitOpenError`` and returns
         **measurably faster** than the client timeout — proving it never
         touched the frozen dependency. The elapsed-time bound carries a
         generous margin (§RT7-toxiproxy: ``pause``-based testing can assert
         timeout/failure, never a precise latency threshold).
      4. After ``wait_ready()`` and ``recovery_timeout``: a probe closes it.

    The breaker is constructed **once** and reused for every call —
    CLAUDE.md's per-call-``CircuitBreaker`` pitfall: a fresh instance per
    call never accumulates enough failures to open.

    Edge cases:
        - The unique ``name=`` keeps this breaker's logging distinguishable
          from the session's other breaker tests.
    """
    chaos = redis_container_chaos
    host, port = _CHAOS_ENDPOINT["redis"]

    client = aioredis.Redis(
        host=host,
        port=port,
        socket_connect_timeout=_CALL_TIMEOUT,
        socket_timeout=_CALL_TIMEOUT,
    )
    breaker = CircuitBreaker(
        CircuitBreakerConfig(
            failure_threshold=_FAILURE_THRESHOLD,
            recovery_timeout=_RECOVERY_TIMEOUT,
            success_threshold=1,
            monitored_on=(Exception,),
        ),
        name=f"redis-blackhole-{uuid.uuid4().hex[:8]}",
    )

    async def ping() -> bool:
        return await client.ping()

    try:
        # (1) Baseline.
        assert breaker.state == CircuitState.CLOSED
        assert await breaker.call_async(ping) is True

        with chaos.paused():
            # (2) Drive real timeouts through the breaker.
            for _ in range(_FAILURE_THRESHOLD):
                with pytest.raises(Exception):  # noqa: B017 — a real timeout/connection error
                    await breaker.call_async(ping)

            assert breaker.state == CircuitState.OPEN, (
                f"{_FAILURE_THRESHOLD} real black-holed calls did not open the "
                f"breaker (state={breaker.state})"
            )

            # (3) Fail fast — no further attempt against the frozen container.
            started = time.monotonic()
            with pytest.raises(CircuitOpenError):
                await breaker.call_async(ping)
            elapsed = time.monotonic() - started

            assert elapsed < _CALL_TIMEOUT / 2, (
                f"an OPEN breaker took {elapsed:.3f}s — it attempted the call "
                f"instead of short-circuiting (client timeout is {_CALL_TIMEOUT}s)"
            )

        # (4) Dependency is back: HALF_OPEN probe closes the circuit.
        chaos.wait_ready()
        deadline = time.monotonic() + _RECOVERY_TIMEOUT + 10.0
        while time.monotonic() < deadline:
            try:
                assert await breaker.call_async(ping) is True
                break
            except Exception:  # noqa: BLE001 — CircuitOpenError or a still-recovering server
                # Poll to the deadline; recovery_timeout must elapse before
                # the breaker will admit a HALF_OPEN probe at all.
                await asyncio.sleep(0.25)
        else:
            pytest.fail("the breaker never accepted a probe call after recovery")

        assert breaker.state == CircuitState.CLOSED, (
            f"a successful probe did not close the breaker (state={breaker.state})"
        )
    finally:
        await client.aclose()
