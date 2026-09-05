"""`InMemoryEventBus.publish()` + `drain()` (Plan 028 / Phase 3, P2).

Fan-out cost with no broker in the picture: one publish reaching three
subscribed handlers. This isolates varco's own dispatch machinery — type-based
routing, middleware chain, error policy — from any network or serialization
cost, which is the only part of "publishing an event" varco actually controls.
"""

from __future__ import annotations

import asyncio

from varco_core.event import Event, InMemoryEventBus


class _OrderPlaced(Event):
    """Minimal concrete event — two scalar fields, no nesting."""

    __event_type__ = "bench.order.placed"

    order_id: str = ""
    total: float = 0.0


async def _noop_handler(event: Event) -> None:
    """Deliberately empty: the benchmark measures dispatch, not handler work."""


def test_publish_to_three_handlers(benchmark) -> None:  # type: ignore[no-untyped-def]
    bus = InMemoryEventBus()
    for _ in range(3):
        bus.subscribe(_OrderPlaced, _noop_handler)
    event = _OrderPlaced(order_id="ord_1", total=9.99)

    async def _publish() -> None:
        await bus.publish(event, channel="bench")
        await bus.drain()

    loop = asyncio.new_event_loop()
    try:
        benchmark(lambda: loop.run_until_complete(_publish()))
    finally:
        loop.close()
