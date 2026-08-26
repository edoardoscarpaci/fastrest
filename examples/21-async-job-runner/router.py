"""
router.py
=========
HTTP endpoints for the ``21-async-job-runner`` example.

Routes
------
``POST /v1/reports``
    Enqueue a report generation job.  Returns ``202 Accepted`` with a
    ``job_id`` and a ``status_url`` the client can poll.

``GET /v1/jobs/{job_id}``
    Poll a job by ID.  Returns the current status, and — once complete —
    the decoded result payload.

DESIGN: plain FastAPI APIRouter over VarcoCRUDRouter
    ✅ Job endpoints are not CRUD over a domain model — they are
       infrastructure endpoints.  APIRouter is the right abstraction.
    ✅ Keeps the example self-contained; no DI container required.
    ✅ Makes the 202 + Location pattern explicit for readers.
    ❌ Does not demonstrate VarcoCRUDRouter's built-in async-job integration
       (``enqueue_task()`` on a CRUD router).  See example 22+ for that.

Thread safety:  ✅ Stateless handler functions; all mutable state lives in the
                   ``AbstractJobStore`` (which is itself thread-safe).
Async safety:   ✅ All handlers are ``async def``.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from jobs import generate_report
from pydantic import BaseModel, Field
from varco_core.job.base import AbstractJobRunner, AbstractJobStore, Job
from varco_fastapi.job.response import JobStatusResponse

# ── Request / response models ─────────────────────────────────────────────────


class ReportRequest(BaseModel):
    """
    Body for ``POST /v1/reports``.

    Attributes:
        title: Human-readable report title.
        rows:  Number of rows to simulate in the report.
    """

    title: str = Field(..., description="Report title")
    rows: int = Field(..., ge=1, le=10_000, description="Number of rows to generate")


class EnqueuedResponse(BaseModel):
    """
    HTTP 202 response body for ``POST /v1/reports``.

    Attributes:
        job_id:     UUID of the submitted job.
        status:     Initial lifecycle status (always ``pending``).
        status_url: URL to poll for status updates.
    """

    job_id: UUID = Field(..., description="UUID of the submitted job")
    status: str = Field(default="pending", description="Initial job status")
    status_url: str = Field(..., description="URL to poll for job status")


# ── Router factory ────────────────────────────────────────────────────────────


def build_router(store: AbstractJobStore, runner: AbstractJobRunner) -> APIRouter:
    """
    Build and return the FastAPI APIRouter with job endpoints.

    Both ``store`` and ``runner`` are passed in at construction time so the
    router is fully testable without a DI container — just pass in an
    ``InMemoryJobStore`` and ``JobRunner`` directly.

    Args:
        store:  ``AbstractJobStore`` for job persistence and status lookups.
        runner: ``AbstractJobRunner`` for enqueueing background jobs.

    Returns:
        A configured ``APIRouter`` with the ``/v1`` prefix.

    Edge cases:
        - ``runner`` must have ``start()`` called before any requests arrive;
          the caller (app.py lifespan) is responsible for this.
        - ``store`` is shared between the router and the runner — both see the
          same job state.

    Thread safety:  ✅ ``APIRouter`` is immutable after construction.
    Async safety:   ✅ No I/O during construction; handlers are async.
    """
    router = APIRouter(prefix="/v1")

    # ── POST /v1/reports ──────────────────────────────────────────────────────

    @router.post("/reports", status_code=202, response_model=EnqueuedResponse)
    async def enqueue_report(body: ReportRequest, request: Request) -> EnqueuedResponse:
        """
        Enqueue a report generation job and return ``202 Accepted``.

        The actual work runs in a background asyncio.Task managed by
        ``JobRunner``.  The caller should poll ``GET /v1/jobs/{job_id}``
        until the status is ``completed`` or ``failed``.

        Args:
            body:    Report parameters (title and rows).
            request: FastAPI request — used to build the ``status_url``.

        Returns:
            ``EnqueuedResponse`` with ``job_id`` and ``status_url``.

        Edge cases:
            - The job is persisted to the store as PENDING *before* the
              asyncio.Task is created, so a process crash between the two
              leaves a recoverable PENDING record.
            - ``status_url`` is absolute (includes scheme + host) so clients
              outside the process can follow it directly.
        """
        job = Job(job_id=uuid4())

        # Enqueue: saves PENDING record first, then schedules the coroutine.
        # enqueue() is the only correct submission path — see AbstractJobRunner docs.
        await runner.enqueue(job, generate_report(body.title, body.rows))

        # Build an absolute status URL using the incoming request's base URL
        status_url = str(request.base_url).rstrip("/") + f"/v1/jobs/{job.job_id}"

        return EnqueuedResponse(
            job_id=job.job_id,
            status="pending",
            status_url=status_url,
        )

    # ── GET /v1/jobs/{job_id} ─────────────────────────────────────────────────

    @router.get("/jobs/{job_id}", response_model=JobStatusResponse)
    async def get_job_status(job_id: UUID) -> JobStatusResponse:
        """
        Poll the status of a background job.

        Args:
            job_id: UUID of the job to poll.

        Returns:
            ``JobStatusResponse`` with the current status, timing fields,
            and — once completed — the decoded ``result`` payload.

        Raises:
            HTTPException 404: If no job with ``job_id`` exists in the store.

        Edge cases:
            - ``result`` is ``None`` for PENDING and RUNNING jobs.
            - ``result`` is a decoded JSON object (dict/list/scalar) for
              COMPLETED jobs.  The ``JobRunner`` stores results as JSON bytes;
              this endpoint decodes them before returning.
            - ``error`` is set for FAILED jobs; ``result`` is ``None``.
        """
        job = await store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        # Decode result bytes → Python object (JobRunner stores as JSON bytes)
        result: Any | None = None
        if job.result is not None:
            try:
                result = json.loads(job.result.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                # Fallback: return raw string if JSON decoding fails
                result = job.result.decode("utf-8", errors="replace")

        return JobStatusResponse(
            job_id=job.job_id,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            result=result,
            error=job.error,
        )

    return router


__all__ = ["build_router", "EnqueuedResponse", "ReportRequest"]
