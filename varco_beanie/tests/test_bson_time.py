"""
Docker-free regression tests for varco_beanie._bson_time and the retention
predicates that use it.

User reports: a chunked ``delete_where(older_than=cutoff, limit=2)`` sweep of
5 dead letters totals 4 and then returns 0, leaving one entry behind. Correct
behaviour is 5, because BSON's millisecond resolution must not shrink the
``older_than`` predicate — the store's own reported ``last_failed_at`` for the
stranded entry is strictly before the cutoff.

These tests need no MongoDB: they exercise the rounding helper directly and
capture the query dict handed to Beanie.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from varco_beanie._bson_time import ceil_to_bson_millisecond

_BASE = datetime(2026, 1, 1, 12, 0, 0, 0, tzinfo=UTC)


class TestCeilToBsonMillisecond:
    def test_regression_sub_millisecond_value_rounds_up(self) -> None:
        assert ceil_to_bson_millisecond(_BASE + timedelta(microseconds=3_100)) == _BASE + timedelta(
            microseconds=4_000
        )

    def test_already_aligned_value_is_unchanged(self) -> None:
        """An aligned cutoff must keep exact ``$lt`` semantics — no widening."""
        aligned = _BASE + timedelta(microseconds=3_000)
        assert ceil_to_bson_millisecond(aligned) is aligned

    def test_one_microsecond_past_a_boundary_rounds_up_a_full_millisecond(self) -> None:
        assert ceil_to_bson_millisecond(_BASE + timedelta(microseconds=1)) == _BASE + timedelta(
            microseconds=1_000
        )

    def test_last_microsecond_of_a_millisecond_rounds_up(self) -> None:
        assert ceil_to_bson_millisecond(_BASE + timedelta(microseconds=3_999)) == _BASE + timedelta(
            microseconds=4_000
        )

    def test_rolls_over_the_second_boundary(self) -> None:
        end = _BASE + timedelta(seconds=1) - timedelta(microseconds=1)
        assert ceil_to_bson_millisecond(end) == _BASE + timedelta(seconds=1)

    def test_tzinfo_is_preserved(self) -> None:
        assert ceil_to_bson_millisecond(_BASE + timedelta(microseconds=1)).tzinfo is UTC

    def test_naive_datetime_is_accepted_and_stays_naive(self) -> None:
        naive = datetime(2026, 1, 1, 12, 0, 0, 3_100)
        assert ceil_to_bson_millisecond(naive).tzinfo is None
        assert ceil_to_bson_millisecond(naive).microsecond == 4_000

    def test_epoch_zero_microsecond_is_unchanged(self) -> None:
        assert ceil_to_bson_millisecond(_BASE) == _BASE


class _FakeFind:
    """Minimal stand-in for a Beanie ``FindMany`` — records nothing itself."""

    def __init__(self, docs: list[Any] | None = None) -> None:
        self._docs = docs or []

    def limit(self, _n: int) -> _FakeFind:
        return self

    def skip(self, _n: int) -> _FakeFind:
        return self

    def sort(self, _s: str) -> _FakeFind:
        return self

    async def to_list(self) -> list[Any]:
        return list(self._docs)

    def __aiter__(self) -> _FakeFind:
        self._it = iter(self._docs)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.fixture
def captured_queries(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from varco_beanie.dlq import DeadLetterDocument

    seen: list[Any] = []

    def _find(*args: Any, **_kwargs: Any) -> _FakeFind:
        seen.append(args[0] if args else {})
        return _FakeFind()

    monkeypatch.setattr(DeadLetterDocument, "find", _find)
    return seen


class TestDeadLetterQueryPredicateRounding:
    async def test_regression_delete_where_widens_older_than_to_next_millisecond(
        self, captured_queries: list[Any]
    ) -> None:
        """The stranded-entry bug: a floored ``$lt`` operand excludes every
        entry stored in the cutoff's own millisecond."""
        from varco_beanie.dlq import BeanieDeadLetterQueue

        cutoff = _BASE + timedelta(microseconds=3_900)
        assert await BeanieDeadLetterQueue().delete_where(older_than=cutoff, limit=2) == 0

        assert captured_queries[0]["last_failed_at"] == {
            "$lt": _BASE + timedelta(microseconds=4_000)
        }

    async def test_regression_list_entries_widens_older_than(
        self, captured_queries: list[Any]
    ) -> None:
        from varco_beanie.dlq import BeanieDeadLetterQueue

        await BeanieDeadLetterQueue().list_entries(older_than=_BASE + timedelta(microseconds=3_900))

        assert captured_queries[0]["last_failed_at"] == {
            "$lt": _BASE + timedelta(microseconds=4_000)
        }

    async def test_regression_newer_than_is_not_widened(self, captured_queries: list[Any]) -> None:
        """``$gt`` must keep pymongo's floor — widening it would over-match."""
        from varco_beanie.dlq import BeanieDeadLetterQueue

        moment = _BASE + timedelta(microseconds=3_900)
        await BeanieDeadLetterQueue().list_entries(newer_than=moment)

        assert captured_queries[0]["last_failed_at"] == {"$gt": moment}

    async def test_millisecond_aligned_cutoff_is_passed_through_unchanged(
        self, captured_queries: list[Any]
    ) -> None:
        from varco_beanie.dlq import BeanieDeadLetterQueue

        cutoff = _BASE + timedelta(microseconds=4_000)
        await BeanieDeadLetterQueue().delete_where(older_than=cutoff)

        assert captured_queries[0]["last_failed_at"] == {"$lt": cutoff}
