# PEP 735 packaging audit

Plan 023 / Phase 2 Step 12, §RL-13-metadata. Expected outcome stated in the plan: "already
compliant; record it and stop" — confirmed below.

## Method

For each of the ten distribution packages plus the root workspace `pyproject.toml`, every
`[dependency-groups]` entry and every `[project.optional-dependencies]` entry was read directly
from the tree and classified as either genuinely **dev/test/docs-only** (never installed by an
end consumer, never reaches a published artifact) or a genuine **runtime extra** (a consumer
installs it deliberately, e.g. `pip install "varco-fastapi[ws]"`).

## Finding

| Package | `[dependency-groups]` | `[project.optional-dependencies]` | Verdict |
|---|---|---|---|
| root | `lint` (ruff/mypy pins), `dev` (asyncpg, `{include-group = "lint"}`, tomlkit), `docs` (mkdocs stack) | — (no `[project]` at all — the root is a workspace coordinator, not a distribution) | Correctly placed |
| varco_core | `dev` (pytest, pytest-asyncio, dev-only `varco-fastapi`) | `tz` (`tzdata` — a genuine runtime extra for slim container images) | Correctly placed |
| varco_kafka | `dev` (pytest, testcontainers) | none | Correctly placed |
| varco_nats | `dev` (pytest, testcontainers) | none | Correctly placed |
| varco_redis | `dev` (pytest, testcontainers) | none | Correctly placed |
| varco_sa | `dev` (pytest, aiosqlite, alembic, testcontainers, dev-only `varco-redis`) | `postgresql` (asyncpg), `migrations` (alembic — genuine consumer-facing extras) | Correctly placed |
| varco_beanie | `dev` (pytest, pymongo, testcontainers) | none | Correctly placed |
| varco_memcached | `dev` (pytest, testcontainers) | none | Correctly placed |
| varco_ws | `dev` (pytest, fastapi/uvicorn/httpx/websockets test harness) | none | Correctly placed |
| varco_fastapi | `dev` (pytest, httpx, uvicorn, anyio, cryptography, dev-only `varco-sa`, alembic, asyncpg, testcontainers) | `ws`, `mcp`, `a2a`, `otel`, `prometheus`, `openapi` — six genuine runtime extras a consumer opts into | Correctly placed |
| varco_casbin | `dev` (pytest, sqlalchemy-adapter, aiosqlite, testcontainers, beanie, motor) | `fastapi`, `sqlalchemy`, `beanie` — three genuine runtime extras selecting a persistence/REST backend | Correctly placed |

## Verdict

**No migration needed.** The root and all ten packages already use `[dependency-groups]`
(PEP 735) for every dev/test/docs dependency set — none of them is published or installed by an
end consumer, so none belongs in `[project.optional-dependencies]`. The remaining
`[project.optional-dependencies]` entries across the workspace (`varco_core.tz`,
`varco_sa.postgresql`/`.migrations`, `varco_fastapi.ws`/`.mcp`/`.a2a`/`.otel`/`.prometheus`/
`.openapi`, `varco_casbin.fastapi`/`.sqlalchemy`/`.beanie`) are all genuine **runtime** extras — a
consumer chooses to `pip install "pkg[extra]"` to opt into optional runtime behaviour, which is
exactly what `[project.optional-dependencies]` is for. PEP 735 replaces
`[project.optional-dependencies]` only for *unpublished* dev/test/docs dependency sets; it was
never meant to replace a package's genuine installable extras, and brief 001 §3's blanket
"`[dependency-groups]`, not `[project.optional-dependencies]`" recommendation is scoped to dev
dependencies only — it must not be over-applied here.
