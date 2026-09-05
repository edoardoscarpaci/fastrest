"""`InMemoryCache` get/set (Plan 028 / Phase 3, P2).

**The cheapest benchmark in the set, and that is its purpose.** A dict write
behind an async method has essentially no varco-side cost to regress, so a
movement here is far more likely to be *harness* drift — a CodSpeed runner
change, an interpreter change, an event-loop policy change — than a varco
regression. It is the control series the other six are read against.
"""

from __future__ import annotations

import asyncio

from varco_core.cache.memory import InMemoryCache


def test_cache_set_then_get(benchmark) -> None:  # type: ignore[no-untyped-def]
    cache: InMemoryCache = InMemoryCache()

    async def _set_then_get() -> object:
        await cache.set("bench:key", {"value": 42})
        return await cache.get("bench:key")

    # asyncio.run per iteration would measure loop construction, which
    # dominates this workload entirely. One loop, reused, keeps the measured
    # region on the cache itself.
    loop = asyncio.new_event_loop()
    try:
        # start() is outside the measured region: InMemoryCache refuses reads
        # and writes until started (it owns an eviction task), and starting it
        # once is what a real application does too.
        loop.run_until_complete(cache.start())
        result = benchmark(lambda: loop.run_until_complete(_set_then_get()))
        loop.run_until_complete(cache.stop())
    finally:
        loop.close()
    assert result == {"value": 42}
