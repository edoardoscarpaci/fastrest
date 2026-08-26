"""
Unit tests for varco_core.observability.attributes (Plan 004 — Phase 2,
deliverable B): the process-wide global attribute registry stamped onto every
span and every metric measurement.

Global-state hygiene: ``GlobalAttributes`` is process-wide mutable state
(module-level singleton). Every test resets it via the autouse fixture below
(Plan 004 "Risks" section — mandatory).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_global_attrs_state():
    def _reset() -> None:
        try:
            from varco_core.observability.attributes import clear_global_attributes

            clear_global_attributes()
        except ImportError:
            pass
        try:
            from varco_core.observability.params import reset_param_capture_state

            reset_param_capture_state()
        except ImportError:
            pass

    _reset()
    yield
    _reset()


# ── Static set / add / remove / clear ───────────────────────────────────────


class TestGlobalAttributesStaticAPI:
    def test_set_replaces_and_snapshot_reflects_it(self) -> None:
        from varco_core.observability.attributes import (
            current_global_attributes,
            set_global_attributes,
        )

        set_global_attributes(pod="p1", env="prod")
        snap = current_global_attributes()
        assert snap["pod"] == "p1"
        assert snap["env"] == "prod"

    def test_set_accepts_mapping_positional_arg(self) -> None:
        from varco_core.observability.attributes import (
            current_global_attributes,
            set_global_attributes,
        )

        set_global_attributes({"k8s.pod.name": "orders-7d9"})
        assert current_global_attributes()["k8s.pod.name"] == "orders-7d9"

    def test_add_single_key(self) -> None:
        from varco_core.observability.attributes import global_attributes

        registry = global_attributes()
        registry.add("release", "blue")
        assert registry.snapshot()["release"] == "blue"

    def test_remove_key(self) -> None:
        from varco_core.observability.attributes import global_attributes

        registry = global_attributes()
        registry.set(pod="p1")
        registry.remove("pod")
        assert "pod" not in registry.snapshot()

    def test_clear_removes_everything(self) -> None:
        from varco_core.observability.attributes import (
            clear_global_attributes,
            current_global_attributes,
            set_global_attributes,
        )

        set_global_attributes(pod="p1")
        clear_global_attributes()
        assert current_global_attributes() == {}


class TestSnapshotImmutability:
    def test_snapshot_is_read_only(self) -> None:
        from varco_core.observability.attributes import global_attributes

        registry = global_attributes()
        registry.set(pod="p1")
        snap = registry.snapshot()
        with pytest.raises(TypeError):
            snap["pod"] = "changed"  # type: ignore[index]

    def test_snapshot_identity_stable_between_reads_without_mutation(self) -> None:
        from varco_core.observability.attributes import global_attributes

        registry = global_attributes()
        registry.set(pod="p1")
        snap1 = registry.snapshot()
        snap2 = registry.snapshot()
        assert snap1 is snap2

    def test_snapshot_identity_changes_after_mutation(self) -> None:
        from varco_core.observability.attributes import global_attributes

        registry = global_attributes()
        registry.set(pod="p1")
        snap1 = registry.snapshot()
        registry.add("release", "blue")
        snap2 = registry.snapshot()
        assert snap1 is not snap2


# ── Providers ────────────────────────────────────────────────────────────────


class TestProviders:
    def test_provider_default_ttl_evaluated_once(self) -> None:
        from varco_core.observability.attributes import global_attributes

        registry = global_attributes()
        calls = []

        def provider():
            calls.append(1)
            return {"pod": "p1"}

        registry.register_provider(provider, name="pod-provider")
        registry.snapshot()
        registry.snapshot()
        registry.snapshot()
        assert len(calls) == 1

    def test_provider_cache_ttl_zero_called_every_snapshot(self) -> None:
        from varco_core.observability.attributes import global_attributes

        registry = global_attributes()
        calls = []

        def provider():
            calls.append(1)
            return {"cohort": "canary"}

        registry.register_provider(provider, name="cohort-provider", cache_ttl=0.0)
        registry.snapshot()
        registry.snapshot()
        registry.snapshot()
        assert len(calls) == 3

    def test_provider_ttl_expiry_reevaluates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from varco_core.observability import attributes as attrs_mod

        registry = attrs_mod.global_attributes()
        clock = {"t": 0.0}
        monkeypatch.setattr(attrs_mod.time, "monotonic", lambda: clock["t"])

        calls = []

        def provider():
            calls.append(1)
            return {"gen": str(len(calls))}

        registry.register_provider(provider, name="gen-provider", cache_ttl=30.0)
        registry.snapshot()
        assert len(calls) == 1

        clock["t"] = 10.0
        registry.snapshot()
        assert len(calls) == 1  # still within TTL

        clock["t"] = 31.0
        registry.snapshot()
        assert len(calls) == 2  # TTL expired → re-evaluated

    def test_raising_provider_is_skipped_and_logged_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from varco_core.observability.attributes import global_attributes

        registry = global_attributes()

        def bad_provider():
            raise RuntimeError("boom")

        registry.register_provider(bad_provider, name="bad-provider")
        with caplog.at_level("WARNING"):
            snap1 = registry.snapshot()
            snap2 = registry.snapshot()

        assert "bad-provider" not in snap1
        assert "bad-provider" not in snap2
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    def test_provider_returning_none_values_are_dropped(self) -> None:
        from varco_core.observability.attributes import global_attributes

        registry = global_attributes()

        def provider():
            return {"present": "yes", "absent": None}

        registry.register_provider(provider, name="none-provider")
        snap = registry.snapshot()
        assert snap["present"] == "yes"
        assert "absent" not in snap

    def test_provider_returning_non_mapping_skipped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from varco_core.observability.attributes import global_attributes

        registry = global_attributes()

        def provider():
            return "not-a-mapping"  # type: ignore[return-value]

        registry.register_provider(provider, name="bad-shape-provider")
        with caplog.at_level("WARNING"):
            snap = registry.snapshot()
        assert "bad-shape-provider" not in snap

    def test_unregister_provider_removes_its_contribution(self) -> None:
        from varco_core.observability.attributes import global_attributes

        registry = global_attributes()
        registry.register_provider(lambda: {"pod": "p1"}, name="pod-provider")
        registry.snapshot()
        registry.unregister_provider("pod-provider")
        assert "pod" not in registry.snapshot()


# ── Env parsing ──────────────────────────────────────────────────────────────


class TestLoadGlobalAttributesFromEnv:
    def test_literal_pairs_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from varco_core.observability.attributes import (
            current_global_attributes,
            load_global_attributes_from_env,
        )

        monkeypatch.setenv(
            "VARCO_OTEL_GLOBAL_ATTRS", "k8s.pod.name=orders-7d9,service.release=blue"
        )
        load_global_attributes_from_env()
        snap = current_global_attributes()
        assert snap["k8s.pod.name"] == "orders-7d9"
        assert snap["service.release"] == "blue"

    def test_malformed_token_skipped_not_raised(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from varco_core.observability.attributes import (
            current_global_attributes,
            load_global_attributes_from_env,
        )

        monkeypatch.setenv("VARCO_OTEL_GLOBAL_ATTRS", "no-equals-sign,valid=ok")
        with caplog.at_level("WARNING"):
            load_global_attributes_from_env()
        snap = current_global_attributes()
        assert snap.get("valid") == "ok"
        assert "no-equals-sign" not in snap

    def test_env_indirection_reads_named_env_var_lazily(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from varco_core.observability.attributes import (
            current_global_attributes,
            load_global_attributes_from_env,
        )

        monkeypatch.setenv("POD_NAME", "orders-7d9")
        monkeypatch.setenv("VARCO_OTEL_GLOBAL_ATTR_ENV", "k8s.pod.name=POD_NAME")
        load_global_attributes_from_env()
        assert current_global_attributes()["k8s.pod.name"] == "orders-7d9"

    def test_env_indirection_unset_target_var_key_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from varco_core.observability.attributes import (
            current_global_attributes,
            load_global_attributes_from_env,
        )

        monkeypatch.delenv("MISSING_VAR", raising=False)
        monkeypatch.setenv("VARCO_OTEL_GLOBAL_ATTR_ENV", "k8s.node.name=MISSING_VAR")
        load_global_attributes_from_env()
        assert "k8s.node.name" not in current_global_attributes()


# ── configure_global_attributes toggles ─────────────────────────────────────


class TestConfigureGlobalAttributes:
    def test_apply_to_spans_default_true(self) -> None:
        from varco_core.observability.attributes import apply_to_spans

        assert apply_to_spans() is True

    def test_apply_to_metrics_default_true(self) -> None:
        from varco_core.observability.attributes import apply_to_metrics

        assert apply_to_metrics() is True

    def test_configure_disables_spans_only(self) -> None:
        from varco_core.observability.attributes import (
            apply_to_metrics,
            apply_to_spans,
            configure_global_attributes,
        )

        configure_global_attributes(apply_to_spans=False)
        assert apply_to_spans() is False
        assert apply_to_metrics() is True

    def test_configure_disables_metrics_only(self) -> None:
        from varco_core.observability.attributes import (
            apply_to_metrics,
            apply_to_spans,
            configure_global_attributes,
        )

        configure_global_attributes(apply_to_metrics=False)
        assert apply_to_metrics() is False
        assert apply_to_spans() is True


# ── GlobalAttrInstrument proxy ───────────────────────────────────────────────


class _FakeInstrument:
    """Minimal stand-in for an OTel Counter/Histogram — records calls."""

    def __init__(self) -> None:
        self.add_calls: list[tuple] = []
        self.record_calls: list[tuple] = []

    def add(self, amount, attributes=None, context=None):
        self.add_calls.append((amount, attributes, context))

    def record(self, value, attributes=None, context=None):
        self.record_calls.append((value, attributes, context))

    def some_other_method(self):
        return "delegated"


class TestGlobalAttrInstrument:
    def test_merges_global_attrs_into_add(self) -> None:
        from varco_core.observability.attributes import (
            GlobalAttrInstrument,
            set_global_attributes,
        )

        set_global_attributes(pod="p1")
        fake = _FakeInstrument()
        proxy = GlobalAttrInstrument(fake)
        proxy.add(1, attributes={"tenant": "acme"})

        amount, attrs, _ = fake.add_calls[0]
        assert amount == 1
        assert attrs["pod"] == "p1"
        assert attrs["tenant"] == "acme"

    def test_merges_global_attrs_into_record(self) -> None:
        from varco_core.observability.attributes import (
            GlobalAttrInstrument,
            set_global_attributes,
        )

        set_global_attributes(pod="p1")
        fake = _FakeInstrument()
        proxy = GlobalAttrInstrument(fake)
        proxy.record(0.5, attributes={"tenant": "acme"})

        value, attrs, _ = fake.record_calls[0]
        assert value == 0.5
        assert attrs["pod"] == "p1"

    def test_caller_attribute_wins_on_key_conflict(self) -> None:
        from varco_core.observability.attributes import (
            GlobalAttrInstrument,
            set_global_attributes,
        )

        set_global_attributes(tenant="global-tenant")
        fake = _FakeInstrument()
        proxy = GlobalAttrInstrument(fake)
        proxy.add(1, attributes={"tenant": "caller-tenant"})

        _, attrs, _ = fake.add_calls[0]
        assert attrs["tenant"] == "caller-tenant"

    def test_empty_registry_passes_attributes_through_unchanged_identity(self) -> None:
        """Edge case: empty registry short-circuits — same dict object, not a copy."""
        from varco_core.observability.attributes import GlobalAttrInstrument

        fake = _FakeInstrument()
        proxy = GlobalAttrInstrument(fake)
        original = {"tenant": "acme"}
        proxy.add(1, attributes=original)

        _, attrs, _ = fake.add_calls[0]
        assert attrs is original

    def test_unwrap_returns_inner_instrument(self) -> None:
        from varco_core.observability.attributes import GlobalAttrInstrument

        fake = _FakeInstrument()
        proxy = GlobalAttrInstrument(fake)
        assert proxy.unwrap() is fake

    def test_getattr_delegates_to_inner(self) -> None:
        from varco_core.observability.attributes import GlobalAttrInstrument

        fake = _FakeInstrument()
        proxy = GlobalAttrInstrument(fake)
        assert proxy.some_other_method() == "delegated"


class TestWrapInstrument:
    def test_wraps_by_default(self) -> None:
        from varco_core.observability.attributes import (
            GlobalAttrInstrument,
            wrap_instrument,
        )

        fake = _FakeInstrument()
        wrapped = wrap_instrument(fake)
        assert isinstance(wrapped, GlobalAttrInstrument)

    def test_returns_raw_instrument_when_apply_to_metrics_false(self) -> None:
        from varco_core.observability.attributes import (
            configure_global_attributes,
            wrap_instrument,
        )

        configure_global_attributes(apply_to_metrics=False)
        fake = _FakeInstrument()
        wrapped = wrap_instrument(fake)
        assert wrapped is fake


class TestWrapGaugeCallback:
    def test_merges_global_attrs_into_observations(self) -> None:
        from opentelemetry.metrics import Observation
        from varco_core.observability.attributes import (
            set_global_attributes,
            wrap_gauge_callback,
        )

        set_global_attributes(pod="p1")

        def cb(options=None):
            return [Observation(42, attributes={"tenant": "acme"})]

        wrapped = wrap_gauge_callback(cb)
        observations = list(wrapped(None))
        assert observations[0].value == 42
        assert observations[0].attributes["pod"] == "p1"
        assert observations[0].attributes["tenant"] == "acme"
