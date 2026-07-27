"""
DI wiring tests for ``varco_redis``.

Why this file exists
--------------------
``varco_redis`` had a fully green test suite while its container was, in fact,
unbootstrappable: ``RedisEventBus.__init__``'s ``serializer`` parameter was
annotated ``Annotated[EventSerializer | None, InjectMeta(optional=True)]``, and
the ``EventSerializer`` alias was a quoted forward reference bound to a ``str``
at runtime — so the annotation evaluated ``str | None`` and raised.  No existing redis test
happened to hit a resolution path that resolves binding annotations, so the
defect was invisible here and only surfaced in ``varco_kafka``/``varco_nats``.

``validate_bindings()`` resolves annotations for *every* registered binding, so
one call covers all present and future singletons in the package.  This is the
coverage gap closer, not a re-test of the alias itself (that lives in
``varco_core/tests/test_event_serializer_alias.py``).

No Redis server is required: only container registration and annotation
resolution are exercised — nothing is instantiated.
"""

from __future__ import annotations

from providify import DIContainer


class TestRedisContainerValidates:
    def test_regression_every_binding_resolves_its_annotations(self) -> None:
        """
        User-visible symptom: an app calling ``varco_redis.di.bootstrap()`` and
        then resolving anything died at startup with
        ``AnnotationResolutionError: Cannot resolve type hints for
        'RedisEventBus.__init__' parameter 'serializer'``.

        Correct behaviour is a container that validates cleanly.
        """
        container = DIContainer()
        container.scan("varco_redis", recursive=True)

        container.validate_bindings()

    def test_regression_streams_bus_bindings_also_validate(self) -> None:
        """
        The streams-backed bus is selected by ``VARCO_REDIS_USE_STREAMS`` at
        resolution time, but its binding is registered by the same scan — so it
        must resolve its annotations regardless of the env var.
        """
        container = DIContainer()
        container.scan("varco_redis", recursive=True)

        implementations = {
            getattr(b, "implementation", None).__name__
            for b in container._bindings
            if getattr(b, "implementation", None) is not None
        }

        assert "RedisEventBus" in implementations
        container.validate_bindings()
