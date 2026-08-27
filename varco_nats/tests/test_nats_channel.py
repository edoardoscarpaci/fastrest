"""
Unit tests for varco_nats.NatsStreamManager
============================================
All tests fake ``nats-py`` — no real NATS broker required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from tests.fakes import FakeJetStream, FakeNatsClient
from varco_nats import NatsChannelManagerSettings, NatsStreamManager

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_js() -> FakeJetStream:
    return FakeJetStream()


@pytest.fixture
def fake_nc(fake_js: FakeJetStream) -> FakeNatsClient:
    return FakeNatsClient(fake_js)


@asynccontextmanager
async def _started_manager(
    nc: FakeNatsClient,
    settings: NatsChannelManagerSettings | None = None,
) -> AsyncIterator[NatsStreamManager]:
    """Build and start a ``NatsStreamManager`` wired to ``nc``."""

    async def _fake_connect(**_: object) -> FakeNatsClient:
        return nc

    with patch("varco_nats.channel.connect", new=_fake_connect):
        async with NatsStreamManager(settings or NatsChannelManagerSettings()) as mgr:
            yield mgr


# ── Settings ──────────────────────────────────────────────────────────────────


class TestNatsChannelManagerSettings:
    def test_defaults(self) -> None:
        cfg = NatsChannelManagerSettings()
        assert cfg.servers == "nats://localhost:4222"
        assert cfg.stream_name == "varco-events"
        assert cfg.subject_prefix == "varco"

    def test_subject_name(self) -> None:
        cfg = NatsChannelManagerSettings(channel_prefix="prod.")
        assert cfg.subject_name("orders") == "varco.prod.orders"

    def test_frozen(self) -> None:
        cfg = NatsChannelManagerSettings()
        with pytest.raises(Exception):
            cfg.stream_name = "other"  # type: ignore[misc]


# ── Lifecycle ─────────────────────────────────────────────────────────────────


class TestLifecycle:
    async def test_operation_before_start_raises(self) -> None:
        mgr = NatsStreamManager(NatsChannelManagerSettings())
        with pytest.raises(RuntimeError, match="not started"):
            await mgr.list_channels()

    async def test_double_start_raises(self, fake_nc: FakeNatsClient) -> None:
        async def _fake_connect(**_: object) -> FakeNatsClient:
            return fake_nc

        with patch("varco_nats.channel.connect", new=_fake_connect):
            mgr = NatsStreamManager(NatsChannelManagerSettings())
            await mgr.start()
            with pytest.raises(RuntimeError, match="already-started"):
                await mgr.start()
            await mgr.stop()

    async def test_stop_before_start_is_noop(self) -> None:
        mgr = NatsStreamManager(NatsChannelManagerSettings())
        await mgr.stop()  # must not raise


# ── declare_channel ───────────────────────────────────────────────────────────


class TestDeclareChannel:
    async def test_declare_creates_backing_stream(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        async with _started_manager(fake_nc) as mgr:
            await mgr.declare_channel("orders")
            assert "varco-events" in fake_js.streams
            assert fake_js.streams["varco-events"].subjects == ["varco.>"]

    async def test_declare_is_idempotent(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        async with _started_manager(fake_nc) as mgr:
            await mgr.declare_channel("orders")
            await mgr.declare_channel("users")  # same backing stream — no error
            assert len(fake_js.streams) == 1


# ── channel_exists ────────────────────────────────────────────────────────────


class TestChannelExists:
    async def test_false_when_no_stream(self, fake_nc: FakeNatsClient) -> None:
        async with _started_manager(fake_nc) as mgr:
            assert await mgr.channel_exists("orders") is False

    async def test_true_when_declared_even_with_no_messages(self, fake_nc: FakeNatsClient) -> None:
        # Plan 019 / RT2-C-contract: declared-or-present, not "carries data".
        # A declared channel with zero messages must still report as
        # existing — see TestDeclarationRegistry for the full round-trip.
        async with _started_manager(fake_nc) as mgr:
            await mgr.declare_channel("orders")
            assert await mgr.channel_exists("orders") is True

    async def test_false_when_channel_never_declared_and_has_no_messages(
        self, fake_nc: FakeNatsClient
    ) -> None:
        # A channel that was never declared through this manager and has
        # never carried a message is still False — the stream may exist for
        # an unrelated reason (another channel's declaration).
        async with _started_manager(fake_nc) as mgr:
            await mgr.declare_channel("users")
            assert await mgr.channel_exists("orders") is False

    async def test_true_after_message_published(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        async with _started_manager(fake_nc) as mgr:
            await mgr.declare_channel("orders")
            await fake_js.publish("varco.orders", b"{}")
            assert await mgr.channel_exists("orders") is True


# ── list_channels ─────────────────────────────────────────────────────────────


class TestListChannels:
    async def test_empty_when_no_stream(self, fake_nc: FakeNatsClient) -> None:
        async with _started_manager(fake_nc) as mgr:
            assert await mgr.list_channels() == []

    async def test_lists_channels_with_messages(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        async with _started_manager(fake_nc) as mgr:
            await mgr.declare_channel("orders")
            await fake_js.publish("varco.orders", b"{}")
            await fake_js.publish("varco.users", b"{}")
            assert await mgr.list_channels() == ["orders", "users"]


# ── delete_channel ────────────────────────────────────────────────────────────


class TestDeleteChannel:
    async def test_delete_purges_channel_messages(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        async with _started_manager(fake_nc) as mgr:
            await mgr.declare_channel("orders")
            await fake_js.publish("varco.orders", b"{}")
            await fake_js.publish("varco.users", b"{}")

            await mgr.delete_channel("orders")

            # Only the orders channel is purged; users is untouched.
            assert await mgr.channel_exists("orders") is False
            assert await mgr.channel_exists("users") is True


# ── Declaration registry (Plan 019 / §RT2-C-contract, Step 18) ───────────────
#
# NatsStreamManager tracks a process-local ``_declared`` registry so
# channel_exists()/list_channels()/delete_channel() satisfy the ABC's
# declared-or-present contract (implemented in channel.py, Step 15).


class TestDeclarationRegistry:
    async def test_declare_then_exists_true_with_empty_stream(
        self, fake_nc: FakeNatsClient
    ) -> None:
        # A freshly declared channel that has never carried a message must
        # still report as existing — the declare→exists round-trip.
        async with _started_manager(fake_nc) as mgr:
            await mgr.declare_channel("orders")
            assert await mgr.channel_exists("orders") is True

    async def test_delete_then_not_exists_even_while_subject_carries_messages(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        # A subject can still carry messages elsewhere (e.g. published by
        # another process) after the registry entry is discarded — the
        # registry discard must win over lingering broker evidence.
        async with _started_manager(fake_nc) as mgr:
            await mgr.declare_channel("orders")
            await fake_js.publish("varco.orders", b"{}")

            await mgr.delete_channel("orders")

            assert await mgr.channel_exists("orders") is False

    async def test_channel_has_messages_preserves_old_semantics(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        # channel_has_messages() is the honestly-named old "has messages"
        # predicate — False before any publish, True after, independent of
        # the declaration registry.
        async with _started_manager(fake_nc) as mgr:
            await mgr.declare_channel("orders")
            assert await mgr.channel_has_messages("orders") is False

            await fake_js.publish("varco.orders", b"{}")
            assert await mgr.channel_has_messages("orders") is True

    async def test_list_channels_unions_registry_and_subjects_without_duplicates(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        # "orders" is declared but empty; "users" carries a message but was
        # never declared through this manager; "orders" must not appear
        # twice even though it is in both the registry and (after a second
        # publish) the subject-count evidence.
        async with _started_manager(fake_nc) as mgr:
            await mgr.declare_channel("orders")
            await fake_js.publish("varco.orders", b"{}")
            await fake_js.publish("varco.users", b"{}")

            channels = await mgr.list_channels()

            assert channels == ["orders", "users"]
