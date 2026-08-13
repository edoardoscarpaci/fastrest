"""
Failing tests for the ``varco`` CLI's ``migrate`` subcommand (Plan 006,
Phase 5, step 49). Calls ``main(argv)`` directly — no subprocess.
"""

from __future__ import annotations

import json

import pytest

# Module-scope registration so `-t tests.test_cli_migrate:migrator_target`
# (the "module:callable" resolution form) can find it.
from varco_core.migration.base import Revision  # noqa: E402
from varco_core.migration.inmemory import InMemoryMigrator  # noqa: E402


def _pending_migrator() -> InMemoryMigrator:
    return InMemoryMigrator(revisions=[Revision(id="0001", label="init")])


def _empty_migrator() -> InMemoryMigrator:
    return InMemoryMigrator(revisions=[])


def _raising_migrator() -> InMemoryMigrator:
    return InMemoryMigrator(
        revisions=[Revision(id="0001", label="init")], fail_on_upgrade_call=1
    )


async def test_pending_exits_1_when_revisions_pending() -> None:
    from varco_core.cli.main import main

    exit_code = main(
        ["migrate", "pending", "-t", "tests.test_cli_migrate:_pending_migrator"]
    )

    assert exit_code == 1


async def test_pending_exits_0_when_nothing_pending() -> None:
    from varco_core.cli.main import main

    exit_code = main(
        ["migrate", "pending", "-t", "tests.test_cli_migrate:_empty_migrator"]
    )

    assert exit_code == 0


async def test_upgrade_on_inmemory_target_applies_and_exits_0() -> None:
    from varco_core.cli.main import main

    exit_code = main(
        ["migrate", "upgrade", "-t", "tests.test_cli_migrate:_pending_migrator"]
    )

    assert exit_code == 0


async def test_upgrade_on_raising_migrator_exits_1_with_error_on_stderr(
    capsys: pytest.CaptureFixture,
) -> None:
    from varco_core.cli.main import main

    exit_code = main(
        ["migrate", "upgrade", "-t", "tests.test_cli_migrate:_raising_migrator"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.strip() != ""


async def test_downgrade_without_yes_refuses_and_exits_2() -> None:
    from varco_core.cli.main import main

    exit_code = main(
        [
            "migrate",
            "downgrade",
            "-t",
            "tests.test_cli_migrate:_pending_migrator",
            "--to",
            "base",
        ]
    )

    assert exit_code == 2


async def test_unresolvable_target_prints_module_callable_form_and_exits_2(
    capsys: pytest.CaptureFixture,
) -> None:
    from varco_core.cli.main import main

    exit_code = main(["migrate", "pending", "-t", "not.a.real.module:whatever"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "module:callable" in captured.err or "module:callable" in captured.out


async def test_json_flag_emits_parseable_json_for_pending(
    capsys: pytest.CaptureFixture,
) -> None:
    from varco_core.cli.main import main

    exit_code = main(
        [
            "migrate",
            "pending",
            "-t",
            "tests.test_cli_migrate:_pending_migrator",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert isinstance(parsed, (list, dict))
    assert exit_code == 1


async def test_json_flag_emits_parseable_json_for_current(
    capsys: pytest.CaptureFixture,
) -> None:
    from varco_core.cli.main import main

    exit_code = main(
        [
            "migrate",
            "current",
            "-t",
            "tests.test_cli_migrate:_empty_migrator",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert isinstance(parsed, (list, dict))
    assert exit_code == 0
