"""Smoke tests for the varco_core.event public API surface."""

from __future__ import annotations


def test_all_event_symbols_importable() -> None:
    """Every symbol in varco_core.event.__all__ must be importable from that path."""
    import varco_core.event as m

    missing = [name for name in m.__all__ if not hasattr(m, name)]
    assert not missing, f"Symbols in __all__ but not importable: {missing}"


def test_domain_event_alias() -> None:
    """DomainEvent must be the same object as Event (alias, not a copy)."""
    from varco_core.event import DomainEvent, Event

    assert DomainEvent is Event


def test_core_event_symbols_importable_from_root() -> None:
    """The most-used event symbols should be importable from varco_core.event directly."""
    from varco_core.event import (  # noqa: F401
        AbstractDeadLetterQueue,
        AbstractEventBus,
        AbstractEventProducer,
        BusEventProducer,
        DomainEvent,
        Event,
        EventConsumer,
        InMemoryDeadLetterQueue,
        InMemoryEventBus,
        NoopEventProducer,
        listen,
    )


def test_all_jwt_symbols_importable() -> None:
    """Every symbol in varco_core.jwt.__all__ must be importable from that path."""
    import varco_core.jwt as m

    missing = [name for name in m.__all__ if not hasattr(m, name)]
    assert not missing, f"Symbols in __all__ but not importable: {missing}"


def test_all_jwt_transform_symbols_importable() -> None:
    """Every symbol in varco_core.jwt.transform.__all__ must be importable."""
    import varco_core.jwt.transform as m

    missing = [name for name in m.__all__ if not hasattr(m, name)]
    assert not missing, f"Symbols in __all__ but not importable: {missing}"


def test_jwt_transform_core_symbols_importable_from_root() -> None:
    """The claim-transform symbols listed in Plan 002 step 14 are importable."""
    from varco_core.jwt import (  # noqa: F401
        CanonicalClaim,
        ClaimMapping,
        ClaimPath,
        ClaimRule,
        ClaimTransformer,
        ClaimTransformError,
        IdentityClaimTransformer,
        MappingClaimTransformer,
        ValueShape,
        configure_claim_transforms,
        read_claim,
        resolve_claim_transformer,
    )
