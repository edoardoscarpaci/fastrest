# `benchmarks/` — the CodSpeed harness

**Plan 028 / Phase 3 (P2), §D-P2-harness.** Seven in-process, deterministic, Docker-free
benchmarks over the paths varco actually pays for per request, plus one subprocess import
benchmark that ties Phase 0's lazy-import win to the same dashboard.

```bash
make bench                                  # plain pytest, no instrumentation, no token
uv run --group bench pytest benchmarks/ -q  # the same thing, spelled out
```

## Three standing rules

1. **These are not tests, and they are never a gate.** `.github/workflows/bench.yml` is a
   *separate* workflow. It is not in `test.yml`'s `needs:`, it must never be added to
   `all-green`'s `needs:`, and it must never be selected as a required status check. `all-green`
   is the only required check in this repo and that is deliberate — a microbenchmark on a shared
   GitHub runner is not a reproducible, actionable failure signal (brief 002 §5: runner noise is
   "acknowledged but unquantified"; almost no major project gates on it). Contrast
   `scripts/import_budget.py`, which *is* wired into `lint` — import time is a structural
   property measured in a fresh subprocess with best-of-N, and its failure mode ("someone added
   a top-level import") is both reproducible and actionable. The asymmetry is the whole of
   §D-P1-oq4.
2. **Never import a backend that needs a container.** No Redis, Kafka, NATS, Memcached, Mongo or
   Postgres client may appear here, and no `testcontainers` import. `bench.yml` runs with no
   `services:` block, so such an import would fail loudly — but the rule exists so it never gets
   written in the first place. `varco_sa`'s *query compiler* is allowed (see
   `bench_query_ast_build.py`): compiling a `Select` to a SQL string touches no database.
3. **Assert nothing about time.** A benchmark that asserts a duration is a flaky test. Timing is
   CodSpeed's job; a correctness assertion belongs in `varco_core/tests/`. The only assertions
   here are the cheap "the work actually happened" guards that stop a benchmark silently
   measuring `None`.

## Why they must also run uninstrumented

Benchmarks that are never gated rot. `make bench` runs every module as a plain pytest test with
no instrumentation and no `CODSPEED_TOKEN`, so a benchmark that stops importing, or stops
exercising the path it names, fails a human's local run and a `bench.yml` run alike. They live
under `benchmarks/` rather than in a package's `tests/` because `scripts/unit_tests.sh` iterates
an explicit suite list (each package's own `tests/`, plus `examples/00-full-stack-post-api`) —
so this directory is never collected by `make test` and never slows the unit legs.

## The seed set

| Module | Path exercised | Why it is here |
|---|---|---|
| `bench_query_parse.py` | `QueryParser.parse()` on a fixed filter string | Plan 028 Phase 4 (P4)'s direct target — the reflection/parser-caching row is ⛔ gated on *this* number |
| `bench_query_ast_build.py` | `QueryBuilder` → AST → SQLAlchemy compile | Phase 5 (P3)'s direct target — the `slots=True` row is gated on the AST node population |
| `bench_dto_roundtrip.py` | `CreateDTO`/`ReadDTO` validate + dump | The pydantic hot path, on every request in and out |
| `bench_service_create.py` | `AsyncService.create()` over an in-memory repo | The composite path users actually pay: authorize → UoW → assemble → save → assemble |
| `bench_event_publish.py` | `InMemoryEventBus.publish()` + `drain()` | Fan-out cost, independent of any broker |
| `bench_cache_get_set.py` | `InMemoryCache` get/set | The cheapest baseline in the set — it exists to detect *harness* drift, not varco drift |
| `bench_import_varco_core.py` | `python -c "import varco_core"` in a subprocess | Ties Phase 0's 289.6 ms → 6.6 ms win to the same dashboard as everything else |

## Operator step

CodSpeed needs a `CODSPEED_TOKEN` repository secret and the repo connected on codspeed.io —
an out-of-repo step, recorded in `design/varco-1-0-release/release-runbook.md` alongside the ten
PyPI environments. Until it exists, `bench.yml` runs and uploads nothing useful; it still never
blocks a merge. Fork PRs skip the job entirely by its `if:` guard rather than failing on the
missing secret.
