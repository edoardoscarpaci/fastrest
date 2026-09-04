"""
varco_core.tls.clients
========================

Four thin adapters converting a ``TrustStore``/``ReloadingTrustStore`` into the shape each
mainstream Python HTTP client wants (Plan 027 / T4b, §D-T4-adapters). **No hard dependency on
any of the four libraries** — every import below is inside a function body, never at module
scope (locked, ``BACKLOG.md:37``; precedent ``varco_fastapi/varco_fastapi/connection.py:333``).
``varco_core/tests/test_tls_no_hard_client_deps.py`` is the structural guard that keeps this
true.

DESIGN: function-body imports, no ``to_*_client()`` factories (§D-T4-adapters)
    ✅ An adapter for an uninstalled library raises a clear ``ImportError`` **when called**,
       and costs nothing at import time — which matters directly for Plan 028 / P1.
    ✅ The error is translated to ``MissingClientDependencyError(ImportError)`` naming the
       pip package, mirroring Plan 025's ``MissingWatchDependencyError``
       (``varco_core.watch.base``) — one consistent shape for "you asked for an optional
       integration you have not installed".
    ✅ Returning a *connector/adapter/context*, not a configured ``Client``/``Session``, keeps
       varco out of the business of every client's timeout/retry/proxy options — the caller
       composes.
    ❌ ``to_requests_adapter()`` must define an ``HTTPAdapter`` subclass — that is the only
       supported way to inject a context (brief 001 §4). It is defined **inside the function
       body**, after the import, so the module has no import-time dependency on ``requests``.
       Slightly unusual; commented at the definition site.
    ❌ ``requests`` is sync-only and this is an async framework. Included anyway because the
       BACKLOG names it and because sync scripts/CLIs around a varco service are exactly the
       PKCS#12/private-PKI audience this cycle targets.

Version evidence (brief 001 §4), one row per adapter:

| Adapter | Target API | Version floor and why |
|---|---|---|
| ``to_httpx_verify()`` | ``httpx.Client(verify=ctx)`` | httpx 0.28+: ``verify=`` accepts an ``ssl.SSLContext`` directly; ``cert=`` is **deprecated** as of 0.28 in favour of building the context yourself — do not re-add a ``cert=`` code path here. |
| ``to_aiohttp_connector()`` | ``aiohttp.ClientSession(connector=...)`` | aiohttp 3.14+: ``TCPConnector(ssl=ctx)``; mutually exclusive with the older ``verify_ssl=``/``fingerprint=`` kwargs, which this adapter never sets. |
| ``to_urllib3_poolmanager()`` | ``urllib3.PoolManager(...)`` | urllib3 v2.x only — v1.26 is EOL and its ``ssl_context=`` support differs. |
| ``to_requests_adapter()`` | ``session.mount("https://", adapter)`` | requests 2.32.3+ — the release that fixed a bug where a custom ``ssl.SSLContext`` passed through a custom ``HTTPAdapter`` subclass was silently ignored. |

**Reload interaction — read at call time, always.** All four accept a ``TrustStore`` *or* a
``ReloadingTrustStore`` and read ``.context``/call ``build_ssl_context()`` **at the moment the
adapter function runs**, never caching it:

- Under ``ReloadStrategy.MUTATE`` (Plan 026 / §D-T3-reload), a client built from these
  adapters keeps working across a rotation with **no action** — the ``ssl.SSLContext`` object
  it holds is the exact object ``ReloadingTrustStore`` mutates in place.
- Under ``SWAP``, an already-built client/connector/adapter holds the **old** context forever
  — rebuilding is the caller's job. ``ReloadingTrustStore.subscribe(cb)`` is the hook: rebuild
  the client in the callback. varco does not rebuild pooled clients on the caller's behalf —
  it does not own them, and closing one out from under in-flight requests is not a library's
  decision to make.
- Brief 001 §2's underlying constraint applies regardless of which of the above: an
  **already-established** TLS connection never sees a rotation; only a *new* handshake does.

Thread safety:  ✅ Every function below is a pure conversion with no shared state of its own.
Async safety:   ⚠️ ``to_aiohttp_connector()`` is itself ``async def`` — aiohttp's
                   ``TCPConnector`` wants to bind to the running event loop at construction,
                   so build it from inside the loop that will use it (see its own docstring).
"""

from __future__ import annotations

import ssl  # stdlib — not one of the four watched libraries, safe at module scope
from typing import Any

# TrustStore/ReloadingTrustStore are varco_core's own internal modules — not one of the four
# watched libraries the structural guard (test_tls_no_hard_client_deps.py) polices, and
# neither store.py nor reload.py imports this module back, so this is not a cycle. A real,
# module-scope import (rather than a TYPE_CHECKING-only one) is what lets _resolve_context()
# below do a plain isinstance() dispatch instead of a duck-typed getattr() mypy cannot narrow.
from varco_core.tls.reload import ReloadingTrustStore
from varco_core.tls.store import TrustStore

type _AnyStore = TrustStore | ReloadingTrustStore


class MissingClientDependencyError(ImportError):
    """
    Raised when an adapter is called for an HTTP client that is not installed.

    Mirrors ``varco_core.watch.base.MissingWatchDependencyError`` — one shape for "you asked
    for an optional integration you have not installed". Always names the ``pip install``
    package, never just the import name that failed.
    """


def _resolve_context(store: _AnyStore) -> ssl.SSLContext:
    """
    Resolve ``store`` to a live ``ssl.SSLContext``, reading fresh every call (see module
    docstring's "Reload interaction" section) — never cached by any adapter.

    Args:
        store: A ``TrustStore`` (built fresh each call) or a ``ReloadingTrustStore`` (whose
            ``.context`` is read as-is, live).

    Returns:
        An ``ssl.SSLContext``.

    Raises:
        ResourceNotLoadedError: ``store`` is a ``ReloadingTrustStore`` whose ``start()`` has
            not been called yet — propagates unchanged from ``ReloadingTrustStore.context``.
    """
    # A plain TrustStore has no `.context` property (it is a spec, not a live object) — build
    # a fresh ssl.SSLContext from it every call, the same "call at the moment you need it"
    # discipline a ReloadingTrustStore gives for free via its own `.context` property. Duck-
    # typed on purpose (an attribute check, not isinstance(store, ReloadingTrustStore)) so a
    # test double — or any future object exposing the same `.context` shape — works without
    # inheriting from ReloadingTrustStore itself.
    context: ssl.SSLContext | None = getattr(store, "context", None)
    if context is not None:
        return context
    return store.build_ssl_context()  # type: ignore[union-attr]


# ── httpx ─────────────────────────────────────────────────────────────────


def to_httpx_verify(store: _AnyStore) -> ssl.SSLContext:
    """
    Convert ``store`` into the ``ssl.SSLContext`` httpx's ``verify=`` parameter accepts.

    Args:
        store: A ``TrustStore`` or ``ReloadingTrustStore``.

    Returns:
        An ``ssl.SSLContext``, suitable for ``httpx.Client(verify=...)`` /
        ``httpx.AsyncClient(verify=...)``.

    Raises:
        MissingClientDependencyError: ``httpx`` is not installed. Install with
            ``pip install httpx``.

    Edge cases:
        - httpx 0.28+ is required — earlier versions' ``verify=`` did not accept an
          ``ssl.SSLContext`` directly, and used the now-deprecated ``cert=`` parameter for
          client identities instead. Do not resurrect a ``cert=`` code path here; the context
          returned by this function already carries the client identity if the store has one.
        - Reload: if ``store`` is a ``ReloadingTrustStore``, the returned context reflects
          whatever ``.context`` currently is — call this function again after a ``SWAP`` to
          get the new one (see module docstring's "Reload interaction").

    Example::

        import httpx
        from varco_core.tls.clients import to_httpx_verify

        client = httpx.Client(verify=to_httpx_verify(store))
    """
    try:
        import httpx  # noqa: PLC0415 — see module docstring: function-body import, locked
    except ImportError as exc:
        raise MissingClientDependencyError(
            "varco_core.tls.clients.to_httpx_verify requires httpx. "
            "Install it with: pip install httpx"
        ) from exc

    del httpx  # only imported to prove/require presence — httpx.Client(verify=ctx) needs no
    # further help from this module beyond a plain ssl.SSLContext.
    return _resolve_context(store)


# ── aiohttp ───────────────────────────────────────────────────────────────


async def to_aiohttp_connector(store: _AnyStore, **kwargs: Any) -> Any:
    # Return type is aiohttp.TCPConnector (see Returns: below) — left as Any because aiohttp
    # is never imported at module scope (locked, see module docstring) and ruff/mypy cannot
    # resolve a forward-referenced name to an unimported module even when quoted.
    """
    Build an ``aiohttp.TCPConnector`` bound to ``store``'s trust configuration.

    Args:
        store: A ``TrustStore`` or ``ReloadingTrustStore``.
        **kwargs: Forwarded verbatim to ``aiohttp.TCPConnector(...)`` (pool size, DNS cache,
            etc.) — never ``ssl=``, which this function always supplies itself.

    Returns:
        An ``aiohttp.TCPConnector`` — pass it to ``aiohttp.ClientSession(connector=...)``.

    Raises:
        MissingClientDependencyError: ``aiohttp`` is not installed. Install with
            ``pip install aiohttp``.
        TypeError: ``ssl=`` was passed in ``kwargs`` — this function always sets it.

    Edge cases:
        - **Must be awaited from inside the event loop that will use the connector** —
          ``aiohttp.TCPConnector`` historically binds to the running loop at construction
          time; building it outside a loop (or in a different loop than the session that
          will use it) is an aiohttp-level footgun, not something this adapter can paper over.
        - aiohttp 3.14+ is required for ``TCPConnector(ssl=ctx)``; older releases used
          ``ssl_context=`` under a different mutual-exclusion contract with ``verify_ssl=``/
          ``fingerprint=`` (this adapter never sets either).
        - Reload: the connector, once built, keeps the context object it was given — under
          ``ReloadStrategy.MUTATE`` that is fine (same object, mutated in place); under
          ``SWAP`` the connector must be rebuilt (see module docstring).

    Example::

        import aiohttp
        from varco_core.tls.clients import to_aiohttp_connector

        connector = await to_aiohttp_connector(store)
        async with aiohttp.ClientSession(connector=connector) as session:
            ...
    """
    try:
        import aiohttp  # noqa: PLC0415 — see module docstring
    except ImportError as exc:
        raise MissingClientDependencyError(
            "varco_core.tls.clients.to_aiohttp_connector requires aiohttp. "
            "Install it with: pip install aiohttp"
        ) from exc

    if "ssl" in kwargs:
        raise TypeError(
            "to_aiohttp_connector() always sets ssl= from the store's context — "
            "do not pass ssl= yourself."
        )
    return aiohttp.TCPConnector(ssl=_resolve_context(store), **kwargs)


# ── urllib3 ───────────────────────────────────────────────────────────────


def to_urllib3_poolmanager(store: _AnyStore, **kwargs: Any) -> Any:
    # Return type is urllib3.PoolManager (see Returns: below) — Any for the same reason as
    # to_aiohttp_connector() above.
    """
    Build a ``urllib3.PoolManager`` bound to ``store``'s trust configuration.

    Args:
        store: A ``TrustStore`` or ``ReloadingTrustStore``.
        **kwargs: Forwarded verbatim to ``urllib3.PoolManager(...)`` — never ``ssl_context=``,
            which this function always supplies itself.

    Returns:
        A ``urllib3.PoolManager``.

    Raises:
        MissingClientDependencyError: ``urllib3`` is not installed. Install with
            ``pip install urllib3``.
        TypeError: ``ssl_context=`` was passed in ``kwargs``.

    Edge cases:
        - urllib3 v2.x only — v1.26 (EOL) has a different ``ssl_context=`` support story.
        - Reload: the pool manager keeps the context object it was built with — same MUTATE
          (transparent) vs. SWAP (rebuild required) split as every other adapter here.

    Example::

        from varco_core.tls.clients import to_urllib3_poolmanager

        pool = to_urllib3_poolmanager(store)
        resp = pool.request("GET", "https://example.com")
    """
    try:
        import urllib3  # noqa: PLC0415 — see module docstring
    except ImportError as exc:
        raise MissingClientDependencyError(
            "varco_core.tls.clients.to_urllib3_poolmanager requires urllib3. "
            "Install it with: pip install urllib3"
        ) from exc

    if "ssl_context" in kwargs:
        raise TypeError(
            "to_urllib3_poolmanager() always sets ssl_context= from the store's context — "
            "do not pass ssl_context= yourself."
        )
    return urllib3.PoolManager(ssl_context=_resolve_context(store), **kwargs)


# ── requests ──────────────────────────────────────────────────────────────


def to_requests_adapter(store: _AnyStore) -> Any:
    # Return type is requests.adapters.HTTPAdapter (see Returns: below) — Any for the same
    # reason as to_aiohttp_connector() above.
    """
    Build a ``requests.adapters.HTTPAdapter`` subclass instance bound to ``store``'s trust
    configuration, ready for ``session.mount("https://", adapter)``.

    Args:
        store: A ``TrustStore`` or ``ReloadingTrustStore``.

    Returns:
        An ``HTTPAdapter`` subclass instance carrying the resolved ``ssl.SSLContext``.

    Raises:
        MissingClientDependencyError: ``requests`` is not installed. Install with
            ``pip install requests``.

    Edge cases:
        - requests 2.32.3+ is required — the release that fixed a bug where a custom
          ``ssl.SSLContext`` passed through a custom ``HTTPAdapter`` subclass's
          ``init_poolmanager``/``cert_verify`` override was silently ignored by urllib3's
          pool-manager plumbing underneath. Earlier versions may appear to work and then
          silently fall back to the system trust store.
        - ``store.verify=False`` (an all-trust context) does **not** make requests emit its
          own ``InsecureRequestWarning`` — that warning only fires when ``verify=False`` is
          passed to the *session*/*request* itself, not derived from the context's own
          ``verify_mode``. A missing warning here is not evidence of safety.
        - Reload: the adapter instance keeps the context object resolved at construction
          time — same MUTATE-transparent/SWAP-needs-rebuild split as the other three.

    Example::

        import requests
        from varco_core.tls.clients import to_requests_adapter

        session = requests.Session()
        session.mount("https://", to_requests_adapter(store))
    """
    try:
        import requests  # noqa: PLC0415 — see module docstring
        from requests.adapters import HTTPAdapter
    except ImportError as exc:
        raise MissingClientDependencyError(
            "varco_core.tls.clients.to_requests_adapter requires requests. "
            "Install it with: pip install requests"
        ) from exc

    del requests  # only imported to prove/require presence
    context = _resolve_context(store)

    # DESIGN: the HTTPAdapter subclass is defined *inside* this function body, after the
    # import, so the module carries no import-time dependency on requests (§D-T4-adapters ❌).
    # This is requests' only supported mechanism for injecting a custom ssl.SSLContext — there
    # is no `HTTPAdapter(ssl_context=...)` constructor kwarg.
    class _TrustStoreHTTPAdapter(HTTPAdapter):  # type: ignore[no-any-unimported,misc]
        def __init__(self, ssl_context: ssl.SSLContext, *args: Any, **kwargs: Any) -> None:
            self._varco_ssl_context = ssl_context
            super().__init__(*args, **kwargs)

        def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
            kwargs["ssl_context"] = self._varco_ssl_context
            super().init_poolmanager(*args, **kwargs)

        def proxy_manager_for(self, *args: Any, **kwargs: Any) -> Any:
            kwargs["ssl_context"] = self._varco_ssl_context
            return super().proxy_manager_for(*args, **kwargs)

    return _TrustStoreHTTPAdapter(context)


__all__ = [
    "MissingClientDependencyError",
    "to_aiohttp_connector",
    "to_httpx_verify",
    "to_requests_adapter",
    "to_urllib3_poolmanager",
]
