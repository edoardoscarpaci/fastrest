"""
varco_fastapi.middleware.error
==============================
ASGI middleware that catches unhandled exceptions and returns structured JSON.

Maps varco_core ``ServiceException`` subclasses to HTTP status codes using the
existing ``error_message_for()`` / ``error_code_for()`` infrastructure, and
wraps any other exception in a generic 500 response.

Exception → HTTP status mapping::

    ServiceNotFoundError      → 404 Not Found
    ServiceAuthorizationError → 403 Forbidden
    ServiceConflictError      → 409 Conflict
    ServiceValidationError    → 422 Unprocessable Entity
    pydantic.ValidationError  → 422 Unprocessable Entity (request body)
    asyncio.TimeoutError      → 504 Gateway Timeout
    HTTPException             → correct status code as JSON (not re-raised)
    QueryException            → 400 Bad Request
    BaseExceptionGroup        → unwrapped; inner exception dispatched as above
    Exception                 → 500 Internal Server Error

DESIGN: middleware over FastAPI exception_handler
    ✅ Catches errors in OTHER middleware (not just route handlers)
    ✅ Single centralized error formatting via varco_core.exception.http
    ✅ Stack traces are NOT included in responses (avoids info leakage)
    ✅ Correlation ID is included in 5xx error responses for log correlation
    ❌ Must re-raise ``HTTPException`` to let FastAPI handle it natively
    ❌ Ordering matters: must be installed AFTER CORSMiddleware so CORS headers
       are still set on error responses

Thread safety:  ✅ Stateless — safe to share across requests.
Async safety:   ✅ All paths are ``async``; no blocking I/O.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp
from fastapi import HTTPException

from varco_core.exception.service import (
    ServiceAuthorizationError,
    ServiceConflictError,
    ServiceException,
    ServiceNotFoundError,
    ServiceValidationError,
)
from varco_core.exception.query import QueryException
from varco_core.exception.http import error_message_for
from varco_core.tracing import current_correlation_id

_logger = logging.getLogger(__name__)


class ErrorMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that catches unhandled exceptions and returns structured JSON.

    Installed at the outermost layer of the middleware stack so it catches
    errors from all downstream middleware and route handlers.

    Error responses always include:
    - ``code``: stable i18n error code (from varco_core.exception.http)
    - ``message``: human-readable description
    - ``correlation_id``: current request's correlation ID (for log lookup)

    Args:
        app:              The ASGI application to wrap.
        debug:            If ``True``, include exception repr in 5xx responses.
                          Default: ``False``.  NEVER set ``True`` in production.
        include_trace_id: If ``True`` (default), add ``correlation_id`` to
                          error response body for log correlation.

    Thread safety:  ✅ Stateless.
    Async safety:   ✅ ``dispatch`` is ``async def``.

    Edge cases:
        - ``HTTPException`` is converted to a JSON response directly (not
          re-raised) so the correct status code is returned even when the
          exception originates inside another ``BaseHTTPMiddleware``.
        - ``asyncio.CancelledError`` is not caught — it propagates to let
          the ASGI server cancel the request cleanly.
        - ``asyncio.TimeoutError`` is caught and mapped to 504 Gateway Timeout
          with a ``GATEWAY_TIMEOUT`` error code.
        - ``BaseExceptionGroup`` (Python 3.11+, also emitted by anyio/Starlette
          task groups) is unwrapped — the first recognisable inner exception is
          dispatched through the same handlers as if it were raised directly;
          unrecognised groups fall back to 500.
        - Errors in ``send()`` (e.g. client disconnected mid-response) may
          cause a double-error situation — log and swallow the second error.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        debug: bool = False,
        include_trace_id: bool = True,
    ) -> None:
        super().__init__(app)
        self._debug = debug
        self._include_trace_id = include_trace_id

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """
        Process the request, catching all exceptions and mapping them to JSON.

        Args:
            request:   Incoming HTTP request.
            call_next: Next middleware / route handler callable.

        Returns:
            The response from the downstream handler, or a JSON error response.
            ``asyncio.TimeoutError`` → 504 with ``GATEWAY_TIMEOUT`` code.
            ``BaseExceptionGroup`` → unwrapped and dispatched as above.

        Edge cases:
            - ``asyncio.CancelledError`` is re-raised, not caught.
            - ``asyncio.TimeoutError`` anywhere in the middleware/handler chain
              returns 504, not 500.
            - ``BaseExceptionGroup`` containing only unrecognised exceptions
              falls back to 500 via ``_internal_error_response``.
        """
        try:
            return await call_next(request)
        except BaseExceptionGroup as eg:
            # DESIGN: Starlette's BaseHTTPMiddleware wraps exceptions from
            # call_next() in a BaseExceptionGroup under anyio task groups on
            # Python 3.11+. An HTTPException(401) raised inside another
            # middleware arrives here as BaseExceptionGroup("...", [exc]) which
            # the bare `except HTTPException` clause below would never catch.
            # We unwrap and dispatch through the same handlers for consistency.
            #
            # ✅ Correct status code propagated from wrapped middleware exceptions
            # ✅ CancelledError inside a group still propagates cleanly
            # ❌ Only the FIRST recognisable inner exception is handled — exotic
            #    multi-exception groups with heterogeneous inner types fall back to 500
            for inner in eg.exceptions:
                if isinstance(inner, HTTPException):
                    headers = dict(inner.headers) if inner.headers else {}
                    return JSONResponse(
                        status_code=inner.status_code,
                        content={"detail": inner.detail},
                        headers=headers,
                    )
                if isinstance(inner, asyncio.CancelledError):
                    # Preserve task cancellation — do not convert to an HTTP error
                    raise inner
                if isinstance(inner, asyncio.TimeoutError):
                    return JSONResponse(
                        status_code=504,
                        content={
                            "code": "GATEWAY_TIMEOUT",
                            "message": "The request timed out.",
                        },
                    )
                if isinstance(inner, ServiceException):
                    return self._service_error_response(inner)
                if isinstance(inner, QueryException):
                    return JSONResponse(
                        status_code=400,
                        content={
                            "code": "QUERY_ERROR",
                            "message": f"Invalid query: {inner}",
                        },
                    )
            # No recognised inner exception — treat the group itself as a 500
            return self._internal_error_response(eg)
        except HTTPException as exc:
            # Convert HTTPException to a JSON response here rather than re-raising.
            # When HTTPException is raised inside another BaseHTTPMiddleware (e.g.
            # RequestContextMiddleware), it propagates through BaseHTTPMiddleware's
            # stream machinery and exits the middleware stack as an unhandled exception
            # before FastAPI's own exception handlers can intercept it.
            # Returning a proper response here ensures the caller receives the correct
            # status code (e.g. 401 for an invalid Bearer token) instead of a 500.
            # DESIGN: return response over re-raise
            #   ✅ Correct status code reaches the client when auth raises HTTPException
            #      from inside middleware (e.g. JwtBearerAuth inside RequestContextMiddleware).
            #   ✅ WWW-Authenticate and other auth challenge headers are preserved.
            #   ❌ Bypasses FastAPI's built-in HTTPException handler — but that handler
            #      only runs for exceptions from route handlers, not middleware, so the
            #      behaviour for route-handler HTTPExceptions is unchanged.
            headers = dict(exc.headers) if exc.headers else {}
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=headers,
            )
        except asyncio.CancelledError:
            # Task cancellation — propagate immediately; ASGI server handles it.
            raise
        except asyncio.TimeoutError:
            # asyncio.TimeoutError means a downstream await (e.g. a resilience
            # @timeout decorator, or asyncio.wait_for) exceeded its deadline.
            # Return 504 Gateway Timeout — not 500 — so upstream load-balancers
            # and clients can distinguish a timeout from an application bug.
            return JSONResponse(
                status_code=504,
                content={
                    "code": "GATEWAY_TIMEOUT",
                    "message": "The request timed out.",
                },
            )
        except ServiceException as exc:
            return self._service_error_response(exc)
        except QueryException as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "QUERY_ERROR",
                    "message": f"Invalid query: {exc}",
                },
            )
        except Exception as exc:  # noqa: BLE001
            return self._internal_error_response(exc)

    def _service_error_response(self, exc: ServiceException) -> JSONResponse:
        """Map a ``ServiceException`` to the correct HTTP status code and body."""
        try:
            msg = error_message_for(exc)
            status_code = msg.http_status
            body: dict[str, Any] = {
                "code": msg.code,
                "message": msg.message,
            }
        except Exception:  # noqa: BLE001
            # Fallback if error_message_for fails (e.g. unmapped exception type)
            status_code = _FALLBACK_STATUS.get(type(exc), 500)
            body = {"code": "INTERNAL_ERROR", "message": str(exc)}

        if self._include_trace_id:
            cid = current_correlation_id()
            if cid:
                body["correlation_id"] = cid

        return JSONResponse(status_code=status_code, content=body)

    def _internal_error_response(self, exc: Exception) -> JSONResponse:
        """Map an unexpected exception to 500 with a sanitized error body."""
        _logger.error(
            "Unhandled exception in request: %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )

        body: dict[str, Any] = {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected error occurred. Please try again later.",
        }

        if self._debug:
            body["detail"] = f"{type(exc).__name__}: {exc}"

        if self._include_trace_id:
            cid = current_correlation_id()
            if cid:
                body["correlation_id"] = cid

        return JSONResponse(status_code=500, content=body)


# Default HTTP status fallback map — used only when error_message_for() fails
_FALLBACK_STATUS: dict[type[ServiceException], int] = {
    ServiceNotFoundError: 404,
    ServiceAuthorizationError: 403,
    ServiceConflictError: 409,
    ServiceValidationError: 422,
}


__all__ = ["ErrorMiddleware"]
