"""
varco_fastapi.client.config
============================
``ClientConfig`` — a flat, frozen configuration object for ``AsyncVarcoClient``
and ``GenericClient``.

Provides factory methods for common deployment scenarios so callers do not
need to remember which kwargs to pass.

DESIGN: flat frozen dataclass over kwargs sprawl
    ✅ Single object to pass around — easy to store, share, and test
    ✅ frozen=True → safe to share across threads and services
    ✅ Factory methods encode best-practice defaults for dev / prod
    ❌ Slightly more ceremony than passing kwargs directly
    Alternative considered: TypedDict — rejected because it cannot have
    methods or be shared safely (mutable by default).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from varco_fastapi.client.middleware import AbstractClientMiddleware


@dataclass(frozen=True)
class ClientConfig:
    """
    Flat, immutable configuration object for an HTTP client.

    Collects the constructor kwargs used by ``AsyncVarcoClient`` and
    ``GenericClient`` into a single reusable bundle.  Explicit constructor
    kwargs always override the values stored here.

    Args:
        base_url:   Target service base URL (e.g. ``"https://api.example.com"``).
        port:       Optional port to append to ``base_url`` (e.g. ``8080``).
        verify:     TLS verification — ``True`` (default), ``False`` (skip),
                    or a ``str`` path to a CA bundle.
        middleware: Middleware stack applied to every request.
        timeout:    Request timeout in seconds.
        headers:    Static headers added to every request.

    Thread safety:  ✅ frozen=True — safe to share across threads/services.
    Async safety:   ✅ Pure data object — no I/O.

    Edge cases:
        - ``port`` is applied on top of ``base_url`` when both are set.
        - ``headers`` defaults to an empty dict so it is always a valid mapping.
        - ``production()`` sets ``verify=True``; ``development()`` also keeps
          ``verify=True`` to avoid training developers to skip TLS validation.
    """

    base_url: str
    port: int | None = None
    verify: bool | str = True
    # tuple keeps ClientConfig hashable (frozen dataclasses hash their fields)
    middleware: tuple[AbstractClientMiddleware, ...] = ()
    timeout: float = 30.0
    # field(default_factory=…) so each instance gets its own dict object
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def development(
        cls,
        base_url: str = "http://localhost",
        **kwargs: Any,
    ) -> ClientConfig:
        """
        Build a ``ClientConfig`` suitable for local development.

        Uses ``http://localhost`` as the default base URL and a shorter timeout
        to fail fast during iteration.

        Args:
            base_url: Target base URL (default ``"http://localhost"``).
            **kwargs: Any additional ``ClientConfig`` fields to override.

        Returns:
            Frozen ``ClientConfig`` for local development.

        Edge cases:
            - ``kwargs`` override the defaults set here — callers can still
              pass ``timeout=5.0`` to shorten further.
        """
        defaults: dict[str, Any] = {"timeout": 10.0}
        defaults.update(kwargs)
        return cls(base_url=base_url, **defaults)

    @classmethod
    def production(
        cls,
        base_url: str,
        *,
        authority: Any = None,
        **kwargs: Any,
    ) -> ClientConfig:
        """
        Build a ``ClientConfig`` suitable for production deployments.

        Adds ``JwtMiddleware`` when ``authority`` is provided.  ``verify=True``
        ensures TLS certificates are validated.

        Args:
            base_url:  Target service base URL (required for production).
            authority: Optional ``JwtAuthority`` for signing outbound JWTs.
                       ``None`` → no JWT middleware added.
            **kwargs:  Any additional ``ClientConfig`` fields to override.

        Returns:
            Frozen ``ClientConfig`` for production deployments.

        Edge cases:
            - When ``authority`` is not ``None``, ``JwtMiddleware`` is appended
              to any middleware passed via ``kwargs["middleware"]``.
        """
        from varco_fastapi.client.middleware import JwtMiddleware  # noqa: PLC0415

        extra_mw: tuple[AbstractClientMiddleware, ...] = kwargs.pop("middleware", ())
        if authority is not None:
            extra_mw = extra_mw + (JwtMiddleware(authority, audience="api", expires_in=3600),)

        defaults: dict[str, Any] = {
            "verify": True,
            "timeout": 30.0,
            "middleware": extra_mw,
        }
        defaults.update(kwargs)
        return cls(base_url=base_url, **defaults)


__all__ = ["ClientConfig"]
