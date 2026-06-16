"""
events.py
=========
Domain events for the ``18-realtime-ws-sse`` live scoreboard example.

Defines ``ScoreUpdatedEvent`` — the sole domain event that propagates through
the in-memory bus and is fanned out to WebSocket and SSE clients.

DESIGN: Event subclass over a plain dict
    ✅ Pydantic validation — ``score`` cannot be a string by accident.
    ✅ Typed — handlers receive a ``ScoreUpdatedEvent``, not ``Any``.
    ✅ model_dump() produces JSON-serialisable output for the wire adapters.
    ❌ Slightly more ceremony than a dict for a simple demo — acceptable.
"""

from __future__ import annotations

from varco_core.event.base import Event


class ScoreUpdatedEvent(Event):
    """
    Emitted when a team's score changes.

    Attributes:
        team:  Name of the team (e.g. ``"Red"``, ``"Blue"``).
        score: New absolute score value for the team (non-negative).

    Edge cases:
        - ``score`` is the absolute current score, not a delta.
        - ``team`` is a free-form string — no enumeration enforced.
    """

    # __event_type__ identifies this event in the wire format.
    # The adapters use event_type_name() which falls back to __event_type__.
    __event_type__ = "scoreboard.score_updated"

    team: str
    score: int


__all__ = ["ScoreUpdatedEvent"]
