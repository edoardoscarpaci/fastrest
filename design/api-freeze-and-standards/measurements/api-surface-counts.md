# Measurement — public export counts per package

**Plan 022 / Phase 0, Step 3.** Produced by `uv run python scripts/api_surface.py`
(§D-AUDIT) against the working tree on branch `plan-022`, Python 3.12,
`uv sync --all-packages --all-extras`.

Method: import each distribution package named by `bash scripts/packages.sh`,
walk its top-level `__all__`, classify each name as `class` / `function` /
`constant`. Raw data: `api-surface.json`; human-readable: `api-surface.md`.

## Counts (ground truth)

| Package | Exports | class | function | constant |
|---|---|---|---|---|
| `varco_core` | 231 | 180 | 38 | 13 |
| `varco_fastapi` | 108 | 74 | 31 | 3 |
| `varco_sa` | 44 | 28 | 7 | 9 |
| `varco_beanie` | 33 | 32 | 1 | 0 |
| `varco_redis` | 17 | 17 | 0 | 0 |
| `varco_nats` | 9 | 9 | 0 | 0 |
| `varco_kafka` | 7 | 7 | 0 | 0 |
| `varco_ws` | 7 | 5 | 2 | 0 |
| `varco_casbin` | 6 | 4 | 2 | 0 |
| `varco_memcached` | 6 | 4 | 2 | 0 |
| **Total** | **468** | **360** | **83** | **25** |

## ⚠️ The plan's advisory numbers were wrong — this run is ground truth

Plan 022 Step 3 predicted `varco_core` 243, `varco_fastapi` 125, `varco_redis` 28,
`varco_kafka` 8, citing line ranges in the two `__init__.py` files. Every one is
too high. Per the plan's own U-8 discipline ("if these differ, the new run is
ground truth and this plan's numbers are advisory"):

| Package | Plan said | Measured | Delta |
|---|---|---|---|
| `varco_core` | 243 | **231** | −12 |
| `varco_fastapi` | 125 | **108** | −17 |
| `varco_redis` | 28 | **17** | −11 |
| `varco_kafka` | 8 | **7** | −1 |

Cause: the plan's figures were derived by counting *lines* in the `__all__`
block (`varco_core/__init__.py:377-643`, `varco_fastapi/__init__.py:241-365`).
Those blocks contain blank lines and `# --- section ---` comments, so a line
count over-reports. The measured figures come from `len(module.__all__)` after
import, which is the number that actually describes the surface.

The conclusion §D-AUDIT drew from the number is unaffected: 468 names is still
far past the point where an eyeball pass is honest.

## Determinism finding (recorded because `--check` is a CI gate)

The first `--check` run against a freshly written snapshot **failed**, on
`varco_core.listen`: two of its default values are module-private `_Unset`
sentinel *instances*, which fall back to `object.__repr__` and therefore render
their heap address (`<varco_core.event.consumer._Unset object at 0x7e07…>`).
That address changes on every interpreter run, so the raw signature string is
not a stable snapshot key.

Fix landed in `scripts/api_surface.py` (`_ADDRESS_RE`): ` at 0x…>` is stripped
from every rendered signature. Related, and the reason class signatures are
**not** recorded at all: class signatures are synthesised from `__init__` /
`__new__` and, for pydantic models and dataclasses, from generated code whose
rendering is not guaranteed identical across the 3.12/3.13 unit-test matrix. A
snapshot that differs by interpreter would make `--check` unrunnable in CI.
Consequence, stated plainly: **`--check` detects removals and *function*
signature changes; a narrowing of a class `__init__` is invisible to it.**
