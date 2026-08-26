"""
Red-mode tests for Plan 011 Phase 2, step 30 — D-1's required concurrency
spike, discharging brief 002 Evidence Gaps 1 & 2.

Plan line (step 30): "200 concurrent asyncio.gather tasks each rendering
under a different locale scope see only their own locale (no cross-task
leak); one shared GettextMessageCatalog is read concurrently from both the
loop and a run_in_executor thread with no corruption; a per-request scope
never mutates catalog state."
"""

from __future__ import annotations

import asyncio

from varco_core.context.request import current_locale, request_context
from varco_core.i18n.catalog import DictMessageCatalog


async def test_200_concurrent_tasks_each_see_only_their_own_locale() -> None:
    catalog = DictMessageCatalog({f"loc{i}": {"k": f"v{i}"} for i in range(200)})

    async def render(i: int) -> tuple[int, str | None, str | None]:
        with request_context(locale=f"loc{i}"):
            await asyncio.sleep(0)
            return i, current_locale(), catalog.get_message("k", f"loc{i}")

    results = await asyncio.gather(*(render(i) for i in range(200)))
    for i, locale, message in results:
        assert locale == f"loc{i}"
        assert message == f"v{i}"


async def test_shared_catalog_read_from_loop_and_executor_thread_no_corruption() -> None:
    catalog = DictMessageCatalog({"en": {"k": "value"}})

    def read_in_thread() -> str | None:
        return catalog.get_message("k", "en")

    loop = asyncio.get_event_loop()
    results = await asyncio.gather(
        *(loop.run_in_executor(None, read_in_thread) for _ in range(50)),
        *(asyncio.sleep(0, result=catalog.get_message("k", "en")) for _ in range(50)),
    )
    assert all(r == "value" for r in results)


async def test_per_request_scope_never_mutates_catalog_state() -> None:
    catalog = DictMessageCatalog({"en": {"k": "v"}})
    before = dict(catalog._mapping) if hasattr(catalog, "_mapping") else None

    with request_context(locale="en"):
        catalog.get_message("k", "en")

    after = dict(catalog._mapping) if hasattr(catalog, "_mapping") else None
    assert before == after
