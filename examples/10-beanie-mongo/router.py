"""
router.py
=========
FastAPI router factory for the ``10-beanie-mongo`` example.

Returns a ``CRUDRouter`` subclass for the ``Post`` entity with all standard
CRUD endpoints pre-wired.  The router is built as a class (not an instance)
because ``create_varco_app`` expects a router *class* to instantiate.

DESIGN: factory function over module-level class
    The ``PostRouter`` class captures ``svc`` from the container at call
    time.  This avoids a module-level ``_service`` assignment that would
    require a global container reference.

    ✅ ``svc`` is resolved once at startup — no per-request DI overhead.
    ✅ ``create_varco_app`` can introspect the class before instantiating it.
    ❌ The function must be called after the container is built.

Thread safety:  ✅ Stateless router class; service is a singleton.
Async safety:   ✅ All handlers are ``async def`` via ``CRUDRouter``.
"""

from __future__ import annotations

from uuid import UUID

from dtos import PostCreate, PostRead, PostUpdate
from models import Post
from service import PostService
from varco_fastapi.router.presets import CRUDRouter


def make_post_router(service: PostService) -> type:
    """
    Build and return the ``PostRouter`` class with ``_service`` wired.

    Args:
        service: Resolved ``PostService`` singleton from the DI container.

    Returns:
        A ``CRUDRouter`` subclass for the Post entity with ``_service`` set.
    """

    class PostRouter(CRUDRouter[Post, UUID, PostCreate, PostRead, PostUpdate]):
        """
        REST router for blog posts backed by MongoDB.

        Standard CRUD endpoints:
            POST   /v1/posts/           → 201 Created + PostRead
            GET    /v1/posts/           → 200 + paged list of PostRead
            GET    /v1/posts/{id}       → 200 + PostRead
            PUT    /v1/posts/{id}       → 200 + PostRead
            PATCH  /v1/posts/{id}       → 200 + PostRead (partial update)
            DELETE /v1/posts/{id}       → 204 No Content

        No authentication — all endpoints are public for this example.
        """

        _prefix = "/v1/posts"
        _tags = ["posts"]
        _service = service

    return PostRouter


__all__ = ["make_post_router"]
