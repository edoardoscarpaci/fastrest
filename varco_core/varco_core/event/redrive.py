"""
varco_core.event.redrive
==========================
``DlqRedriver`` — operator-triggered DLQ redrive (Plan 009, Phase 4 / R1).

Deviation from the backlog's literal ask (justified in the plan): the ABC
(`AbstractDeadLetterQueue`) gains only read/delete primitives (`get`,
`list_entries`, `delete`, `delete_where`) — not `redrive()` itself. Putting
`redrive()` on the ABC would force it to hold an `AbstractEventBus`, inverting
the documented "DLQ is independent of the bus" invariant on every backend.
`DlqRedriver` owns the redrive *policy* instead, and — like `OutboxRelay` and
`EventConsumer.register_to()` — is one of the few classes explicitly
permitted to hold an `AbstractEventBus` directly (this is infrastructure, not
application logic; see CLAUDE.md's layer-rule paragraph).

DESIGN: publish-then-ack, never ack-then-publish
    ✅ A crash between the two re-delivers the dead letter — at-least-once,
       the correct bias for a message you already nearly lost.
    ❌ A duplicate republish is possible; the inbox/dedup primitives already
       handle it (this module does not reinvent deduplication).

DESIGN: `DlqRedriver` is a plain object — no `start()`/`stop()`
    ✅ Structurally cannot become the parked "auto-redrive scheduler" — every
       call is operator-triggered (CLI or REST admin), never a background loop.
    ❌ No automatic retry of a redrive that failed to publish — deliberate;
       see the plan's Non-goals.

Thread safety:  ⚠️ Not thread-safe — construct/use from a single event loop,
                    same as `OutboxRelay`.
Async safety:   ✅ All public methods are ``async def``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from varco_core.event.dlq import DeadLetterSource

if TYPE_CHECKING:
    from varco_core.event.base import AbstractEventBus
    from varco_core.event.dlq import AbstractDeadLetterQueue, DeadLetterEntry


class DeadLetterNotAddressable(RuntimeError):
    """
    Raised by ``DlqRedriver.redrive(entry_id)`` when the backing DLQ cannot
    address a single entry by id (RD-4 — a stream-shaped store: Kafka/NATS).

    The message names the backend class and points at ``redrive_batch()`` /
    the CLI's ``--batch`` flag, which work on every backend.
    """


@dataclass(frozen=True)
class RedriveOutcome:
    """Outcome of one entry's redrive attempt."""

    entry_id: UUID
    published: bool
    acked: bool
    error: str | None = None


@dataclass(frozen=True)
class RedriveReport:
    """Aggregate outcome of a ``redrive_batch()`` call."""

    attempted: int
    succeeded: int
    failed: int
    outcomes: tuple[RedriveOutcome, ...] = ()
    dry_run: bool = False


class DlqRedriver:
    """
    Operator-triggered redrive of dead letters back onto the event bus.

    Args:
        dlq: The ``AbstractDeadLetterQueue`` to redrive from.
        bus: The ``AbstractEventBus`` to republish onto. ``DlqRedriver`` is
             one of the few classes permitted to hold this directly — see
             the module docstring.
        default_channel: Channel to publish to when an entry's own
             ``channel`` is empty. ``None`` means "no fallback" — an
             empty-channel entry with no default raises ``ValueError``.

    Edge cases:
        - See ``redrive()``/``redrive_batch()`` docstrings for the full
          per-entry decision table (payload-only, job-sourced, empty
          channel, unknown id, publish failure).
    """

    def __init__(
        self,
        dlq: AbstractDeadLetterQueue,
        bus: AbstractEventBus,
        *,
        default_channel: str | None = None,
    ) -> None:
        self._dlq = dlq
        self._bus = bus
        self._default_channel = default_channel

    async def redrive(self, entry_id: UUID, *, dry_run: bool = False) -> RedriveOutcome:
        """
        Redrive a single entry by id.

        Algorithm:
            1. Resolve the entry via ``dlq.get(entry_id)``.
            2. Reject payload-only and job-sourced entries (see below).
            3. ``bus.publish(entry.event, channel=...)``.
            4. On success: ``dlq.ack(entry.entry_id)`` (publish-then-ack).
            5. On publish failure: do NOT ack; record the error.
            6. ``dry_run=True`` skips steps 3–4 entirely.

        Args:
            entry_id: The ``DeadLetterEntry.entry_id`` to redrive.
            dry_run:  When ``True``, resolves and validates the entry but
                      never publishes or acks — the outcome reports what
                      *would* have happened.

        Returns:
            A ``RedriveOutcome``. ``published=False, error="not found"`` for
            an unknown ``entry_id`` — never raises for that case.

        Raises:
            DeadLetterNotAddressable: the backing DLQ cannot address a single
                entry by id (RD-4) — try ``redrive_batch()`` instead.
            ValueError: the entry's ``channel`` is empty and no
                ``default_channel`` was configured.
        """
        try:
            entry = await self._dlq.get(entry_id)
        except NotImplementedError as exc:
            raise DeadLetterNotAddressable(
                f"{type(self._dlq).__name__} does not support single-entry "
                f"redrive (no random access) — use redrive_batch() / the "
                f"CLI's --batch flag instead. Original error: {exc}"
            ) from exc

        if entry is None:
            return RedriveOutcome(
                entry_id=entry_id, published=False, acked=False, error="not found"
            )

        return await self._redrive_entry(entry, dry_run=dry_run)

    async def redrive_batch(
        self,
        *,
        limit: int = 10,
        channel: str | None = None,
        source: DeadLetterSource | None = None,
        tenant_id: str | None = None,
        dry_run: bool = False,
    ) -> RedriveReport:
        """
        Redrive up to ``limit`` entries.

        Works on every backend, including stream-shaped ones (Kafka/NATS):
        uses ``list_entries()`` (non-destructive filtered read) when
        supported, falling back to ``pop_batch()`` (which already IS the
        portable read on a stream backend — there is no other portable read).

        Args:
            limit:     Maximum entries to redrive in this call.
            channel:   Filter (only used with the ``list_entries()`` path —
                       ``pop_batch()`` has no filter support).
            source:    Filter (same caveat).
            tenant_id: Filter (same caveat).
            dry_run:   See ``redrive()``.

        Returns:
            A ``RedriveReport`` aggregating every entry's ``RedriveOutcome``.
            A publish failure on one entry does not stop the batch — the
            report's ``failed`` count reflects it (the CLI exits 1 on any
            failure).
        """
        try:
            entries = await self._dlq.list_entries(
                limit=limit, channel=channel, source=source, tenant_id=tenant_id
            )
        except NotImplementedError:
            # Stream-shaped backend — pop_batch() IS the portable read there.
            entries = await self._dlq.pop_batch(limit=limit)

        outcomes: list[RedriveOutcome] = []
        for entry in entries:
            outcomes.append(await self._redrive_entry(entry, dry_run=dry_run))

        succeeded = sum(
            1 for o in outcomes if o.published or (dry_run and o.error is None)
        )
        failed = len(outcomes) - succeeded
        return RedriveReport(
            attempted=len(outcomes),
            succeeded=succeeded,
            failed=failed,
            outcomes=tuple(outcomes),
            dry_run=dry_run,
        )

    async def _redrive_entry(
        self, entry: DeadLetterEntry, *, dry_run: bool
    ) -> RedriveOutcome:
        """Apply the per-entry redrive decision table to one already-resolved entry."""
        if entry.source == DeadLetterSource.JOB:
            return RedriveOutcome(
                entry_id=entry.entry_id,
                published=False,
                acked=False,
                error="job-sourced entry; re-enqueue via the job store",
            )

        if entry.event is None:
            return RedriveOutcome(
                entry_id=entry.entry_id,
                published=False,
                acked=False,
                error="payload-only entry; not republishable",
            )

        channel = entry.channel or self._default_channel
        if not channel:
            raise ValueError(
                f"DeadLetterEntry {entry.entry_id} has no channel and no "
                f"default_channel was configured on this DlqRedriver."
            )

        if dry_run:
            return RedriveOutcome(
                entry_id=entry.entry_id, published=False, acked=False, error=None
            )

        try:
            await self._bus.publish(entry.event, channel=channel)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - record per-entry failure, continue batch
            return RedriveOutcome(
                entry_id=entry.entry_id, published=False, acked=False, error=str(exc)
            )

        await self._dlq.ack(entry.entry_id)
        return RedriveOutcome(
            entry_id=entry.entry_id, published=True, acked=True, error=None
        )


__all__ = [
    "DeadLetterNotAddressable",
    "DlqRedriver",
    "RedriveOutcome",
    "RedriveReport",
]
