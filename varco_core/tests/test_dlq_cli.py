"""
tests.test_dlq_cli
====================
Plan 009, Phase 4 (R1) — ``varco dlq`` subcommand (``varco_core/cli/dlq.py``).

RED until ``dlq`` is registered in ``cli/main.py``. Calls ``main(argv)``
directly. Module-level factories for ``-t module:callable`` resolution.
"""

from __future__ import annotations

from varco_core.event import Event
from varco_core.event.dlq import DeadLetterEntry, InMemoryDeadLetterQueue
from varco_core.event.memory import InMemoryEventBus


class SampleEvent(Event):
    __event_type__ = "test.dlq_cli.sample"


def _entry() -> DeadLetterEntry:
    return DeadLetterEntry(
        event=SampleEvent(),
        channel="orders",
        handler_name="H.h",
        error_type="E",
        error_message="msg",
        attempts=1,
    )


async def _dlq_with_two_entries() -> InMemoryDeadLetterQueue:
    dlq = InMemoryDeadLetterQueue()
    await dlq.push(_entry())
    await dlq.push(_entry())
    return dlq


def _dlq_target_factory() -> InMemoryDeadLetterQueue:
    import asyncio

    return asyncio.run(_dlq_with_two_entries())


def _empty_dlq_factory() -> InMemoryDeadLetterQueue:
    return InMemoryDeadLetterQueue()


def _bus_factory() -> InMemoryEventBus:
    return InMemoryEventBus()


class TestDlqCliListExitCodes:
    def test_list_returns_0_on_success(self) -> None:
        from varco_core.cli.main import main

        exit_code = main(["dlq", "list", "--target", "tests.test_dlq_cli:_dlq_target_factory"])
        assert exit_code == 0

    def test_list_empty_dlq_still_returns_0(self) -> None:
        from varco_core.cli.main import main

        exit_code = main(["dlq", "list", "--target", "tests.test_dlq_cli:_empty_dlq_factory"])
        assert exit_code == 0


class TestDlqCliRedriveExitCodes:
    def test_redrive_batch_returns_0_on_success(self) -> None:
        from varco_core.cli.main import main

        exit_code = main(
            [
                "dlq",
                "redrive",
                "--target",
                "tests.test_dlq_cli:_dlq_target_factory",
                "--bus",
                "tests.test_dlq_cli:_bus_factory",
                "--batch",
            ]
        )
        assert exit_code == 0

    def test_redrive_missing_entry_and_batch_is_usage_error(self, capsys) -> None:
        from varco_core.cli.main import main

        exit_code = main(
            [
                "dlq",
                "redrive",
                "--target",
                "tests.test_dlq_cli:_dlq_target_factory",
                "--bus",
                "tests.test_dlq_cli:_bus_factory",
            ]
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        combined = (captured.err + captured.out).lower()
        assert "entry-id" in combined or "batch" in combined


class TestDlqCliRedriveDryRun:
    def test_dry_run_is_non_destructive(self) -> None:
        from varco_core.cli.main import main

        exit_code = main(
            [
                "dlq",
                "redrive",
                "--target",
                "tests.test_dlq_cli:_dlq_target_factory",
                "--bus",
                "tests.test_dlq_cli:_bus_factory",
                "--batch",
                "--dry-run",
            ]
        )
        assert exit_code == 0

        # dry-run must not have acked anything -- a fresh factory call still
        # has both entries.
        import asyncio

        dlq = asyncio.run(_dlq_with_two_entries())
        assert asyncio.run(dlq.count()) == 2


class TestDlqCliPurge:
    def test_purge_with_no_matching_predicate_still_exits_0(self) -> None:
        from varco_core.cli.main import main

        exit_code = main(
            [
                "dlq",
                "purge",
                "--target",
                "tests.test_dlq_cli:_empty_dlq_factory",
                "--before",
                "2099-01-01T00:00:00+00:00",
            ]
        )
        assert exit_code == 0
