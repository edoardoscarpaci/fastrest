"""
Tests for the providify DI integration module (varco_beanie.di).

Most of the tests below use no real container resolution — instead:
- BeanieSettings is tested as a plain dataclass.
- BeanieModule @Provider methods are called directly on an instance.
- _make_repo_provider is tested as a pure function.
- bind_repositories is tested against a mock container.

A final section (``TestBeanieContainerValidates``, Plan 013 / F9) performs
real container resolution: ``DIContainer().scan("varco_beanie",
recursive=True); validate_bindings()``. Spot-check finding recorded in Plan
013's Design section: this recursive-scan pattern already existed
incidentally via ``test_beanie_tenancy_di.py``/``test_beanie_dlq.py``, but
neither of those files is named for nor asserts anything about
``BeanieModule``/``bind_repositories``/``bootstrap`` by name — this class
makes that coverage explicit, named, and independent of those sub-areas'
lifetimes.

Coverage:
- BeanieSettings:          frozen, field defaults, type annotations
- BeanieModule:            repository_provider() creates + inits the provider,
                           query_compiler() returns BeanieQueryCompiler
- _make_repo_provider:     produces a @Provider function with correct return annotation
- bind_repositories:       calls container.provide() for each entity class,
                           raises ValueError when called with no classes
- TestBeanieContainerValidates: real DIContainer scan + validate_bindings(),
                           and bind_repositories() against a real container

Thread safety:  N/A (unit tests)
Async safety:   ✅ BeanieModule.repository_provider is async — tested with await
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from providify import DIContainer, Provider

from varco_core.model import DomainModel
from varco_core.providers import RepositoryProvider
from varco_core.repository import AsyncRepository
from varco_beanie.config import BeanieSettings
from varco_beanie.di import (
    _make_repo_provider,
    bind_repositories,
)
from varco_beanie.provider import BeanieRepositoryProvider
from varco_beanie.query.compiler import BeanieQueryCompiler


# ── Test domain models ────────────────────────────────────────────────────────


@dataclass
class _User(DomainModel):
    name: str = ""


@dataclass
class _Post(DomainModel):
    title: str = ""


# ── BeanieSettings ────────────────────────────────────────────────────────────


def test_beanie_settings_is_frozen() -> None:
    """BeanieSettings is immutable — assigning a field after construction raises."""
    settings = BeanieSettings(mongo_client=MagicMock(), db_name="test")

    with pytest.raises((AttributeError, TypeError)):
        settings.db_name = "other"  # type: ignore[misc]


def test_beanie_settings_default_entity_classes_is_empty_tuple() -> None:
    """entity_classes defaults to an empty tuple — no domain classes pre-registered."""
    settings = BeanieSettings(mongo_client=MagicMock(), db_name="test")
    assert settings.entity_classes == ()


def test_beanie_settings_default_transactional_is_false() -> None:
    """transactional defaults to False — most deployments use standalone MongoDB."""
    settings = BeanieSettings(mongo_client=MagicMock(), db_name="test")
    assert settings.transactional is False


def test_beanie_settings_stores_provided_values() -> None:
    """All provided values are stored as attributes."""
    client = MagicMock()
    settings = BeanieSettings(
        mongo_client=client,
        db_name="mydb",
        entity_classes=(_User,),
        transactional=True,
    )

    assert settings.mongo_client is client
    assert settings.db_name == "mydb"
    assert settings.entity_classes == (_User,)
    assert settings.transactional is True


# ── BeanieRepositoryProvider (DI-injected via BeanieSettings) ────────────────


def test_beanie_module_repository_provider_returns_repository_provider() -> None:
    """BeanieRepositoryProvider is a RepositoryProvider."""
    mock_client = MagicMock()
    settings = BeanieSettings(mongo_client=mock_client, db_name="testdb")

    with patch("varco_beanie.provider.BeanieModelFactory"):
        provider = BeanieRepositoryProvider(settings)

    assert isinstance(provider, RepositoryProvider)


async def test_beanie_module_repository_provider_calls_init() -> None:
    """BeanieRepositoryProvider.init() calls beanie.init_beanie()."""
    mock_client = MagicMock()
    settings = BeanieSettings(mongo_client=mock_client, db_name="testdb")

    with patch("varco_beanie.provider.BeanieModelFactory"):
        provider = BeanieRepositoryProvider(settings)

    with (
        patch("beanie.init_beanie", new_callable=AsyncMock) as mock_init,
        patch("varco_beanie.provider.BeanieDocRegistry") as mock_registry,
    ):
        mock_registry.all_documents.return_value = []
        await provider.init()

    mock_init.assert_awaited_once()


def test_beanie_module_repository_provider_registers_entity_classes() -> None:
    """
    BeanieRepositoryProvider registers entity_classes from settings in __init__.
    """
    mock_client = MagicMock()
    settings = BeanieSettings(
        mongo_client=mock_client,
        db_name="testdb",
        entity_classes=(_User, _Post),
    )

    with patch("varco_beanie.provider.BeanieModelFactory") as mock_factory_cls:
        mock_factory = MagicMock()
        mock_factory.build.return_value = (MagicMock(), MagicMock())
        mock_factory_cls.return_value = mock_factory

        BeanieRepositoryProvider(settings)

    assert mock_factory.build.call_count == 2


def test_beanie_module_repository_provider_skips_register_when_no_entities() -> None:
    """
    BeanieRepositoryProvider does NOT call register() when entity_classes is empty.
    """
    settings = BeanieSettings(mongo_client=MagicMock(), db_name="testdb")

    with patch("varco_beanie.provider.BeanieModelFactory") as mock_factory_cls:
        mock_factory = MagicMock()
        mock_factory_cls.return_value = mock_factory

        BeanieRepositoryProvider(settings)

    mock_factory.build.assert_not_called()


# ── BeanieQueryCompiler ───────────────────────────────────────────────────────


def test_beanie_module_query_compiler_returns_beanie_query_compiler() -> None:
    """BeanieQueryCompiler can be instantiated directly."""
    result = BeanieQueryCompiler()

    assert isinstance(result, BeanieQueryCompiler)


# ── _make_repo_provider() ────────────────────────────────────────────────────


def test_make_repo_provider_produces_callable() -> None:
    """_make_repo_provider returns a callable (the factory function)."""
    fn = _make_repo_provider(_User)
    assert callable(fn)


def test_make_repo_provider_sets_correct_return_annotation() -> None:
    """
    The factory's return annotation is patched to AsyncRepository[_User].

    This is the key mechanism that lets providify register the binding under
    the correct generic alias — without this, all repos would collide on the
    bare AsyncRepository interface.
    """
    fn = _make_repo_provider(_User)
    # The underlying function is the @Provider-decorated version — unwrap it
    # by looking at __wrapped__ or the __annotations__ directly on the fn
    # (Provider stamps metadata but returns the original function object).
    return_annotation = fn.__annotations__.get("return")
    assert return_annotation == AsyncRepository[_User]


def test_make_repo_provider_different_entities_have_different_annotations() -> None:
    """Each call produces a factory with a distinct return annotation."""
    fn_user = _make_repo_provider(_User)
    fn_post = _make_repo_provider(_Post)

    assert fn_user.__annotations__["return"] != fn_post.__annotations__["return"]
    assert fn_user.__annotations__["return"] == AsyncRepository[_User]
    assert fn_post.__annotations__["return"] == AsyncRepository[_Post]


def test_make_repo_provider_function_name_includes_entity_name() -> None:
    """The factory __name__ includes the entity class name for debugging."""
    fn = _make_repo_provider(_User)
    assert "_User" in fn.__name__


# ── bind_repositories() ───────────────────────────────────────────────────────


def test_bind_repositories_calls_provide_for_each_entity() -> None:
    """bind_repositories() calls container.provide() once per entity class."""
    mock_container = MagicMock()
    bind_repositories(mock_container, _User, _Post)

    assert mock_container.provide.call_count == 2


def test_bind_repositories_raises_value_error_with_no_entities() -> None:
    """
    bind_repositories() raises ValueError when called with no entity classes.

    Edge case: calling with an empty list is likely a programming mistake —
    fail fast with a clear message rather than silently registering nothing.
    """
    mock_container = MagicMock()

    with pytest.raises(ValueError, match="requires at least one entity class"):
        bind_repositories(mock_container)


def test_bind_repositories_passes_provider_functions_to_container() -> None:
    """Each call to container.provide() receives a callable factory."""
    mock_container = MagicMock()
    bind_repositories(mock_container, _User)

    provided_fn = mock_container.provide.call_args[0][0]
    assert callable(provided_fn)


def test_bind_repositories_each_factory_has_distinct_annotation() -> None:
    """
    Each factory passed to container.provide() has a distinct return annotation.

    Verifies that the closure correctly captures each entity class — a common
    mistake is late binding where all factories close over the last value of
    the loop variable.
    """
    provided_fns: list = []

    def capture_provide(fn):
        provided_fns.append(fn)

    mock_container = MagicMock()
    mock_container.provide.side_effect = capture_provide
    bind_repositories(mock_container, _User, _Post)

    annotations = [fn.__annotations__["return"] for fn in provided_fns]
    assert AsyncRepository[_User] in annotations
    assert AsyncRepository[_Post] in annotations
    # No duplicates — each entity gets its own distinct binding
    assert len(set(str(a) for a in annotations)) == 2


# ── TestBeanieContainerValidates (Plan 013 / F9) ────────────────────────────


class TestBeanieContainerValidates:
    def test_regression_scan_validates_bindings(self) -> None:
        """
        User-visible symptom: an app calling ``varco_beanie.di.bootstrap()``
        and then resolving anything died at startup with an
        ``AnnotationResolutionError`` for a binding contributed by
        ``varco_beanie`` (e.g. a future quoted ``@Provider`` return
        annotation silently disabling injection container-wide — see
        CLAUDE.md's pitfall table). Correct behaviour is a container that
        validates cleanly.
        """
        container = DIContainer()
        container.scan("varco_beanie", recursive=True)

        container.validate_bindings()

    def test_regression_bind_repositories_against_real_container_validates(
        self,
    ) -> None:
        """
        No existing ``varco_beanie`` test calls ``bind_repositories()``
        against a real ``DIContainer`` — only mock-based coverage exists
        above. A per-entity ``AsyncRepository[D]`` provider is generated via
        runtime annotation patching (``_make_repo_provider``); a real
        container is required to prove that patched annotation is actually
        something providify can resolve type hints for, which a
        ``MagicMock()`` container can never catch.
        """
        container = DIContainer()
        container.scan("varco_beanie", recursive=True)

        bind_repositories(container, _User)

        container.validate_bindings()

    async def test_regression_bound_repository_resolves_through_aget(self) -> None:
        """
        Plan 014 / Step 11 — characterization for site 6 before the
        ``@Provider`` annotation-patch extraction (``provide_factory()``,
        Step 22). ``validate_bindings()`` resolves annotations without
        constructing anything; this actually builds the repository through
        ``container.aget()`` (the factory is async — see ``bind_repositories``'
        docstring) to prove the runtime-patched ``AsyncRepository[entity_cls]``
        alias is genuinely resolvable, not just annotation-clean.

        ``BeanieRepositoryProvider.init()`` (an async ``@PostConstruct``) is
        stubbed via ``beanie.init_beanie`` — same rationale as
        ``test_beanie_module_repository_provider_calls_init`` above: no real
        MongoDB connection is required to prove DI wiring.
        """

        @Provider(singleton=True, priority=100)
        def _beanie_settings_for_test() -> BeanieSettings:
            return BeanieSettings(
                mongo_client=MagicMock(),
                db_name="testdb",
                entity_classes=(_User, _Post),
            )

        container = DIContainer()
        container.scan("varco_beanie", recursive=True)
        container.provide(_beanie_settings_for_test)
        bind_repositories(container, _User, _Post)

        with patch("beanie.init_beanie", new_callable=AsyncMock):
            user_repo = await container.aget(AsyncRepository[_User])
            post_repo = await container.aget(AsyncRepository[_Post])

        assert user_repo is not None
        assert user_repo is not post_repo

    async def test_regression_bound_repository_is_dependent_scoped_not_singleton(
        self,
    ) -> None:
        """
        Pins ``Scope.DEPENDENT`` (``varco_beanie/varco_beanie/di.py``
        deliberately does not pass ``singleton=True``) — two resolutions of
        the same generic alias must return two different instances.
        """

        @Provider(singleton=True, priority=100)
        def _beanie_settings_for_test() -> BeanieSettings:
            return BeanieSettings(
                mongo_client=MagicMock(),
                db_name="testdb",
                entity_classes=(_User,),
            )

        container = DIContainer()
        container.scan("varco_beanie", recursive=True)
        container.provide(_beanie_settings_for_test)
        bind_repositories(container, _User)

        with patch("beanie.init_beanie", new_callable=AsyncMock):
            first = await container.aget(AsyncRepository[_User])
            second = await container.aget(AsyncRepository[_User])

        assert first is not second
