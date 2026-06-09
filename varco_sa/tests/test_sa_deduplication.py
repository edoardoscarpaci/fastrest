"""
Unit and integration tests for varco_sa.deduplication
======================================================
Covers ``SADeduplicator`` and ``SADedupConfig``.

Unit tests run against a mock ``AsyncEngine`` — no database connection needed.
Integration tests use an in-memory SQLite database (aiosqlite) — no Docker
or PostgreSQL instance required.

Sections
--------
- ``SADedupConfig``         — defaults, frozen dataclass, custom ttl
- ``SADeduplicator`` unit   — mocked engine; is_duplicate, mark_seen, ensure_table,
                              purge_expired, error handling
- ``SADeduplicator`` integration — real SQLite; full lifecycle + TTL boundary

📚 Docs
- 🐍 https://docs.python.org/3/library/unittest.mock.html
  unittest.mock — AsyncMock, MagicMock, patch
- 🔍 https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
  SQLAlchemy asyncio — create_async_engine, AsyncEngine
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from varco_sa.deduplication import SADedupConfig, SADeduplicator, dedup_metadata


# ── Helpers ────────────────────────────────────────────────────────────────────


def _eid() -> uuid.UUID:
    """Return a fresh random UUID for use as an event_id."""
    return uuid.uuid4()


# ── SADedupConfig ─────────────────────────────────────────────────────────────


class TestSADedupConfig:
    def test_default_ttl(self) -> None:
        """Default TTL is 24 hours (86 400 seconds)."""
        cfg = SADedupConfig()
        assert cfg.ttl_seconds == 86_400

    def test_custom_ttl(self) -> None:
        """Custom TTL is stored correctly."""
        cfg = SADedupConfig(ttl_seconds=3600)
        assert cfg.ttl_seconds == 3600

    def test_frozen(self) -> None:
        """Config is immutable — setting an attribute raises FrozenInstanceError."""
        cfg = SADedupConfig()
        with pytest.raises(Exception):
            cfg.ttl_seconds = 1  # type: ignore[misc]

    def test_equality(self) -> None:
        """Two configs with the same TTL are equal (frozen dataclass)."""
        assert SADedupConfig(ttl_seconds=100) == SADedupConfig(ttl_seconds=100)


# ── Unit tests — mocked engine ────────────────────────────────────────────────


class TestSADeduplicatorUnit:
    """Tests that mock the AsyncEngine to avoid a real database connection."""

    def _make_dedup(self, ttl: int = 86_400) -> tuple[SADeduplicator, MagicMock]:
        """
        Build an SADeduplicator with a fully-mocked AsyncEngine.

        Returns both the deduplicator and the mock engine so tests can
        assert on method calls.
        """
        engine = MagicMock()
        # begin() and connect() return async context managers that yield a mock
        # connection.  The connection's execute() returns a mock result.
        conn_mock = AsyncMock()
        async_ctx = AsyncMock()
        async_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        engine.begin = MagicMock(return_value=async_ctx)
        engine.connect = MagicMock(return_value=async_ctx)

        dedup = SADeduplicator(engine, config=SADedupConfig(ttl_seconds=ttl))
        return dedup, conn_mock

    # ── is_duplicate ──────────────────────────────────────────────────────────

    async def test_is_duplicate_returns_false_for_unknown_event(self) -> None:
        """is_duplicate returns False when the SELECT returns no row."""
        dedup, conn = self._make_dedup()
        # Simulate empty SELECT result — fetchone() returns None.
        result_mock = MagicMock()
        result_mock.fetchone.return_value = None
        conn.execute = AsyncMock(return_value=result_mock)

        assert await dedup.is_duplicate(_eid()) is False

    async def test_is_duplicate_returns_true_when_row_exists(self) -> None:
        """is_duplicate returns True when the SELECT returns a row."""
        dedup, conn = self._make_dedup()
        # Simulate a row returned — fetchone() returns a truthy mock row.
        result_mock = MagicMock()
        result_mock.fetchone.return_value = MagicMock()  # any non-None value
        conn.execute = AsyncMock(return_value=result_mock)

        assert await dedup.is_duplicate(_eid()) is True

    async def test_is_duplicate_returns_false_on_db_error(self) -> None:
        """is_duplicate swallows DB errors and returns False (safe default)."""
        dedup, conn = self._make_dedup()
        conn.execute = AsyncMock(side_effect=RuntimeError("DB is down"))

        # Must not raise; must return False (process the event anyway).
        result = await dedup.is_duplicate(_eid())
        assert result is False

    # ── mark_seen ─────────────────────────────────────────────────────────────

    async def test_mark_seen_executes_insert(self) -> None:
        """mark_seen fires an INSERT with the given event_id and a future expires_at."""
        dedup, conn = self._make_dedup(ttl=3600)
        before = datetime.now(UTC)
        conn.execute = AsyncMock()

        event_id = _eid()
        await dedup.mark_seen(event_id)

        # Verify that execute was called exactly once.
        conn.execute.assert_awaited_once()
        # Inspect the INSERT statement's compiled params for expires_at.
        call_args = conn.execute.call_args
        stmt = call_args.args[0]
        # The statement should carry expires_at as a bound parameter.
        compiled = stmt.compile(compile_kwargs={"literal_binds": False})
        params = compiled.params
        # event_id and expires_at must be present.
        assert "event_id" in params
        assert params["event_id"] == event_id
        assert "expires_at" in params
        after = datetime.now(UTC)
        # expires_at should be roughly now + 3600 seconds.
        expires_at = params["expires_at"]
        # Allow generous tolerance for test execution time.
        assert (
            before + timedelta(seconds=3599)
            <= expires_at
            <= after + timedelta(seconds=3601)
        )

    async def test_mark_seen_does_not_raise_on_db_error(self) -> None:
        """mark_seen swallows all DB errors — MUST NOT raise."""
        dedup, conn = self._make_dedup()
        conn.execute = AsyncMock(side_effect=RuntimeError("connection refused"))

        # Must return None, never raise.
        result = await dedup.mark_seen(_eid())
        assert result is None

    async def test_mark_seen_does_not_raise_on_integrity_error(self) -> None:
        """mark_seen swallows IntegrityError (conflict) — idempotent."""
        from sqlalchemy.exc import IntegrityError

        dedup, conn = self._make_dedup()
        conn.execute = AsyncMock(
            side_effect=IntegrityError("INSERT ... ON CONFLICT", {}, Exception())
        )

        # Must not raise — duplicate inserts are expected and harmless.
        result = await dedup.mark_seen(_eid())
        assert result is None

    # ── ensure_table ──────────────────────────────────────────────────────────

    async def test_ensure_table_calls_create_all(self) -> None:
        """ensure_table calls create_all on dedup_metadata via run_sync."""
        engine = MagicMock()
        conn_mock = AsyncMock()
        conn_mock.run_sync = AsyncMock()
        async_ctx = AsyncMock()
        async_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
        async_ctx.__aexit__ = AsyncMock(return_value=False)
        engine.begin = MagicMock(return_value=async_ctx)

        dedup = SADeduplicator(engine)
        await dedup.ensure_table()

        conn_mock.run_sync.assert_awaited_once()
        # First positional arg to run_sync should be create_all bound method.
        call_args = conn_mock.run_sync.call_args
        assert callable(call_args.args[0])

    # ── purge_expired ─────────────────────────────────────────────────────────

    async def test_purge_expired_returns_deleted_row_count(self) -> None:
        """purge_expired returns the number of rows deleted."""
        dedup, conn = self._make_dedup()
        result_mock = MagicMock()
        result_mock.rowcount = 42
        conn.execute = AsyncMock(return_value=result_mock)

        deleted = await dedup.purge_expired()
        assert deleted == 42

    async def test_purge_expired_zero_when_no_expired_rows(self) -> None:
        """purge_expired returns 0 when nothing is deleted."""
        dedup, conn = self._make_dedup()
        result_mock = MagicMock()
        result_mock.rowcount = 0
        conn.execute = AsyncMock(return_value=result_mock)

        assert await dedup.purge_expired() == 0

    # ── repr ──────────────────────────────────────────────────────────────────

    def test_repr(self) -> None:
        """__repr__ includes class name and ttl_seconds."""
        engine = MagicMock()
        dedup = SADeduplicator(engine, config=SADedupConfig(ttl_seconds=1800))
        r = repr(dedup)
        assert "SADeduplicator" in r
        assert "1800" in r


# ── dedup_metadata ────────────────────────────────────────────────────────────


class TestDedupMetadata:
    def test_metadata_contains_dedup_table(self) -> None:
        """dedup_metadata is exported and contains varco_dedup_log."""
        assert dedup_metadata is not None
        assert "varco_dedup_log" in dedup_metadata.tables

    def test_dedup_table_has_event_id_pk(self) -> None:
        """varco_dedup_log has event_id as primary key."""
        table = dedup_metadata.tables["varco_dedup_log"]
        pk_cols = [c.name for c in table.primary_key.columns]
        assert "event_id" in pk_cols

    def test_dedup_table_has_expires_at_column(self) -> None:
        """varco_dedup_log has an expires_at column."""
        table = dedup_metadata.tables["varco_dedup_log"]
        assert "expires_at" in table.c


# ── Integration tests — real SQLite ───────────────────────────────────────────
#
# These tests use an in-memory SQLite database via aiosqlite — no Docker or
# external PostgreSQL required.  They are NOT tagged @pytest.mark.integration
# because SQLite is zero-dependency; they run in normal CI.


@pytest_asyncio.fixture
async def sqlite_engine():
    """In-memory SQLite engine with varco_dedup_log created."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(dedup_metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(dedup_metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def dedup(sqlite_engine) -> SADeduplicator:
    """SADeduplicator backed by the in-memory SQLite engine."""
    return SADeduplicator(sqlite_engine, config=SADedupConfig(ttl_seconds=3600))


class TestSADeduplicatorIntegration:
    """Integration tests: full round-trip via SQLite + aiosqlite."""

    async def test_unknown_event_is_not_duplicate(self, dedup: SADeduplicator) -> None:
        """A never-seen event_id is not a duplicate."""
        assert await dedup.is_duplicate(_eid()) is False

    async def test_mark_seen_then_is_duplicate_returns_true(
        self, dedup: SADeduplicator
    ) -> None:
        """mark_seen followed by is_duplicate returns True."""
        eid = _eid()
        assert await dedup.is_duplicate(eid) is False
        await dedup.mark_seen(eid)
        assert await dedup.is_duplicate(eid) is True

    async def test_mark_seen_idempotent(self, dedup: SADeduplicator) -> None:
        """Calling mark_seen twice for the same event_id does not raise."""
        eid = _eid()
        await dedup.mark_seen(eid)
        await dedup.mark_seen(eid)  # second call — must not raise
        assert await dedup.is_duplicate(eid) is True

    async def test_ensure_table_idempotent(self, sqlite_engine) -> None:
        """ensure_table() can be called multiple times without raising."""
        dedup = SADeduplicator(sqlite_engine)
        await dedup.ensure_table()
        await dedup.ensure_table()  # must not raise

    async def test_is_duplicate_false_after_ttl_expires(self, sqlite_engine) -> None:
        """is_duplicate returns False when expires_at is in the past."""
        # Use a 1-second TTL so we can manipulate the expires_at directly.
        import sqlalchemy as sa
        from varco_sa.deduplication import _dedup_table

        eid = _eid()
        dedup = SADeduplicator(sqlite_engine, config=SADedupConfig(ttl_seconds=1))
        await dedup.mark_seen(eid)

        # Manually back-date the expires_at to simulate TTL expiry.
        past = datetime.now(UTC) - timedelta(seconds=10)
        async with sqlite_engine.begin() as conn:
            await conn.execute(
                sa.update(_dedup_table)
                .where(_dedup_table.c.event_id == eid)
                .values(expires_at=past)
            )

        # After back-dating, the event should appear new again.
        assert await dedup.is_duplicate(eid) is False

    async def test_purge_expired_removes_expired_rows(self, sqlite_engine) -> None:
        """purge_expired deletes rows with expires_at in the past."""
        import sqlalchemy as sa
        from varco_sa.deduplication import _dedup_table

        dedup = SADeduplicator(sqlite_engine)
        eid1, eid2 = _eid(), _eid()

        # Mark both events as seen.
        await dedup.mark_seen(eid1)
        await dedup.mark_seen(eid2)

        # Back-date eid1 to simulate expiry.
        past = datetime.now(UTC) - timedelta(hours=2)
        async with sqlite_engine.begin() as conn:
            await conn.execute(
                sa.update(_dedup_table)
                .where(_dedup_table.c.event_id == eid1)
                .values(expires_at=past)
            )

        deleted = await dedup.purge_expired()
        assert deleted == 1

        # eid1 is gone; eid2 remains.
        assert await dedup.is_duplicate(eid1) is False
        assert await dedup.is_duplicate(eid2) is True
