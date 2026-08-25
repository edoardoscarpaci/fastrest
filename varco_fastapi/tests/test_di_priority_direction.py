"""
Regression guard — providify "HIGHER priority value wins" (Plan 016 / RL-3c,
Design §RL-3c, Step 30).

providify's CHANGELOG (§2.0.0, lines 233-235) states the code's ``max()`` in
``_get_best_candidate`` was ALWAYS correct — only the OLD docs wrongly said
"lower priority wins". No runtime behaviour changed at 2.0.0; this test pins
the correct, already-shipped direction against varco_fastapi's own
framework-default bindings (``varco_fastapi/varco_fastapi/di.py:75-104`` — the
``priority=-sys.maxsize - 1`` family), so a future accidental flip (in either
providify or varco) is caught immediately.

``container.get_all()`` is documented (``providify/container.py:1324``) to
return candidates sorted by ASCENDING priority (lowest number first) — so the
winner (highest priority, the one ``.get()``/injection actually resolves)
must be the LAST element of ``get_all()``.

Thread safety:  N/A (unit tests)
Async safety:   N/A (all providers under test are synchronous)
"""

from __future__ import annotations

import sys

import pytest
from providify import DIContainer, Provider

from varco_fastapi.auth.trust_store import TrustStore
from varco_fastapi.client.base import ClientProfile
from varco_fastapi.middleware.cors import CORSConfig


def _app_trust_store_provider():
    @Provider(singleton=True, priority=100)
    def app_trust_store() -> TrustStore:
        # include_system_cas=False distinguishes this from the framework
        # default (TrustStore.from_env() -> include_system_cas=True).
        return TrustStore(include_system_cas=False)

    return app_trust_store, lambda ts: ts.include_system_cas is False


def _app_cors_config_provider():
    @Provider(singleton=True, priority=100)
    def app_cors_config() -> CORSConfig:
        # Distinguishable from CORSConfig.from_env()'s ("*",) default.
        return CORSConfig(allow_origins=("https://app.example.com",))

    return app_cors_config, lambda cc: cc.allow_origins == ("https://app.example.com",)


def _app_client_profile_provider():
    @Provider(singleton=True, priority=100)
    def app_client_profile() -> ClientProfile:
        # Distinguishable from ClientProfile.from_env()'s default timeout.
        return ClientProfile.development(timeout=1.0)

    return app_client_profile, lambda cp: cp.timeout == 1.0


FRAMEWORK_DEFAULT_FAMILIES = [
    pytest.param(TrustStore, _app_trust_store_provider, id="TrustStore"),
    pytest.param(CORSConfig, _app_cors_config_provider, id="CORSConfig"),
    pytest.param(ClientProfile, _app_client_profile_provider, id="ClientProfile"),
]


@pytest.mark.parametrize("interface, make_app_provider", FRAMEWORK_DEFAULT_FAMILIES)
def test_app_binding_registered_after_bootstrap_wins_over_framework_default(
    interface: type, make_app_provider
) -> None:
    """
    A plain app @Provider registered AFTER container.scan("varco_fastapi") —
    i.e. after the framework's priority=-sys.maxsize - 1 default is already
    bound — must win resolution, because a plain positive priority is always
    HIGHER than -sys.maxsize - 1.
    """
    app_provider_fn, is_app_instance = make_app_provider()

    container = DIContainer()
    container.scan("varco_fastapi", recursive=True)
    container.provide(app_provider_fn)

    resolved = container.get(interface)

    assert is_app_instance(resolved), (
        f"expected the app-registered {interface.__name__} (priority=100) to "
        f"win over the framework default (priority=-sys.maxsize - 1)"
    )


@pytest.mark.parametrize("interface, make_app_provider", FRAMEWORK_DEFAULT_FAMILIES)
def test_get_all_returns_ascending_priority_with_winner_last(
    interface: type, make_app_provider
) -> None:
    """
    container.get_all() sorts ascending by priority (lowest first,
    providify/container.py:1324's own docstring) — the framework default
    (priority=-sys.maxsize - 1) must therefore come FIRST and the
    app-registered override (priority=100) must come LAST, and the last
    entry must be the same instance .get() resolves.
    """
    app_provider_fn, is_app_instance = make_app_provider()

    container = DIContainer()
    container.scan("varco_fastapi", recursive=True)
    container.provide(app_provider_fn)

    all_instances = container.get_all(interface)

    assert len(all_instances) == 2
    assert not is_app_instance(
        all_instances[0]
    ), "framework default (lowest priority) must sort first in get_all()"
    assert is_app_instance(
        all_instances[-1]
    ), "app override (highest priority) must sort last in get_all()"
    assert container.get(interface) is all_instances[-1]


def test_sys_maxsize_framework_default_is_the_lowest_possible_priority() -> None:
    """
    Sanity check on the constant itself: -sys.maxsize - 1 is the most
    negative value practically usable as a priority — anchoring WHY every
    plain positive-priority app override always wins, regardless of the
    specific value chosen.
    """
    assert -sys.maxsize - 1 < -1_000_000
    assert -sys.maxsize - 1 < 0
