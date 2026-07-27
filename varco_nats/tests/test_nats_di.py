"""
DI wiring tests for ``varco_nats``.

``NatsChannelManagerSettings`` is a pydantic ``BaseSettings`` subclass.  It used
to carry ``@Singleton`` on the class itself, which cannot work: providify
injects the constructor, and pydantic's ``BaseSettings.__init__`` signature is
``(**values: Any)``.  Resolution therefore died with
``LookupError: Cannot resolve 'values: typing.Any'``, taking every consumer of
those settings (``NatsStreamManager``) down with it.

Settings classes must be registered through a ``@Provider`` instead — the same
rule already applied in ``varco_casbin/di.py`` and ``varco_fastapi/di.py``.

No NATS server is required: only container resolution is exercised.  The stream
manager itself is *not* resolved here — its ``@PostConstruct`` opens a
connection, which belongs to the integration suite.
"""

from __future__ import annotations

from providify import DIContainer, Provider

from varco_core.event.channel import ChannelManager
from varco_nats.channel import NatsChannelManagerSettings, NatsStreamManager


@Provider(singleton=True, priority=100)
def _custom_settings() -> NatsChannelManagerSettings:
    """App-supplied override — module scope so its lazy annotation resolves."""
    return NatsChannelManagerSettings(stream_name="custom-stream")


class TestNatsChannelManagerSettingsDI:
    def test_regression_settings_resolve_from_a_scanned_container(self) -> None:
        """
        User-visible symptom: ``container.get(ChannelManager, qualifier="nats")``
        raised ``LookupError: Cannot resolve 'values: typing.Any'``.  Correct
        behaviour is a ready-to-use settings instance, because a pydantic
        ``BaseSettings`` must be built by a factory, never constructor-injected.
        """
        container = DIContainer()
        container.scan("varco_nats", recursive=True)

        settings = container.get(NatsChannelManagerSettings)

        assert isinstance(settings, NatsChannelManagerSettings)
        assert settings.stream_name

    def test_settings_are_a_singleton(self) -> None:
        container = DIContainer()
        container.scan("varco_nats", recursive=True)

        assert container.get(NatsChannelManagerSettings) is container.get(
            NatsChannelManagerSettings
        )

    def test_user_supplied_settings_win_over_the_default(self) -> None:
        """The default provider registers at the lowest possible priority."""
        container = DIContainer()
        container.scan("varco_nats", recursive=True)
        container.provide(_custom_settings)

        assert container.get(NatsChannelManagerSettings).stream_name == "custom-stream"

    def test_stream_manager_binding_is_registered(self) -> None:
        """
        The manager is not *resolved* here (its ``@PostConstruct`` connects to
        a NATS server) — only its binding presence is asserted, so a future
        refactor cannot silently drop it.
        """
        container = DIContainer()
        container.scan("varco_nats", recursive=True)

        implementations = {
            getattr(b, "implementation", None) for b in container._bindings
        }
        assert NatsStreamManager in implementations
        assert any(
            b.interface in (ChannelManager, NatsStreamManager)
            for b in container._bindings
        )


class TestNatsContainerValidates:
    def test_regression_every_binding_resolves_its_annotations(self) -> None:
        """
        User-visible symptom: bootstrapping an app that scanned ``varco_nats``
        died with ``AnnotationResolutionError`` on ``NatsEventBus.__init__``
        parameter ``serializer``, because the ``EventSerializer`` alias was a quoted
        forward reference bound to a ``str`` at runtime, making
        ``EventSerializer | None`` unevaluatable.

        ``validate_bindings()`` resolves annotations for *every* registered
        binding, so it catches an unresolvable constructor hint on any singleton
        in the package — not just the ones a test happens to resolve directly.
        """
        container = DIContainer()
        container.scan("varco_nats", recursive=True)

        container.validate_bindings()
