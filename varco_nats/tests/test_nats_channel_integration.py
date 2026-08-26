"""
Real-JetStream ``NatsStreamManager`` round-trips (Plan 018 / RT2, Step 9).

``NatsStreamManager`` (``varco_nats/varco_nats/channel.py:192-455``) had
**zero** real-broker coverage before this module — ``declare_channel`` /
``channel_exists`` / ``list_channels`` / ``delete_channel`` were exercised
only against fakes. These four tests drive each one against a live
JetStream server on the session-scoped ``nats_url`` fixture.

⚠️ Note on ``channel_exists`` semantics: the implementation defines
"exists" as *the channel's subject currently carries at least one message*
(a per-subject message count from ``stream_info``), NOT *the backing stream
was declared*. ``declare_channel`` creates the stream, not a per-channel
object — NATS has no such object. The first test asserts the plan's stated
contract (declare → exists is ``True``); if that is red, the finding is a
contract mismatch between ``ChannelManager``'s ABC docstring and the NATS
implementation, and per CLAUDE.md §Test Conventions it becomes an
``xfail(strict=True)`` + a BACKLOG row, never a source edit.

Per-test namespacing: stream names and subject prefixes carry a
``uuid4().hex[:8]`` run id, and every test deletes its stream in a
``finally`` so a shared session broker does not accumulate streams.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from varco_core.event.base import ChannelConfig
from varco_nats.channel import NatsChannelManagerSettings, NatsStreamManager

pytestmark = pytest.mark.integration


def _settings(nats_url: str, run_id: str) -> NatsChannelManagerSettings:
    return NatsChannelManagerSettings(
        servers=nats_url,
        stream_name=f"chan-{run_id}",
        subject_prefix=f"chan{run_id}",
    )


@asynccontextmanager
async def _manager(nats_url: str, run_id: str) -> AsyncIterator[NatsStreamManager]:
    """
    Start a ``NatsStreamManager`` and always tear its backing stream down.

    Yields:
        A started ``NatsStreamManager`` scoped to this test's run id.

    Edge cases:
        - The stream delete is best-effort in a ``finally``: a test that
          fails mid-assertion must still not leak a stream onto the shared
          session-scoped broker.
    """
    settings = _settings(nats_url, run_id)
    manager = NatsStreamManager(settings)
    await manager.start()
    try:
        yield manager
    finally:
        try:
            await manager._js.delete_stream(settings.stream_name)  # noqa: SLF001
        except Exception:  # noqa: BLE001 — best-effort cleanup of a shared broker
            pass
        await manager.stop()


_CHANNEL_EXISTS_ABC_GAP_REASON = (
    "BUG: NatsStreamManager.channel_exists()/list_channels() implement a "
    "'has messages' predicate, not an 'exists' predicate (channel.py:377-395, "
    ":417) — declare_channel()'s `channel` argument is documented as 'only "
    "used for logging', so a freshly declared, empty channel reports as not "
    "existing. This is an ABC-contract gap on the NATS ChannelManager "
    "implementation, not a test bug. See BACKLOG.md's RT2-C row."
)


@pytest.mark.xfail(reason=_CHANNEL_EXISTS_ABC_GAP_REASON, strict=True)
async def test_declare_channel_then_channel_exists_is_true(nats_url: str) -> None:
    """A declared channel must report as existing (§RT2-scope's stated contract)."""
    run_id = uuid.uuid4().hex[:8]
    async with _manager(nats_url, run_id) as manager:
        await manager.declare_channel("orders", ChannelConfig(replication_factor=1))

        assert await manager.channel_exists("orders") is True


@pytest.mark.xfail(reason=_CHANNEL_EXISTS_ABC_GAP_REASON, strict=True)
async def test_list_channels_contains_a_declared_channel(nats_url: str) -> None:
    """``list_channels`` must report a channel that was declared on this stream."""
    run_id = uuid.uuid4().hex[:8]
    async with _manager(nats_url, run_id) as manager:
        await manager.declare_channel("orders", ChannelConfig(replication_factor=1))

        assert "orders" in await manager.list_channels()


async def test_delete_channel_then_channel_exists_is_false(nats_url: str) -> None:
    """After ``delete_channel`` the channel must no longer report as existing."""
    run_id = uuid.uuid4().hex[:8]
    async with _manager(nats_url, run_id) as manager:
        await manager.declare_channel("orders", ChannelConfig(replication_factor=1))

        await manager.delete_channel("orders")

        assert await manager.channel_exists("orders") is False


@pytest.mark.xfail(reason=_CHANNEL_EXISTS_ABC_GAP_REASON, strict=True)
async def test_declare_channel_twice_is_idempotent(nats_url: str) -> None:
    """
    A second ``declare_channel`` for the same channel must not raise.

    Edge cases:
        - Idempotency is what makes ``declare_channel`` safe to call from
          every replica's startup path; a "stream already exists" error here
          would make a multi-pod deploy racy.
    """
    run_id = uuid.uuid4().hex[:8]
    async with _manager(nats_url, run_id) as manager:
        await manager.declare_channel("orders", ChannelConfig(replication_factor=1))
        await manager.declare_channel("orders", ChannelConfig(replication_factor=1))

        assert await manager.channel_exists("orders") is True
