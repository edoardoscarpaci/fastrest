"""
``abandon_lease`` — the "worker crash" step shared by
``varco_sa/tests/test_sa_job_lease_crash.py`` and
``varco_redis/tests/test_redis_job_lease_crash.py`` (Plan 018 / RT7a,
Step 3/26/27).

**No ``chaos`` marker anywhere this helper is used, deliberately**
(§RT7-shape): "a worker crashed" means *this process stopped renewing its
lease* — nothing at the container level is killed, restarted, or paused.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from varco_core.job.base import AbstractJobStore


async def abandon_lease(store: AbstractJobStore, job_id: UUID) -> None:
    """
    Simulate a worker crashing mid-lease: it never calls ``renew()`` again.

    ``AbstractJobStore.try_claim()`` (``varco_core/varco_core/job/base.py:770``)
    takes a lease but starts no background renew loop of its own — heartbeat
    renewal is the *caller's* (a runner's) responsibility, driven by its own
    ``asyncio.Task``. In every test that uses this helper today, no such task
    was ever started — the test drives ``try_claim``/``renew``/
    ``reap_expired_leases``/``save`` directly against the store. "Abandoning"
    the lease is therefore achieved simply by the test not calling
    ``renew()`` again and letting ``lease_ttl`` elapse — this function is a
    **documented no-op** today.

    It exists as a named call site (rather than the test simply doing
    nothing) for two reasons: it makes the "the worker crashes here" moment
    explicit and searchable in both twin test modules, and it gives a
    real background-renew-task refactor exactly one place to add a
    ``task.cancel()`` call later, so both ``varco_sa`` and ``varco_redis``
    lease-crash tests would pick up the more realistic simulation from one
    edit instead of two.

    Args:
        store: The job store the lease was claimed against. Unused today —
            kept in the signature so a future renew-task-cancelling
            implementation does not change either call site.
        job_id: The job whose lease is being abandoned. Unused today, same
            reason as ``store``.

    Returns:
        None.

    Edge cases:
        - Calling this twice, or calling it for a job that was never
          claimed, is a no-op both times — there is no state to corrupt.
    """
    return None
