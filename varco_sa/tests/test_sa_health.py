"""
Unit tests for varco_sa.health
===============================
Covers:
  - ``SAHealthCheck`` — liveness probe (SELECT 1)
  - ``SAPoolSaturationCheck`` — pool-saturation readiness probe

Uses an in-memory SQLite engine (no mock) for the "healthy" path, and
monkeypatching for error/timeout scenarios.  No external database required
for unit tests.

Sections
--------
SAHealthCheck
    - Healthy probe     — SELECT 1 on real SQLite → HEALTHY with latency
    - Timeout           — _probe hangs → wait_for fires → UNHEALTHY
    - DB error          — engine raises OperationalError → UNHEALTHY with detail
    - Never-raise       — exceptions never propagate to the caller
    - Repr              — human-readable string for logging
    - Integration       — real PostgreSQL via testcontainers (marked integration)

SAPoolSaturationCheck
    - name property     — returns "sqlalchemy-pool"
    - Not saturated     — pool_metrics mock returns is_saturated=False → HEALTHY
    - Saturated         — pool_metrics mock returns is_saturated=True → DEGRADED
    - Detail format     — detail string contains checked_out and total counts
    - Constructor error — neither config nor engine → TypeError
    - Never-raise       — unexpected pool_metrics exception → UNHEALTHY
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from varco_core.health import HealthStatus
from varco_sa.health import SAHealthCheck, SAPoolSaturationCheck
from varco_sa.pool_metrics import SAPoolMetrics

from tests.conftest import asyncpg_url

# ── Helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def sqlite_engine():
    """
    Yield a real in-memory SQLite async engine and dispose it afterwards.

    Using a real SQLite engine for the healthy-path tests avoids mocking
    SQLAlchemy internals — the test validates the actual SELECT 1 path.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    yield engine
    await engine.dispose()


# ── Healthy probe ─────────────────────────────────────────────────────────────


async def test_healthy_returns_healthy_status(sqlite_engine) -> None:
    check = SAHealthCheck(engine=sqlite_engine)
    result = await check.check()
    assert result.status is HealthStatus.HEALTHY


async def test_healthy_returns_latency(sqlite_engine) -> None:
    check = SAHealthCheck(engine=sqlite_engine)
    result = await check.check()
    assert result.latency_ms is not None
    assert result.latency_ms >= 0.0


async def test_healthy_component_name(sqlite_engine) -> None:
    check = SAHealthCheck(engine=sqlite_engine)
    result = await check.check()
    assert result.component == "sqlalchemy"


async def test_healthy_no_detail(sqlite_engine) -> None:
    # A healthy result should not carry a detail string — cleaner logs.
    check = SAHealthCheck(engine=sqlite_engine)
    result = await check.check()
    assert result.detail is None


# ── Timeout ───────────────────────────────────────────────────────────────────


async def test_timeout_returns_unhealthy(sqlite_engine) -> None:
    # Replace _probe with a coroutine that hangs, then set timeout=0.001 so
    # wait_for fires immediately.
    async def _hang() -> None:
        await asyncio.sleep(999)

    check = SAHealthCheck(engine=sqlite_engine, timeout=0.001)
    with patch.object(check, "_probe", new=_hang):
        result = await check.check()

    assert result.status is HealthStatus.UNHEALTHY
    assert result.latency_ms is None
    assert "timed out" in (result.detail or "")


# ── DB error ──────────────────────────────────────────────────────────────────


async def test_db_error_returns_unhealthy(sqlite_engine) -> None:
    from sqlalchemy.exc import OperationalError

    check = SAHealthCheck(engine=sqlite_engine)
    with patch.object(
        check,
        "_probe",
        new=AsyncMock(side_effect=OperationalError("no such table", {}, None)),
    ):
        result = await check.check()

    assert result.status is HealthStatus.UNHEALTHY
    assert result.detail is not None


async def test_generic_exception_returns_unhealthy(sqlite_engine) -> None:
    check = SAHealthCheck(engine=sqlite_engine)
    with patch.object(check, "_probe", new=AsyncMock(side_effect=RuntimeError("bang"))):
        result = await check.check()

    assert result.status is HealthStatus.UNHEALTHY


# ── Never-raise contract ──────────────────────────────────────────────────────


async def test_check_never_raises(sqlite_engine) -> None:
    check = SAHealthCheck(engine=sqlite_engine)
    with patch.object(check, "_probe", new=AsyncMock(side_effect=Exception("boom"))):
        # Must not propagate
        result = await check.check()

    assert result.status is HealthStatus.UNHEALTHY


# ── Repr ──────────────────────────────────────────────────────────────────────


async def test_repr_contains_timeout(sqlite_engine) -> None:
    check = SAHealthCheck(engine=sqlite_engine, timeout=3.5)
    assert "3.5" in repr(check)


# ── SAPoolSaturationCheck: helpers ───────────────────────────────────────────


def _make_metrics(*, checked_out: int, size: int, max_overflow: int) -> SAPoolMetrics:
    """
    Build a synthetic ``SAPoolMetrics`` snapshot for use in unit tests.

    Using a real ``SAPoolMetrics`` frozen dataclass (rather than a MagicMock)
    ensures that ``is_saturated``, ``utilisation``, and all other computed
    properties behave exactly as they would at runtime.

    Args:
        checked_out:   Connections currently held by active sessions.
        size:          Base pool size (``pool_size`` kwarg to create_engine).
        max_overflow:  Overflow allowance (``max_overflow`` kwarg).

    Returns:
        A frozen ``SAPoolMetrics`` instance with ``checked_in`` inferred as
        ``max(0, size - checked_out)`` for a plausible snapshot.
        ``invalid`` is 0 and ``pool_type`` is ``"QueuePool"`` (typical defaults).
    """
    import datetime

    # checked_in is the remainder of the base pool; clamp to 0 when overflowed.
    checked_in = max(0, size - checked_out)
    return SAPoolMetrics(
        captured_at=datetime.datetime.now(datetime.timezone.utc),
        size=size,
        checked_in=checked_in,
        checked_out=checked_out,
        overflow=max(0, checked_out - size),
        max_overflow=max_overflow,
        # invalid=0: no broken connections in these unit-test scenarios.
        invalid=0,
        # pool_type="QueuePool": the default pool class; irrelevant to health logic.
        pool_type="QueuePool",
    )


# ── SAPoolSaturationCheck: name ───────────────────────────────────────────────


async def test_pool_saturation_name(sqlite_engine) -> None:
    # The name must be distinct from SAHealthCheck ("sqlalchemy") so health
    # dashboards can tell apart connectivity and pool-utilisation issues.
    check = SAPoolSaturationCheck(engine=sqlite_engine)
    assert check.name == "sqlalchemy-pool"


# ── SAPoolSaturationCheck: HEALTHY when not saturated ────────────────────────


async def test_pool_not_saturated_returns_healthy(sqlite_engine) -> None:
    # Simulate a pool with 5/10 connections in use — well below saturation.
    metrics = _make_metrics(checked_out=5, size=10, max_overflow=0)
    check = SAPoolSaturationCheck(engine=sqlite_engine)

    with patch("varco_sa.health.pool_metrics", return_value=metrics):
        result = await check.check()

    assert result.status is HealthStatus.HEALTHY


async def test_pool_empty_returns_healthy(sqlite_engine) -> None:
    # Freshly created pool with nothing checked out — must be HEALTHY.
    metrics = _make_metrics(checked_out=0, size=5, max_overflow=5)
    check = SAPoolSaturationCheck(engine=sqlite_engine)

    with patch("varco_sa.health.pool_metrics", return_value=metrics):
        result = await check.check()

    assert result.status is HealthStatus.HEALTHY


# ── SAPoolSaturationCheck: DEGRADED when saturated ───────────────────────────


async def test_pool_saturated_returns_degraded(sqlite_engine) -> None:
    # Pool of 5 + overflow of 5 = 10 total; all 10 checked out → saturated.
    metrics = _make_metrics(checked_out=10, size=5, max_overflow=5)
    assert metrics.is_saturated  # sanity-check the fixture itself
    check = SAPoolSaturationCheck(engine=sqlite_engine)

    with patch("varco_sa.health.pool_metrics", return_value=metrics):
        result = await check.check()

    assert result.status is HealthStatus.DEGRADED


async def test_pool_at_base_limit_with_no_overflow_is_degraded(sqlite_engine) -> None:
    # max_overflow=0 means the base pool size IS the hard limit.
    metrics = _make_metrics(checked_out=5, size=5, max_overflow=0)
    assert metrics.is_saturated
    check = SAPoolSaturationCheck(engine=sqlite_engine)

    with patch("varco_sa.health.pool_metrics", return_value=metrics):
        result = await check.check()

    assert result.status is HealthStatus.DEGRADED


# ── SAPoolSaturationCheck: detail format ──────────────────────────────────────


async def test_pool_detail_contains_checked_out_and_total(sqlite_engine) -> None:
    # Detail must include "checked_out=N/M" so operators can see raw numbers
    # in health dashboards without parsing status enums.
    metrics = _make_metrics(checked_out=3, size=5, max_overflow=5)
    check = SAPoolSaturationCheck(engine=sqlite_engine)

    with patch("varco_sa.health.pool_metrics", return_value=metrics):
        result = await check.check()

    # Total = size(5) + max_overflow(5) = 10
    assert result.detail is not None
    assert "checked_out=3" in result.detail
    assert "10" in result.detail  # total capacity visible in detail


async def test_pool_detail_present_when_healthy(sqlite_engine) -> None:
    # Unlike SAHealthCheck (detail=None on HEALTHY), the pool probe always
    # provides detail so operators can monitor utilisation trends.
    metrics = _make_metrics(checked_out=0, size=5, max_overflow=5)
    check = SAPoolSaturationCheck(engine=sqlite_engine)

    with patch("varco_sa.health.pool_metrics", return_value=metrics):
        result = await check.check()

    assert result.status is HealthStatus.HEALTHY
    assert result.detail is not None


# ── SAPoolSaturationCheck: constructor validation ─────────────────────────────


async def test_pool_saturation_check_requires_config_or_engine() -> None:
    # Constructing without either config or engine is a programming error;
    # TypeError is the correct signal (wrong arguments, not a runtime failure).
    with pytest.raises(TypeError, match="SAPoolSaturationCheck requires"):
        SAPoolSaturationCheck()


# ── SAPoolSaturationCheck: never-raise contract ───────────────────────────────


async def test_pool_saturation_check_never_raises(sqlite_engine) -> None:
    # If pool_metrics() unexpectedly raises (e.g. pool replaced at runtime),
    # the probe must still return a result rather than propagating the exception
    # and crashing the aggregated health endpoint.
    check = SAPoolSaturationCheck(engine=sqlite_engine)

    with patch("varco_sa.health.pool_metrics", side_effect=RuntimeError("unexpected")):
        result = await check.check()

    assert result.status is HealthStatus.UNHEALTHY
    assert result.detail is not None


# ── SAPoolSaturationCheck: component name in result ──────────────────────────


async def test_pool_saturation_result_component_matches_name(sqlite_engine) -> None:
    # HealthResult.component must match check.name — required by the aggregator.
    metrics = _make_metrics(checked_out=0, size=5, max_overflow=5)
    check = SAPoolSaturationCheck(engine=sqlite_engine)

    with patch("varco_sa.health.pool_metrics", return_value=metrics):
        result = await check.check()

    assert result.component == check.name


# ── Integration: real PostgreSQL ──────────────────────────────────────────────


# pg_container (module-scoped) was replaced by the session-scoped
# postgres_container fixture in tests/conftest.py (Plan 012 / RT1, Step 6/9).


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("VARCO_RUN_INTEGRATION"),
    reason="Integration tests disabled — set VARCO_RUN_INTEGRATION=1",
)
async def test_integration_healthy_against_real_db(postgres_container) -> None:
    """
    Health check reports HEALTHY against a real PostgreSQL.

    DESIGN: testcontainers, not a DATABASE_URL fallback.
        This test used to default to
        ``postgresql+asyncpg://postgres:postgres@localhost/test`` when
        ``DATABASE_URL`` was unset. The integration runner sets
        ``VARCO_RUN_INTEGRATION=1`` but never ``DATABASE_URL``, so the test
        ran, dialled a Postgres nobody had started, and asserted UNHEALTHY is
        HEALTHY — a guaranteed failure rather than a meaningful check.
        ✅ Self-contained, matching every other integration test in the repo.
        ❌ Requires Docker — already the documented prerequisite.
    """
    url = asyncpg_url(postgres_container)
    engine = create_async_engine(url)
    try:
        check = SAHealthCheck(engine=engine, timeout=5.0)
        result = await check.check()
        assert result.status is HealthStatus.HEALTHY
        assert result.latency_ms is not None
    finally:
        await engine.dispose()
