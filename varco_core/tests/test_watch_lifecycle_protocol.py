"""
Structural-conformance test: watch/reload objects satisfy ``AbstractLifecycle`` (Plan 025 / T1,
Step 14) with zero import from ``varco_core`` to ``varco_fastapi`` in production code.

NOTE: importing ``varco_fastapi`` here is test-only. ``varco-fastapi`` is already a
dev-only, test-time dependency of ``varco_core`` (varco_core/pyproject.toml) — this must
never be "fixed" into a runtime import inside varco_core/varco_core/.

RED phase: ``varco_core.watch`` / ``varco_core.reload`` do not exist yet.
"""

from __future__ import annotations

from pathlib import Path

from varco_core.reload import ReloadableResource
from varco_core.watch.base import WatchTarget
from varco_core.watch.poll import StatPollWatcher
from varco_fastapi.lifespan import AbstractLifecycle


def test_stat_poll_watcher_structurally_satisfies_abstract_lifecycle(tmp_path: Path) -> None:
    target = WatchTarget(root=tmp_path, patterns=("*",), recursive=True)
    watcher = StatPollWatcher([target])

    assert isinstance(watcher, AbstractLifecycle)


def test_reloadable_resource_structurally_satisfies_abstract_lifecycle() -> None:
    resource = ReloadableResource(loader=lambda: "v")

    assert isinstance(resource, AbstractLifecycle)
