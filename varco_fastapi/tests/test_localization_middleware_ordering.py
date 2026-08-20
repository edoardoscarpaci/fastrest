"""
Red-mode tests for Plan 011 Phase 2, step 34 — RD-3's named ordering hazard
for LocalizationMiddleware.

Plan line (step 33): "one LocalizationMiddleware resolving locale and/or
timezone in a single pass, setting one merged RequestContext token,
resetting in finally, AND mirroring the resolved context onto
request.state so ErrorMiddleware (which is outside it) can still read it.
Each half independently gated ... with both off the middleware is not
added at all."
Plan line (step 34): "An exception raised in a handler is rendered with the
resolved locale even though the ContextVar token was already reset by the
time ErrorMiddleware formats the body (the request.state path); a 404 from
the router ... is localized identically; the middleware sits AFTER
TenantResolutionMiddleware in request order."
"""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from varco_core.exception.service import ServiceNotFoundError
from varco_core.i18n.settings import I18nSettings
from varco_fastapi.middleware.error import ErrorMiddleware
from varco_fastapi.middleware.localization import LocalizationMiddleware


class SomeEntity:
    pass


def make_app(*, i18n_enabled: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ErrorMiddleware)
    app.add_middleware(
        LocalizationMiddleware,
        i18n_settings=I18nSettings(
            enabled=i18n_enabled, supported_locales=("en", "fr")
        ),
    )

    @app.get("/boom")
    async def boom():
        raise ServiceNotFoundError(entity_id="1", entity_cls=SomeEntity)

    return app


async def test_exception_in_handler_is_localized_via_request_state_mirror() -> None:
    app = make_app(i18n_enabled=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom?lang=fr")
    assert response.headers.get("Content-Language") == "fr"


async def test_router_level_404_is_localized_identically() -> None:
    app = make_app(i18n_enabled=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/does-not-exist?lang=fr")
    assert response.status_code == 404
    assert response.headers.get("Content-Language") == "fr"


async def test_middleware_after_tenant_resolution_sees_current_tenant() -> None:
    # RD-3: "inserted AFTER TenantResolutionMiddleware in request order,
    # because the tenant-default step of both precedence chains needs
    # current_tenant() to be populated."
    from varco_fastapi.middleware.tenant_resolution import TenantResolutionMiddleware

    # AMBIGUITY NOTE: Starlette's app.user_middleware ordering/index
    # semantics relative to add_middleware() call order are an
    # implementation detail this test infers from RD-3's prose ("after
    # TenantResolutionMiddleware"), not from a pinned-down index
    # convention. If this assertion's polarity is backwards once the
    # real create_varco_app wiring lands, trust CLAUDE.md's stated
    # request order over this test and flip the comparison.

    app = FastAPI()
    app.add_middleware(ErrorMiddleware)
    app.add_middleware(
        LocalizationMiddleware,
        i18n_settings=I18nSettings(enabled=True, supported_locales=("en", "fr")),
    )
    app.add_middleware(TenantResolutionMiddleware)

    middleware_classes = [m.cls for m in app.user_middleware]
    # Starlette's app.user_middleware is outermost-first (last add_middleware()
    # call wins the outermost/dispatches-first position). CLAUDE.md's documented
    # request order is "... -> [TenantResolution] -> [Localization] -> handler",
    # i.e. TenantResolution dispatches BEFORE Localization, i.e. TenantResolution
    # must have a SMALLER index (be more outer) than Localization so that
    # current_tenant() is populated by the time Localization's tenant-default
    # precedence step runs. Flipped per this test's own escape hatch: "trust
    # CLAUDE.md's stated request order over this test and flip the comparison."
    assert middleware_classes.index(
        TenantResolutionMiddleware
    ) < middleware_classes.index(LocalizationMiddleware)
