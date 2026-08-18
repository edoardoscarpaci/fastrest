"""
varco_core.reliability.wiring
================================
Process-wide default ``ReliabilityPreset`` (Plan 009, Phase 9 / R5, RD-7).

``set_default_reliability_preset()`` is opt-in — the default is
``ReliabilityPreset.off()``, so ``@listen``'s resolved ``(retry_policy, dlq)``
pair is byte-identical to today's behaviour unless this function is called.
Resolution happens at ``EventConsumer.register_to()`` time (not at
``@listen`` decoration time), so a preset set *after* a ``@listen``-decorated
class is defined still applies — see
``varco_core.event.consumer.EventConsumer.register_to()``.

Thread safety:  ⚠️ A bare module-level variable, same as every other
                   process-wide singleton config in this codebase
                   (``_instrument_cache`` et al.) — set once at startup
                   before concurrent access, not mutated per-request.
Async safety:   ✅ Both functions are synchronous, no I/O.
"""

from __future__ import annotations

from varco_core.reliability.preset import ReliabilityPreset

_default_preset: ReliabilityPreset = ReliabilityPreset.off()


def set_default_reliability_preset(preset: ReliabilityPreset) -> None:
    """
    Set the process-wide default ``ReliabilityPreset``.

    Args:
        preset: The new default. Every bare ``@listen`` handler (one that
            declared neither ``retry_policy=`` nor ``dlq=``, and whose
            consumer's ``register_to()`` call carries no instance-level
            fallback either) inherits ``preset.retry_policy``/``preset.dlq``
            from the next ``register_to()`` call onward.

    Edge cases:
        - Calling this twice (e.g. two ``create_varco_app()`` calls in a
          composite deployment) makes the last writer win — the *global*
          default is genuinely process-wide, unlike each app's own
          ``ReliabilityLifecycle``. Composite deployments should either
          share one preset or avoid the global default entirely (use
          per-``@listen`` ``retry_policy=``/``dlq=`` instead).
    """
    global _default_preset
    _default_preset = preset


def get_default_reliability_preset() -> ReliabilityPreset:
    """Return the current process-wide default ``ReliabilityPreset``."""
    return _default_preset


__all__ = ["get_default_reliability_preset", "set_default_reliability_preset"]
