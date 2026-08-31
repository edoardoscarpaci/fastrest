"""
Red test for RIDER-1 (Plan 022 / Phase 3, step 16).

``varco_redis.di.bootstrap()`` returns ``None`` when providify is absent
(``di.py``'s ``except ImportError: return None``).  ``async_bootstrap()``
assigns that ``None`` straight back into ``container`` and then calls
``await container.ainstall(...)``, so with ``setup_cache=True`` and no
providify installed the caller gets
``AttributeError: 'NoneType' object has no attribute 'ainstall'`` instead of
the documented graceful no-op.

providify *is* installed in this workspace, so the absent-providify path is
simulated at the only seam that matters: ``bootstrap`` returning ``None``.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def bootstrap_returns_none(monkeypatch: pytest.MonkeyPatch):
    """Simulate the providify-absent path without uninstalling providify."""
    import varco_redis.di as di

    monkeypatch.setattr(di, "bootstrap", lambda *args, **kwargs: None)
    return di


async def test_async_bootstrap_returns_none_when_bootstrap_returns_none(
    bootstrap_returns_none,
) -> None:
    """setup_cache=True is the crashing path — it is the one that must be guarded."""
    result = await bootstrap_returns_none.async_bootstrap(setup_cache=True)

    assert result is None


async def test_async_bootstrap_does_not_raise_attribute_error(
    bootstrap_returns_none,
) -> None:
    """The defect's exact symptom: 'NoneType' object has no attribute 'ainstall'."""
    try:
        await bootstrap_returns_none.async_bootstrap(setup_cache=True, streams=True)
    except AttributeError as exc:  # pragma: no cover - the failing branch
        pytest.fail(f"async_bootstrap() crashed instead of returning None: {exc}")


async def test_async_bootstrap_returns_none_without_cache_setup(
    bootstrap_returns_none,
) -> None:
    """setup_cache=False already survives — pin it so the guard does not change it."""
    assert await bootstrap_returns_none.async_bootstrap(setup_cache=False) is None
