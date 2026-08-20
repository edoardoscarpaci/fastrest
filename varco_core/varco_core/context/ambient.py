"""
varco_core.context.ambient
===========================
``AmbientVar[T]`` — the generic request-scoped ambient-value primitive (X1).

This is the *generalization* of two implementations that already existed and
already agreed with each other: ``tenant_context()``
(``varco_core.service.tenant``, sync context manager, token-based reset) and
``correlation_context()`` (``varco_core.tracing``, async context manager,
token-based reset). Both are **not** rewritten onto this primitive — see
Plan 011 D-6 — they remain the precedent this module documents and
generalizes; ``current_tenant()`` stays the single source of truth for "who
is the tenant".

DESIGN: module-scope ``ContextVar`` construction is correct here
    ✅ PEP 567 requires a ``ContextVar`` to be created once, typically at
       module scope, to be usable and shared correctly across ``asyncio``
       tasks. Unlike ``asyncio.Lock`` (which requires a running event loop
       to bind to), ``ContextVar()`` construction has no such requirement —
       it is plain object construction.
    ❌ "Everything must be constructed lazily inside a method" is the wrong
       lesson to generalize from CLAUDE.md's ``asyncio.Lock`` rule — that
       rule exists specifically because a lock created outside a running
       loop can bind to the wrong loop. A ``ContextVar`` has no loop
       affinity at all.

ASGI note
    A value set via ``scope()``/``ascope()`` inside a middleware is visible
    to everything running *inside* that middleware's ``with``/``async with``
    block, and is gone the instant that block's ``finally`` resets the
    token. A middleware layered *outside* the one that set the value will
    not see it once the inner middleware has returned control (e.g. after
    the request/response cycle unwinds through it). ``varco_fastapi``'s
    ``LocalizationMiddleware`` works around this for error rendering by
    also mirroring the resolved value onto ``request.state`` — see RD-3.

Thread safety:  ✅ ``ContextVar`` is task-local; no explicit lock is needed.
Async safety:   ✅ Each ``asyncio.Task`` copies its parent's context at
                spawn time; mutations inside a child task never propagate
                back to the parent (copy-on-spawn — asserted by
                ``test_context_ambient.py``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from typing import AsyncIterator, Generic, Iterator, TypeVar

T = TypeVar("T")

__all__ = ["AmbientVar"]


class AmbientVar(Generic[T]):
    """
    A generic, named, request-scoped ambient value.

    Wraps a single ``contextvars.ContextVar[T | None]``, created eagerly at
    construction time (see the module DESIGN block for why this is correct
    even though CLAUDE.md requires *locks* to be created lazily).

    Args:
        name: A unique, human-legible name for the underlying ``ContextVar``
            — shows up in debuggers/tracebacks. Two ``AmbientVar`` instances
            constructed with the *same* name string are still independent;
            each wraps its own ``ContextVar`` object.
        default: Value returned by ``get()`` when no ``scope()``/``ascope()``
            is active. Defaults to ``None``.

    Edge cases:
        - Nesting ``scope()``/``ascope()`` restores the *exact* enclosing
          value on exit (token-based reset), not just the constructor
          default.
        - A value set inside a spawned ``asyncio.Task`` is invisible to the
          parent task once the task is scheduled — Python's context
          copy-on-spawn semantics, not a varco-specific behaviour.

    Async safety: ✅ Backed by ``ContextVar`` — isolated per ``asyncio.Task``.
    """

    def __init__(self, name: str, *, default: T | None = None) -> None:
        self._name = name
        self._default = default
        # DESIGN: eager construction — see module docstring. PEP 567
        # requires this for ContextVars to behave correctly across tasks.
        self._var: ContextVar[T | None] = ContextVar(name, default=default)

    def get(self) -> T | None:
        """
        Return the active value, or the constructor ``default`` if unset.

        Async safety: ✅ Pure read of a task-local ``ContextVar``.
        """
        return self._var.get()

    def set_for_task(self, value: T) -> Token[T | None]:
        """
        Set the value for the current task and return the raw ``Token``.

        Explicit set/reset API for callers that need manual control outside
        a context-manager shape (e.g. framework middleware wiring that must
        set on request entry and reset in a ``finally`` several stack frames
        away). Prefer ``scope()``/``ascope()`` when a context-manager shape
        is available.

        Args:
            value: The value to activate.

        Returns:
            A ``contextvars.Token`` — pass it to ``reset()`` to restore the
            prior value.
        """
        return self._var.set(value)

    def reset(self, token: Token[T | None]) -> None:
        """
        Restore the value active before the matching ``set_for_task()`` call.

        Args:
            token: The ``Token`` returned by ``set_for_task()``.
        """
        self._var.reset(token)

    @contextmanager
    def scope(self, value: T) -> Iterator[T]:
        """
        Synchronous context manager activating ``value`` for the block.

        Always resets in ``finally`` — including on the exceptional path —
        so a failed request never leaks ambient state into whatever runs
        next on the same task.

        Args:
            value: The value to activate for the duration of the block.

        Yields:
            ``value``, for convenience at the call site.
        """
        token = self._var.set(value)
        try:
            yield value
        finally:
            self._var.reset(token)

    @asynccontextmanager
    async def ascope(self, value: T) -> AsyncIterator[T]:
        """
        Async context manager activating ``value`` for the block.

        Same reset guarantee as ``scope()``, usable across an ``await``.

        Args:
            value: The value to activate for the duration of the block.

        Yields:
            ``value``, for convenience at the call site.
        """
        token = self._var.set(value)
        try:
            yield value
        finally:
            self._var.reset(token)
