"""
varco_core.cache.policy
=========================
``CachePolicy`` — the single frozen configuration object that drives
``read_through()`` (Plan 010).  Every field defaults to today's observable
behaviour: ``CachePolicy()`` is the identity policy — no envelope, no
singleflight, no jitter, no negative caching.

One policy, four features
--------------------------
::

    CachePolicy(
        ttl=300.0,             # hard TTL
        ttl_jitter=0.1,        # ±10% randomized TTL (no synchronized cliff)
        soft_ttl=240.0,        # stale-while-revalidate window
        negative_ttl=30.0,     # opt-in negative caching (D-4)
        stale_if_error=600.0,  # serve stale on loader failure
        singleflight=True,     # coalesce concurrent recomputes
        refresh_mode="background",
    )

DESIGN: one CachePolicy over four independent feature flags/configs
    ✅ ``read_through()`` takes one object, not four optional parameters —
       the "C2 and C4 interact" landmine (see Plan 010 Design section) is
       resolved by having every feature read the same policy.
    ✅ Frozen — safe to share across concurrent callers, hashable, cheap to
       construct once per ``@cached`` decoration or ``CacheServiceMixin``
       subclass.
    ❌ A single dataclass with five semantically-linked-but-independent
       knobs is less discoverable than five separate objects — mitigated by
       the validation in ``__post_init__`` and this module's docstring.

Thread safety:  ✅ Frozen dataclass — immutable after construction.
Async safety:   ✅ No I/O.
"""

from __future__ import annotations

import dataclasses
import random as _random_module
from typing import Literal

RefreshMode = Literal["background", "blocking"]


@dataclasses.dataclass(frozen=True)
class CachePolicy:
    """
    Frozen read-through cache policy.

    Attributes:
        ttl:            Hard TTL in seconds.  ``None`` = no expiry beyond the
                         backend's own default (today's behaviour).
        ttl_jitter:      Symmetric fractional jitter applied to ``ttl`` by
                         ``effective_ttl()`` — ``0.0`` (default) is
                         deterministic; must be in the half-open interval
                         ``[0.0, 1.0)``.
        soft_ttl:       Stale-while-revalidate window in seconds.  Must be
                         strictly less than ``ttl`` when both are set — a
                         soft TTL at or beyond the hard TTL can never fire.
                         ``None`` (default) disables SWR.
        negative_ttl:   TTL for a cached ``None`` (D-4).  ``None`` (default)
                         means a ``None`` loader result is never cached —
                         identical to pre-Plan-010 ``@cached`` behaviour.
        stale_if_error: If the loader raises and a stale value exists within
                         this many seconds of its hard expiry, serve the
                         stale value instead of propagating the exception.
                         Requires ``ttl`` to be set (there is nothing to
                         measure staleness against otherwise).
        singleflight:    Whether ``read_through()`` should coalesce
                         concurrent recomputes for the same key via the
                         ``Singleflight`` passed in.  ``False`` (default)
                         reproduces today's per-caller-recomputes behaviour.
        refresh_mode:    ``"background"`` (default) — a soft-stale reader
                         gets the stale value immediately and the refresh
                         runs in the background.  ``"blocking"`` — the
                         reader awaits the refresh before returning.
        name:            Bounded ``cache=`` metric attribute (C3). Defaults
                         to ``""`` — callers should set this to a stable,
                         low-cardinality name (never a key or tenant id).

    Properties:
        requires_envelope: ``True`` when any of ``soft_ttl``,
                         ``negative_ttl``, or ``stale_if_error`` is set —
                         i.e. when ``read_through()`` must write the
                         ``CacheEnvelope`` wire format instead of the raw
                         value (D-5).

    Raises:
        ValueError: ``ttl_jitter`` outside ``[0.0, 1.0)``; ``soft_ttl >=
            ttl`` when both are set; ``stale_if_error`` set without ``ttl``.

    Thread safety:  ✅ Frozen — safe to share across coroutines/tasks.
    """

    ttl: float | None = None
    ttl_jitter: float = 0.0
    soft_ttl: float | None = None
    negative_ttl: float | None = None
    stale_if_error: float | None = None
    singleflight: bool = False
    refresh_mode: RefreshMode = "background"
    name: str = ""

    def __post_init__(self) -> None:
        if not (0.0 <= self.ttl_jitter < 1.0):
            raise ValueError(
                f"CachePolicy.ttl_jitter must be in [0.0, 1.0); got {self.ttl_jitter!r}."
            )
        if (
            self.soft_ttl is not None
            and self.ttl is not None
            and self.soft_ttl >= self.ttl
        ):
            raise ValueError(
                f"CachePolicy.soft_ttl ({self.soft_ttl!r}) must be strictly less "
                f"than ttl ({self.ttl!r}) — a soft TTL at or beyond the hard TTL "
                "can never fire."
            )
        if self.stale_if_error is not None and self.ttl is None:
            raise ValueError(
                "CachePolicy.stale_if_error requires ttl to be set — there is "
                "nothing to measure staleness against otherwise."
            )

    @property
    def requires_envelope(self) -> bool:
        """
        ``True`` when ``read_through()`` must write the ``CacheEnvelope``
        wire format rather than the raw value (D-5).
        """
        return (
            self.soft_ttl is not None
            or self.negative_ttl is not None
            or self.stale_if_error is not None
        )

    def effective_ttl(
        self, *, rng: _random_module.Random | None = None
    ) -> float | None:
        """
        Apply symmetric fractional jitter to ``ttl``.

        Args:
            rng: Random source.  Defaults to the module-level ``random``
                 instance.  Tests pass a seeded ``random.Random`` for
                 determinism.

        Returns:
            ``None`` if ``ttl`` is ``None``.  Otherwise a value uniformly
            drawn from ``[ttl * (1 - ttl_jitter), ttl * (1 + ttl_jitter)]``.
            With ``ttl_jitter=0.0`` (the default), returns exactly ``ttl``
            every time — deterministic.
        """
        if self.ttl is None:
            return None
        if self.ttl_jitter == 0.0:
            return self.ttl
        source = rng if rng is not None else _random_module
        low = self.ttl * (1.0 - self.ttl_jitter)
        high = self.ttl * (1.0 + self.ttl_jitter)
        return source.uniform(low, high)


__all__ = ["CachePolicy", "RefreshMode"]
