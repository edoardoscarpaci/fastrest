"""
Unit tests for varco_memcached.di
===================================
Tests for ``bootstrap()`` and ``async_bootstrap()``.

No real Memcached instance is required — ``MemcachedCacheConfiguration``
is mocked via ``unittest.mock`` to verify the DI wiring without opening
any network connections.

Sections
--------
- ``bootstrap``       — returns container, creates container when None, ImportError guard
- ``async_bootstrap`` — delegates to bootstrap, calls ainstall
- ``__init__`` re-export — bootstrap and async_bootstrap importable from package root
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch


from varco_memcached.di import async_bootstrap, bootstrap


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_mock_container() -> MagicMock:
    """
    Build a ``MagicMock`` that stands in for a ``DIContainer``.

    ``ainstall`` must be an ``AsyncMock`` so ``await container.ainstall(...)``
    doesn't raise a ``TypeError`` inside the async path.
    """
    mock = MagicMock()
    # ainstall is called with ``await`` — it must be coroutine-compatible
    mock.ainstall = AsyncMock(return_value=None)
    return mock


# ── bootstrap ─────────────────────────────────────────────────────────────────


class TestBootstrap:
    """Tests for the synchronous ``bootstrap()`` helper."""

    def test_returns_provided_container(self) -> None:
        """
        bootstrap(container) must return the same container object.

        Ensures that callers who chain calls can fluently pass the result
        to the next bootstrap step.
        """
        container = _make_mock_container()
        result = bootstrap(container)
        assert result is container, (
            "bootstrap() must return the container it was given so callers "
            "can chain: container = bootstrap(container)."
        )

    def test_scans_varco_memcached_package(self) -> None:
        """
        bootstrap() must call ``container.scan("varco_memcached", recursive=True)``.

        The scan is what auto-discovers @Singleton classes
        (MemcachedCacheSettings, MemcachedHealthCheck).
        """
        container = _make_mock_container()
        bootstrap(container)
        container.scan.assert_called_once_with("varco_memcached", recursive=True)

    def test_uses_di_container_current_when_none(self) -> None:
        """
        When container=None, bootstrap() must fall back to DIContainer.current().

        This mirrors every other varco bootstrap() — it makes the single-container
        app case work with no explicit container argument.
        """
        mock_container = _make_mock_container()

        # Patch sys.modules["providify"] so the local import inside bootstrap()
        # resolves our mock DIContainer instead of the real one.
        # This is the only reliable approach because bootstrap() does
        # ``from providify import DIContainer`` inside the function body —
        # patching the di module's namespace directly would not intercept
        # that local import statement.
        mock_di_container_cls = MagicMock()
        mock_di_container_cls.current.return_value = mock_container

        fake_providify = MagicMock()
        fake_providify.DIContainer = mock_di_container_cls

        original = sys.modules.get("providify")
        sys.modules["providify"] = fake_providify  # type: ignore[assignment]
        try:
            result = bootstrap(None)
        finally:
            # Restore original so other tests using real providify are unaffected
            if original is None:
                sys.modules.pop("providify", None)
            else:
                sys.modules["providify"] = original

        mock_di_container_cls.current.assert_called_once()
        assert result is mock_container

    def test_importerror_guard_returns_none(self) -> None:
        """
        bootstrap() must return None when providify is not installed.

        This ensures the package loads cleanly in environments where
        providify is absent (e.g. libraries used without a DI container).
        """
        # Temporarily hide providify from sys.modules to simulate absence
        original = sys.modules.get("providify")
        # Inject a sentinel that raises ImportError when imported
        sys.modules["providify"] = None  # type: ignore[assignment]
        try:
            result = bootstrap(_make_mock_container())
        finally:
            if original is None:
                sys.modules.pop("providify", None)
            else:
                sys.modules["providify"] = original

        assert result is None, (
            "bootstrap() must return None when providify is absent so callers "
            "don't crash when the DI framework is optional."
        )

    def test_importerror_guard_no_container(self) -> None:
        """
        bootstrap(None) must return None (not raise) when providify is absent.

        The None path runs DIContainer.current() which would raise
        AttributeError — the ImportError guard must short-circuit before that.
        """
        original = sys.modules.get("providify")
        sys.modules["providify"] = None  # type: ignore[assignment]
        try:
            result = bootstrap(None)  # no container — would blow up without guard
        finally:
            if original is None:
                sys.modules.pop("providify", None)
            else:
                sys.modules["providify"] = original

        assert result is None


# ── async_bootstrap ───────────────────────────────────────────────────────────


class TestAsyncBootstrap:
    """Tests for the asynchronous ``async_bootstrap()`` helper."""

    async def test_returns_container(self) -> None:
        """
        async_bootstrap() must return the container so callers can chain awaits.
        """
        container = _make_mock_container()

        # MemcachedCacheConfiguration is imported locally inside async_bootstrap
        # (not at module level), so we must patch it at its source module.
        # Patching "varco_memcached.di.MemcachedCacheConfiguration" would fail
        # because the name doesn't exist in the di module's namespace.
        with patch("varco_memcached.di.bootstrap", return_value=container), patch(
            "varco_memcached.cache.MemcachedCacheConfiguration"
        ):
            result = await async_bootstrap(container)

        assert result is container

    async def test_calls_bootstrap_first(self) -> None:
        """
        async_bootstrap() must delegate the sync scan to bootstrap() before
        doing any async work.

        This ensures the @Singleton classes are registered before ainstall()
        tries to resolve them.
        """
        container = _make_mock_container()

        with patch(
            "varco_memcached.di.bootstrap", return_value=container
        ) as mock_bs, patch("varco_memcached.cache.MemcachedCacheConfiguration"):
            await async_bootstrap(container)

        mock_bs.assert_called_once_with(container)

    async def test_calls_ainstall_with_configuration(self) -> None:
        """
        async_bootstrap() must call ``await container.ainstall(MemcachedCacheConfiguration)``.

        ``ainstall`` is what triggers ``MemcachedCacheConfiguration.setup()``,
        which constructs and starts the cache.  Without this call the
        CacheBackend binding is never registered.

        The local import inside ``async_bootstrap()`` means we verify
        ``container.ainstall`` was called with the *real*
        ``MemcachedCacheConfiguration`` class, not a mock.
        """
        from varco_memcached.cache import MemcachedCacheConfiguration  # noqa: PLC0415

        container = _make_mock_container()

        with patch("varco_memcached.di.bootstrap", return_value=container):
            await async_bootstrap(container)

        # ainstall must be awaited exactly once with the real configuration class
        container.ainstall.assert_awaited_once_with(MemcachedCacheConfiguration)

    async def test_no_kwargs_call_awaits_ainstall_exactly_once(self) -> None:
        """
        Plan 014 / audit F7 — characterization of TODAY'S unconditional
        install: ``await async_bootstrap(container)`` with **no kwargs**
        must await ``container.ainstall(MemcachedCacheConfiguration)``
        exactly once.

        This is the behaviour Plan 014 step 18's new ``setup_cache: bool =
        True`` parameter must preserve byte-for-byte as its default — this
        test is written before that parameter exists, precisely so it pins
        the pre-change contract (see the plan's Risks section).
        """
        from varco_memcached.cache import MemcachedCacheConfiguration  # noqa: PLC0415

        container = _make_mock_container()

        with patch("varco_memcached.di.bootstrap", return_value=container):
            await async_bootstrap(container)

        container.ainstall.assert_awaited_once_with(MemcachedCacheConfiguration)

    async def test_passes_none_container_to_bootstrap(self) -> None:
        """
        async_bootstrap(None) must pass None through to bootstrap() so
        bootstrap()'s DIContainer.current() fallback logic kicks in.
        """
        mock_container = _make_mock_container()

        with patch(
            "varco_memcached.di.bootstrap", return_value=mock_container
        ) as mock_bs, patch("varco_memcached.cache.MemcachedCacheConfiguration"):
            await async_bootstrap(None)

        # None must be forwarded — not resolved here — so bootstrap() can apply
        # its own DIContainer.current() fallback consistently.
        mock_bs.assert_called_once_with(None)

    async def test_setup_cache_false_does_not_await_ainstall(self) -> None:
        """
        Plan 014 / audit F7, step 19 — ``setup_cache=False`` must not await
        ``container.ainstall(...)`` and must still return the container.
        """
        container = _make_mock_container()

        with patch("varco_memcached.di.bootstrap", return_value=container):
            result = await async_bootstrap(container, setup_cache=False)

        container.ainstall.assert_not_awaited()
        assert result is container

    async def test_setup_cache_false_with_providify_absent_returns_none(self) -> None:
        """
        Plan 014 / audit F7, step 19 — ``setup_cache=False`` with providify
        absent still returns ``None`` (the ``container is None`` guard runs
        before the ``setup_cache`` branch either way).
        """
        original = sys.modules.get("providify")
        sys.modules["providify"] = None  # type: ignore[assignment]
        try:
            result = await async_bootstrap(_make_mock_container(), setup_cache=False)
        finally:
            if original is None:
                sys.modules.pop("providify", None)
            else:
                sys.modules["providify"] = original

        assert result is None

    async def test_setup_cache_default_true_awaits_ainstall(self) -> None:
        """
        Plan 014 / audit F7, step 19 — the default (``setup_cache=True``)
        path is unchanged: ``ainstall`` is still awaited exactly once.
        """
        from varco_memcached.cache import MemcachedCacheConfiguration  # noqa: PLC0415

        container = _make_mock_container()

        with patch("varco_memcached.di.bootstrap", return_value=container):
            result = await async_bootstrap(container)

        container.ainstall.assert_awaited_once_with(MemcachedCacheConfiguration)
        assert result is container

    async def test_importerror_guard_returns_none(self) -> None:
        """
        async_bootstrap() must return None (not raise AttributeError) when
        providify is not installed.

        Before the fix, ``container = bootstrap(container)`` would return
        ``None`` and the subsequent ``await container.ainstall(...)`` would
        crash with ``AttributeError: 'NoneType' object has no attribute
        'ainstall'``.  The guard must short-circuit before that point.
        """
        # Temporarily hide providify from sys.modules to simulate absence.
        # bootstrap() — called inside async_bootstrap() — checks for providify
        # and returns None when it is absent.
        original = sys.modules.get("providify")
        sys.modules["providify"] = None  # type: ignore[assignment]
        try:
            result = await async_bootstrap(_make_mock_container())
        finally:
            if original is None:
                sys.modules.pop("providify", None)
            else:
                sys.modules["providify"] = original

        assert result is None, (
            "async_bootstrap() must return None when providify is absent — "
            "not raise AttributeError from trying to call .ainstall() on None."
        )


# ── package-level re-export ───────────────────────────────────────────────────


class TestPackageReexport:
    """
    Verify that bootstrap and async_bootstrap are importable from
    the package root (``varco_memcached``).

    This matters because downstream code does::

        from varco_memcached import bootstrap, async_bootstrap

    and ``__init__.py`` must export them in ``__all__``.
    """

    def test_bootstrap_importable_from_package(self) -> None:
        """bootstrap is accessible from the top-level package namespace."""
        import varco_memcached  # noqa: PLC0415

        assert hasattr(varco_memcached, "bootstrap"), (
            "'bootstrap' not found in varco_memcached namespace — "
            "add it to __init__.py imports and __all__."
        )
        assert callable(varco_memcached.bootstrap)

    def test_async_bootstrap_importable_from_package(self) -> None:
        """async_bootstrap is accessible from the top-level package namespace."""
        import varco_memcached  # noqa: PLC0415

        assert hasattr(varco_memcached, "async_bootstrap"), (
            "'async_bootstrap' not found in varco_memcached namespace — "
            "add it to __init__.py imports and __all__."
        )
        assert callable(varco_memcached.async_bootstrap)

    def test_both_in_dunder_all(self) -> None:
        """Both helpers must appear in ``__all__`` for star-import clarity."""
        import varco_memcached  # noqa: PLC0415

        assert (
            "bootstrap" in varco_memcached.__all__
        ), "'bootstrap' missing from varco_memcached.__all__"
        assert (
            "async_bootstrap" in varco_memcached.__all__
        ), "'async_bootstrap' missing from varco_memcached.__all__"
