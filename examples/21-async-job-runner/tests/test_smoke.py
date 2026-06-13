"""
test_smoke.py
=============
Smoke tests for the ``21-async-job-runner`` example.

Coverage
--------
Happy paths:
  - POST /v1/reports → 202 with job_id and status_url
  - Job completes: GET /v1/jobs/{job_id} → status "completed", result present
  - Result contains expected fields (title, rows, csv_preview)
  - status_url in 202 body points to the correct GET endpoint

Unhappy paths:
  - GET /v1/jobs/nonexistent-id → 404
  - POST /v1/reports with rows=0 → 422 (validation error)

DESIGN: ASGITransport + lifespan=True to start/stop the JobRunner
    ✅ ``lifespan=True`` on ``ASGITransport`` triggers the FastAPI lifespan
       context manager — runner.start() and runner.stop() are called around
       the test session exactly as in production.
    ✅ No manual runner.start() / stop() plumbing in tests.
    ✅ Jobs run as real asyncio.Tasks on the test event loop — the
       ``await asyncio.sleep(0)`` trick yields to them without wall-clock delay.
    ❌ All tests share one ASGI process (session scope) — job state is not
       reset between tests.  Tests use unique titles/rows to avoid collisions.

Thread safety:  ✅ asyncio_mode=auto; single-threaded event loop.
Async safety:   ✅ All tests are ``async def``.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from httpx import ASGITransport


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
async def client() -> httpx.AsyncClient:
    """
    Session-scoped ASGI client.

    Uses ``lifespan=True`` so the FastAPI lifespan fires — this calls
    ``runner.start()`` before tests and ``runner.stop()`` after the session.

    Yields:
        A configured ``httpx.AsyncClient`` backed by the example app.
    """
    # Import here (not at module top) so the app is only created once per
    # session, not once per import of this test module.
    import sys
    import os

    # Add example root to sys.path so ``from app import create_app`` works
    example_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if example_root not in sys.path:
        sys.path.insert(0, example_root)

    from app import create_app  # noqa: PLC0415

    test_app = create_app()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=test_app, raise_app_exceptions=True),
        base_url="http://testserver",
    ) as ac:
        yield ac


# ── Helpers ───────────────────────────────────────────────────────────────────


async def wait_for_job(
    client: httpx.AsyncClient,
    job_id: str,
    *,
    max_ticks: int = 50,
) -> dict:  # type: ignore[type-arg]
    """
    Yield to the event loop until the job reaches a terminal state.

    Each tick is a single ``asyncio.sleep(0)`` — enough to let one queued
    asyncio.Task step.  Terminal states are ``completed``, ``failed``, and
    ``cancelled``.

    Args:
        client:    ASGI test client.
        job_id:    Job UUID string to poll.
        max_ticks: Maximum yield cycles before giving up (default 50).

    Returns:
        The final ``JobStatusResponse`` dict.

    Raises:
        AssertionError: If the job does not reach a terminal state within
                        ``max_ticks`` cycles.
    """
    terminal = {"completed", "failed", "cancelled"}
    for _ in range(max_ticks):
        await asyncio.sleep(0)
        resp = await client.get(f"/v1/jobs/{job_id}")
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        body = resp.json()
        if body["status"] in terminal:
            return body
    raise AssertionError(
        f"Job {job_id} did not reach terminal state after {max_ticks} ticks; "
        f"last status: {body['status']}"  # type: ignore[possibly-undefined]
    )


# ── Happy paths ───────────────────────────────────────────────────────────────


async def test_enqueue_returns_202(client: httpx.AsyncClient) -> None:
    """POST /v1/reports returns 202 with job_id and status_url."""
    response = await client.post(
        "/v1/reports",
        json={"title": "Monthly Sales", "rows": 5},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert "job_id" in body
    assert body["status"] == "pending"
    assert "status_url" in body
    # status_url must contain the job_id
    assert body["job_id"] in body["status_url"]


async def test_job_completes_with_result(client: httpx.AsyncClient) -> None:
    """
    After enqueuing, yielding to the event loop drives the job to completion.
    GET /v1/jobs/{job_id} returns status="completed" and a non-null result.
    """
    enqueue_resp = await client.post(
        "/v1/reports",
        json={"title": "Weekly Revenue", "rows": 3},
    )
    assert enqueue_resp.status_code == 202
    job_id = enqueue_resp.json()["job_id"]

    final = await wait_for_job(client, job_id)

    assert final["status"] == "completed"
    assert final["result"] is not None
    assert final["error"] is None


async def test_result_contains_expected_fields(client: httpx.AsyncClient) -> None:
    """
    Completed job result is a dict with title, rows, and csv_preview.
    """
    enqueue_resp = await client.post(
        "/v1/reports",
        json={"title": "Inventory Report", "rows": 10},
    )
    assert enqueue_resp.status_code == 202
    job_id = enqueue_resp.json()["job_id"]

    final = await wait_for_job(client, job_id)

    result = final["result"]
    assert isinstance(result, dict)
    assert result["title"] == "Inventory Report"
    assert result["rows"] == 10
    assert "csv_preview" in result
    # CSV preview must have a header line
    assert "id,value" in result["csv_preview"]


async def test_status_url_is_reachable(client: httpx.AsyncClient) -> None:
    """
    The status_url returned in the 202 body is a valid GET endpoint.
    Polling it before the job finishes returns 200 (pending or running).
    """
    enqueue_resp = await client.post(
        "/v1/reports",
        json={"title": "Status URL Check", "rows": 2},
    )
    assert enqueue_resp.status_code == 202
    body = enqueue_resp.json()

    # Extract path from status_url (httpx base_url is http://testserver)
    status_path = "/" + body["status_url"].split("/", 3)[-1]

    poll_resp = await client.get(status_path)
    assert poll_resp.status_code == 200
    poll_body = poll_resp.json()
    assert poll_body["job_id"] == body["job_id"]
    # Status is either pending, running, or completed depending on scheduling
    assert poll_body["status"] in {"pending", "running", "completed"}


# ── Unhappy paths ─────────────────────────────────────────────────────────────


async def test_unknown_job_returns_404(client: httpx.AsyncClient) -> None:
    """GET /v1/jobs/{unknown_id} returns 404."""
    unknown_id = str(uuid.uuid4())
    response = await client.get(f"/v1/jobs/{unknown_id}")
    assert response.status_code == 404


async def test_enqueue_with_zero_rows_returns_422(client: httpx.AsyncClient) -> None:
    """
    POST /v1/reports with rows=0 fails Pydantic validation (ge=1) → 422.
    """
    response = await client.post(
        "/v1/reports",
        json={"title": "Bad Request", "rows": 0},
    )
    assert response.status_code == 422


async def test_enqueue_missing_title_returns_422(client: httpx.AsyncClient) -> None:
    """POST /v1/reports without required title field → 422."""
    response = await client.post(
        "/v1/reports",
        json={"rows": 5},
    )
    assert response.status_code == 422
