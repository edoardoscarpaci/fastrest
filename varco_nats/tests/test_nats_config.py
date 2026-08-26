"""
Unit tests for varco_nats.config
=================================
``NatsEventBusSettings`` and ``NatsDeliverySemantics`` — pure config, no broker.
"""

from __future__ import annotations

import pytest

from varco_nats import NatsDeliverySemantics, NatsEventBusSettings

# ── NatsDeliverySemantics ─────────────────────────────────────────────────────


class TestNatsDeliverySemantics:
    def test_values_are_plain_strings(self) -> None:
        # str + Enum → JSON-serialisable, env-var readable.
        assert NatsDeliverySemantics.AT_LEAST_ONCE.value == "at_least_once"
        assert NatsDeliverySemantics.AT_MOST_ONCE.value == "at_most_once"
        assert NatsDeliverySemantics.EXACTLY_ONCE.value == "exactly_once"

    def test_constructable_from_value(self) -> None:
        assert NatsDeliverySemantics("exactly_once") is NatsDeliverySemantics.EXACTLY_ONCE


# ── NatsEventBusSettings — defaults ───────────────────────────────────────────


class TestNatsEventBusSettingsDefaults:
    def test_defaults(self) -> None:
        cfg = NatsEventBusSettings()
        assert cfg.servers == "nats://localhost:4222"
        assert cfg.stream_name == "varco-events"
        assert cfg.subject_prefix == "varco"
        assert cfg.durable_name == "varco-default"
        assert cfg.channel_prefix == ""
        assert cfg.auto_create_stream is True
        assert cfg.delivery_semantics is NatsDeliverySemantics.AT_LEAST_ONCE

    def test_frozen(self) -> None:
        cfg = NatsEventBusSettings()
        # frozen=True — settings must be immutable after construction.
        with pytest.raises(Exception):
            cfg.durable_name = "other"  # type: ignore[misc]


# ── NatsEventBusSettings — subject helpers ────────────────────────────────────


class TestNatsEventBusSettingsSubjects:
    def test_subject_name_no_channel_prefix(self) -> None:
        cfg = NatsEventBusSettings()
        assert cfg.subject_name("orders") == "varco.orders"

    def test_subject_name_with_channel_prefix(self) -> None:
        cfg = NatsEventBusSettings(channel_prefix="prod.")
        # channel_prefix nests INSIDE subject_prefix.
        assert cfg.subject_name("orders") == "varco.prod.orders"

    def test_subject_name_custom_subject_prefix(self) -> None:
        cfg = NatsEventBusSettings(subject_prefix="acme")
        assert cfg.subject_name("orders") == "acme.orders"

    def test_wildcard_subject(self) -> None:
        cfg = NatsEventBusSettings(subject_prefix="acme")
        assert cfg.wildcard_subject() == "acme.>"

    def test_channel_from_subject_roundtrips(self) -> None:
        cfg = NatsEventBusSettings(channel_prefix="prod.")
        subject = cfg.subject_name("orders")
        # channel_from_subject is the inverse of subject_name.
        assert cfg.channel_from_subject(subject) == "orders"

    def test_channel_from_subject_no_prefix(self) -> None:
        cfg = NatsEventBusSettings()
        assert cfg.channel_from_subject("varco.orders") == "orders"

    def test_durable_for_sanitises_dots(self) -> None:
        cfg = NatsEventBusSettings(durable_name="svc")
        # Dots are illegal in NATS durable names — they must be replaced.
        assert cfg.durable_for("orders.eu") == "svc-orders_eu"

    def test_durable_for_simple_channel(self) -> None:
        cfg = NatsEventBusSettings(durable_name="svc")
        assert cfg.durable_for("orders") == "svc-orders"

    def test_to_servers_list_splits_on_comma(self) -> None:
        cfg = NatsEventBusSettings(servers="nats://a:4222, nats://b:4222 ,nats://c:4222")
        # Whitespace around comma-separated entries is stripped.
        assert cfg.to_servers_list() == [
            "nats://a:4222",
            "nats://b:4222",
            "nats://c:4222",
        ]
