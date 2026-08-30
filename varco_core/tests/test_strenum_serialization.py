"""
Characterization tests for Plan 020 / RL-15's ``StrEnum`` migration.

Written against the CURRENT ``class Foo(str, Enum)`` form and expected to
keep passing byte-for-byte after the migration to ``enum.StrEnum`` — these
lock down the two serialization boundaries research brief 002 (§5,
§"Pydantic v2 Behaviour") reports as unaffected by the migration (EC-3):
``json.dumps`` and pydantic v2's ``model_dump(mode="json")`` both serialize
by *value* under both forms.

If any of these fails BEFORE migration, something already differs from
brief 002's claim and §RL-15 must be re-litigated before Step 38 proceeds
(per the plan's own Risks table). If any fails AFTER migration, the
migration broke something brief 002 promised would not change — also a stop
condition, not a rewrite-the-test situation.

What THIS suite deliberately does not test: ``str()``/``f"{...}"``
formatting, which brief 002 §1 explains genuinely changes on Python 3.11+
between the two forms (`Enum.__format__`'s CPython #100458 regression,
which ``StrEnum`` exists specifically to undo) — that is the intended,
documented BREAKING change, not a boundary this test guards.

Async safety: N/A — pure serialization assertions, no I/O.
"""

from __future__ import annotations

import json

from pydantic import BaseModel
from varco_core.health import HealthStatus


class _ModelWithHealthStatus(BaseModel):
    status: HealthStatus


class TestStdlibJsonSerializationUnaffectedByStrEnumMigration:
    """``json.JSONEncoder`` does an ``isinstance(obj, str)`` check (brief 002 §5) —
    identical output whether the enum inherits `str` explicitly or via `StrEnum`.
    """

    def test_json_dumps_serializes_by_value(self) -> None:
        assert json.dumps({"k": HealthStatus.HEALTHY}) == '{"k": "healthy"}'


class TestPydanticV2SerializationUnaffectedByStrEnumMigration:
    """Pydantic v2 serializes both ``(str, Enum)`` and ``StrEnum`` members by
    *value* in JSON mode (brief 002 §"Pydantic v2 Behaviour") — no observable
    difference at this boundary.
    """

    def test_model_dump_json_mode_serializes_by_value(self) -> None:
        model = _ModelWithHealthStatus(status=HealthStatus.HEALTHY)
        assert model.model_dump(mode="json") == {"status": "healthy"}


class TestBaseSettingsEnvParsingUnaffectedByStrEnumMigration:
    """``BaseSettings`` env-var parsing of a ``(str, Enum)``-typed field is
    identical to ``StrEnum`` parsing (brief 002 §"Pydantic v2 Behaviour") —
    both accept the plain string value from the environment.
    """

    def test_kafka_delivery_semantics_parses_from_env_string(self, monkeypatch) -> None:
        from varco_kafka.config import KafkaDeliverySemantics, KafkaEventBusSettings

        monkeypatch.setenv("VARCO_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        monkeypatch.setenv("VARCO_KAFKA_DELIVERY_SEMANTICS", "at_most_once")

        settings = KafkaEventBusSettings()

        assert settings.delivery_semantics == KafkaDeliverySemantics.AT_MOST_ONCE
        assert settings.delivery_semantics == "at_most_once"
