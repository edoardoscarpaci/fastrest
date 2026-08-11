# The A2A v1.0.0 surface + SkillSource

Plan 005, Phase 7 (gaps U-3 + U-4 — "one piece of work upstream, not two").
Closes: (U-4) the Agent Card and task surface predated the Google A2A v1.0.0
spec (no nested `capabilities` object, no JSON-RPC dispatch); (U-3) the
adapter's *subject* was hard-coupled to `VarcoRouter` introspection, so a
non-router agent (a data pipeline, a wrapper around a third-party API) could
not be exposed over A2A without inventing a fake router.

Both changes had to land together: redesigning the subject seam only made
sense once written against the v1.0 surface, not retrofitted onto the old one.

## What already worked before this phase (source correction)

`ARCHITECTURE.md` used to claim A2A tasks were synchronous with "echo-back,
no history stored" polling. That was never true in source:
`SkillAdapter.__init__` already accepted `job_runner`/`job_store`/
`conversation_store`, `POST /tasks/send` already returned `state: working`
when a `job_runner` was wired, and `GET /tasks/{task_id}/history` already
returned real turns when a `conversation_store` was wired. Phase 7 did not add
async A2A — it moved the *protocol shape* to v1.0.0 and decoupled the
*subject*. If you were relying on async submission before this phase, nothing
about that behaviour changed.

## The v1.0.0 surface

Mounted **always** by `adapter.mount(app)`:

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/.well-known/agent-card.json` | Agent Card — capability flags nested under `capabilities`, **no top-level `id`** (the v1.0.0 shape; the legacy card put flags at the top level and included `id`) |
| `POST` | `/a2a` | JSON-RPC 2.0 dispatch endpoint |

JSON-RPC methods dispatched at `/a2a`:

| Method | Effect |
|---|---|
| `message/send` | Execute a skill — synchronous or async depending on whether `job_runner` is wired, same underlying `handle_task()` path as legacy `POST /tasks/send` |
| `message/stream` | Reserved — streaming push is not implemented (`capabilities.streaming: false`); dispatches like `message/send` today |
| `tasks/get` | Poll a task's status — same underlying lookup as legacy `GET /tasks/{task_id}` |
| `tasks/list` | List known tasks |
| `tasks/cancel` | Cancel a task |
| `tasks/resubscribe` | Reserved for streaming resume — no-op beyond acknowledging today |

Every dispatch returns a JSON-RPC 2.0 envelope: `{"jsonrpc": "2.0", "id": ..., "result": ...}`
on success, `{"jsonrpc": "2.0", "id": ..., "error": {"code": ..., "message": ...}}` on
failure. **A `SkillSource.invoke()` that raises is mapped to an error envelope, never a
bare HTTP 500** — the JSON-RPC dispatcher (`varco_fastapi.router.a2a.jsonrpc`) wraps every
dispatch in a try/except at the transport boundary. Unknown methods and malformed params
also return an error envelope with HTTP 200 (per JSON-RPC 2.0 — errors are communicated in
the body, not the status code).

## The pre-v1.0.0 (legacy) surface

Mounted only when `legacy_paths=True` (the default, for one minor release —
Plan 005, Phase 7, Step 82):

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/.well-known/agent.json` | Legacy Agent Card — flags at the top level, includes `id` |
| `POST` | `/tasks/send` | Execute a skill (sync, or async with `state: working` when `job_runner` is wired) |
| `GET`  | `/tasks/{task_id}` | Poll task status |
| `GET`  | `/tasks/{task_id}/history` | Full turn history, when `conversation_store` is wired |

One deprecation warning is logged per mount when the legacy paths are served.
The default flips to `False` in the **following** minor release; hitting a
legacy path while `legacy_paths=False` returns a plain 404 (the route was
never registered) — name the flip in your release notes if you own an app
built on `varco_fastapi`.

```python
adapter.mount(app, base_url="https://api.example.com")                  # both surfaces
adapter.mount(app, base_url="https://api.example.com", legacy_paths=False)  # v1.0.0 only
```

## SkillSource — decoupling the subject from VarcoRouter

```python
from typing import Any, Protocol, runtime_checkable
from varco_core.auth.base import AuthContext

@runtime_checkable
class SkillSource(Protocol):
    def skills(self) -> list[SkillDefinition]: ...
    def agent_metadata(self) -> AgentMetadata: ...
    async def invoke(
        self, skill_id: str, payload: dict[str, Any], *, ctx: AuthContext | None = None
    ) -> Any: ...
```

`SkillAdapter.__init__` takes **exactly one** of `router_cls` (positional, unchanged) or
`source=` — passing both or neither raises `ValueError` at construction:

```python
SkillAdapter(OrderRouter, agent_name="OrderAgent", ...)             # router_cls path
SkillAdapter(None, source=MySkillSource(), agent_name="X", ...)     # source path
SkillAdapter(OrderRouter, source=MySkillSource(), ...)               # ValueError — both given
SkillAdapter(None, agent_name="X", ...)                               # ValueError — neither given
```

`router_cls` is wrapped internally into `RouterSkillSource`
(`varco_fastapi.router.a2a.router_source`) — **today's `introspect_routes()`
behaviour, extracted verbatim**, not rewritten. `.router_class` keeps working
on a router-backed adapter and returns `None` for a non-router `source`.

### A non-router example — expose a data pipeline over A2A

```python
from varco_fastapi.router.a2a.source import AgentMetadata, SkillDefinition
from varco_fastapi.router.skill import SkillAdapter

class ReportSkillSource:
    def skills(self) -> list[SkillDefinition]:
        return [
            SkillDefinition(
                id="generate_report",
                name="Generate Report",
                description="Builds a PDF summary for the given date range",
                input_modes=("application/json",),
                output_modes=("application/json",),
                route=None,   # no VarcoRouter route backs this skill
            )
        ]

    def agent_metadata(self) -> AgentMetadata:
        return AgentMetadata(name="ReportAgent", description="Generates PDF reports")

    async def invoke(self, skill_id, payload, *, ctx=None):
        return {"report_url": await build_report(payload, requested_by=ctx)}

adapter = SkillAdapter(
    None,
    source=ReportSkillSource(),
    agent_name="ReportAgent",
    agent_description="Generates PDF reports",
)
adapter.mount(app)
```

`skills=` on `SkillAdapter.__init__` additionally accepts author-supplied
`SkillDefinition` objects, appended **verbatim** to whatever the source
already returns — U-3's R-039 ask: hand-written skill text (careful,
reviewed copy for an Agent Card other agents will read) must reach the card
unaltered, never regenerated from route names.

## The auth-passthrough contract (U-3)

`SkillSource.invoke(skill_id, payload, *, ctx=)` is handed the **verified
caller's `AuthContext`** — resolved from `varco_fastapi.context.get_auth_context_or_none()`
at the JSON-RPC dispatch boundary — or `None` when no auth middleware
populated one for that request. This is what makes the three caller classes
distinguishable in a `SkillSource`'s own audit trail:

- an end user calling through a UI that forwards their bearer token,
- another agent calling on behalf of a user or autonomously,
- an integrating platform calling with a service credential.

`RouterSkillSource` does not thread `ctx` into `AsyncVarcoClient` calls today
— it dispatches exactly as the legacy `router_cls` path always did. `ctx`
passthrough is a `SkillSource` contract for **new**, custom sources; the
router-backed source keeps its pre-existing dispatch behaviour unchanged.

## The async task lifecycle (already worked — see Source correction 2)

Nothing here is new behaviour; Phase 7 only moved which protocol paths front
it:

1. `message/send` / `POST /tasks/send` is submitted. With `job_runner` wired,
   the adapter enqueues the dispatch coroutine via `AbstractJobRunner.enqueue`
   and returns immediately with `state: working`.
2. The caller polls `tasks/get` / `GET /tasks/{task_id}`. With `job_store`
   wired, the returned state is mapped from the real, persisted `JobStatus`:
   `PENDING`/`RUNNING` → `working`, `COMPLETED` → `completed` (with the
   decoded artifact), `FAILED`/`CANCELLED` → `failed`.
3. With `conversation_store` wired, every user message and agent response is
   recorded, and `GET /tasks/{task_id}/history` (legacy path) returns the
   full ordered turn list. There is no v1.0.0 JSON-RPC equivalent of the
   history endpoint yet — `tasks/get` returns current status only.

Without a `job_runner`, tasks execute synchronously and the result is in the
same HTTP response — `tasks/get` on an unknown task ID returns a `failed`-shaped
result (sync mode never wrote anything pollable).

## Legacy-path deprecation timeline

- **This release**: `legacy_paths=True` by default — both surfaces mounted,
  one deprecation warning logged per mount when the legacy paths are served.
- **Next minor release**: default flips to `False`. Callers still on the
  legacy Agent Card/task paths must pass `legacy_paths=True` explicitly to
  keep them mounted, and should migrate to the v1.0.0 surface before that
  release.
- **No removal date is set for the legacy paths themselves** — only the
  default changes. `mount(..., legacy_paths=True)` keeps working indefinitely
  as an explicit opt-in.
