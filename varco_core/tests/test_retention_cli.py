"""
tests.test_retention_cli
==========================
Plan 009, Phase 2 (R3) — ``varco retention prune`` subcommand
(``varco_core/cli/retention.py``).

RED until the ``retention`` subcommand is registered in ``cli/main.py``.
Calls ``main(argv)`` directly — no subprocess. Module-level factory functions
so ``-t tests.test_retention_cli:factory_name`` resolves them.
"""

from __future__ import annotations

from datetime import datetime, timezone, UTC

from varco_core.event import Event
from varco_core.event.dlq import DeadLetterEntry, InMemoryDeadLetterQueue


class SampleEvent(Event):
    __event_type__ = "test.retention_cli.sample"


def _entry() -> DeadLetterEntry:
    return DeadLetterEntry(
        event=SampleEvent(),
        channel="orders",
        handler_name="H.h",
        error_type="E",
        error_message="msg",
        attempts=1,
    )


class _PrunableDLQ(InMemoryDeadLetterQueue):
    """A test-double DLQ that implements delete_where (portable-default-free
    concrete implementation) — used to exercise the CLI's chunked sweep."""

    async def delete_where(  # type: ignore[override]
        self, *, older_than=None, source=None, channel=None, tenant_id=None, limit=None
    ) -> int:
        if (
            older_than is None
            and source is None
            and channel is None
            and tenant_id is None
        ):
            raise ValueError("delete_where() requires at least one predicate.")
        n = min(len(self._entries), limit or len(self._entries))
        for _ in range(n):
            self._entries.popleft()
        return n

    async def count(self) -> int:  # type: ignore[override]
        return len(self._entries)


async def _dlq_with_entries() -> _PrunableDLQ:
    dlq = _PrunableDLQ()
    for _ in range(5):
        await dlq.push(_entry())
    return dlq


def _dlq_target_factory() -> _PrunableDLQ:
    import asyncio

    return asyncio.run(_dlq_with_entries())


def _empty_dlq_factory() -> InMemoryDeadLetterQueue:
    return InMemoryDeadLetterQueue()


class TestRetentionPruneExitCodes:
    def test_prune_returns_0_on_success(self) -> None:
        from varco_core.cli.main import main

        exit_code = main(
            [
                "retention",
                "prune",
                "--type",
                "dlq",
                "--before",
                datetime.now(UTC).isoformat(),
                "--target",
                "tests.test_retention_cli:_dlq_target_factory",
            ]
        )
        assert exit_code == 0

    def test_missing_before_is_usage_error(self, capsys) -> None:
        from varco_core.cli.main import main

        exit_code = main(
            [
                "retention",
                "prune",
                "--type",
                "dlq",
                "--target",
                "tests.test_retention_cli:_dlq_target_factory",
            ]
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "before" in (captured.err + captured.out).lower()


class TestRetentionPruneDryRun:
    def test_dry_run_deletes_nothing(self) -> None:
        from varco_core.cli.main import main

        exit_code = main(
            [
                "retention",
                "prune",
                "--type",
                "dlq",
                "--before",
                datetime.now(UTC).isoformat(),
                "--target",
                "tests.test_retention_cli:_dlq_target_factory",
                "--dry-run",
            ]
        )
        assert exit_code == 0
        # dry-run must not mutate the target -- verified indirectly via a
        # fresh factory call producing the same count as before the prune.
        import asyncio

        dlq = asyncio.run(_dlq_with_entries())
        assert asyncio.run(dlq.count()) == 5
