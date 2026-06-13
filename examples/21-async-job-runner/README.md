# 21 — Async Job Runner

Demonstrates the varco async job pattern: enqueue a slow operation via HTTP,
return `202 Accepted` immediately, then poll for status and result.

## What this teaches

| Concept | Where |
|---------|-------|
| `InMemoryJobStore` | `app.py` — wired directly, no DI |
| `JobRunner` lifecycle (`start`/`stop`) | `app.py` lifespan |
| Enqueue via `runner.enqueue(job, coro)` | `router.py` |
| `202 Accepted` + `status_url` | `router.py` |
| Poll `GET /v1/jobs/{job_id}` | `router.py` |
| `@varco_task` + `TaskRegistry` | `jobs.py` |
| Decode `job.result` bytes → JSON | `router.py` |

## Endpoints

```
POST /v1/reports          body: {title, rows}
                          → 202 {job_id, status, status_url}

GET  /v1/jobs/{job_id}    → {job_id, status, result?, error?}
```

`status` lifecycle: `pending` → `running` → `completed` | `failed`

## Run locally

```bash
cd examples/21-async-job-runner
uv run uvicorn app:app --reload
```

Try it:

```bash
# Enqueue a report job
curl -s -X POST http://localhost:8000/v1/reports \
  -H "Content-Type: application/json" \
  -d '{"title":"Q3 Revenue","rows":100}' | jq

# Poll status (replace UUID from above)
curl -s http://localhost:8000/v1/jobs/<job_id> | jq
```

## Run tests

```bash
# From the workspace root:
uv run pytest .claude/worktrees/feature+examples-catalog/examples/21-async-job-runner/tests/ -v
```

## Key design decisions

**`runner.enqueue(job, coro)` is the only correct submission path.**
`enqueue` saves the PENDING record to the store *before* creating the
asyncio.Task, so a crash between the two leaves a recoverable record rather
than a silent drop.  Calling `runner.submit()` directly skips the persistence
step.

**`lifespan=True` on `ASGITransport` starts the runner in tests.**
Without this, `runner.start()` is never called and `runner._started` is
`False`, causing a silent no-op.  The lifespan context manager handles
`start()` / `stop()` symmetrically around the test session.

**`await asyncio.sleep(0)` drives jobs in tests.**
`JobRunner` uses one asyncio.Task per job.  A single `sleep(0)` yields
control to the event loop, which steps the task forward.  Multiple ticks
(`wait_for_job`) are used for jobs that have real `asyncio.sleep` calls
inside them.

**`job.result` is JSON-encoded bytes.**
`JobRunner._run_job` serializes the coroutine return value with
`json.dumps(...).encode()`.  The poll endpoint decodes it back with
`json.loads(job.result.decode())` before returning it in the HTTP response.
