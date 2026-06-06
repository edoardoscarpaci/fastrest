"""
varco_fastapi.client.generic
==============================
``GenericClient`` — a URL-first HTTP client that uses the full varco middleware
pipeline without requiring a ``VarcoRouter`` subclass.

Use this when:
- Calling a third-party API (no varco router exists for it)
- Quickly prototyping against an internal service
- Building an ``OpenAPIClient`` (which delegates to ``GenericClient``)

DESIGN: subclass of AsyncVarcoClient with no type parameter
    ✅ Inherits the full middleware pipeline, context manager, and .sync wrapper
    ✅ _VarcoClientMeta skips CRUD injection because no [R] type param is bound
    ✅ Simple constructor — pass a URL, get an HTTP client
    ❌ No generated CRUD methods; callers use get/post/put/patch/delete directly
    Alternative considered: standalone class — rejected because it would duplicate
    the entire middleware pipeline instead of reusing it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar


from varco_fastapi.client.base import AsyncVarcoClient, ClientProfile

if TYPE_CHECKING:
    from varco_fastapi.client.config import ClientConfig
    from varco_fastapi.client.middleware import AbstractClientMiddleware

T = TypeVar("T")


class GenericClient(AsyncVarcoClient):
    """
    URL-first HTTP client built on the varco middleware pipeline.

    Unlike ``AsyncVarcoClient[R]``, this class requires no ``VarcoRouter`` —
    it is suitable for calling any HTTP API.  All middleware, resilience, and
    TLS configuration inherited from ``AsyncVarcoClient`` applies here.

    Provides explicit HTTP verb methods (``get``, ``post``, ``put``, ``patch``,
    ``delete``) as thin wrappers over ``_request()``.

    Usage::

        async with GenericClient("https://api.example.com", port=443) as client:
            data = await client.get("/users", params={"active": "true"})
            result = await client.post("/users", body={"name": "Alice"})

    Args:
        base_url:   Target service base URL.
        port:       Optional port appended to ``base_url`` (e.g. ``8080``).
        verify:     TLS verification — ``True`` (default), ``False``, or CA bundle path.
        middleware: Middleware stack applied to every request.
        timeout:    Request timeout in seconds.
        headers:    Static headers merged into every request via ``HeadersMiddleware``.
        profile:    ``ClientProfile`` bundle — overrides ``middleware`` / ``timeout``
                    when provided.
        config:     ``ClientConfig`` bundle; explicit kwargs override config fields.

    Thread safety:  ✅ Each instance owns its own ``httpx.AsyncClient``.
    Async safety:   ✅ Concurrent requests within one context manager are safe.

    Edge cases:
        - ``headers`` is applied via a ``HeadersMiddleware`` prepended to ``middleware``.
        - ``profile`` and ``middleware`` are additive: passing both appends middleware
          on top of the profile stack.
        - Context manager and ``.sync`` property are inherited unchanged.
    """

    def __init__(
        self,
        base_url: str,
        *,
        port: int | None = None,
        verify: bool | str = True,
        middleware: tuple[AbstractClientMiddleware, ...] = (),
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        profile: ClientProfile | None = None,
        config: ClientConfig | None = None,
    ) -> None:
        """
        Args:
            base_url:   Target service base URL (required; no configurator fallback).
            port:       Optional port appended to ``base_url``.
            verify:     TLS verification flag or CA bundle path.
            middleware: Middleware instances applied left-to-right.
            timeout:    Request timeout in seconds.
            headers:    Static headers added to every request via ``HeadersMiddleware``.
            profile:    Optional ``ClientProfile`` bundle.
            config:     Optional ``ClientConfig`` bundle.

        Edge cases:
            - ``headers`` is ignored when it is ``None`` or empty — no
              ``HeadersMiddleware`` is added in that case to avoid overhead.
        """
        # Prepend HeadersMiddleware when static headers are requested.
        # Prepend (not append) so custom headers arrive early in the pipeline,
        # allowing downstream middleware to see or override them.
        effective_mw: tuple[AbstractClientMiddleware, ...] = middleware
        if headers:
            from varco_fastapi.client.middleware import (
                HeadersMiddleware,
            )  # noqa: PLC0415

            effective_mw = (HeadersMiddleware(headers),) + middleware

        super().__init__(
            base_url,
            port=port,
            verify=verify,
            config=config,
            middleware=effective_mw if effective_mw else None,
            timeout=timeout,
            profile=profile,
        )

    # ── HTTP verb methods ────────────────────────────────────────────────────

    async def get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        response_model: type[T] | None = None,
        expected_status: int = 200,
    ) -> T | dict[str, Any] | None:
        """
        Execute a GET request.

        Args:
            path:            URL path (e.g. ``"/users"`` or ``"/users/42"``).
            params:          Query string parameters.
            response_model:  Pydantic model class for response deserialization.
                             ``None`` → returns raw ``dict``.
            expected_status: Expected HTTP status code (default 200).

        Returns:
            Deserialized response or raw ``dict`` when ``response_model`` is ``None``.

        Raises:
            httpx.HTTPStatusError: Response status did not match ``expected_status``.
        """
        return await self._request(
            "GET",
            path,
            query_params=params or {},
            response_model=response_model,
            expected_status=expected_status,
        )

    async def post(
        self,
        path: str,
        body: Any = None,
        *,
        params: dict[str, str] | None = None,
        response_model: type[T] | None = None,
        expected_status: int = 201,
    ) -> T | dict[str, Any] | None:
        """
        Execute a POST request.

        Args:
            path:            URL path.
            body:            Request body — Pydantic model or plain dict.
            params:          Query string parameters.
            response_model:  Pydantic model class for response deserialization.
            expected_status: Expected HTTP status code (default 201).

        Returns:
            Deserialized response or raw ``dict``.

        Raises:
            httpx.HTTPStatusError: Response status mismatch.
        """
        return await self._request(
            "POST",
            path,
            body=body,
            query_params=params or {},
            response_model=response_model,
            expected_status=expected_status,
        )

    async def put(
        self,
        path: str,
        body: Any = None,
        *,
        params: dict[str, str] | None = None,
        response_model: type[T] | None = None,
        expected_status: int = 200,
    ) -> T | dict[str, Any] | None:
        """
        Execute a PUT request.

        Args:
            path:            URL path.
            body:            Request body — Pydantic model or plain dict.
            params:          Query string parameters.
            response_model:  Pydantic model class for response deserialization.
            expected_status: Expected HTTP status code (default 200).

        Returns:
            Deserialized response or raw ``dict``.

        Raises:
            httpx.HTTPStatusError: Response status mismatch.
        """
        return await self._request(
            "PUT",
            path,
            body=body,
            query_params=params or {},
            response_model=response_model,
            expected_status=expected_status,
        )

    async def patch(
        self,
        path: str,
        body: Any = None,
        *,
        params: dict[str, str] | None = None,
        response_model: type[T] | None = None,
        expected_status: int = 200,
    ) -> T | dict[str, Any] | None:
        """
        Execute a PATCH request.

        Args:
            path:            URL path.
            body:            Request body — Pydantic model or plain dict.
            params:          Query string parameters.
            response_model:  Pydantic model class for response deserialization.
            expected_status: Expected HTTP status code (default 200).

        Returns:
            Deserialized response or raw ``dict``.

        Raises:
            httpx.HTTPStatusError: Response status mismatch.
        """
        return await self._request(
            "PATCH",
            path,
            body=body,
            query_params=params or {},
            response_model=response_model,
            expected_status=expected_status,
        )

    async def delete(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        expected_status: int = 204,
    ) -> None:
        """
        Execute a DELETE request.

        Args:
            path:            URL path.
            params:          Query string parameters.
            expected_status: Expected HTTP status code (default 204).

        Returns:
            ``None`` — DELETE responses have no body by convention.

        Raises:
            httpx.HTTPStatusError: Response status mismatch.
        """
        await self._request(
            "DELETE",
            path,
            query_params=params or {},
            response_model=None,
            expected_status=expected_status,
        )

    def __repr__(self) -> str:
        """Return a concise debug representation."""
        return f"GenericClient(url={self._base_url!r}, verify={self._verify!r})"


__all__ = ["GenericClient"]
