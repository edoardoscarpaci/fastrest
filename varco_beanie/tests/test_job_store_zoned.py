"""
Unit tests for Plan 011 Phase 4 step 57 — ``JobDocument``'s zoned-schedule
fields and the implicit-null deserialization path.

Not part of the red-phase test suite the plan shipped with — written per
the plan's explicit instruction (steps 55-57 are implementer-authored).
Relies on the same ``bypass_beanie_collection_check`` conftest fixture as
``test_beanie_job_store.py`` (autouse — no explicit import needed).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from varco_beanie.job_store import BeanieJobStore, JobDocument, _doc_to_job, _job_to_doc
from varco_core.job.base import Job, JobStatus


def _pending_job(**kwargs) -> Job:
    return Job(job_id=uuid4(), **kwargs)


def test_beanie_job_store_declares_zoned_schedule_support() -> None:
    assert BeanieJobStore.supports_zoned_schedules is True


def test_job_document_zoned_fields_default_to_none_and_zero() -> None:
    doc = JobDocument(
        id=uuid4(),
        status=JobStatus.PENDING.value,
        created_at=datetime.now(UTC),
    )
    assert doc.run_at_wall is None
    assert doc.run_at_tz is None
    assert doc.run_at_fold == 0


def test_job_to_doc_to_job_round_trips_a_zoned_job() -> None:
    job = _pending_job(
        run_at=datetime(2026, 6, 1, 13, 0, tzinfo=UTC),
        run_at_wall=datetime(2026, 6, 1, 9, 0),
        run_at_tz="America/New_York",
        run_at_fold=0,
    )
    doc = _job_to_doc(job)
    assert doc.run_at_wall == job.run_at_wall
    assert doc.run_at_tz == "America/New_York"
    assert doc.run_at_fold == 0

    round_tripped = _doc_to_job(doc)
    assert round_tripped.run_at_wall == job.run_at_wall
    assert round_tripped.run_at_tz == "America/New_York"
    assert round_tripped.run_at_fold == 0


def test_doc_to_job_implicit_null_deserialization_for_pre_plan_document() -> None:
    # Simulates a pre-Plan-011 document: the three new attributes are
    # entirely absent (not just None-valued) — as a raw find() bypassing
    # JobDocument's own pydantic default resolution would look. _doc_to_job
    # must still resolve to the unzoned state via its getattr() fallback,
    # no migration required (Mongo's implicit-null path).
    class _PreExistingDoc:
        id = uuid4()
        status = JobStatus.PENDING.value
        created_at = datetime.now(UTC)
        started_at = None
        completed_at = None
        result = None
        error = None
        callback_url = None
        auth_snapshot = None
        request_token = None
        job_metadata: dict = {}  # noqa: RUF012 — plain test double, not a dataclass
        task_payload = None
        run_at = None
        attempt = 0
        max_attempts = 1
        owner_id = None
        lease_expires_at = None
        lease_epoch = 0
        expires_at = None
        request_issuer = None
        request_subject = None
        request_token_hash = None
        # NOTE: run_at_wall / run_at_tz / run_at_fold are deliberately absent.

    job = _doc_to_job(_PreExistingDoc())  # type: ignore[arg-type]
    assert job.run_at_wall is None
    assert job.run_at_tz is None
    assert job.run_at_fold == 0
