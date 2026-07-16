---
name: project-qol-scan-2026-06-18
description: QOL release scan (2026-06-18): open bugs, ergonomics gaps, test hygiene, example style issues from FINDINGS.md and codebase audit
metadata:
  type: project
---

## Open bugs and active ergonomics gaps (as of 2026-06-18)

**Why:** This is the basis for the QOL release plan. No new features — pure cleanup, ergonomics, and hardening.
**How to apply:** Use as the source of truth when implementing the QOL release.

### Critical: Lark parse errors → HTTP 500 (not 400)
- `HttpQueryParams.to_query_params()` calls `QueryParser().parse(self.q)` which can raise `lark.UnexpectedCharacters`, `lark.UnexpectedEOF`, or `lark.UnexpectedToken`.
- The docstring on `to_query_params()` says "Propagates up to ErrorMiddleware → HTTP 400" — but this is **wrong**: ErrorMiddleware only handles `ServiceException`, `HTTPException`, and `asyncio.CancelledError`. All Lark exceptions fall into the generic `Exception` → 500 branch.
- Fix: wrap Lark exceptions in `to_query_params()` and convert to a `ServiceValidationError` (→ 422) or an `HTTPException(400)`.
- Confirmed with: `QueryParser().parse("name = 'Alice'")` raises `UnexpectedCharacters`.

### Critical: ExceptionGroup wrapping (F09) still unhandled
- When `HeaderAuth` or any other middleware raises `HTTPException` inside a `BaseHTTPMiddleware` context, anyio wraps it in an `ExceptionGroup`.
- `ErrorMiddleware.dispatch` catches `HTTPException` but NOT `ExceptionGroup[HTTPException]` → returns 500 instead of 401/403.
- `ErrorMiddleware` needs an `except* HTTPException` clause (Python 3.11+ syntax) or unwrap the group manually.
- Affected: any deployment using `HeaderAuth` or auth inside middleware.

### High: `DomainEvent` alias missing (F29)
- `from varco_core.event import DomainEvent` raises `ImportError`.
- The base class is `Event`; CLAUDE.md and docs call it `Event` but user-facing intuition expects `DomainEvent`.
- Fix: add `DomainEvent = Event` alias in `varco_core.event.__init__` and `__all__`.

### High: Examples use private module paths instead of public API (F28 related)
- 28 imports in examples use private paths: `from varco_core.event.producer import`, `from varco_core.event.consumer import`, `from varco_core.event.base import`, `from varco_core.event.memory import`.
- 8 imports use the canonical public path: `from varco_core.event import`.
- The public path works for everything. Private paths are an internal detail.
- Fix: standardize all example imports to use `from varco_core.event import ...`.

### High: Missing pytest markers in varco_sa, varco_core, varco_ws
- `varco_sa/pyproject.toml` has no `markers` declaration → `PytestUnknownMarkWarning` on 3 tests.
- `varco_core/pyproject.toml` has no `markers` declaration.
- `varco_ws/pyproject.toml` has no `markers` declaration.
- Contrast: `varco_kafka`, `varco_redis`, `varco_beanie`, `varco_casbin`, `varco_fastapi`, `varco_nats`, `varco_memcached` all declare the `integration` marker.

### Medium: Examples missing README files
- `01-minimal-crud-api/` has no `README.md`.
- `22-multi-tenant-soft-delete/` has no `README.md`.
- All other 21 examples have READMEs.

### Medium: httpx deprecation warning in skill adapter test
- `varco_fastapi/tests/milestone_f/test_skill_adapter.py:523` uses `data="not json"` (deprecated) instead of `content=b"not json"`.
- Produces `DeprecationWarning: Use 'content=<...>' to upload raw bytes/text content.`

### Medium: Coroutine leak warning in skill adapter conversation test
- `varco_fastapi/tests/milestone_f/test_skill_adapter_conversation.py::test_agent_card_multi_turn_true_with_store` produces `RuntimeWarning: coroutine 'SkillAdapter._dispatch' was never awaited` from a mock.

### Medium: F11 grammar single-quote: no wrap, no clear error
- Grammar only supports double-quoted strings (`ESCAPED_STRING`).
- Single-quoted strings produce a raw `lark.UnexpectedCharacters` → 500 (see Critical item above).
- Should produce a clear 400/422 with a message like "Filter syntax error: use double-quotes for string values".

### Low: CLAUDE.md states "two pre-existing test failures" but both now pass
- `test_cache.py::TestTTLStrategy::test_cache_evicts_expired_on_read` — passes now.
- `test_event.py::TestJsonEventSerializer::test_serialize_produces_bytes` — passes now.
- CLAUDE.md should be updated to remove the stale warning.

### Low: varco_fastapi `@Provider` injection docstring mismatch
- `router/base.py` line 196 comment says "caught by ErrorMiddleware and mapped to HTTP 400" but this is not what actually happens (Lark errors → 500). The comment is incorrect and misleading.

### Confirmed still-open from prior scans
- No `DomainEvent` alias (F29) — still missing.
- Single-quoted query strings → 500 (F11, combined with Critical above).
- ExceptionGroup wrapping → 500 (F09).
