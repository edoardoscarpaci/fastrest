"""
varco_core.context.precedence
================================
``resolve_precedence`` — the one precedence-resolution helper I2 (locale) and
T1 (timezone) are thin consumers of, rather than two divergent copies of the
same "first non-None wins" logic (Plan 011 D-6).

Pure, synchronous, no I/O, no logging — the *caller* logs ``Resolved.source``.
Kept synchronous and side-effect-free deliberately so it stays callable from
the error-rendering path, which must never ``await``. Async candidate sources
(e.g. a ``TenantDefaultsProvider`` lookup) are awaited by the caller *before*
building the candidate list.

DESIGN: explicit ``(source, value)`` pairs over an ``or``-chain
    ✅ A falsy-but-legitimate value (``""``, ``0``) is correctly selected —
       an ``or``-chain would skip past it to the next candidate, which is
       exactly the bug this function exists to avoid.
    ✅ ``Resolved.source`` turns "why did this user get German?" from a
       debugging session into one DEBUG log line at the call site.
    ❌ Callers must pre-compute every candidate (including ones that may
       never be used) before calling — acceptable because every current
       caller's candidates are cheap or already-awaited values.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

__all__ = ["Resolved", "resolve_precedence"]


@dataclass(frozen=True)
class Resolved(Generic[T]):
    """
    The result of ``resolve_precedence()`` — a value plus the name of the
    precedence-chain source that supplied it.

    Args:
        value: The resolved value.
        source: The name of the candidate slot that supplied ``value`` (e.g.
            ``"query_param"``, ``"tenant_default"``, ``"fallback"``).
    """

    value: T
    source: str


def resolve_precedence(
    candidates: Sequence[tuple[str, T | None]]
) -> Resolved[T] | None:
    """
    Return the first non-``None`` candidate, wrapped with its source name.

    Args:
        candidates: An ordered sequence of ``(source_name, value)`` pairs,
            highest precedence first. ``value`` may be ``None`` to indicate
            "this source had nothing to offer".

    Returns:
        ``Resolved(value, source)`` for the first candidate whose ``value``
        is not ``None``, or ``None`` if every candidate is ``None`` (or the
        sequence is empty).

    Edge cases:
        - A falsy-but-not-``None`` value (``""``, ``0``, ``False``) **is**
          selected — this function exists specifically to avoid the
          ``or``-chain bug where such a value is silently skipped.

    Async safety: ✅ Pure, synchronous, no I/O — safe to call from any
        context, including the non-async error-rendering path.
    """
    for source, value in candidates:
        if value is not None:
            return Resolved(value=value, source=source)
    return None
