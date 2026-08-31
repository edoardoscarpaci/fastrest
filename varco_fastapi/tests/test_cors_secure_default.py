"""
Red tests for AB-5 — the CORS secure-by-default change (Plan 022 / Phase 3).

`design/api-freeze-and-standards/api-break-candidates.md` records AB-5's
verdict as `change-default`: ``CORSConfig.allow_origins`` moves from
``("*",)`` to ``()``. ``allow_credentials`` deliberately stays ``True`` — the
dangerous combination is *wildcard* + credentials, not credentials alone.

Every assertion below describes the POST-change state, so on today's tree the
default-value and behavioural tests must fail.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from varco_fastapi.middleware.cors import CORSConfig, install_cors

EVIL_ORIGIN = "https://evil.example.com"


def _app_with_cors(config: CORSConfig) -> FastAPI:
    app = FastAPI()
    install_cors(app, config)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    return app


# ── default value ─────────────────────────────────────────────────────────────


def test_default_allow_origins_is_empty() -> None:
    """AB-5: the class default must no longer be the wildcard."""
    assert CORSConfig().allow_origins == ()


def test_default_allow_credentials_stays_true() -> None:
    """AB-5 hardens origins only — flipping credentials too would be a second, unapproved break."""
    assert CORSConfig().allow_credentials is True


def test_restrictive_now_equals_the_default_for_origins() -> None:
    """restrictive() must remain callable and become redundant-but-harmless, not diverge."""
    assert CORSConfig.restrictive().allow_origins == CORSConfig().allow_origins == ()


# ── from_env() ────────────────────────────────────────────────────────────────


def test_from_env_with_origins_unset_yields_empty_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    """from_env() carries its own hardcoded ("*",) fallback — it must be hardened too."""
    monkeypatch.delenv("VARCO_CORS_ORIGINS", raising=False)

    assert CORSConfig.from_env().allow_origins == ()


def test_from_env_still_parses_a_set_origins_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """No regression: an explicit env value must parse exactly as before."""
    monkeypatch.setenv("VARCO_CORS_ORIGINS", "https://app.example.com, http://localhost:3000")

    assert CORSConfig.from_env().allow_origins == (
        "https://app.example.com",
        "http://localhost:3000",
    )


def test_from_env_still_honours_an_explicit_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hardening the *default* must not remove the operator's ability to opt back in."""
    monkeypatch.setenv("VARCO_CORS_ORIGINS", "*")

    assert CORSConfig.from_env().allow_origins == ("*",)


# ── behavioural: a real ASGI app ──────────────────────────────────────────────


async def test_default_config_does_not_reflect_an_unknown_origin() -> None:
    """The actual security consequence: Starlette reflects Origin when credentials are on."""
    app = _app_with_cors(CORSConfig())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ping", headers={"Origin": EVIL_ORIGIN})

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") != EVIL_ORIGIN
    assert response.headers.get("access-control-allow-origin") != "*"


async def test_default_config_rejects_a_preflight_from_an_unknown_origin() -> None:
    """Preflight is the path a browser actually consults before a credentialed cross-origin call."""
    app = _app_with_cors(CORSConfig())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/ping",
            headers={
                "Origin": EVIL_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.headers.get("access-control-allow-origin") != EVIL_ORIGIN
    assert response.headers.get("access-control-allow-origin") != "*"


async def test_explicitly_listed_origin_is_still_allowed() -> None:
    """No regression for the configured-correctly case."""
    app = _app_with_cors(CORSConfig(allow_origins=("https://app.example.com",)))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ping", headers={"Origin": "https://app.example.com"})

    assert response.headers.get("access-control-allow-origin") == "https://app.example.com"


async def test_explicit_wildcard_opt_in_still_works() -> None:
    """The capability must survive the hardening — only the default changes."""
    config = CORSConfig(allow_origins=("*",), allow_credentials=False)
    app = _app_with_cors(config)

    assert config.allow_origins == ("*",)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ping", headers={"Origin": EVIL_ORIGIN})

    assert response.headers.get("access-control-allow-origin") == "*"
