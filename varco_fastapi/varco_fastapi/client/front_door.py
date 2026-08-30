"""
varco_fastapi.client.front_door
==================================
``client_for`` / ``client_class_for`` — THE documented way to call another
varco service (Plan 009, Phase 3 / C1).

Collapses the pre-Phase-3 surface (``make_client``, ``GenericClient``,
``OpenAPIClient``, ``ClientConfigurator``, ``generate_client`` — still
available, just demoted to ``varco_fastapi.client.advanced``) down to one
function that returns a live, ready-to-call client instance.

DESIGN: one function returning a live instance, not a class
    ✅ The complaint this phase answers is "very complex to use" — the 90%
       call site wants an object it can immediately call ``await
       client.list()`` on.
    ✅ ``client_class_for`` is still available for the ``Inject[VarcoClient[R]]``
       DI-binding case (``bind_clients_from``).
    ❌ Two functions instead of one — the second is documented as the DI-only
       entry point, not the default recommendation.

Thread safety:  ✅ The memo dict is a module-level ``dict[type, type]`` — a
                   racing double-build recomputes an equivalent class at
                   worst (idempotent); no lock is used deliberately (module-
                   level ``asyncio.Lock`` would violate the "create lazily
                   inside a running loop" rule for no benefit here — class
                   construction is synchronous and side-effect-free).
Async safety:   ✅ Both functions are synchronous; the returned client's own
                   async methods are unaffected.
"""

from __future__ import annotations

from typing import Any

from varco_fastapi.client.base import AsyncVarcoClient, ClientProfile, _VarcoClientMeta

# Memoizes router_cls -> generated AsyncVarcoClient subclass so repeated
# client_for(SameRouter, ...) calls do not re-run the metaclass.
_client_class_memo: dict[type, type[AsyncVarcoClient[Any]]] = {}


def client_class_for(router_cls: type) -> type[AsyncVarcoClient[Any]]:
    """
    Return (and memoize) the generated client CLASS for ``router_cls``.

    Intended for the DI-binding case (``bind_clients_from`` /
    ``Inject[VarcoClient[R]]``) where a *class*, not an instance, is needed.
    Most call sites want ``client_for()`` instead.

    Args:
        router_cls: A ``VarcoRouter`` subclass.

    Returns:
        A memoized ``AsyncVarcoClient`` subclass parameterized on
        ``router_cls`` — repeated calls for the same router return the
        identical class object.

    Raises:
        TypeError: ``router_cls`` is not a ``VarcoRouter`` subclass.
    """
    from varco_fastapi.router.base import VarcoRouter

    if not (isinstance(router_cls, type) and issubclass(router_cls, VarcoRouter)):
        raise TypeError(
            f"client_class_for() requires a VarcoRouter subclass, got {router_cls!r}. "
            "Pass the router CLASS (e.g. OrderRouter), not an instance."
        )

    cached = _client_class_memo.get(router_cls)
    if cached is not None:
        return cached

    cls = _VarcoClientMeta(
        f"{router_cls.__name__}Client",
        (AsyncVarcoClient,),
        {"__orig_bases__": (AsyncVarcoClient[router_cls],)},  # type: ignore[valid-type]
    )
    _client_class_memo[router_cls] = cls
    return cls


def client_for(
    router_cls: type,
    base_url: str | None = None,
    *,
    profile: ClientProfile | None = None,
    timeout: float | None = None,
    verify: bool | str = True,
    middleware: tuple[Any, ...] | None = None,
    headers: Any = None,
) -> AsyncVarcoClient[Any]:
    """
    THE documented way to get a client for a varco service.

    Args:
        router_cls: The peer service's ``VarcoRouter`` subclass.
        base_url:   Target service base URL. ``None`` defers resolution to
                    first request (see ``AsyncVarcoClient.__init__`` — the
                    error at that point names both this parameter and, once
                    Phase 11 lands, ``VARCO_PEER_<NAME>_URL``).
        profile:    ``ClientProfile`` bundle (middleware/TLS/timeout).
        timeout:    Request timeout in seconds.
        verify:     TLS verification flag or CA bundle path.
        middleware: Extra middleware stack applied to every request.
        headers:    Static headers merged into every request.

    Returns:
        A ready-to-call ``AsyncVarcoClient`` instance with typed CRUD +
        custom-route methods derived from ``router_cls``.

    Raises:
        TypeError: ``router_cls`` is not a ``VarcoRouter`` subclass.

    Edge cases:
        - Concurrent ``client_for(SameRouter, ...)`` calls are safe — the
          underlying class memo is idempotent under CPython's GIL.
    """
    cls = client_class_for(router_cls)
    kwargs: dict[str, Any] = {"verify": verify, "profile": profile}
    if timeout is not None:
        kwargs["timeout"] = timeout

    mw: tuple[Any, ...] = middleware or ()
    if headers:
        from varco_fastapi.client.middleware import HeadersMiddleware

        mw = (*mw, HeadersMiddleware(dict(headers)))
    if mw:
        kwargs["middleware"] = mw

    instance = cls(base_url, **kwargs)
    # DESIGN: eagerly build the httpx.AsyncClient rather than deferring to
    # __aenter__/first-request lazy creation (AsyncVarcoClient's own default).
    #   ✅ "Ready-to-call instance" is the whole promise of client_for() — no
    #      `async with` ceremony required for the common one-off-call case.
    #   ✅ One persistent connection pool reused across every call on this
    #      instance, instead of a fresh one per call outside a context
    #      manager (AsyncVarcoClient._call_httpx's own documented fallback).
    #   ❌ Callers that never call `.aclose()`/use `async with` leak the pool
    #      until GC — same caveat as any long-lived httpx.AsyncClient.
    instance._client = instance._build_httpx_client()
    return instance


__all__ = ["client_class_for", "client_for"]
