"""
Unit tests for varco_beanie.schedule (Plan 032 / D6, Step 10).

Covers ``ScheduleDocument`` field defaults and ``BeanieScheduleRepository``
using mocked Beanie operations — no MongoDB connection required (same
convention as ``test_beanie_job_store.py``). The conftest
``bypass_beanie_collection_check`` fixture allows instantiating
``ScheduleDocument`` without ``init_beanie()``.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from varco_beanie.schedule import BeanieScheduleRepository, ScheduleDocument
from varco_core.schedule.entity import CatchUpPolicy, Schedule
from varco_core.tz.schedule import GapPolicy, OverlapPolicy


def _doc(**overrides: Any) -> ScheduleDocument:
    now = datetime.now(UTC)
    defaults: dict[str, Any] = {
        "cron_expr": "0 * * * *",
        "timezone": "UTC",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return ScheduleDocument(**defaults)


@contextmanager
def _patch_doc_attrs(**overrides: Any) -> Generator[None, None, None]:
    """Class-attribute patch/restore — same pattern as ``test_beanie_job_store.py``
    (Beanie's model metaclass intercepts plain ``setattr`` for field names)."""
    originals: dict[str, Any] = {}
    for name in overrides:
        originals[name] = ScheduleDocument.__dict__.get(name)
    try:
        for name, value in overrides.items():
            setattr(ScheduleDocument, name, value)
        yield
    finally:
        for name, orig in originals.items():
            if orig is not None:
                setattr(ScheduleDocument, name, orig)
            elif hasattr(ScheduleDocument, name):
                delattr(ScheduleDocument, name)


class TestScheduleDocument:
    def test_collection_name(self) -> None:
        assert ScheduleDocument.Settings.name == "schedules"

    def test_defaults(self) -> None:
        doc = _doc()
        assert doc.enabled is True
        assert doc.gap_policy == GapPolicy.NEXT_VALID.value
        assert doc.overlap_policy == OverlapPolicy.FIRST.value
        assert doc.catchup_policy == CatchUpPolicy.SKIP.value
        assert doc.max_backfill == 100
        assert doc.payload == {}
        assert doc.tenant_id is None
        assert doc.callback_url is None

    def test_schedule_id_is_unique_per_instance(self) -> None:
        assert _doc().schedule_id != _doc().schedule_id


class TestDocToEntity:
    def test_maps_every_field(self) -> None:
        anchor = datetime(2026, 1, 1, tzinfo=UTC)
        doc = _doc(
            tenant_id="acme",
            cron_expr="*/5 * * * *",
            payload={"foo": "bar"},
            last_materialized_at=anchor,
            max_backfill=7,
        )
        repo = BeanieScheduleRepository(url="mongodb://unused", db_name="unused")
        entity = repo._doc_to_entity(doc)
        assert isinstance(entity, Schedule)
        assert entity.pk == doc.id
        assert entity.schedule_id == doc.schedule_id
        assert entity.tenant_id == "acme"
        assert entity.cron_expr == "*/5 * * * *"
        assert entity.payload == {"foo": "bar"}
        assert entity.last_materialized_at == anchor
        assert entity.max_backfill == 7
        assert entity.gap_policy == GapPolicy.NEXT_VALID
        assert entity.overlap_policy == OverlapPolicy.FIRST
        assert entity.catchup_policy == CatchUpPolicy.SKIP


class TestBeanieScheduleRepositorySave:
    async def test_save_without_pk_inserts_a_new_document(self) -> None:
        repo = BeanieScheduleRepository(url="mongodb://unused", db_name="unused")
        schedule = Schedule(cron_expr="0 * * * *", timezone="UTC")

        inserted: list[ScheduleDocument] = []

        async def fake_insert(self: ScheduleDocument) -> None:
            inserted.append(self)

        with _patch_doc_attrs(insert=fake_insert):
            saved = await repo.save(schedule)

        assert len(inserted) == 1
        assert saved.schedule_id == schedule.schedule_id
        assert schedule.pk is not None

    async def test_save_with_pk_updates_the_existing_document(self) -> None:
        repo = BeanieScheduleRepository(url="mongodb://unused", db_name="unused")
        existing = _doc(cron_expr="0 0 * * *")
        schedule = Schedule(
            cron_expr="0 12 * * *",
            timezone="UTC",
            schedule_id=existing.schedule_id,
        )
        schedule.pk = existing.id

        saved_docs: list[ScheduleDocument] = []

        async def fake_save(self: ScheduleDocument) -> None:
            saved_docs.append(self)

        with (
            _patch_doc_attrs(get=AsyncMock(return_value=existing), save=fake_save),
        ):
            result = await repo.save(schedule)

        assert len(saved_docs) == 1
        assert saved_docs[0].cron_expr == "0 12 * * *"
        assert result.cron_expr == "0 12 * * *"


class TestBeanieScheduleRepositoryFind:
    async def test_find_by_id_returns_none_for_unknown_pk(self) -> None:
        repo = BeanieScheduleRepository(url="mongodb://unused", db_name="unused")
        with _patch_doc_attrs(get=AsyncMock(return_value=None)):
            assert await repo.find_by_id(uuid4()) is None

    async def test_find_by_id_maps_the_found_document(self) -> None:
        repo = BeanieScheduleRepository(url="mongodb://unused", db_name="unused")
        doc = _doc(tenant_id="acme")
        with _patch_doc_attrs(get=AsyncMock(return_value=doc)):
            found = await repo.find_by_id(doc.id)
        assert found is not None
        assert found.tenant_id == "acme"

    async def test_find_all_enabled_delegates_to_find(self) -> None:
        repo = BeanieScheduleRepository(url="mongodb://unused", db_name="unused")
        enabled_doc = _doc(enabled=True)

        find_result = MagicMock()
        find_result.to_list = AsyncMock(return_value=[enabled_doc])
        fake_find = MagicMock(return_value=find_result)

        # ScheduleDocument.enabled (an ExpressionField descriptor normally
        # installed by init_beanie()) doesn't exist pre-init — a plain
        # MagicMock stands in so `ScheduleDocument.enabled == True` (the
        # filter expression find_all_enabled() builds) doesn't raise.
        with _patch_doc_attrs(find=fake_find, enabled=MagicMock()):
            results = await repo.find_all_enabled()

        assert len(results) == 1
        assert results[0].schedule_id == enabled_doc.schedule_id


class TestBeanieScheduleRepositoryDelete:
    async def test_delete_deletes_the_document(self) -> None:
        repo = BeanieScheduleRepository(url="mongodb://unused", db_name="unused")
        doc = _doc()
        deleted: list[ScheduleDocument] = []

        async def fake_delete(self: ScheduleDocument) -> None:
            deleted.append(self)

        with _patch_doc_attrs(get=AsyncMock(return_value=doc), delete=fake_delete):
            await repo.delete(doc.id)

        assert deleted == [doc]

    async def test_delete_unknown_pk_is_a_noop(self) -> None:
        repo = BeanieScheduleRepository(url="mongodb://unused", db_name="unused")
        with _patch_doc_attrs(get=AsyncMock(return_value=None)):
            await repo.delete(uuid4())  # must not raise
