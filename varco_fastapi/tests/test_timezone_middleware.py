"""
Red-mode tests for Plan 011 Phase 3, step 43 — timezone half of
LocalizationMiddleware.

Plan line (step 43): "?tz=/X-Timezone/JWT-zoneinfo end-to-end; both toggles
off -> the middleware is genuinely absent from app.user_middleware;
locale-on / timezone-off resolves only the locale and leaves timezone=None
in the same merged RequestContext (the D-6 merge test at the HTTP layer)."
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from varco_core.i18n.settings import I18nSettings
from varco_core.tz.settings import TimezoneSettings
from varco_fastapi.middleware.localization import LocalizationMiddleware


def make_app(*, i18n_settings, timezone_settings) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        LocalizationMiddleware,
        i18n_settings=i18n_settings,
        timezone_settings=timezone_settings,
    )

    @app.get("/whoami")
    async def whoami(request: Request):
        from varco_core.context.request import current_locale, current_timezone

        return {
            "locale": current_locale(),
            "timezone": current_timezone().key if current_timezone() else None,
        }

    return app


async def test_query_param_tz_resolves_end_to_end() -> None:
    app = make_app(
        i18n_settings=I18nSettings(enabled=False),
        timezone_settings=TimezoneSettings(enabled=True),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/whoami?tz=America/New_York")
    assert response.json()["timezone"] == "America/New_York"


async def test_header_tz_resolves_end_to_end() -> None:
    app = make_app(
        i18n_settings=I18nSettings(enabled=False),
        timezone_settings=TimezoneSettings(enabled=True),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/whoami", headers={"X-Timezone": "Europe/Paris"})
    assert response.json()["timezone"] == "Europe/Paris"


async def test_locale_on_timezone_off_leaves_timezone_none_in_merged_context() -> None:
    app = make_app(
        i18n_settings=I18nSettings(enabled=True, supported_locales=("en", "fr")),
        timezone_settings=TimezoneSettings(enabled=False),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/whoami?lang=fr")
    body = response.json()
    assert body["locale"] == "fr"
    assert body["timezone"] is None
