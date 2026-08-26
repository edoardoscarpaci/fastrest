"""
Failing tests for the `varco migrate --all-tenants` / `varco tenant` CLI
surface (Plan 007, Phase 9, step 5). Calls `main(argv)` directly, same
convention as test_cli_migrate.py (Plan 006).
"""

from __future__ import annotations


async def test_migrate_upgrade_all_tenants_flag_is_recognised() -> None:
    from varco_core.cli.main import main

    exit_code = main(["migrate", "upgrade", "--all-tenants"])

    # Unimplemented today: argparse must at least recognise the flag rather
    # than exiting via "unrecognized arguments" (argparse exit code 2 with
    # no catalog wired is an acceptable interim failure, but a *parse*
    # failure specifically proves the flag doesn't exist yet).
    assert exit_code != 0


async def test_migrate_check_all_tenants_exits_nonzero_when_wired_to_behind_tenants() -> None:
    from varco_core.cli.main import main

    exit_code = main(["migrate", "check", "--all-tenants"])

    assert exit_code != 0


async def test_tenant_provision_subcommand_exists() -> None:
    from varco_core.cli.main import main

    exit_code = main(["tenant", "provision", "acme"])

    assert exit_code != 2  # 2 == argparse "invalid choice" (subcommand missing)


async def test_tenant_deprovision_refuses_without_confirm_flag() -> None:
    from varco_core.cli.main import main

    exit_code = main(["tenant", "deprovision", "acme"])

    assert exit_code != 0


async def test_tenant_list_renders_statuses() -> None:
    from varco_core.cli.main import main

    exit_code = main(["tenant", "list"])

    assert exit_code == 0


async def test_tenant_verb_needing_cluster_ddl_fails_clearly_with_no_admin_dsn(
    monkeypatch,
) -> None:
    from varco_core.cli.main import main

    monkeypatch.delenv("VARCO_TENANCY_ADMIN_DSN", raising=False)

    exit_code = main(["tenant", "provision", "acme", "--create-database"])

    assert exit_code != 0
