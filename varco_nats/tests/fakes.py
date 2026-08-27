"""
Fake nats-py doubles for varco_nats unit tests
==============================================
These fakes replace the real ``nats-py`` client + JetStream API so unit tests
run fast and require no NATS broker.

DESIGN: hand-written fakes over unittest.mock auto-spec
    ✅ Explicit behaviour — JetStream stream/consumer semantics are modelled
       just faithfully enough to exercise varco_nats code paths.
    ✅ Test failures point at real bugs, not mock misconfiguration.
    ❌ More code than ``MagicMock`` — justified: nats-py's async API and
       JetStream stream/consumer model are non-trivial to fake correctly.

The fakes model:
    - subject wildcard matching (``*`` / ``>``)
    - one or more JetStream streams, each with a message list and retention
    - push subscriptions (callback delivery on publish)
    - pull subscriptions (``fetch`` with batch + timeout)
    - WorkQueue retention — ``ack()`` deletes the message from the stream
"""

from __future__ import annotations

from typing import Any

from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.errors import NotFoundError
from varco_core.event.base import Event

# ── Test event types ──────────────────────────────────────────────────────────


class OrderPlacedEvent(Event):
    """Event used across varco_nats unit tests."""

    __event_type__ = "order.placed.nats_test"
    order_id: str


class OrderCancelledEvent(Event):
    """Second event type — used to exercise type-based dispatch filtering."""

    __event_type__ = "order.cancelled.nats_test"
    reason: str = ""


# ── Subject matching ──────────────────────────────────────────────────────────


def subject_matches(pattern: str, subject: str) -> bool:
    """
    Return ``True`` if ``subject`` matches the NATS subject ``pattern``.

    Implements the two NATS wildcards:
    - ``*`` matches exactly one token.
    - ``>`` matches one or more trailing tokens.

    Args:
        pattern: A subject pattern, possibly containing ``*`` / ``>``.
        subject: A concrete subject to test.

    Returns:
        ``True`` if the pattern matches the subject.

    Edge cases:
        - ``>`` must be the last token to match anything — a trailing ``>``
          with no following tokens in ``subject`` does not match.
    """
    p_tokens = pattern.split(".")
    s_tokens = subject.split(".")
    for i, tok in enumerate(p_tokens):
        if tok == ">":
            # ``>`` matches one OR MORE remaining tokens.
            return len(s_tokens) > i
        if i >= len(s_tokens):
            return False
        if tok == "*":
            continue
        if tok != s_tokens[i]:
            return False
    return len(p_tokens) == len(s_tokens)


# ── Stored message + JetStream Msg fake ───────────────────────────────────────


class _StoredMsg:
    """A message persisted in a fake JetStream stream."""

    def __init__(self, subject: str, payload: bytes, headers: dict | None) -> None:
        self.subject = subject
        self.payload = payload
        self.headers = headers
        # delivered=True means a pull consumer has handed it out but not yet
        # acked it — it should not be re-fetched within the same session.
        self.delivered = False


class FakeMsgMetadata:
    """
    Fake nats-py ``Msg.metadata`` — exposes ``num_delivered`` for direct
    attribute access (research 005 §C: "direct attribute access, no getter").
    """

    def __init__(self, num_delivered: int = 1) -> None:
        self.num_delivered = num_delivered


class FakeMsg:
    """
    Fake nats-py ``Msg`` delivered to a callback or returned by ``fetch()``.

    Tracks ack/nak/term so tests can assert acknowledgement behaviour, and
    carries a settable ``metadata.num_delivered`` so redelivery-count-driven
    branches (nak vs term at ``max_deliver``) can be exercised without a
    real broker.
    """

    def __init__(
        self,
        subject: str,
        data: bytes,
        headers: dict | None = None,
        *,
        stored: _StoredMsg | None = None,
        stream: FakeStream | None = None,
        num_delivered: int = 1,
    ) -> None:
        self.subject = subject
        self.data = data
        self.headers = headers
        self.acked = False
        self.naked = False
        self.termed = False
        self.nak_delay: float | None = None
        self.metadata = FakeMsgMetadata(num_delivered)
        # When backed by a WorkQueue stream, ack()/term() delete the stored
        # message — this is what makes NatsDLQ.count() exact.
        self._stored = stored
        self._stream = stream

    async def ack(self) -> None:
        """Fire-and-forget ack, mirroring nats-py's ``Msg.ack``."""
        self.acked = True
        self._remove_if_workqueue()

    async def ack_sync(self, timeout: float = 1.0) -> FakeMsg:
        """
        Confirming ack, mirroring nats-py's ``Msg.ack_sync``.

        The real ``Msg.ack()`` only publishes to the reply subject and returns
        before the server has processed it, so ``NatsDLQ.ack()`` must use this
        variant to honour the "entry is removed on return" postcondition.
        """
        self.acked = True
        self._remove_if_workqueue()
        return self

    async def nak(self, delay: float | None = None) -> None:
        self.naked = True
        self.nak_delay = delay

    async def term(self) -> None:
        self.termed = True
        self._remove_if_workqueue()

    def _remove_if_workqueue(self) -> None:
        """Delete the backing stored message under WorkQueue retention."""
        if (
            self._stream is not None
            and self._stored is not None
            and self._stream.retention == "workqueue"
            and self._stored in self._stream.messages
        ):
            self._stream.messages.remove(self._stored)


# ── Stream + subscription fakes ───────────────────────────────────────────────


class FakeStream:
    """A fake JetStream stream — holds a message list and retention policy."""

    def __init__(self, name: str, subjects: list[str], retention: str) -> None:
        self.name = name
        self.subjects = subjects
        self.retention = retention
        self.messages: list[_StoredMsg] = []


class _FakeStreamState:
    """Stand-in for ``StreamInfo.state`` — exposes ``messages`` and ``subjects``."""

    def __init__(self, messages: int, subjects: dict[str, int]) -> None:
        self.messages = messages
        self.subjects = subjects


class _FakeStreamConfig:
    """Stand-in for ``StreamInfo.config`` — exposes ``subjects``."""

    def __init__(self, subjects: list[str]) -> None:
        self.subjects = subjects


class FakeStreamInfo:
    """Stand-in for nats-py ``StreamInfo``."""

    def __init__(self, config: _FakeStreamConfig, state: _FakeStreamState) -> None:
        self.config = config
        self.state = state


class FakePushSubscription:
    """Fake nats-py ``PushSubscription`` — records unsubscribe calls."""

    def __init__(self, subject: str) -> None:
        self.subject = subject
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class FakePullSubscription:
    """Fake nats-py ``PullSubscription`` — ``fetch`` reads from a stream."""

    def __init__(self, stream: FakeStream, subject: str) -> None:
        self._stream = stream
        self._subject = subject

    async def fetch(self, batch: int, timeout: float | None = None) -> list[FakeMsg]:
        """
        Return up to ``batch`` undelivered messages from the bound stream.

        Raises:
            NatsTimeoutError: If no undelivered messages are available — this
                              is exactly how nats-py signals an empty fetch.
        """
        out: list[FakeMsg] = []
        for stored in self._stream.messages:
            if len(out) >= batch:
                break
            if stored.delivered or not subject_matches(self._subject, stored.subject):
                continue
            stored.delivered = True
            out.append(
                FakeMsg(
                    stored.subject,
                    stored.payload,
                    stored.headers,
                    stored=stored,
                    stream=self._stream,
                )
            )
        if not out:
            raise NatsTimeoutError
        return out


# ── JetStream context + client fakes ──────────────────────────────────────────


class FakeJetStream:
    """
    Fake nats-py ``JetStreamContext``.

    Models streams, push subscriptions (callback on publish) and pull
    subscriptions (fetch).  Publishing routes to BOTH any matching stream
    (storage) and any matching push subscription (delivery).
    """

    def __init__(self) -> None:
        # subject → (payload, headers) publish log, in order.
        self.published: list[tuple[str, bytes, dict | None]] = []
        self.streams: dict[str, FakeStream] = {}
        # subject → (FakePushSubscription, callback)
        self.push_subs: dict[str, tuple[FakePushSubscription, Any]] = {}
        self.account_info_ok = True

    async def stream_info(self, name: str, subjects_filter: str | None = None) -> FakeStreamInfo:
        if name not in self.streams:
            raise NotFoundError
        stream = self.streams[name]
        # Build per-subject counts (state.subjects) for the requested filter.
        subjects: dict[str, int] = {}
        for stored in stream.messages:
            if subjects_filter is None or subject_matches(subjects_filter, stored.subject):
                subjects[stored.subject] = subjects.get(stored.subject, 0) + 1
        return FakeStreamInfo(
            config=_FakeStreamConfig(list(stream.subjects)),
            state=_FakeStreamState(messages=len(stream.messages), subjects=subjects),
        )

    async def add_stream(self, **params: Any) -> FakeStreamInfo:
        name = params["name"]
        subjects = list(params.get("subjects", []))
        # retention may arrive as a RetentionPolicy enum or a plain string.
        retention = params.get("retention", "limits")
        retention = getattr(retention, "value", retention)
        stream = FakeStream(name, subjects, str(retention))
        self.streams[name] = stream
        return FakeStreamInfo(
            config=_FakeStreamConfig(subjects),
            state=_FakeStreamState(0, {}),
        )

    async def delete_stream(self, name: str) -> bool:
        return self.streams.pop(name, None) is not None

    async def purge_stream(self, name: str, subject: str | None = None) -> bool:
        if name not in self.streams:
            raise NotFoundError
        stream = self.streams[name]
        if subject is None:
            stream.messages.clear()
        else:
            stream.messages = [
                m for m in stream.messages if not subject_matches(subject, m.subject)
            ]
        return True

    async def publish(self, subject: str, payload: bytes, headers: dict | None = None) -> Any:
        self.published.append((subject, payload, headers))
        # Store into every stream whose configured subjects cover this subject.
        for stream in self.streams.values():
            if any(subject_matches(p, subject) for p in stream.subjects):
                stream.messages.append(_StoredMsg(subject, payload, headers))
        # Deliver to every matching push subscription (the consumer side).
        for sub_subject, (_sub, cb) in list(self.push_subs.items()):
            if cb is not None and subject_matches(sub_subject, subject):
                await cb(FakeMsg(subject, payload, headers))
        return object()  # stand-in PubAck

    async def subscribe(
        self,
        subject: str,
        durable: str | None = None,
        cb: Any = None,
        manual_ack: bool = False,
        **_: Any,
    ) -> FakePushSubscription:
        sub = FakePushSubscription(subject)
        self.push_subs[subject] = (sub, cb)
        return sub

    async def pull_subscribe(
        self,
        subject: str,
        durable: str | None = None,
        stream: str | None = None,
        **_: Any,
    ) -> FakePullSubscription:
        # Resolve the bound stream — by explicit name, else by subject coverage.
        target: FakeStream | None = None
        if stream is not None and stream in self.streams:
            target = self.streams[stream]
        else:
            for st in self.streams.values():
                if any(subject_matches(p, subject) for p in st.subjects):
                    target = st
                    break
        if target is None:
            raise NotFoundError
        return FakePullSubscription(target, subject)

    async def account_info(self) -> Any:
        if not self.account_info_ok:
            raise RuntimeError("JetStream not enabled")
        return object()


class FakeNatsClient:
    """Fake nats-py ``Client`` — wraps a ``FakeJetStream``."""

    def __init__(self, js: FakeJetStream | None = None) -> None:
        self._js = js or FakeJetStream()
        self.is_connected = True
        self.closed = False
        self.drained = False

    def jetstream(self) -> FakeJetStream:
        return self._js

    async def drain(self) -> None:
        self.drained = True
        self.closed = True

    async def close(self) -> None:
        self.closed = True
