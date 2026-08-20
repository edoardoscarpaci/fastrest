"""
Real-network CircuitBreaker chaos test (Plan 012 / RT7, Step 30).

``CircuitBreaker`` against a **real** network failure rather than a raised
mock: point a client at a real Redis container, stop the container, assert
the shared breaker transitions CLOSED -> OPEN after ``failure_threshold``
real failures and that calls then fail fast (measurably faster than the
connect timeout), then restart the container and assert HALF_OPEN -> CLOSED
recovery after ``recovery_timeout``.

Uses a dedicated, function-scoped ``redis_container_fresh`` fixture
(``tests/conftest.py``) rather than the session-scoped ``redis_url`` —
this test stops the underlying container, which would break every other
test sharing the session-scoped container.

Container stop/start via ``container.get_wrapped_container().stop()/start()``
(testcontainers' docker-py handle — plan's ASSUMPTION A-2), confirmed to
work locally.
"""

from __future__ import annotations

import time

import pytest
import redis.asyncio as aioredis

from varco_core.resilience import CircuitBreaker, CircuitBreakerConfig
from varco_core.resilience.circuit_breaker import CircuitOpenError, CircuitState

pytestmark = pytest.mark.integration


async def test_breaker_opens_on_real_disconnect_and_recovers_after_restart(
    redis_container_fresh,
) -> None:
    container = redis_container_fresh
    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(6379))

    # A short connect/socket timeout so a real network failure surfaces
    # quickly and "calls then fail fast" is meaningfully measurable, rather
    # than waiting out redis-py's much longer default timeouts.
    client = aioredis.Redis(
        host=host, port=port, socket_connect_timeout=1.0, socket_timeout=1.0
    )

    # Shared breaker instance for the whole test (CLAUDE.md's per-call-breaker
    # pitfall) — one CircuitBreaker per external dependency, reused across
    # every call below.
    breaker = CircuitBreaker(
        CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=3.0,
            success_threshold=1,
            monitored_on=(Exception,),
        ),
        name="redis-chaos-test",
    )

    async def ping() -> bool:
        return await client.ping()

    # Baseline: the circuit is closed and the real broker is reachable.
    assert breaker.state == CircuitState.CLOSED
    assert await breaker.call_async(ping) is True

    # Kill the broker mid-test — a real network failure, not a raised mock.
    wrapped = container.get_wrapped_container()
    wrapped.stop()

    try:
        # Drive failure_threshold real connection failures through the
        # breaker. Each call pays the (short) connect timeout until the
        # circuit actually opens.
        for _ in range(3):
            with pytest.raises(Exception):  # noqa: B017 — real redis/connection error
                await breaker.call_async(ping)

        assert breaker.state == CircuitState.OPEN

        # Once OPEN, calls must fail fast — CircuitOpenError, not another
        # real (slow) connection attempt. Measurably faster than the
        # 1s connect timeout configured above.
        start = time.monotonic()
        with pytest.raises(CircuitOpenError):
            await breaker.call_async(ping)
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, f"OPEN-state call took {elapsed:.3f}s — not failing fast"

        # Restart the broker and wait out recovery_timeout, then the next
        # call is the HALF_OPEN probe.
        wrapped.start()

        # Docker reassigns a NEW random host port on this restart (observed
        # in this environment — the container's port mapping is not
        # preserved across stop()/start(), unlike a typical `docker restart`).
        # Re-resolve the mapped port from the container after it comes back
        # up rather than reusing the pre-stop value, and rebuild the client
        # against it.
        wrapped.reload()
        new_port = int(container.get_exposed_port(6379))
        if new_port != port:
            await client.aclose()
            client = aioredis.Redis(
                host=host,
                port=new_port,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )

        # Generous margin: container restart + Redis re-accepting
        # connections + the breaker's own recovery_timeout, all real.
        deadline = time.monotonic() + 30.0
        last_exc: Exception | None = None
        recovered = False
        while time.monotonic() < deadline:
            try:
                result = await breaker.call_async(ping)
            except CircuitOpenError:
                # Still within recovery_timeout — keep waiting.
                pass
            except Exception as exc:  # noqa: BLE001 — broker may still be booting
                last_exc = exc
            else:
                if result is True and breaker.state == CircuitState.CLOSED:
                    recovered = True
                    break
            import asyncio  # noqa: PLC0415

            await asyncio.sleep(0.5)

        assert recovered, (
            f"breaker never recovered to CLOSED within 30s; "
            f"final state={breaker.state}, last_exc={last_exc!r}"
        )
    finally:
        # Best-effort: ensure the container is running before the fixture's
        # own teardown (RedisContainer.__exit__ stop/cleanup) runs.
        try:
            wrapped.start()
        except Exception:  # noqa: BLE001 — already running is fine
            pass
        await client.aclose()
