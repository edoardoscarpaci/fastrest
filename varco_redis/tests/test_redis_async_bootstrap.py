"""
tests.test_redis_async_bootstrap
===================================
Plan 014 / audit F7 — pins the *reference contract*
``varco_redis.di.async_bootstrap()`` establishes: with no ``setup_cache``
kwarg, no cache is installed; ``setup_cache=True`` installs it.

No existing test in this package covers ``async_bootstrap`` at all — this
file closes that gap and is the mirror of the memcached characterization
added alongside it in ``varco_memcached/tests/test_di.py`` (Plan 014 step
12). Written against **unmodified** production code (Phase 1 of Plan 014).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from varco_redis.di import async_bootstrap


def _make_mock_container() -> MagicMock:
    """Build a ``MagicMock`` standing in for a ``DIContainer``."""
    mock = MagicMock()
    mock.ainstall = AsyncMock(return_value=None)
    return mock


class TestAsyncBootstrapSetupCacheDefault:
    async def test_no_kwargs_does_not_call_ainstall(self) -> None:
        """
        ``await async_bootstrap(container)`` with no kwargs must NOT call
        ``container.ainstall(...)`` — ``setup_cache`` defaults to ``False``.

        This is the reference shape Plan 014 / F7's design section points
        to: ``varco_memcached``'s new ``setup_cache: bool = True`` default
        is deliberately the opposite, and this test is what pins the redis
        side of that documented asymmetry.
        """
        container = _make_mock_container()

        with patch("varco_redis.di.bootstrap", return_value=container):
            result = await async_bootstrap(container)

        container.ainstall.assert_not_awaited()
        assert result is container

    async def test_setup_cache_true_calls_ainstall(self) -> None:
        """``setup_cache=True`` installs ``RedisCacheConfiguration`` via ``ainstall``."""
        from varco_redis.cache import RedisCacheConfiguration  # noqa: PLC0415

        container = _make_mock_container()

        with patch("varco_redis.di.bootstrap", return_value=container):
            await async_bootstrap(container, setup_cache=True)

        container.ainstall.assert_awaited_once_with(RedisCacheConfiguration)

    async def test_setup_cache_false_explicit_does_not_call_ainstall(self) -> None:
        """``setup_cache=False`` (explicit) is identical to the default — no install."""
        container = _make_mock_container()

        with patch("varco_redis.di.bootstrap", return_value=container):
            result = await async_bootstrap(container, setup_cache=False)

        container.ainstall.assert_not_awaited()
        assert result is container
