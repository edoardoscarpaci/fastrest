"""
varco_fastapi.router.a2a.jsonrpc
===================================
JSON-RPC 2.0 envelope + dispatch for the A2A v1.0.0 transport.

Plan 005, Phase 7, Step 80. Methods: ``message/send``, ``message/stream``,
``tasks/get``, ``tasks/list``, ``tasks/cancel``, ``tasks/resubscribe``.

Dispatch goes through ``SkillAdapter._source.invoke()`` (the ``SkillSource``
seam) — **not** through the legacy ``handle_task()``/``self._client`` path,
which stays untouched for backward compatibility with the pre-v1.0.0
``/tasks/send`` surface (see ``varco_fastapi.router.skill``). This is what
makes a custom, non-router ``SkillSource`` reachable over the v1.0.0 surface:
``RouterSkillSource.invoke()`` and any hand-written ``SkillSource.invoke()``
are dispatched identically.

Task states (v1.0.0): ``submitted`` / ``working`` / ``completed`` / ``failed`` /
``canceled``. This dispatcher only ever produces ``completed``/``failed``
(synchronous dispatch, mirroring how ``SkillSource.invoke()`` is a single
awaited call) — the ``submitted``/``working`` states are reserved for a future
job-runner-backed async binding through ``SkillSource``, which is not part of
this phase (the existing ``job_runner``/``job_store`` async machinery remains
reachable only via the legacy ``/tasks/send`` + ``GET /tasks/{id}`` path).

DESIGN: exceptions from SkillSource.invoke() map to a JSON-RPC error envelope
    ✅ Never leaks a bare 500 for a failed skill call — U-4's explicit ask.
    ✅ Symmetric with ``handle_task()``'s existing "never raise past the
       adapter boundary" contract on the legacy surface.
    ❌ The JSON-RPC ``error.message`` is ``str(exc)`` — callers must not
       assume a stable machine-readable error taxonomy (matches the legacy
       ``_failed_response`` behaviour).

Async safety:   ✅ ``JsonRpcDispatcher.dispatch()`` is the only I/O-performing
                   entry point; task bookkeeping is an in-process dict, no lock
                   needed (single-writer per task_id under normal use).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from varco_core.auth.base import AuthContext
    from varco_fastapi.router.skill import SkillAdapter

_logger = logging.getLogger(__name__)

# ── JSON-RPC 2.0 error codes (spec-reserved range) ──────────────────────────────
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603
_TASK_NOT_FOUND = -32001  # A2A-specific, outside the JSON-RPC reserved range

# ── A2A v1.0.0 task states ──────────────────────────────────────────────────────
TASK_STATE_SUBMITTED = "submitted"
TASK_STATE_WORKING = "working"
TASK_STATE_COMPLETED = "completed"
TASK_STATE_FAILED = "failed"
TASK_STATE_CANCELED = "canceled"


class _JsonRpcError(Exception):
    """Internal control-flow exception carrying a JSON-RPC error code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _BadParamsError(_JsonRpcError):
    def __init__(self, message: str) -> None:
        super().__init__(_INVALID_PARAMS, message)


def _error_envelope(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result_envelope(id_: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _serialise(data: Any) -> Any:
    """Best-effort JSON-safe serialisation (Pydantic models → dict)."""
    try:
        return data.model_dump(mode="json") if hasattr(data, "model_dump") else data
    except Exception:  # noqa: BLE001
        return str(data)


def _task_dict(
    task_id: str,
    state: str,
    *,
    result: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id,
        "status": {
            "state": state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
    if error is not None:
        task["status"]["message"] = error
    if result is not None:
        task["artifacts"] = [{"parts": [{"type": "data", "data": _serialise(result)}]}]
    else:
        task["artifacts"] = []
    return task


class JsonRpcDispatcher:
    """
    Dispatch JSON-RPC 2.0 requests for the A2A v1.0.0 surface.

    One instance per ``SkillAdapter`` — holds a reference back to the adapter
    to read its ``SkillSource`` and to record submitted tasks for later
    ``tasks/get``/``tasks/list``/``tasks/cancel`` lookups.

    Args:
        adapter: The owning ``SkillAdapter``.

    Thread safety:  ✅ Constructed once per adapter, at mount time.
    Async safety:   ✅ ``dispatch()`` is the only async entry point.
    """

    def __init__(self, adapter: "SkillAdapter") -> None:
        self._adapter = adapter
        # In-process task bookkeeping for tasks/get, tasks/list, tasks/cancel.
        # DESIGN: plain dict, not the JobStore
        #   ✅ The v1.0.0 JSON-RPC surface dispatches synchronously (see module
        #      docstring) — there is no crash-recovery requirement to justify
        #      a durable store for this bookkeeping.
        #   ❌ Task history is lost on process restart — acceptable for a
        #      synchronous dispatch surface; use the legacy job_runner-backed
        #      /tasks/send path when crash recovery matters.
        self._tasks: dict[str, dict[str, Any]] = {}
        self._methods: dict[
            str, Callable[[dict[str, Any], "AuthContext | None"], Awaitable[Any]]
        ] = {
            "message/send": self._message_send,
            "message/stream": self._message_send,  # no push transport — single-shot
            "tasks/get": self._tasks_get,
            "tasks/list": self._tasks_list,
            "tasks/cancel": self._tasks_cancel,
            "tasks/resubscribe": self._tasks_get,  # no push transport — same as get
        }

    async def dispatch(
        self, body: dict[str, Any], *, ctx: "AuthContext | None" = None
    ) -> dict[str, Any]:
        """
        Dispatch one JSON-RPC 2.0 request.

        Args:
            body: The parsed JSON-RPC request object.
            ctx:  The verified caller's ``AuthContext`` (or ``None``) —
                  forwarded to ``SkillSource.invoke()`` for ``message/send``.

        Returns:
            A JSON-RPC 2.0 response envelope — either ``{"result": ...}`` or
            ``{"error": {...}}``, always with ``"jsonrpc": "2.0"``.

        Edge cases:
            - Malformed envelope (missing ``method``, wrong ``jsonrpc``) →
              ``-32600 Invalid Request``.
            - Unknown ``method`` → ``-32601 Method not found``.
            - Handler-raised ``_BadParamsError`` → ``-32602 Invalid params``.
            - Any other exception from a handler (including
              ``SkillSource.invoke()``) → ``-32603 Internal error`` — never a
              bare 500.
        """
        id_ = body.get("id") if isinstance(body, dict) else None
        if (
            not isinstance(body, dict)
            or body.get("jsonrpc") != "2.0"
            or not isinstance(body.get("method"), str)
        ):
            return _error_envelope(
                id_, _INVALID_REQUEST, "Invalid JSON-RPC 2.0 request"
            )

        method = body["method"]
        params = body.get("params") or {}
        if not isinstance(params, dict):
            return _error_envelope(id_, _INVALID_PARAMS, "params must be an object")

        handler = self._methods.get(method)
        if handler is None:
            return _error_envelope(id_, _METHOD_NOT_FOUND, f"Unknown method '{method}'")

        try:
            result = await handler(params, ctx)
        except _JsonRpcError as exc:
            return _error_envelope(id_, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "A2A JSON-RPC method %s failed: %s", method, exc, exc_info=True
            )
            return _error_envelope(id_, _INTERNAL_ERROR, str(exc))

        return _result_envelope(id_, result)

    # ── Method handlers ──────────────────────────────────────────────────────

    async def _message_send(
        self, params: dict[str, Any], ctx: "AuthContext | None"
    ) -> dict[str, Any]:
        skill_id = params.get("skill_id")
        if not skill_id or not isinstance(skill_id, str):
            raise _BadParamsError("params.skill_id (str) is required")
        payload = params.get("input", {})
        if not isinstance(payload, dict):
            raise _BadParamsError("params.input must be an object")

        task_id = params.get("task_id") or str(uuid.uuid4())
        source = self._adapter._source  # noqa: SLF001 — dispatcher is adapter-internal
        try:
            result = await source.invoke(skill_id, dict(payload), ctx=ctx)
        except Exception as exc:  # noqa: BLE001
            task = _task_dict(task_id, TASK_STATE_FAILED, error=str(exc))
            self._tasks[task_id] = task
            return task

        task = _task_dict(task_id, TASK_STATE_COMPLETED, result=result)
        self._tasks[task_id] = task
        return task

    async def _tasks_get(
        self, params: dict[str, Any], ctx: "AuthContext | None"
    ) -> dict[str, Any]:
        task_id = params.get("task_id")
        if not task_id or not isinstance(task_id, str):
            raise _BadParamsError("params.task_id (str) is required")
        task = self._tasks.get(task_id)
        if task is None:
            raise _JsonRpcError(_TASK_NOT_FOUND, f"Unknown task '{task_id}'")
        return task

    async def _tasks_list(
        self, params: dict[str, Any], ctx: "AuthContext | None"
    ) -> dict[str, Any]:
        return {"tasks": list(self._tasks.values())}

    async def _tasks_cancel(
        self, params: dict[str, Any], ctx: "AuthContext | None"
    ) -> dict[str, Any]:
        task_id = params.get("task_id")
        if not task_id or not isinstance(task_id, str):
            raise _BadParamsError("params.task_id (str) is required")
        task = _task_dict(task_id, TASK_STATE_CANCELED)
        self._tasks[task_id] = task
        return task


__all__ = [
    "JsonRpcDispatcher",
    "TASK_STATE_SUBMITTED",
    "TASK_STATE_WORKING",
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
]
