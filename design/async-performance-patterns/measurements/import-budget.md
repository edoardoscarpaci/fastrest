# Import-time budget — how to read `import-budget.json`

**Plan 028 / Phase 1 (P1b), Step 10.** The sibling `import-budget.json` is the committed artifact;
this file is its header comment, kept out of the JSON because JSON has no comments.

## What the numbers mean

Each entry is one **distribution package**, derived by executing `scripts/packages.sh` (RL-18 —
never a hand-written list), with three fields:

| Field | Meaning |
|---|---|
| `measured_ms` | The delta observed on the implementer's machine when the entry was last written, by `scripts/import_budget.py --update`. **Informational.** It exists so the headroom below is visible rather than implied. |
| `ceiling_ms` | The **hard budget**. `--check` fails a target whose measured delta exceeds it. Only ever changed by a human, in a reviewed diff, with the justification in the commit message. |
| `observations` | Real deltas seen in CI, appended by Plan 028's Step 13. The evidence that justifies (or refutes) flipping the check from warn-only to a gate in Step 14. Empty today. |

## The metric: a baseline-normalised delta, best-of-5

`scripts/import_budget.py` runs `python -X importtime -c "import <target>"` in a fresh subprocess
five times, sums the **self** column across every emitted line (the true total — `-X importtime`
emits several independent trees, so no single cumulative figure covers everything), takes the
**minimum**, and subtracts a same-methodology `import sys` baseline measured in the same job.

- **Minimum, not mean** — import work is a fixed amount of CPU. Every upward deviation is
  scheduler noise, so the minimum is the closest available estimate of the real cost.
- **Delta, not absolute** — subtracting an interpreter baseline measured *in the same job* is what
  makes a hard ceiling survivable on a slow CI runner. It is also the form the original
  measurement already took (`BACKLOG.md:53-57`: 419 ms *against a 7 ms baseline*).

## The headroom rule

**Ceilings sit at ≈2× `measured_ms`, rounded to a reviewable 5 ms.** That is enough to catch a
*structural* regression — someone adds an eager top-level `import pydantic` to a package
`__init__.py` — and not so tight that 20% runner noise turns it red.

One deliberate exception: **`varco_core`'s ceiling is 25.0 ms, not 2 × 6.6 = 15 ms.** Below ~10 ms
the delta is the same order of magnitude as run-to-run drift in the baseline itself (~0.6 ms
measured, on a quiet machine), so a strict 2× multiplier on a near-zero measurement is a
proportional rule applied where proportion stops being meaningful. 25 ms is a floor, and it still
catches the regression that matters: re-eagerising even one of the four measured contributors
(`lark` 32 ms, `jwt` 45 ms, `psutil` 17 ms, `opentelemetry.sdk`) blows straight through it.

## Status: warn-only

`--check` is wired into `make lint`'s no-`PKG` path and into `test.yml`'s `lint` job **with
`--warn-only`**, so a breach prints and CI stays green. Flipping it to a real gate is Plan 028's
Phase 2 (Steps 13-14) and is deliberately gated on ≥10 real CI observations across both matrix
legs, because the ≈2× headroom is an *assumption* about GitHub-runner variance that no source
quantifies (brief 002 §5). That is U-8 evidence discipline applied to our own gate.

⚠️ If the observed spread turns out to exceed the headroom, the honest responses are to raise the
ceilings **once** with the data, or to leave the check warn-only. **Not** to switch to a ratchet —
`scripts/import_budget.py`'s own `DESIGN:` block rejects that on three independent grounds.

## Where the current values came from

`design/async-performance-patterns/measurements/p1-side-effect-audit.md` §8 — the post-P1a
measurement of all ten distributions on one machine in one session (CPython 3.12, Linux/WSL2).
`varco_core` is the one target this cycle actually optimised: **289.6 ms → 6.6 ms**. The other
nine are recorded as observations for §D-P1-scope; they improved ~20% for free (their eager
`from varco_core import X` lines now pull only the submodules they name) and remain dominated by
their own third-party dependency.
