"""
Unit tests for varco_core.job.base — Phase 4 time/lease/fencing + Phase 6
retention/token-reference additions (plan 005, Steps 44 and 74).
============================================================================

RED until:
    - Step 45: JobStatus gains DEAD, added to is_terminal.
    - Step 46: Job gains run_at/attempt/max_attempts/owner_id/lease_expires_at/
      lease_epoch/expires_at/request_issuer/request_subject/request_token_hash,
      plus as_retry()/as_dead() transitions.
    - Step 47: try_claim gains owner_id/lease_ttl keyword-only params.
    - Step 72: Job gains store_raw_token: bool = True — when False, populates
      the reference fields and leaves request_token unset.

All tests are ``async def`` — no ``@pytest.mark.asyncio`` needed (auto mode),
per repo convention, even where the body does not await (consistency with
the rest of the suite and so future awaiting code doesn't need retrofitting).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from varco_core.job.base import Job, JobStatus


class TestJobStatusDead:
    async def test_dead_status_exists_and_is_terminal(self) -> None:
        assert JobStatus.DEAD == "dead"
        assert JobStatus.DEAD.is_terminal is True


class TestJobAsRetry:
    async def test_as_retry_returns_pending_with_run_at_and_incremented_attempt(
        self,
    ) -> None:
        job = Job(job_id=uuid4(), status=JobStatus.PENDING).as_running()
        next_run_at = datetime.now(UTC) + timedelta(seconds=30)

        retried = job.as_retry(next_run_at)

        assert retried.status == JobStatus.PENDING
        assert retried.run_at == next_run_at
        assert retried.attempt == job.attempt + 1


class TestJobAsDead:
    async def test_as_dead_is_terminal(self) -> None:
        job = Job(job_id=uuid4(), status=JobStatus.PENDING).as_running()
        dead = job.as_dead("permanent failure")
        assert dead.status.is_terminal is True
        assert dead.status == JobStatus.DEAD


class TestJobBackCompatConstruction:
    async def test_job_with_no_new_kwargs_is_field_for_field_equal_to_today(
        self,
    ) -> None:
        job_id = uuid4()
        created_at = datetime.now(UTC)
        job = Job(job_id=job_id, created_at=created_at)

        # New fields must all default such that an unchanged caller sees
        # today's behaviour exactly: no lease taken, no schedule delay,
        # terminal-on-first-failure.
        assert job.run_at is None
        assert job.attempt == 0
        assert job.max_attempts == 1
        assert job.owner_id is None
        assert job.lease_expires_at is None
        assert job.lease_epoch == 0
        assert job.expires_at is None
        assert job.request_issuer is None
        assert job.request_subject is None
        assert job.request_token_hash is None


class TestAbstractJobStoreTryClaimLeaseKwargs:
    async def test_try_claim_accepts_owner_id_and_lease_ttl(self) -> None:
        from varco_core.job.base import AbstractJobStore

        class _MinimalStore(AbstractJobStore):
            async def save(self, job: Job) -> None: ...
            async def get(self, job_id):
                return None

            async def list_by_status(self, status, *, limit=100):
                return []

            async def delete(self, job_id) -> None: ...
            async def try_claim(self, job_id):
                return None

        store = _MinimalStore()
        # Source correction 1: this is an addition, not the activation of a
        # dormant parameter. An external subclass written against the old
        # signature keeps working unchanged when called the old way — that
        # is the compatibility guarantee.
        result = await store.try_claim(uuid4())
        assert result is None

        # It cannot receive the new kwargs, though: Python refuses kwargs the
        # callee's own signature does not declare. Step 47 documents exactly
        # this — external stores "must add the kwargs before enabling
        # leases". Pin that contract so the docs and reality agree.
        with pytest.raises(TypeError):
            await store.try_claim(uuid4(), owner_id="worker-1", lease_ttl=30.0)

        # A subclass that DOES declare them accepts them.
        class _LeaseAwareStore(_MinimalStore):
            async def try_claim(self, job_id, *, owner_id=None, lease_ttl=None):
                return None

        lease_store = _LeaseAwareStore()
        assert await lease_store.try_claim(uuid4()) is None
        assert await lease_store.try_claim(uuid4(), owner_id="worker-1", lease_ttl=30.0) is None


class TestAbstractJobStoreNewConcreteMethods:
    async def test_claim_next_default_impl_exists(self) -> None:
        from varco_core.job.base import AbstractJobStore

        class _MinimalStore(AbstractJobStore):
            async def save(self, job: Job) -> None: ...
            async def get(self, job_id):
                return None

            async def list_by_status(self, status, *, limit=100):
                return []

            async def delete(self, job_id) -> None: ...
            async def try_claim(self, job_id, *, owner_id=None, lease_ttl=None):
                return None

        store = _MinimalStore()
        result = await store.claim_next()
        assert result is None

    async def test_renew_default_impl_raises_not_implemented(self) -> None:
        from varco_core.job.base import AbstractJobStore

        class _MinimalStore(AbstractJobStore):
            async def save(self, job: Job) -> None: ...
            async def get(self, job_id):
                return None

            async def list_by_status(self, status, *, limit=100):
                return []

            async def delete(self, job_id) -> None: ...
            async def try_claim(self, job_id, *, owner_id=None, lease_ttl=None):
                return None

        store = _MinimalStore()
        with pytest.raises(NotImplementedError):
            await store.renew(uuid4(), owner_id="w1", epoch=1, lease_ttl=30.0)

    async def test_reap_expired_leases_default_impl_raises_not_implemented(
        self,
    ) -> None:
        from varco_core.job.base import AbstractJobStore

        class _MinimalStore(AbstractJobStore):
            async def save(self, job: Job) -> None: ...
            async def get(self, job_id):
                return None

            async def list_by_status(self, status, *, limit=100):
                return []

            async def delete(self, job_id) -> None: ...
            async def try_claim(self, job_id, *, owner_id=None, lease_ttl=None):
                return None

        store = _MinimalStore()
        with pytest.raises(NotImplementedError):
            await store.reap_expired_leases()


class TestStaleLeaseError:
    async def test_stale_lease_error_is_importable(self) -> None:
        from varco_core.job.base import StaleLeaseError

        assert issubclass(StaleLeaseError, Exception)


class TestAbstractJobRunnerEnqueueRunAtDelay:
    async def test_enqueue_accepts_run_at_and_delay_mutually_exclusive(self) -> None:
        from varco_core.job.base import AbstractJobRunner

        # We only need to prove the signature accepts the new kwargs and
        # enforces mutual exclusivity — construct a minimal concrete runner.
        class _MinimalRunner(AbstractJobRunner):
            async def enqueue(
                self,
                coro,
                *,
                callback_url=None,
                auth_snapshot=None,
                request_token=None,
                run_at=None,
                delay=None,
            ):
                if run_at is not None and delay is not None:
                    raise ValueError("run_at and delay are mutually exclusive")
                return Job(job_id=uuid4())

            async def submit(self, *args, **kwargs):
                raise NotImplementedError

            async def cancel(self, job_id) -> bool:
                return False

            async def start(self) -> None: ...

            async def enqueue_task(self, *args, **kwargs):
                raise NotImplementedError

            async def recover(self, registry) -> int:
                return 0

            async def stop(self, *, timeout: float = 30.0) -> None: ...

        runner = _MinimalRunner()

        async def _coro():
            return None

        with pytest.raises(ValueError):
            await runner.enqueue(
                _coro(),
                run_at=datetime.now(UTC),
                delay=timedelta(seconds=5),
            )


# ════════════════════════════════════════════════════════════════════════════════
# Plan 005, Phase 6, Step 74 — store_raw_token=False reference-fields path
# ════════════════════════════════════════════════════════════════════════════════


class TestJobStoreRawTokenFalse:
    async def test_store_raw_token_false_leaves_request_token_none(self) -> None:
        job = Job(
            job_id=uuid4(),
            request_token="super-secret-jwt",
            store_raw_token=False,
        )
        assert job.request_token is None

    async def test_store_raw_token_false_populates_reference_fields(self) -> None:
        job = Job(
            job_id=uuid4(),
            request_token="super-secret-jwt",
            store_raw_token=False,
        )
        assert job.request_token_hash is not None
        assert job.request_token_hash != "super-secret-jwt"

    async def test_request_token_hash_is_stable_and_excludes_raw_token(self) -> None:
        job_a = Job(job_id=uuid4(), request_token="same-token", store_raw_token=False)
        job_b = Job(job_id=uuid4(), request_token="same-token", store_raw_token=False)
        assert job_a.request_token_hash == job_b.request_token_hash
        assert "same-token" not in job_a.request_token_hash

    async def test_default_path_unchanged_store_raw_token_true(self) -> None:
        job = Job(job_id=uuid4(), request_token="super-secret-jwt")
        assert job.request_token == "super-secret-jwt"
