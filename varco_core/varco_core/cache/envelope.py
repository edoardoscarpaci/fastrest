"""
varco_core.cache.envelope
===========================
``CacheEnvelope`` — the wire format that lets a single cache entry carry a
soft TTL, a hard TTL, and a negative-cache marker (Plan 010 / D-5).

The envelope is written **only** when the active ``CachePolicy`` requires it
(``policy.requires_envelope``) — i.e. when ``soft_ttl``, ``negative_ttl``, or
``stale_if_error`` is set.  Otherwise ``read_through()`` stores the raw value
exactly as today, byte-identical to pre-Plan-010 behaviour.

Wire format::

    {
        "__varco_cache__": 1,       # MARKER: WIRE_VERSION
        "v": <value>,               # the cached value (or None for negative)
        "sa": <float>,              # stored_at — UNIX timestamp
        "se": <float | None>,       # soft_expires_at
        "he": <float | None>,       # hard_expires_at
        "neg": <bool>,              # is_negative
    }

``unwrap()`` tolerates a payload with no marker (or with a marker whose
version does not match ``WIRE_VERSION``) by returning ``None`` — the caller
then treats the payload as a fresh legacy value.  This is what makes the
Phase-1 → Phase-4 rollout safe: a **new** pod reading an **old** pod's
(unwrapped) entries is safe.  The unsafe direction — an **old** pod reading a
**new** pod's envelope — is why enabling an envelope-requiring policy field
against a shared L2 cache is a two-step deploy (D-5): roll out this varco
version everywhere with the new fields off, then turn them on.

DESIGN: wire format frozen in Phase 0, activated in Phase 4
    ✅ ``se``/``he`` are written from the very first commit, even though only
       Phase 4 sets ``se`` — avoids a *second* rolling-deploy hazard from
       adding a field to the envelope later.
    ❌ ``se`` is ``None`` for every entry until Phase 4's ``soft_ttl`` support
       ships — a small amount of always-present-but-often-unused shape.

DESIGN: unwrap() returns None (not raises) for a non-envelope payload
    ✅ Lets ``read_through()`` treat "not an envelope" and "no marker key" as
       the same case — one branch, not a try/except around every read.
    ❌ A payload that legitimately contains a dict with a colliding
       ``__varco_cache__`` key (wrong version) is also treated as legacy —
       an accepted, documented ambiguity (the marker name is namespaced
       enough that a real collision is exceedingly unlikely).

Compatibility note — ``NoOpSerializer``:
    A cache backed by a serializer that only accepts ``bytes`` (e.g. a
    hypothetical ``NoOpSerializer`` that passes values through unchanged to a
    ``bytes``-only transport) is incompatible with envelope mode: ``wrap()``
    produces a plain ``dict``, and handing a ``dict`` to a ``bytes``-only
    serializer fails loudly at the first ``set()`` — which is the intended,
    fail-fast behaviour rather than silent data corruption.

Thread safety:  ✅ ``CacheEnvelope`` is frozen; ``wrap``/``unwrap``/``coerce``
                   are pure functions.
Async safety:   ✅ No I/O — safe to call from any context.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

_logger = logging.getLogger(__name__)

#: Marker key written into every envelope payload.
MARKER = "__varco_cache__"

#: Current wire format version.  Bump only if the envelope shape changes in a
#: way that requires distinguishing old envelopes from new ones on read.
WIRE_VERSION = 1


@dataclasses.dataclass(frozen=True)
class CacheEnvelope:
    """
    The value-plus-metadata wire format for an envelope-requiring cache entry.

    Attributes:
        value:           The cached value.  ``None`` for a negative entry
                          (``is_negative=True``) — distinguishable from "no
                          entry at all" because ``unwrap()`` returns a
                          ``CacheEnvelope`` object, not ``None``, when the
                          payload IS an envelope.
        stored_at:       UNIX timestamp when this entry was written.
        soft_expires_at: UNIX timestamp after which the entry is
                          soft-expired (stale-while-revalidate territory).
                          ``None`` when ``CachePolicy.soft_ttl`` is unset.
        hard_expires_at: UNIX timestamp after which the entry must be treated
                          as absent and recomputed.  ``None`` means "rely on
                          the backend's own TTL enforcement" (today's
                          behaviour when only ``negative_ttl``/
                          ``stale_if_error`` triggered the envelope).
        is_negative:     ``True`` for a cached "not found" (``None``) result
                          (``CachePolicy.negative_ttl``, D-4).

    Thread safety:  ✅ Frozen dataclass — no mutable state.
    """

    value: Any
    stored_at: float
    soft_expires_at: float | None
    hard_expires_at: float | None
    is_negative: bool


def wrap(envelope: CacheEnvelope) -> dict[str, Any]:
    """
    Serialize a ``CacheEnvelope`` into a JSON-round-trippable dict.

    Args:
        envelope: The envelope to serialize.

    Returns:
        A plain ``dict`` carrying ``MARKER: WIRE_VERSION`` plus the
        envelope's fields under short keys (``v``/``sa``/``se``/``he``/
        ``neg``) — short keys keep the wire payload compact for backends
        that store the JSON-encoded form (e.g. Redis).
    """
    return {
        MARKER: WIRE_VERSION,
        "v": envelope.value,
        "sa": envelope.stored_at,
        "se": envelope.soft_expires_at,
        "he": envelope.hard_expires_at,
        "neg": envelope.is_negative,
    }


def unwrap(payload: Any) -> CacheEnvelope | None:
    """
    Deserialize a stored payload into a ``CacheEnvelope``, or ``None`` if it
    is not a (current-version) envelope.

    Args:
        payload: The raw value returned by a cache backend's ``get()``.

    Returns:
        A ``CacheEnvelope`` if ``payload`` is a dict carrying
        ``MARKER: WIRE_VERSION``; ``None`` otherwise — including for a
        legacy raw value, a dict that happens to contain an unrelated
        ``MARKER`` key, or a dict whose ``MARKER`` value does not match
        ``WIRE_VERSION`` (a future wire version this code doesn't
        understand yet).

    Edge cases:
        - ``payload`` is not a dict at all (e.g. a plain string/int/list) →
          ``None``.
        - A negative envelope (``is_negative=True``, ``value=None``) still
          returns a ``CacheEnvelope`` — distinguishable from a genuine
          cache miss (which is ``None`` from the *backend's* ``get()``, a
          layer below this function).
    """
    if not isinstance(payload, dict):
        return None
    if payload.get(MARKER) != WIRE_VERSION:
        return None
    return CacheEnvelope(
        value=payload.get("v"),
        stored_at=payload.get("sa", 0.0),
        soft_expires_at=payload.get("se"),
        hard_expires_at=payload.get("he"),
        is_negative=bool(payload.get("neg", False)),
    )


def coerce(value: Any, type_hint: type | None) -> Any:
    """
    Re-apply a type hint to an unwrapped envelope value.

    Envelope mode passes ``type_hint=None`` to the backend (the envelope
    itself is what gets serialized, not the typed value directly), so the
    hint must be re-applied here after ``unwrap()``.

    Uses pydantic's ``TypeAdapter`` for both pydantic models and stdlib
    dataclasses — pydantic is already a ``varco_core`` dependency
    (``VarcoSettings``).

    Args:
        value:     The raw (already-JSON-decoded) value from the envelope.
        type_hint: The type to coerce ``value`` into.  ``None`` → pass
                   through unchanged.

    Returns:
        ``value`` coerced to ``type_hint``, or ``value`` unchanged if
        ``type_hint`` is ``None`` or coercion fails.

    Edge cases:
        - ``coerce()`` never raises on an unrecognised hint — it logs at
          DEBUG and returns ``value`` unchanged.  A caller depending on a
          serializer-specific reconstruction that ``TypeAdapter`` cannot
          reproduce sees a plain dict instead of the typed object; this is
          documented, not a silent data-loss bug.
    """
    if type_hint is None:
        return value
    try:
        from pydantic import TypeAdapter

        return TypeAdapter(type_hint).validate_python(value)
    except Exception as exc:  # noqa: BLE001 - coercion must never raise
        _logger.debug("envelope.coerce(): could not coerce value to %r: %s", type_hint, exc)
        return value


__all__ = ["MARKER", "WIRE_VERSION", "CacheEnvelope", "coerce", "unwrap", "wrap"]
