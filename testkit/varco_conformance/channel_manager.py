"""
ChannelManagerConformance — shared contract tests for ``ChannelManager``
implementations (Plan 019 / §RT2-C-contract, Step 11).

The contract enforced here is the round-trip invariant
``varco_core/varco_core/event/channel.py`` documents:
``declare_channel(c)`` implies ``channel_exists(c)`` is ``True`` until
``delete_channel(c)``. Backends without a per-channel broker object (Redis
Pub/Sub, NATS subjects) satisfy this via a **process-local declaration
registry** — a manager in another process may report ``False`` for a
channel it never itself declared and that has never carried a message. This
is documented, not a violation (mirrors Redis's long-standing behaviour).

Subclass and override the ``manager`` fixture to opt a backend in::

    from varco_conformance.channel_manager import ChannelManagerConformance

    class TestNatsChannelManagerConformance(ChannelManagerConformance):
        @pytest.fixture
        async def manager(self, nats_url):
            async with NatsStreamManager(settings) as manager:
                yield manager

Not named ``Test*`` — never collected standalone (see package docstring).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from varco_core.event.channel import ChannelManager


class ChannelManagerConformance:
    """
    Shared behavioural contract for ``ChannelManager``.

    Every test below namespaces its channel name with a fresh ``uuid4()``
    suffix so backends whose ``manager`` fixture is backed by a shared
    session-scoped broker never collide across tests.
    """

    @pytest.fixture
    async def manager(self) -> ChannelManager:
        """Abstract — must be overridden by every subclass."""
        raise NotImplementedError(
            "ChannelManagerConformance subclasses must override the `manager` "
            "fixture with a concrete, started ChannelManager implementation."
        )

    def _channel(self) -> str:
        return f"conformance-{uuid4().hex[:8]}"

    async def test_declare_then_exists_is_true(self, manager: ChannelManager) -> None:
        channel = self._channel()
        await manager.declare_channel(channel)

        assert await manager.channel_exists(channel) is True

    async def test_declare_then_list_channels_contains_it(self, manager: ChannelManager) -> None:
        channel = self._channel()
        await manager.declare_channel(channel)

        assert channel in await manager.list_channels()

    async def test_delete_then_not_exists(self, manager: ChannelManager) -> None:
        channel = self._channel()
        await manager.declare_channel(channel)

        await manager.delete_channel(channel)

        assert await manager.channel_exists(channel) is False

    async def test_declare_twice_is_idempotent_and_still_exists(
        self, manager: ChannelManager
    ) -> None:
        channel = self._channel()
        await manager.declare_channel(channel)
        await manager.declare_channel(channel)  # must not raise

        assert await manager.channel_exists(channel) is True
