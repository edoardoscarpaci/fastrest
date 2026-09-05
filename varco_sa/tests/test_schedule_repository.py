"""
Unit tests for varco_sa.schedule (Plan 032 / D6, Step 10).

Uses an in-memory SQLite engine (same convention as
``test_job_store_zoned.py``) — no Docker/Postgres required for the shape
this test covers (insert/update/find/delete round trips + the enabled-only
sweep). Cross-tenant/production-Postgres-specific behaviour is covered by
integration tests (not part of this red-mode plan step).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from varco_core.schedule.entity import CatchUpPolicy, Schedule
from varco_core.tz.schedule import GapPolicy, OverlapPolicy
from varco_sa.schedule import SAScheduleRepository


@pytest.fixture
async def repo():
    repository = SAScheduleRepository(url="sqlite+aiosqlite:///:memory:")
    await repository.start()
    yield repository
    await repository.stop()


def _schedule(**overrides: object) -> Schedule:
    defaults: dict[str, object] = {
        "cron_expr": "0 * * * *",
        "timezone": "UTC",
    }
    defaults.update(overrides)
    return Schedule(**defaults)  # type: ignore[arg-type]


class TestSAScheduleRepositoryRoundTrip:
    async def test_save_assigns_pk_on_insert(self, repo: SAScheduleRepository) -> None:
        schedule = _schedule()
        saved = await repo.save(schedule)
        assert saved.pk is not None
        assert saved.schedule_id == schedule.schedule_id

    async def test_find_by_id_returns_saved_schedule(self, repo: SAScheduleRepository) -> None:
        saved = await repo.save(_schedule(tenant_id="acme", cron_expr="*/5 * * * *"))
        found = await repo.find_by_id(saved.pk)
        assert found is not None
        assert found.tenant_id == "acme"
        assert found.cron_expr == "*/5 * * * *"
        assert found.gap_policy == GapPolicy.NEXT_VALID
        assert found.overlap_policy == OverlapPolicy.FIRST
        assert found.catchup_policy == CatchUpPolicy.SKIP

    async def test_find_by_id_returns_none_for_unknown_pk(self, repo: SAScheduleRepository) -> None:
        assert await repo.find_by_id(uuid4()) is None

    async def test_save_with_pk_updates_existing_row(self, repo: SAScheduleRepository) -> None:
        saved = await repo.save(_schedule(cron_expr="0 0 * * *"))
        saved.cron_expr = "0 12 * * *"
        saved.enabled = False
        updated = await repo.save(saved)
        assert updated.pk == saved.pk
        assert updated.cron_expr == "0 12 * * *"
        assert updated.enabled is False

    async def test_find_all_enabled_excludes_disabled_schedules(
        self, repo: SAScheduleRepository
    ) -> None:
        enabled = await repo.save(_schedule(enabled=True))
        await repo.save(_schedule(enabled=False))
        results = await repo.find_all_enabled()
        assert [s.pk for s in results] == [enabled.pk]

    async def test_delete_removes_the_row(self, repo: SAScheduleRepository) -> None:
        saved = await repo.save(_schedule())
        await repo.delete(saved.pk)
        assert await repo.find_by_id(saved.pk) is None

    async def test_delete_unknown_pk_is_a_noop(self, repo: SAScheduleRepository) -> None:
        await repo.delete(uuid4())  # must not raise

    async def test_payload_and_last_materialized_at_round_trip(
        self, repo: SAScheduleRepository
    ) -> None:
        anchor = datetime(2026, 1, 1, tzinfo=UTC)
        saved = await repo.save(
            _schedule(payload={"foo": "bar"}, last_materialized_at=anchor, max_backfill=5)
        )
        found = await repo.find_by_id(saved.pk)
        assert found is not None
        assert found.payload == {"foo": "bar"}
        assert found.last_materialized_at == anchor
        assert found.max_backfill == 5


async def test_require_engine_raises_before_start() -> None:
    repository = SAScheduleRepository(url="sqlite+aiosqlite:///:memory:")
    with pytest.raises(RuntimeError, match="before start"):
        await repository.find_by_id(uuid4())


async def test_schedule_metadata_uses_the_shared_engine_directly() -> None:
    # Sanity check that schedule_metadata.create_all is idempotent when the
    # table already exists (checkfirst=True) — start() twice must not raise.
    repository = SAScheduleRepository(url="sqlite+aiosqlite:///:memory:")
    await repository.start()
    await repository.start()
    await repository.stop()


async def test_engine_kwargs_forwarded_to_create_async_engine() -> None:
    repository = SAScheduleRepository(url="sqlite+aiosqlite:///:memory:", echo=False)
    await repository.start()
    engine = repository._require_engine()
    assert engine is not None
    await repository.stop()
