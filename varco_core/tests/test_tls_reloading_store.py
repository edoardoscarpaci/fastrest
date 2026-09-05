"""
Plan 026 / Step 11 — failing-first tests for ``varco_core.tls.reload.ReloadingTrustStore``
(§D-T3-reload).

Oracle note (Risk section, "the MUTATE branch is observable"): verified empirically before
writing these tests (see the plan's Risk paragraph) that ``ssl.SSLContext.get_ca_certs()``
DOES observe a live ``load_verify_locations`` addition on the *same* context object —

    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=str(ca1_path))
    len(ctx.get_ca_certs())        # 1
    ctx.load_verify_locations(cafile=str(ca2_path))
    len(ctx.get_ca_certs())        # 2 -- same object, growth is visible

So the MUTATE-branch assertions below use the introspection oracle
(``id(store.context)`` unchanged + ``get_ca_certs()`` grew), NOT a loopback handshake. If this
ever regresses, per the plan's Risk section the fix is to change the oracle to an actual
handshake — not to delete the test.

``varco_core.tls`` does not exist yet — every test fails with ``ModuleNotFoundError`` until
Step 12 lands ``varco_core/varco_core/tls/reload.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from _tls_test_certs import mint_ca, write_pem


@pytest.fixture
def ca_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "ca_folder"
    folder.mkdir()
    ca = mint_ca("ca-initial")
    write_pem(folder / "ca-initial.pem", ca)
    return folder


async def test_start_loads_and_context_is_usable(ca_folder: Path) -> None:
    from varco_core.tls.reload import ReloadingTrustStore
    from varco_core.tls.store import TrustStore

    spec = TrustStore(ca_folders=ca_folder, include_system_cas=False)
    store = ReloadingTrustStore(spec)
    await store.start()
    try:
        assert store.context is not None
        assert len(store.context.get_ca_certs()) == 1
    finally:
        await store.stop()


async def test_context_raises_before_start() -> None:
    from varco_core.tls.reload import ReloadingTrustStore
    from varco_core.tls.store import TrustStore

    spec = TrustStore(ca_folders=None, include_system_cas=True)
    store = ReloadingTrustStore(spec)

    with pytest.raises(Exception):  # ResourceNotLoadedError, from Plan 025 / T2
        _ = store.context


async def test_adding_a_ca_file_mutates_without_changing_context_identity(
    ca_folder: Path, tmp_path: Path
) -> None:
    from varco_core.tls.reload import ReloadingTrustStore
    from varco_core.tls.store import TrustStore
    from varco_core.watch import WatchTarget, default_watcher

    spec = TrustStore(ca_folders=ca_folder, include_system_cas=False)
    watcher = default_watcher([WatchTarget(root=ca_folder, patterns=("*.pem",))], interval=0.05)
    store = ReloadingTrustStore(spec, watcher=watcher)
    await store.start()
    try:
        ctx_before = store.context
        certs_before = len(ctx_before.get_ca_certs())

        new_ca = mint_ca("ca-added")
        write_pem(ca_folder / "ca-added.pem", new_ca)

        await _wait_until(lambda: len(store.context.get_ca_certs()) > certs_before)

        assert store.context is ctx_before  # MUTATE: identity unchanged
        assert len(store.context.get_ca_certs()) == certs_before + 1
        assert store.generation == 0  # MUTATE never bumps generation
    finally:
        await store.stop()


async def test_removing_a_file_swaps_context_and_bumps_generation(ca_folder: Path) -> None:
    from varco_core.tls.reload import ReloadingTrustStore
    from varco_core.tls.store import TrustStore
    from varco_core.watch import WatchTarget, default_watcher

    watcher = default_watcher([WatchTarget(root=ca_folder, patterns=("*.pem",))], interval=0.05)
    spec = TrustStore(ca_folders=ca_folder, include_system_cas=False)
    store = ReloadingTrustStore(spec, watcher=watcher)
    await store.start()
    try:
        ctx_before = store.context
        gen_before = store.generation

        for f in ca_folder.glob("*.pem"):
            f.unlink()
        # Removing the only CA leaves zero — replace with a fresh one so build succeeds too.
        new_ca = mint_ca("ca-replacement")
        write_pem(ca_folder / "ca-replacement.pem", new_ca)

        await _wait_until(lambda: store.generation > gen_before, timeout=5.0)

        assert store.context is not ctx_before  # SWAP: identity changed
        assert store.generation == gen_before + 1
    finally:
        await store.stop()


async def test_explicit_swap_strategy_always_swaps_even_for_additions_only(
    ca_folder: Path,
) -> None:
    from varco_core.tls.reload import ReloadingTrustStore, ReloadStrategy
    from varco_core.tls.store import TrustStore
    from varco_core.watch import WatchTarget, default_watcher

    watcher = default_watcher([WatchTarget(root=ca_folder, patterns=("*.pem",))], interval=0.05)
    spec = TrustStore(ca_folders=ca_folder, include_system_cas=False)
    store = ReloadingTrustStore(spec, watcher=watcher, strategy=ReloadStrategy.SWAP)
    await store.start()
    try:
        ctx_before = store.context
        gen_before = store.generation

        new_ca = mint_ca("ca-added-2")
        write_pem(ca_folder / "ca-added-2.pem", new_ca)

        await _wait_until(lambda: store.generation > gen_before, timeout=5.0)

        assert store.context is not ctx_before
    finally:
        await store.stop()


async def test_mid_rotation_unreadable_file_keeps_last_good(ca_folder: Path) -> None:
    from varco_core.tls.reload import ReloadingTrustStore
    from varco_core.tls.store import TrustStore
    from varco_core.watch import WatchTarget, default_watcher

    watcher = default_watcher([WatchTarget(root=ca_folder, patterns=("*.pem",))], interval=0.05)
    spec = TrustStore(ca_folders=ca_folder, include_system_cas=False)
    store = ReloadingTrustStore(spec, watcher=watcher)
    await store.start()
    try:
        ctx_before = store.context

        (ca_folder / "garbage.pem").write_text("not a valid certificate at all")

        # Give the watcher/reload loop a chance to observe and fail on the bad file.
        await asyncio.sleep(0.5)

        # keep-last-good: context is still usable and unchanged.
        assert store.context is ctx_before
    finally:
        await store.stop()


async def test_subscribe_fires_once_per_successful_swap(ca_folder: Path) -> None:
    from varco_core.tls.reload import ReloadingTrustStore, ReloadStrategy
    from varco_core.tls.store import TrustStore
    from varco_core.watch import WatchTarget, default_watcher

    watcher = default_watcher([WatchTarget(root=ca_folder, patterns=("*.pem",))], interval=0.05)
    spec = TrustStore(ca_folders=ca_folder, include_system_cas=False)
    store = ReloadingTrustStore(spec, watcher=watcher, strategy=ReloadStrategy.SWAP)
    await store.start()
    calls: list[object] = []
    unsubscribe = store.subscribe(lambda ctx: calls.append(ctx))
    try:
        new_ca = mint_ca("ca-added-3")
        write_pem(ca_folder / "ca-added-3.pem", new_ca)

        await _wait_until(lambda: len(calls) >= 1, timeout=5.0)

        assert len(calls) == 1
    finally:
        unsubscribe()
        await store.stop()


async def test_stop_is_idempotent(ca_folder: Path) -> None:
    from varco_core.tls.reload import ReloadingTrustStore
    from varco_core.tls.store import TrustStore

    spec = TrustStore(ca_folders=ca_folder, include_system_cas=False)
    store = ReloadingTrustStore(spec)
    await store.start()
    await store.stop()
    await store.stop()  # must not raise


async def test_async_context_manager(ca_folder: Path) -> None:
    from varco_core.tls.reload import ReloadingTrustStore
    from varco_core.tls.store import TrustStore

    spec = TrustStore(ca_folders=ca_folder, include_system_cas=False)
    store = ReloadingTrustStore(spec)

    async with store:
        assert store.context is not None


async def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.05) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")
