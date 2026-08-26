# 04-profiling-hotspot

Demonstrates varco's built-in CPU + memory profiling system for diagnosing
hotspots in FastAPI applications.

## What you'll learn

| Feature | Where |
|---------|-------|
| `@profile` decorator | `work.py` → `cpu_heavy_work()` |
| `profiled()` context manager | `work.py` → `memory_work()` |
| `ProfilingMiddleware` (headers) | `app.py` |
| Custom CPU backend via `register_cpu_backend` | `work.py` → `_CountingCpuBackend` |
| Global kill-switch `set_profiling_enabled` | `app.py`, tests |

## Endpoints

| Method | Path | Demonstrates |
|--------|------|-------------|
| GET | `/v1/compute` | `@profile(ProfileConfig(top_n=5))` on a CPU loop |
| GET | `/v1/allocate` | `profiled()` context manager with report inspection |
| GET | `/v1/custom-backend` | Custom `"counting"` CPU backend |

All responses carry `X-Profile-Wall-Ms` and `X-Profile-Mem-Kb` headers when
`ProfilingMiddleware` is active.

## Run locally

```bash
cd examples/04-profiling-hotspot
uv run uvicorn app:app --reload
```

Then call an endpoint:

```bash
curl -i http://localhost:8000/v1/compute
# HTTP/1.1 200 OK
# x-profile-wall-ms: 12.3
# x-profile-mem-kb: 4.5
# {"result": 41662458330000, "iterations": 50000}
```

## Run tests

```bash
uv run pytest examples/04-profiling-hotspot/tests/ -v
```

## Key concepts

### `@profile` — decoration-time kill-switch

```python
set_profiling_enabled(True)  # must be set BEFORE @profile is evaluated


@profile(ProfileConfig(top_n=5, cpu=True, memory=True))
async def slow_query() -> list[Row]: ...
```

When `is_profiling_enabled()` is `False` at decoration time, `@profile` returns
the original function untouched — zero call-path overhead.

### `profiled()` — call-time kill-switch

```python
async with profiled("batch_job") as session:
    await do_work()
print(session.report.wall_time_ms)  # None when profiling is disabled
```

`profiled()` checks the kill-switch at call time, not decoration time — toggling
`set_profiling_enabled` between calls works correctly.

### Custom backend

```python
from varco_core.profiling import CpuProfileResult, register_cpu_backend


class MyBackend:
    name = "my-backend"

    def start(self) -> None: ...
    def collect(self, top_n: int, sort_by: str) -> CpuProfileResult: ...


register_cpu_backend("my-backend", MyBackend)


@profile(ProfileConfig(cpu_backend="my-backend"))
async def fn() -> None: ...
```

### Caveats

- `cProfile` and `tracemalloc` are **process-global** — one profiling session at
  a time. `ProfilingMiddleware` serialises with an `asyncio.Lock`; concurrent
  requests pass through unprofiled.
- **Never leave profiling always-on in production** — 20–100% overhead. Use
  `set_profiling_enabled(False)` or `VARCO_PROFILING_ENABLED=false`.
