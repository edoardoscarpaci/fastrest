"""
Plan 022 / Phase 4 (RL-8a), Step 21 — repurposed as regression tests.

``design/api-freeze-and-standards/measurements/predestroy-vs-lifespan.md``
Part 2 read all ten ``stop()`` implementations and found them idempotent; this
replaces that read with a test, because §D-8a2(b) is about to make the property
load-bearing (``_stop_all()`` first, then ``container.ashutdown()`` — two
``stop()`` calls for anything on both paths).

Rows #3 (``NatsEventBus``) and #4 (``NatsStreamManager``, a confirmed orphan).
Both ``start()`` implementations open a real connection, so only the
never-started double-``stop()`` path is covered offline — the started path is
Step 25's integration test.

NOTE: may legitimately pass on arrival — a regression guard, not a red test.

Thread safety:  N/A (unit test)
Async safety:   ✅ every stop() is awaited.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from varco_nats.bus import NatsEventBus, NatsEventBusSettings
from varco_nats.channel import NatsChannelManagerSettings, NatsStreamManager

COMPONENTS: list[tuple[str, Callable[[], Any]]] = [
    ("NatsEventBus", lambda: NatsEventBus(config=NatsEventBusSettings())),
    ("NatsStreamManager", lambda: NatsStreamManager(NatsChannelManagerSettings())),
]


@pytest.mark.parametrize(("name", "factory"), COMPONENTS, ids=[n for n, _ in COMPONENTS])
async def test_stop_twice_on_never_started_component_is_a_noop(
    name: str, factory: Callable[[], Any]
) -> None:
    # A container sweep can reach a singleton the lifespan never started.
    component = factory()

    await component.stop()
    await component.stop()  # must not raise


async def test_stream_manager_sentinels_stay_cleared_after_two_stops() -> None:
    # channel.py:322/:325-326 — the sentinels that make #4 idempotent.
    manager = NatsStreamManager(NatsChannelManagerSettings())

    await manager.stop()
    await manager.stop()

    assert manager._nc is None  # noqa: SLF001
    assert manager._js is None  # noqa: SLF001
