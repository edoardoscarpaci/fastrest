"""
varco_fastapi.webhook.router
===============================
``build_webhook_router`` — the REST admin surface over a
``WebhookSubscriptionRepository`` (Plan 031 / D4d, Step 17-18, §D-D4-admin).

A plain ``APIRouter``, mirroring ``build_dlq_router``/``build_tenant_router``
— a standalone admin surface with hand-written JSON handlers, not a
``VarcoRouter`` generic-CRUD generator (a webhook subscription's admin
surface needs replay-through-``DlqRedriver`` and secret rotation, neither of
which fit generic CRUD).

Tenant scoping: this router reads ``X-Tenant-Id`` directly off the request
rather than the ambient ``current_tenant()`` context — the admin surface
must behave identically whether mounted into a full ``create_varco_app()``
(where ``TenantResolutionMiddleware`` may or may not be installed) or a bare
``FastAPI()`` (RD-9's standalone-admin-surface shape, same reasoning
``build_tenant_router`` documents for its own error translation).

Thread safety:  N/A — stateless route handlers.
Async safety:   ✅ All handlers are ``async def``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Request

if TYPE_CHECKING:
    from varco_core.event.redrive import DlqRedriver
    from varco_core.webhook.base import WebhookSubscriptionRepository

__all__ = ["build_webhook_router"]


def _subscription_to_dict(sub: Any, *, reveal_secrets: bool = False) -> dict[str, Any]:
    """
    Serialize a ``WebhookSubscription`` for an API response.

    ``active_secrets`` is NEVER included unless ``reveal_secrets=True`` —
    only the create/rotate-secret responses set that (the plan's Risks
    table: "Secrets leaked via admin API or logs" — mitigation is "never
    returned by any read endpoint").
    """
    body = {
        "pk": str(sub.pk),
        "tenant_id": sub.tenant_id,
        "target_url": sub.target_url,
        "event_patterns": sub.event_patterns,
        "status": sub.status,
        "consecutive_failures": sub.consecutive_failures,
        "signer": sub.signer,
        "custom_headers": sub.custom_headers,
    }
    if reveal_secrets:
        body["active_secrets"] = sub.active_secrets
    return body


async def _require_role(server_auth: Any, admin_role: str, request: Request) -> None:
    """
    Resolve ``server_auth`` (a plain callable dependency, not necessarily an
    ``AbstractServerAuth`` — see module docstring) and enforce ``admin_role``.

    Raises:
        HTTPException: 403 if the resolved context lacks ``admin_role`` in
            its ``roles`` attribute.
    """
    import inspect

    if inspect.signature(server_auth).parameters:
        ctx = (
            await server_auth(request)
            if inspect.iscoroutinefunction(server_auth)
            else server_auth(request)
        )
    else:
        ctx = await server_auth() if inspect.iscoroutinefunction(server_auth) else server_auth()

    roles = getattr(ctx, "roles", [])
    if admin_role not in roles:
        raise HTTPException(status_code=403, detail=f"{admin_role!r} role required.")


def build_webhook_router(
    repository: WebhookSubscriptionRepository,
    *,
    redriver: DlqRedriver | None = None,
    server_auth: Any | None = None,
    admin_role: str = "webhook-admin",
    prefix: str = "/webhooks",
) -> APIRouter:
    """
    Build the webhook subscription admin ``APIRouter``.

    Args:
        repository:  The ``WebhookSubscriptionRepository`` to administer.
        redriver:    A ``DlqRedriver`` bound to the DLQ webhooks land in on
                     exhaustion. ``None`` (default) — the replay route is
                     not registered at all (same "absent capability, absent
                     route" rule as ``build_dlq_router``).
        server_auth: Optional callable resolving to an object exposing
                     ``.roles`` — enforced via ``admin_role`` on every
                     route when given. ``None`` mounts unauthenticated.
        admin_role:  Role required on every route (when ``server_auth`` is
                     given). Defaults to ``"webhook-admin"``.
        prefix:      URL prefix. Defaults to ``"/webhooks"``.

    Routes:
        GET    {prefix}/subscriptions               list (optionally
                                                      scoped by ``X-Tenant-Id``)
        POST   {prefix}/subscriptions                create — response
                                                      includes secrets ONCE
        GET    {prefix}/subscriptions/{pk}
        PATCH  {prefix}/subscriptions/{pk}/disable
        PATCH  {prefix}/subscriptions/{pk}/enable
        POST   {prefix}/subscriptions/{pk}/rotate-secret  response includes
                                                      the new secret ONCE
        DELETE {prefix}/subscriptions/{pk}
        POST   {prefix}/deliveries/{entry_id}/replay      only when
                                                      ``redriver`` is given
    """
    router = APIRouter(prefix=prefix, tags=["webhook-admin"])

    async def _enforce(request: Request) -> None:
        if server_auth is not None:
            await _require_role(server_auth, admin_role, request)

    @router.get("/subscriptions")
    async def list_subscriptions(request: Request) -> list[dict[str, Any]]:
        await _enforce(request)
        tenant_id = request.headers.get("X-Tenant-Id")
        if tenant_id is not None:
            subs = await repository.find_by_tenant(tenant_id)
        else:
            # No tenant header — cross-tenant listing is an explicit,
            # already-authenticated admin action (never the default path a
            # tenant-scoped caller would hit).
            subs = []
        return [_subscription_to_dict(s) for s in subs]

    @router.post("/subscriptions", status_code=201)
    async def create_subscription(
        request: Request, body: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        await _enforce(request)
        from varco_core.webhook.models import WebhookSubscription

        sub = WebhookSubscription(
            tenant_id=body["tenant_id"],
            target_url=body["target_url"],
            event_patterns=body.get("event_patterns", []),
            active_secrets=body.get("active_secrets") or [f"whsec_{UUID(int=0).hex}"],
            status="ACTIVE",
            consecutive_failures=0,
            signer=body.get("signer", "standard_webhooks"),
            custom_headers=body.get("custom_headers", {}),
        )
        saved = await repository.save(sub)
        return _subscription_to_dict(saved, reveal_secrets=True)

    @router.get("/subscriptions/{pk}")
    async def get_subscription(pk: UUID, request: Request) -> dict[str, Any]:
        await _enforce(request)
        sub = await repository.find_by_id(pk)
        if sub is None:
            raise HTTPException(status_code=404, detail="Webhook subscription not found.")
        return _subscription_to_dict(sub)

    @router.patch("/subscriptions/{pk}/disable")
    async def disable_subscription(pk: UUID, request: Request) -> dict[str, Any]:
        await _enforce(request)
        sub = await repository.find_by_id(pk)
        if sub is None:
            raise HTTPException(status_code=404, detail="Webhook subscription not found.")
        sub.status = "DISABLED"
        saved = await repository.save(sub)
        return _subscription_to_dict(saved)

    @router.patch("/subscriptions/{pk}/enable")
    async def enable_subscription(pk: UUID, request: Request) -> dict[str, Any]:
        await _enforce(request)
        sub = await repository.find_by_id(pk)
        if sub is None:
            raise HTTPException(status_code=404, detail="Webhook subscription not found.")
        sub.status = "ACTIVE"
        sub.consecutive_failures = 0
        saved = await repository.save(sub)
        return _subscription_to_dict(saved)

    @router.post("/subscriptions/{pk}/rotate-secret")
    async def rotate_secret(pk: UUID, request: Request) -> dict[str, Any]:
        await _enforce(request)
        import secrets as _secrets

        sub = await repository.find_by_id(pk)
        if sub is None:
            raise HTTPException(status_code=404, detail="Webhook subscription not found.")
        new_secret = f"whsec_{_secrets.token_urlsafe(32)}"
        # Keep the newest-last convention (§D-D4-signing rotation) — old
        # secret(s) remain active until an operator explicitly prunes them.
        sub.active_secrets = [*sub.active_secrets, new_secret]
        saved = await repository.save(sub)
        return _subscription_to_dict(saved, reveal_secrets=True)

    @router.delete("/subscriptions/{pk}")
    async def delete_subscription(pk: UUID, request: Request) -> dict[str, Any]:
        await _enforce(request)
        await repository.delete(pk)
        return {"deleted": True}

    if redriver is not None:

        @router.post("/deliveries/{entry_id}/replay")
        async def replay_delivery(
            entry_id: UUID, request: Request, dry_run: bool = False
        ) -> dict[str, Any]:
            await _enforce(request)
            from varco_core.event.redrive import DeadLetterNotAddressable

            try:
                outcome = await redriver.redrive(entry_id, dry_run=dry_run)
            except DeadLetterNotAddressable as exc:
                raise HTTPException(status_code=501, detail=str(exc)) from exc
            return {
                "entry_id": str(outcome.entry_id),
                "published": outcome.published,
                "acked": outcome.acked,
                "error": outcome.error,
            }

    return router
