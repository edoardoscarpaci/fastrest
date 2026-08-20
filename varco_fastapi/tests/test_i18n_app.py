"""
Red-mode tests for Plan 011 Phase 2, step 36 — end-to-end i18n through
create_varco_app.

Plan line (step 36): "?lang=fr renders a DictMessageCatalog message and
sets Content-Language: fr."
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from providify import DIContainer, Provider
from varco_core.exception.service import ServiceNotFoundError
from varco_core.i18n.catalog import DictMessageCatalog
from varco_core.i18n.settings import I18nSettings
from varco_fastapi.app import create_varco_app
from varco_fastapi.router.endpoint import route
from varco_fastapi.router.presets import GenericRouter


class SomeEntity:
    pass


class BoomRouter(GenericRouter):
    _prefix = "/test"

    @route("GET", "/boom")
    async def boom(self):
        raise ServiceNotFoundError(entity_id="1", entity_cls=SomeEntity)


@Provider(singleton=True)
def _fr_catalog() -> DictMessageCatalog:
    # Module-scope @Provider, not container.provide(lambda: ...) — the
    # latter raises ProviderBindingNotDecoratedError per CLAUDE.md's pitfall
    # table ("container.provide(lambda: X())").
    return DictMessageCatalog({"fr": {"varco.error.not_found": "Introuvable"}})


async def test_lang_query_param_end_to_end_sets_content_language_header() -> None:
    container = DIContainer()
    container.provide(_fr_catalog)
    app = create_varco_app(
        container,
        routers=[BoomRouter],
        # AMBIGUITY NOTE: step 35 only says "create_varco_app(i18n=None)
        # registering nothing by default" — the accepted shape (I18nSettings
        # instance vs. dict vs. bool) is not pinned down. Passing an
        # I18nSettings instance here as the most-consistent-with-existing-
        # kwargs (tenancy=, reliability=) guess; reconcile if wrong.
        i18n=I18nSettings(enabled=True, supported_locales=("en", "fr")),
        validate=False,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/test/boom?lang=fr")

    assert response.headers.get("Content-Language") == "fr"
