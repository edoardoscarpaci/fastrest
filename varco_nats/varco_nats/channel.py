"""
varco_nats.channel
==================
NATS JetStream-backed ``ChannelManager`` implementation.

``NatsStreamManager`` administers the JetStream **stream** that backs the event
bus — creating it, purging per-channel messages, checking which channels carry
data, and listing them.  It is intentionally separate from ``NatsEventBus``
because stream administration may require broader credentials than a service
that only publishes and consumes events.

NATS channel model
------------------
Unlike Kafka — where each channel is a distinct topic — NATS channels are
**subjects under a single stream's wildcard**.  One JetStream stream
(``stream_name``) captures ``{subject_prefix}.>`` so every channel lives in it.

Consequences for this manager:

- ``declare_channel`` ensures the *backing stream* exists.  Individual channels
  need no per-channel declaration — they are covered by the wildcard the
  moment the stream exists.
- ``channel_exists`` reports whether a channel's subject currently carries at
  least one message in the stream (JetStream tracks per-subject counts).
- ``list_channels`` enumerates the subjects that currently carry messages.
- ``delete_channel`` purges a channel's messages from the stream.

Configuration
-------------
``NatsChannelManagerSettings`` uses the ``VARCO_NATS_ADMIN_`` prefix so admin
settings stay separate from the bus's ``VARCO_NATS_`` namespace::

    bus_config = NatsEventBusSettings(
        servers="nats://nats.internal:4222",
        durable_name="my-service",
    )
    manager_config = NatsChannelManagerSettings(
        servers="nats://nats.internal:4222",
        stream_name="varco-events",   # must match the bus
    )

DESIGN: separate from NatsEventBus
    ✅ Admin credentials never bleed into the bus.
    ✅ Services that only produce/consume events have no admin dependency.
    ✅ Infrastructure / migration scripts can use NatsStreamManager standalone.
    ❌ Two objects instead of one — justified by the privilege separation.

Thread safety:  ❌  Not thread-safe.  Use from a single event loop.
Async safety:   ✅  All public methods are ``async def``.

📚 Docs
- 🔍 https://docs.nats.io/nats-concepts/jetstream/streams — JetStream streams
- 🔍 https://nats-io.github.io/nats.py/ — nats-py JetStreamManager API
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from nats import connect
from nats.js.errors import NotFoundError
from providify import Inject, PostConstruct, PreDestroy, Provider, Singleton
from pydantic import Field
from pydantic_settings import SettingsConfigDict
from varco_core.config import VarcoSettings
from varco_core.event.base import ChannelConfig
from varco_core.event.channel import ChannelManager

_logger = logging.getLogger(__name__)


# ── NatsChannelManagerSettings ────────────────────────────────────────────────


class NatsChannelManagerSettings(VarcoSettings):
    """
    Configuration for ``NatsStreamManager``.

    Uses the ``VARCO_NATS_ADMIN_`` prefix to separate admin settings from the
    bus's ``VARCO_NATS_`` namespace.

    Attributes:
        servers:                  Comma-separated ``nats://host:port`` URLs the
                                  admin client connects to.
                                  Env var: ``VARCO_NATS_ADMIN_SERVERS``.
        stream_name:              Name of the JetStream stream to administer.
                                  **Must match** the bus's ``stream_name``.
                                  Env var: ``VARCO_NATS_ADMIN_STREAM_NAME``.
        subject_prefix:           Root subject token — **must match** the bus's
                                  ``subject_prefix``.
                                  Env var: ``VARCO_NATS_ADMIN_SUBJECT_PREFIX``.
        channel_prefix:           Channel-name prefix — **must match** the bus's
                                  ``channel_prefix``.
                                  Env var: ``VARCO_NATS_ADMIN_CHANNEL_PREFIX``.
        duplicate_window_seconds: Dedup window used when this manager creates
                                  the stream.
                                  Env var: ``VARCO_NATS_ADMIN_DUPLICATE_WINDOW_SECONDS``.
        connect_kwargs:           Extra kwargs forwarded to ``nats.connect()``.
                                  **Not env-readable** — use kwargs / ``from_dict()``.

    Thread safety:  ✅ Immutable — frozen=True.
    Async safety:   ✅ No mutable state.

    Edge cases:
        - ``stream_name``, ``subject_prefix`` and ``channel_prefix`` must match
          the bus's values exactly, or the manager administers a different
          stream / subjects than the bus uses.
    """

    model_config = SettingsConfigDict(
        env_prefix="VARCO_NATS_ADMIN_",
        frozen=True,
    )

    servers: str = "nats://localhost:4222"
    """Admin client server URLs.  Env: ``VARCO_NATS_ADMIN_SERVERS``."""

    stream_name: str = "varco-events"
    """JetStream stream to administer.  Env: ``VARCO_NATS_ADMIN_STREAM_NAME``."""

    subject_prefix: str = "varco"
    """Root subject token.  Must match the bus.  Env: ``VARCO_NATS_ADMIN_SUBJECT_PREFIX``."""

    channel_prefix: str = ""
    """Channel-name prefix.  Must match the bus.  Env: ``VARCO_NATS_ADMIN_CHANNEL_PREFIX``."""

    duplicate_window_seconds: float = 120.0
    """Dedup window used at stream creation.  Env: ``VARCO_NATS_ADMIN_DUPLICATE_WINDOW_SECONDS``."""

    connect_kwargs: dict[str, Any] = Field(default_factory=dict)
    """Extra kwargs for ``nats.connect()``.  Not env-readable."""

    def to_servers_list(self) -> list[str]:
        """Return the configured NATS server URLs as a list (split on commas)."""
        return [part.strip() for part in self.servers.split(",") if part.strip()]

    def subject_name(self, channel: str) -> str:
        """
        Return the full JetStream subject for a logical channel.

        Args:
            channel: Logical event channel name.

        Returns:
            ``{subject_prefix}.{channel_prefix}{channel}``.
        """
        return f"{self.subject_prefix}.{self.channel_prefix}{channel}"

    def wildcard_subject(self) -> str:
        """Return ``{subject_prefix}.>`` — the subject the backing stream captures."""
        return f"{self.subject_prefix}.>"

    def channel_from_subject(self, subject: str) -> str:
        """Recover the logical channel name from a full subject (inverse of subject_name)."""
        return subject.removeprefix(f"{self.subject_prefix}.").removeprefix(self.channel_prefix)


@Provider(singleton=True, priority=-sys.maxsize)
def nats_channel_manager_settings() -> NatsChannelManagerSettings:
    """
    Default ``NatsChannelManagerSettings`` binding, discovered by
    ``container.scan("varco_nats", recursive=True)``.

    DESIGN: ``@Provider`` factory instead of ``@Singleton`` on the class
        A pydantic ``BaseSettings`` declares ``__init__(self, **values: Any)``.
        providify resolves a ``ClassBinding`` by injecting the constructor
        signature, so a class-level ``@Singleton`` made every resolution fail
        with ``LookupError: Cannot resolve 'values: typing.Any'`` — which also
        broke ``NatsStreamManager`` (it injects these settings).  A factory has
        no injectable parameters, so the container just calls it.
        ✅ ``container.get(NatsChannelManagerSettings)`` works after a plain scan.
        ✅ Same precedent as ``varco_casbin/di.py`` and ``varco_fastapi/di.py``.
        ❌ Settings are no longer discoverable by class decoration alone — this
           module must stay importable by the scanner (it always is).

    ``priority=-sys.maxsize`` keeps this the lowest-priority binding, so any
    application-supplied provider wins without needing an explicit priority.

    Returns:
        ``NatsChannelManagerSettings`` populated from ``VARCO_NATS_ADMIN_*``
        environment variables (pydantic reads them at construction).
    """
    return NatsChannelManagerSettings()


# ── NatsStreamManager ─────────────────────────────────────────────────────────


@Singleton(priority=-sys.maxsize, qualifier="nats")
class NatsStreamManager(ChannelManager):
    """
    JetStream stream administration via the nats-py JetStream management API.

    Implements ``ChannelManager`` for NATS.  Because NATS channels are subjects
    under one stream's wildcard, the channel-keyed operations administer that
    single backing stream:

    - ``declare_channel`` → ensure the backing stream exists.
    - ``channel_exists``  → does the channel's subject carry any message?
    - ``list_channels``   → which subjects currently carry messages?
    - ``delete_channel``  → purge the channel's messages from the stream.

    Lifecycle:
        Must be started before any operation::

            manager = NatsStreamManager(settings)
            await manager.start()
            await manager.declare_channel("orders")
            await manager.stop()

        Or as an async context manager (preferred)::

            async with NatsStreamManager(settings) as manager:
                await manager.declare_channel("orders")

    Args:
        settings: Admin configuration injected from the container.

    Thread safety:  ❌  Not thread-safe.
    Async safety:   ✅  All public methods are ``async def``.

    Edge cases:
        - ``declare_channel`` is idempotent — an existing stream is left as-is.
        - ``channel_exists`` returns ``False`` for a channel that has never
          received a message, even though the wildcard would accept one.
        - ``stream_name`` / ``subject_prefix`` / ``channel_prefix`` MUST match
          the bus's values or the manager administers the wrong stream.
    """

    def __init__(self, settings: Inject[NatsChannelManagerSettings]) -> None:
        """
        Args:
            settings: Admin configuration injected from the container.
        """
        self._settings = settings
        # NATS client + JetStream context are created in start() — nats-py
        # objects must be created inside a running event loop.
        self._nc: Any | None = None
        self._js: Any | None = None

    # ── ChannelManager implementation ─────────────────────────────────────────

    @PostConstruct
    async def start(self) -> None:
        """
        Connect the admin NATS client and JetStream context.

        Raises:
            RuntimeError:   If already started.
            NoServersError: (nats-py) If the configured servers are unreachable.
        """
        if self._nc is not None:
            raise RuntimeError(
                "NatsStreamManager.start() called on an already-started manager. Call stop() first."
            )
        self._nc = await connect(
            servers=self._settings.to_servers_list(),
            **self._settings.connect_kwargs,
        )
        # jetstream() is synchronous — it wraps the client with the JetStream
        # management + publish API surface.
        self._js = self._nc.jetstream()
        _logger.info(
            "NatsStreamManager started (servers=%s, stream=%s)",
            self._settings.servers,
            self._settings.stream_name,
        )

    @PreDestroy
    async def stop(self) -> None:
        """Close the admin connection.  Idempotent — safe to call multiple times."""
        if self._nc is None:
            return
        await self._nc.close()
        self._nc = None
        self._js = None
        _logger.info("NatsStreamManager stopped.")

    async def declare_channel(
        self,
        channel: str,
        config: ChannelConfig | None = None,
    ) -> None:
        """
        Ensure the backing JetStream stream exists.

        Because every channel is a subject under the stream's ``{prefix}.>``
        wildcard, declaring *any* channel ensures the *stream* — there is no
        per-channel object in NATS.  This method is therefore idempotent stream
        creation: the ``channel`` argument is only used for logging.

        Args:
            channel: Logical channel name (used for logging only).
            config:  Optional channel configuration.  Only
                     ``replication_factor`` is honoured — it maps to the
                     JetStream stream's ``num_replicas``.  ``num_partitions``
                     has no NATS equivalent and is ignored.

        Raises:
            RuntimeError: If called before ``start()``.

        Edge cases:
            - Idempotent — if the stream already exists it is left untouched
              (its replica count is NOT changed to match ``config``).
            - If another stream already captures the wildcard subject, the
              underlying ``add_stream`` raises — overlapping subjects are a
              configuration error and must surface.
        """
        self._require_started()
        cfg = config or ChannelConfig()
        stream = self._settings.stream_name

        # Probe first so an existing stream is left as-is (idempotent).
        try:
            await self._js.stream_info(stream)  # type: ignore[union-attr]
            _logger.debug(
                "JetStream stream %r already exists — declare_channel(%r) is a no-op.",
                stream,
                channel,
            )
            return
        except NotFoundError:
            # Stream is absent — create it below.
            pass

        await self._js.add_stream(  # type: ignore[union-attr]
            name=stream,
            subjects=[self._settings.wildcard_subject()],
            num_replicas=cfg.replication_factor,
            duplicate_window=self._settings.duplicate_window_seconds,
        )
        _logger.info(
            "Created JetStream stream %r (subjects=[%s], replicas=%d) while declaring channel %r",
            stream,
            self._settings.wildcard_subject(),
            cfg.replication_factor,
            channel,
        )

    async def delete_channel(self, channel: str) -> None:
        """
        Purge all messages for ``channel`` from the backing stream.

        NATS has no per-channel object to delete — instead this purges every
        message stored under the channel's subject.  The stream itself and
        other channels are unaffected.

        Args:
            channel: Logical channel name whose messages should be purged.

        Raises:
            RuntimeError: If called before ``start()``.

        Edge cases:
            - Irreversible — purged messages are permanently gone.
            - Purging a channel that never received a message is a no-op
              (``purge_stream`` simply removes zero messages).
            - To delete the whole stream, use the nats-py
              ``JetStreamManager.delete_stream`` API directly.
        """
        self._require_started()
        subject = self._settings.subject_name(channel)
        # purge_stream with a subject filter removes only that subject's messages.
        await self._js.purge_stream(  # type: ignore[union-attr]
            self._settings.stream_name,
            subject=subject,
        )
        _logger.info(
            "Purged channel %r (subject=%r) from stream %r",
            channel,
            subject,
            self._settings.stream_name,
        )

    async def channel_exists(self, channel: str) -> bool:
        """
        Return ``True`` if the channel's subject currently carries any message.

        JetStream tracks a per-subject message count in the stream state.  This
        queries that count for the channel's subject.

        Args:
            channel: Logical channel name to check.

        Returns:
            ``True`` if the channel's subject has at least one message in the
            backing stream.

        Raises:
            RuntimeError: If called before ``start()``.

        Edge cases:
            - Returns ``False`` for a channel that has never received a message
              — even though the stream wildcard would accept one.  "Exists"
              here means "carries data", not "would be routed".
            - Returns ``False`` (not an error) if the backing stream itself
              does not exist yet.
        """
        self._require_started()
        subject = self._settings.subject_name(channel)
        try:
            # subjects_filter makes JetStream populate state.subjects — a dict
            # of {subject: message_count} restricted to the filter.
            info = await self._js.stream_info(  # type: ignore[union-attr]
                self._settings.stream_name,
                subjects_filter=subject,
            )
        except NotFoundError:
            # No backing stream → no channel carries data.
            return False

        subjects = getattr(info.state, "subjects", None) or {}
        return subjects.get(subject, 0) > 0

    async def list_channels(self) -> list[str]:
        """
        Return all channels that currently carry messages, sorted.

        Derived from JetStream's per-subject message counts — every subject
        under the stream wildcard with at least one message is reported.

        Returns:
            Sorted list of logical channel names (subject prefix stripped).

        Raises:
            RuntimeError: If called before ``start()``.

        Edge cases:
            - Channels that have never received a message are NOT listed.
            - Returns an empty list (not an error) if the backing stream does
              not exist.
        """
        self._require_started()
        try:
            info = await self._js.stream_info(  # type: ignore[union-attr]
                self._settings.stream_name,
                subjects_filter=self._settings.wildcard_subject(),
            )
        except NotFoundError:
            return []

        subjects = getattr(info.state, "subjects", None) or {}
        # Map each subject carrying messages back to its logical channel name.
        channels = [
            self._settings.channel_from_subject(subject)
            for subject, count in subjects.items()
            if count > 0
        ]
        return sorted(channels)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _require_started(self) -> None:
        """
        Raise ``RuntimeError`` if the admin client has not been started.

        Raises:
            RuntimeError: If ``start()`` has not been called.
        """
        if self._js is None:
            raise RuntimeError(
                f"{type(self).__name__} is not started. "
                f"Call 'await manager.start()' or use 'async with manager' first."
            )

    def __repr__(self) -> str:
        started = self._nc is not None
        return (
            f"NatsStreamManager("
            f"servers={self._settings.servers!r}, "
            f"stream={self._settings.stream_name!r}, "
            f"started={started})"
        )


__all__ = [
    "NatsChannelManagerSettings",
    "NatsStreamManager",
]
