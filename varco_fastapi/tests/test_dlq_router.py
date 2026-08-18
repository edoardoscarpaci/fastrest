"""
tests.test_dlq_router
=======================
Plan 009, Phase 10 (R6) — varco_fastapi.admin.dlq_router / mount.

RED until ``varco_fastapi/admin/dlq_router.py`` and ``admin/mount.py`` land.

Covers RD-9 (``mount_reliability_admin`` refuses to mount without
``acknowledge_bundled_admin=True``) and the capability-gap → 501 mapping
(RD-4) for a stream-shaped DLQ.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from varco_core.event.dlq import DeadLetterEntry, InMemoryDeadLetterQueue
from varco_core.event import Event


class SampleEvent(Event):
    __event_type__ = "test.dlq_router.sample"


class TestMountReliabilityAdminAcknowledgement:
    def test_mount_without_acknowledgement_raises_value_error(self) -> None:
        from varco_fastapi.admin.mount import mount_reliability_admin

        app = FastAPI()
        dlq = InMemoryDeadLetterQueue()

        with pytest.raises(ValueError, match="acknowledge_bundled_admin"):
            mount_reliability_admin(app, dlq=dlq)

    def test_mount_with_acknowledgement_succeeds(self) -> None:
        from varco_fastapi.admin.mount import mount_reliability_admin

        app = FastAPI()
        dlq = InMemoryDeadLetterQueue()

        mount_reliability_admin(app, dlq=dlq, acknowledge_bundled_admin=True)

        client = TestClient(app)
        resp = client.get("/reliability/dlq/entries")
        assert resp.status_code != 404


class TestDlqRouterCapabilityGap:
    def test_redrive_route_absent_without_redriver(self) -> None:
        """RD-4/DESIGN: an absent capability should not appear in the OpenAPI
        schema at all -- not surface as 501."""
        from varco_fastapi.admin.dlq_router import build_dlq_router

        router = build_dlq_router(InMemoryDeadLetterQueue(), redriver=None)
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.post(f"/dlq/entries/{uuid.uuid4()}/redrive")
        assert resp.status_code == 404

    async def test_stream_backend_single_entry_get_returns_501(self) -> None:
        from varco_core.event.dlq import AbstractDeadLetterQueue
        from varco_fastapi.admin.dlq_router import build_dlq_router

        class _StreamDLQ(AbstractDeadLetterQueue):
            supports_random_access = False

            async def push(self, entry: DeadLetterEntry) -> None: ...
            async def pop_batch(self, *, limit: int = 10) -> list[DeadLetterEntry]:
                return []

            async def ack(self, entry_id) -> None: ...
            async def count(self) -> int:
                return 0

            async def get(self, entry_id):
                raise NotImplementedError("stream-backed DLQ has no random access")

        router = build_dlq_router(_StreamDLQ())
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get(f"/dlq/entries/{uuid.uuid4()}")
        assert resp.status_code == 501


class TestDlqRouterStats:
    async def test_stats_returns_count(self) -> None:
        from varco_fastapi.admin.dlq_router import build_dlq_router

        dlq = InMemoryDeadLetterQueue()
        await dlq.push(
            DeadLetterEntry(
                event=SampleEvent(),
                channel="orders",
                handler_name="H.h",
                error_type="E",
                error_message="msg",
                attempts=1,
            )
        )
        router = build_dlq_router(dlq)
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/dlq/stats")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
