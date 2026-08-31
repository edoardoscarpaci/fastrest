"""
Plan 022 / Phase 4 (RL-8a), Step 21 — repurposed as regression tests.

``design/api-freeze-and-standards/measurements/predestroy-vs-lifespan.md``
Part 2 read all ten ``stop()`` implementations and found them idempotent; this
replaces that read with a test, because §D-8a2(b) is about to make the property
load-bearing (``_stop_all()`` first, then ``container.ashutdown()`` — two
``stop()`` calls for anything on both paths).

Rows #1 (``KafkaEventBus``) and #2 (``KafkaChannelManager``, a confirmed
orphan).  Both ``start()`` implementations open a real connection, so only the
never-started double-``stop()`` path is covered offline — the started path is
Step 25's integration test, and faking a connection here would test the fake.

NOTE: may legitimately pass on arrival — a regression guard, not a red test.

Thread safety:  N/A (unit test)
Async safety:   ✅ every stop() is awaited.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from varco_kafka.bus import KafkaEventBus, KafkaEventBusSettings
from varco_kafka.channel import KafkaChannelManager, KafkaChannelManagerSettings

COMPONENTS: list[tuple[str, Callable[[], Any]]] = [
    ("KafkaEventBus", lambda: KafkaEventBus(config=KafkaEventBusSettings())),
    ("KafkaChannelManager", lambda: KafkaChannelManager(KafkaChannelManagerSettings())),
]


@pytest.mark.parametrize(("name", "factory"), COMPONENTS, ids=[n for n, _ in COMPONENTS])
async def test_stop_twice_on_never_started_component_is_a_noop(
    name: str, factory: Callable[[], Any]
) -> None:
    # A container sweep can reach a singleton the lifespan never started.
    component = factory()

    await component.stop()
    await component.stop()  # must not raise


async def test_channel_manager_admin_sentinel_stays_cleared_after_two_stops() -> None:
    # channel.py:237/:240 — the sentinel that makes #2 idempotent IS the contract.
    manager = KafkaChannelManager(KafkaChannelManagerSettings())

    await manager.stop()
    await manager.stop()

    assert manager._admin is None  # noqa: SLF001
