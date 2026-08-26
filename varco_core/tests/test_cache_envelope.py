"""
tests.test_cache_envelope
===========================
Plan 010 Phase 0, step 2 — ``varco_core.cache.envelope``.

RED until ``varco_core/cache/envelope.py`` lands.
"""

from __future__ import annotations

from dataclasses import dataclass


class TestEnvelopeRoundTrip:
    async def test_wrap_unwrap_round_trips_through_json_serializer(self) -> None:
        # The envelope must survive an actual JSON encode/decode cycle since
        # it is the wire format written to Redis via JsonSerializer.
        import json

        from varco_core.cache.envelope import (
            MARKER,
            WIRE_VERSION,
            CacheEnvelope,
            unwrap,
            wrap,
        )

        env = CacheEnvelope(
            value={"a": 1},
            stored_at=100.0,
            soft_expires_at=None,
            hard_expires_at=200.0,
            is_negative=False,
        )
        payload = wrap(env)
        assert payload[MARKER] == WIRE_VERSION
        round_tripped = json.loads(json.dumps(payload))
        result = unwrap(round_tripped)
        assert result is not None
        assert result.value == {"a": 1}
        assert result.hard_expires_at == 200.0
        assert result.is_negative is False

    async def test_unwrap_on_legacy_raw_dict_returns_none(self) -> None:
        from varco_core.cache.envelope import unwrap

        # A legacy value stored before this plan has no marker at all — must
        # be treated as "not an envelope", not raise.
        assert unwrap({"title": "hello"}) is None

    async def test_unwrap_on_dict_with_wrong_marker_version_returns_none(self) -> None:
        from varco_core.cache.envelope import MARKER, unwrap

        assert unwrap({MARKER: 999, "v": "x"}) is None

    async def test_coerce_with_pydantic_model_type_hint(self) -> None:
        from pydantic import BaseModel
        from varco_core.cache.envelope import coerce

        class UserModel(BaseModel):
            id: int
            name: str

        result = coerce({"id": 1, "name": "a"}, UserModel)
        assert isinstance(result, UserModel)
        assert result.id == 1

    async def test_coerce_with_dataclass_type_hint(self) -> None:
        from varco_core.cache.envelope import coerce

        @dataclass
        class Point:
            x: int
            y: int

        result = coerce({"x": 1, "y": 2}, Point)
        assert isinstance(result, Point)
        assert result.x == 1

    async def test_coerce_with_no_type_hint_passes_through(self) -> None:
        from varco_core.cache.envelope import coerce

        assert coerce({"raw": True}, None) == {"raw": True}

    async def test_negative_envelope_round_trips_with_value_none(self) -> None:
        # A cached "not found" result must be distinguishable from "absent" —
        # unwrap() returning a CacheEnvelope with value=None and is_negative=True
        # is different from unwrap() returning None (no entry at all).
        from varco_core.cache.envelope import CacheEnvelope, unwrap, wrap

        env = CacheEnvelope(
            value=None,
            stored_at=100.0,
            soft_expires_at=None,
            hard_expires_at=130.0,
            is_negative=True,
        )
        payload = wrap(env)
        result = unwrap(payload)
        assert result is not None
        assert result.value is None
        assert result.is_negative is True
