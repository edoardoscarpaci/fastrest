"""
DI wiring tests for ``varco_kafka``.

``KafkaChannelManagerSettings`` is a pydantic ``BaseSettings`` subclass.  It
used to carry ``@Singleton`` on the class itself, which cannot work: providify
injects the constructor, and pydantic's ``BaseSettings.__init__`` signature is
``(**values: Any)``.  Resolution therefore died with
``LookupError: Cannot resolve 'values: typing.Any'``, taking every consumer of
those settings (``KafkaChannelManager``) down with it.

Settings classes must be registered through a ``@Provider`` instead — the same
rule already applied in ``varco_casbin/di.py`` and ``varco_fastapi/di.py``.

No broker is required: only container resolution is exercised.  The channel
manager itself is *not* resolved here — its ``@PostConstruct`` opens an admin
connection, which belongs to the integration suite.
"""

from __future__ import annotations

from providify import DIContainer, Provider

from varco_core.event.channel import ChannelManager
from varco_kafka.channel import KafkaChannelManager, KafkaChannelManagerSettings


@Provider(singleton=True, priority=100)
def _custom_settings() -> KafkaChannelManagerSettings:
    """App-supplied override — module scope so its lazy annotation resolves."""
    return KafkaChannelManagerSettings(topic_prefix="custom.")


class TestKafkaChannelManagerSettingsDI:
    def test_regression_settings_resolve_from_a_scanned_container(self) -> None:
        """
        User-visible symptom: ``container.get(ChannelManager, qualifier="kafka")``
        raised ``LookupError: Cannot resolve 'values: typing.Any'``.  Correct
        behaviour is a ready-to-use settings instance, because a pydantic
        ``BaseSettings`` must be built by a factory, never constructor-injected.
        """
        container = DIContainer()
        container.scan("varco_kafka", recursive=True)

        settings = container.get(KafkaChannelManagerSettings)

        assert isinstance(settings, KafkaChannelManagerSettings)
        assert settings.bootstrap_servers

    def test_settings_are_a_singleton(self) -> None:
        container = DIContainer()
        container.scan("varco_kafka", recursive=True)

        assert container.get(KafkaChannelManagerSettings) is container.get(
            KafkaChannelManagerSettings
        )

    def test_user_supplied_settings_win_over_the_default(self) -> None:
        """The default provider registers at the lowest possible priority."""
        container = DIContainer()
        container.scan("varco_kafka", recursive=True)
        container.provide(_custom_settings)

        assert container.get(KafkaChannelManagerSettings).topic_prefix == "custom."

    def test_channel_manager_binding_is_registered(self) -> None:
        """
        The manager is not *resolved* here (its ``@PostConstruct`` connects to
        a broker) — only its binding presence is asserted, so a future refactor
        cannot silently drop it.
        """
        container = DIContainer()
        container.scan("varco_kafka", recursive=True)

        implementations = {
            getattr(b, "implementation", None) for b in container._bindings
        }
        assert KafkaChannelManager in implementations
        assert any(
            b.interface in (ChannelManager, KafkaChannelManager)
            for b in container._bindings
        )


class TestKafkaContainerValidates:
    def test_regression_every_binding_resolves_its_annotations(self) -> None:
        """
        User-visible symptom: bootstrapping an app that scanned ``varco_kafka``
        died with ``AnnotationResolutionError`` on
        ``KafkaEventBus.__init__`` parameter ``serializer``, because
        the ``EventSerializer`` alias was a quoted forward reference bound to a
        ``str`` at runtime, making ``EventSerializer | None`` unevaluatable.

        ``validate_bindings()`` is the single call that resolves annotations for
        *every* registered binding, so it catches an unresolvable constructor
        hint on any singleton in the package — not just the ones a test happens
        to resolve directly.
        """
        container = DIContainer()
        container.scan("varco_kafka", recursive=True)

        container.validate_bindings()
