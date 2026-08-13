"""
varco_beanie.migration.indexes
================================
``IndexReconciler`` — applies the *missing* half of ``BeanieIndexGuard``'s
drift report (D5). Never drops ``unexpected_indexes``.

DESIGN: only create missing indexes, never drop unexpected ones
    ✅ Dropping an index someone added deliberately outside varco's model
       (e.g. a DBA-added compound index for a slow query) is destructive
       and out of scope — this reconciler only fills gaps.
    ❌ ``unexpected_indexes`` accumulate forever unless an operator cleans
       them up by hand — documented, not solved here.

Thread safety:  ✅ Stateless — delegates to ``BeanieIndexGuard``.
Async safety:   ✅ All methods are ``async def``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from varco_beanie.index_guard import BeanieIndexGuard, IndexDriftReport


class IndexReconciler:
    """
    Applies missing indexes reported by a ``BeanieIndexGuard``.

    ⚠️ **D5 warning**: ``apply()`` builds indexes synchronously against a
    live collection — on a large collection this is minutes-to-hours of
    work, and on a replica set it replicates and can stall secondaries.
    This is why ``BeanieMigrator``'s ``index_mode`` defaults to
    ``"check"`` even under ``mode="upgrade"`` (Plan 006 D5) — running
    ``apply()`` belongs in a pre-deploy job
    (``varco migrate beanie index --create``), not a startup hook.

    Args:
        guard: The ``BeanieIndexGuard`` to consult.
        db:    The ``AsyncIOMotorDatabase`` to reconcile against.
    """

    def __init__(self, guard: BeanieIndexGuard, db: Any) -> None:
        self._guard = guard
        self._db = db

    async def report(self) -> IndexDriftReport:
        """Delegate to ``BeanieIndexGuard.report()`` verbatim."""
        return await self._guard.report(self._db)

    async def apply(self) -> IndexDriftReport:
        """
        Create every currently-missing index, never drop unexpected ones.

        ⚠️ Unsafe on large collections — see the D5 warning in the class
        docstring. Prefer running this from the CLI as a pre-deploy job.

        Returns:
            The ``IndexDriftReport`` observed BEFORE applying — a caller
            wanting the post-apply state should call ``report()`` again.
        """
        from pymongo import ASCENDING, IndexModel

        drift = await self._guard.report(self._db)
        if not drift.missing_indexes:
            return drift

        # BeanieIndexGuard's own report only carries human labels — reuse
        # its private expected-index builder to get the (key_fields, unique)
        # tuples needed to actually create the missing ones. This mirrors
        # BeanieIndexGuard._compare()'s own matching logic (key tuple +
        # unique flag, not by name) so "missing" here means exactly what
        # the guard's report says is missing.
        expected = self._guard._build_expected_indexes()

        by_collection: dict[str, list[Any]] = {}
        for idx in expected:
            by_collection.setdefault(idx.collection, []).append(idx)

        for collection_name in drift.missing_indexes:
            actual_raw = await self._db[collection_name].index_information()
            actual_sigs = {
                (
                    tuple(f for f, _ in info.get("key", [])),
                    bool(info.get("unique", False)),
                )
                for name, info in actual_raw.items()
                if name != "_id_"
            }

            to_create = [
                IndexModel(
                    [(field, ASCENDING) for field in exp.key_fields],
                    unique=exp.unique,
                )
                for exp in by_collection.get(collection_name, [])
                if (exp.key_fields, exp.unique) not in actual_sigs
            ]
            if to_create:
                await self._db[collection_name].create_indexes(to_create)

        return drift


__all__ = ["IndexReconciler"]
