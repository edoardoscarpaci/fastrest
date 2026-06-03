"""
varco_nats.di
=============
Providify DI integration for ``varco_nats``.

All singletons (``NatsEventBus``, ``NatsHealthCheck``, ``NatsStreamManager``,
``NatsEventBusSettings``, ``NatsChannelManagerSettings``) carry ``@Singleton``
on their class definitions and are discovered automatically by
``container.scan("varco_nats", recursive=True)``.

No ``@Configuration`` class or ``ainstall()`` call is required for the bus.
The DLQ is the one exception — ``NatsDLQConfiguration`` (in ``varco_nats.dlq``)
must be installed explicitly when a DLQ is needed.

Usage
-----
Event bus only (most common)::

    from varco_nats.di import bootstrap
    from varco_core.event import AbstractEventBus

    container = bootstrap()
    bus = await container.aget(AbstractEventBus)
    await container.ashutdown()   # calls NatsEventBus.stop() via @PreDestroy

Or manually::

    from providify import DIContainer

    container = DIContainer()
    container.scan("varco_nats", recursive=True)
    bus = await container.aget(AbstractEventBus)

Overriding the default settings::

    container = DIContainer()
    container.provide(
        lambda: NatsEventBusSettings(
            servers=os.environ["NATS_SERVERS"],
            durable_name=os.environ["SERVICE_NAME"],
        ),
        NatsEventBusSettings,
    )
    container.scan("varco_nats", recursive=True)
"""

from __future__ import annotations

from typing import Any


# ── bootstrap ─────────────────────────────────────────────────────────────────


def bootstrap(container: Any = None) -> Any:
    """
    Bootstrap ``varco_nats`` into a ``DIContainer``.

    Calls ``container.scan("varco_nats", recursive=True)`` to discover all
    ``@Singleton``-annotated classes — ``NatsEventBusSettings``, ``NatsEventBus``,
    ``NatsHealthCheck``, ``NatsChannelManagerSettings`` and ``NatsStreamManager``.

    No ``ainstall()`` call is required for the bus — settings self-register via
    ``@Singleton`` on the Pydantic ``BaseSettings`` subclasses.  Install
    ``NatsDLQConfiguration`` separately if a dead-letter queue is needed.

    Call this **once** at application startup, before resolving any singletons::

        from varco_nats.di import bootstrap

        container = bootstrap()
        bus = await container.aget(AbstractEventBus)
        await container.ashutdown()

    Override defaults before calling bootstrap::

        from varco_nats.config import NatsEventBusSettings
        from providify import DIContainer

        container = DIContainer()
        # Register a higher-priority provider — wins over the @Singleton default.
        container.provide(
            lambda: NatsEventBusSettings(
                servers=os.environ["NATS_SERVERS"],
                durable_name=os.environ["SERVICE_NAME"],
            ),
            NatsEventBusSettings,
        )
        bootstrap(container)

    Args:
        container: An existing ``DIContainer`` to scan into.  When ``None``,
                   ``DIContainer.current()`` is used — the process-level
                   singleton.

    Returns:
        The ``DIContainer`` after scanning, or ``None`` if providify is not
        installed.

    Edge cases:
        - Calling twice is safe — scanning is idempotent.
        - ``container.ashutdown()`` must be awaited at process exit to call
          ``NatsEventBus.stop()`` via its ``@PreDestroy`` hook.

    Thread safety:  ✅ Bootstrap is intended for single-threaded startup only.
    Async safety:   ✅ Scanning is synchronous; the function itself is sync.
    """
    try:
        from providify import DIContainer  # noqa: PLC0415
    except ImportError:
        return None

    if container is None:
        # Use the process-level singleton container so callers don't need to
        # pass it around — consistent with varco_kafka.di.bootstrap().
        container = DIContainer.current()

    # Discover all @Singleton/@Component classes in varco_nats recursively.
    container.scan("varco_nats", recursive=True)

    return container


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "bootstrap",
]
