"""
varco_core.cli.tenant
=======================
The ``tenant`` subcommand group — provisioning primitives for the tenant
control plane (Plan 007, Phase 9, step 5-6).

This is a control-plane tool (RD-4): any verb needing cluster DDL
(``--create-database``/``--create-schema``) refuses to run without an
explicit ``VARCO_TENANCY_ADMIN_DSN`` — the same confinement the REST admin
surface and ``SADatabaseProvisioner`` enforce.

Exit codes: ``0`` ok, ``1`` operation error, ``2`` usage error.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from varco_core.tenancy.catalog import StaticTenantCatalog
from varco_core.tenancy.provisioner import (
    DestructiveOperationRefused,
    ExternalTenantProvisioner,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``tenant`` subcommand parser and its verbs."""
    parser = subparsers.add_parser("tenant", help="Tenant control-plane operations")
    parser.set_defaults(_run=_run)

    verb_parsers = parser.add_subparsers(dest="verb", required=True)

    provision_p = verb_parsers.add_parser("provision")
    provision_p.add_argument("tenant_id")
    provision_p.add_argument(
        "--create-database",
        action="store_true",
        help="Provision a real database via cluster DDL (requires VARCO_TENANCY_ADMIN_DSN).",
    )
    provision_p.add_argument(
        "--create-schema",
        action="store_true",
        help="Provision a real Postgres schema via cluster DDL (requires VARCO_TENANCY_ADMIN_DSN).",
    )

    deprovision_p = verb_parsers.add_parser("deprovision")
    deprovision_p.add_argument("tenant_id")
    deprovision_p.add_argument(
        "--yes-i-really-mean-it", action="store_true", dest="confirm"
    )

    verb_parsers.add_parser("list")


async def _run_async(args: argparse.Namespace) -> int:
    catalog = StaticTenantCatalog()

    if args.verb == "list":
        tenants = await catalog.list_tenants(status=None)
        if not tenants:
            print("(no tenants)")
        for descriptor in tenants:
            print(f"{descriptor.tenant_id}\t{descriptor.status.value}")
        return 0

    if args.verb == "provision":
        needs_cluster_ddl = getattr(args, "create_database", False) or getattr(
            args, "create_schema", False
        )
        if needs_cluster_ddl and not os.environ.get("VARCO_TENANCY_ADMIN_DSN"):
            print(
                "Refusing to provision with cluster DDL "
                "(--create-database/--create-schema): VARCO_TENANCY_ADMIN_DSN "
                "is not set. Cluster DDL is confined to the control plane "
                "(RD-4) — set the admin DSN in the control plane's own "
                "environment, never an app pod's.",
                file=sys.stderr,
            )
            return 1

        provisioner = ExternalTenantProvisioner()
        try:
            await provisioner.provision(args.tenant_id)
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            print(str(exc), file=sys.stderr)
            return 1
        print(
            f"Provisioning requested for tenant {args.tenant_id!r} (status: pending)."
        )
        print(f"varco migrate upgrade --tenant {args.tenant_id}")
        return 0

    if args.verb == "deprovision":
        provisioner = ExternalTenantProvisioner()
        try:
            await provisioner.deprovision(args.tenant_id, confirm_destroy=args.confirm)
        except DestructiveOperationRefused as exc:
            print(
                f"{exc}\nThis would destroy tenant {args.tenant_id!r}'s data. "
                "Re-run with --yes-i-really-mean-it to confirm.",
                file=sys.stderr,
            )
            return 1
        print(f"Deprovisioning requested for tenant {args.tenant_id!r}.")
        return 0

    print(f"Unknown verb: {args.verb!r}", file=sys.stderr)
    return 2


def _run(args: argparse.Namespace) -> int:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run_async(args))

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _run_async(args))
        return future.result()


__all__ = ["register"]
