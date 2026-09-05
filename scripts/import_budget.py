#!/usr/bin/env python3
"""Measure, record and gate `python -X importtime` cost per distribution package.

Plan 028 / Phase 1 (P1b), design section §D-P1-oq4. Phase 0 made
``import varco_core`` lazy (289.6 ms → 6.6 ms on the implementer's machine);
this script is what stops that win from silently eroding the next time someone
adds a top-level import to a package ``__init__.py``.

For each target it runs ``python -X importtime -c "import <target>"`` in a
**fresh subprocess**, five times, sums the *self* column across every reported
line (the true total import cost), takes the minimum, and subtracts a
same-methodology ``import sys`` baseline measured in the same job. The
resulting **delta** is compared against a hard ceiling committed in
``design/async-performance-patterns/measurements/import-budget.json``.

DESIGN: a hard, baseline-normalised ceiling — not a ratchet (§D-P1-oq4)
  ✅ The objection to a hard number is that a slow runner fails it. Subtracting
     an interpreter baseline measured *in the same job* removes most of that;
     the BACKLOG's own measurement is already expressed that way (419 ms
     against a 7 ms baseline), so the metric is the one already in use.
  ✅ A hard number is a single reviewable line in a JSON file. Raising it
     requires a diff a reviewer sees, with a commit message justifying it.
     That is the whole value of a budget.
  ✅ ``best-of-5`` in separate subprocesses is ``importtime-waterfall``'s own
     methodology (brief 002 §1), not an invention. Minimum, not mean: import
     work is a fixed amount of CPU, so every deviation upward is scheduler
     noise and the minimum is the closest estimate of the real cost.
  ❌ A ratchet is rejected on three grounds: (a) it must rewrite a committed
     number on every improving PR, guaranteeing merge conflicts on a file every
     PR touches; (b) one lucky-fast CI run permanently lowers the bar and every
     subsequent honest run fails — an unfixable red with no code defect behind
     it; (c) "cannot be gamed by a slow runner" is answered better by baseline
     normalisation than by a moving target.
  ❌ A hard number needs headroom, so ceilings sit at ~2x the measured value
     and the measurement is committed *next to* the ceiling, making the
     headroom visible rather than implied.

DESIGN: warn-only today, a gate after ten observations
  ✅ Plan 028 Step 9 lands this in ``--warn-only`` mode deliberately. The
     ~2x headroom is an **assumption** about GitHub-runner variance that no
     source quantifies (brief 002 §5). Steps 13-14 collect >= 10 real CI
     observations into each entry's ``observations`` array before the
     ``--warn-only`` flag is dropped from ``make lint`` and ``test.yml``.
  ❌ Until then a genuine regression only prints. Accepted: that is U-8
     evidence discipline applied to our own gate, and printing is enough to
     gather the evidence that justifies switching it on.

Usage::

    uv run python scripts/import_budget.py --check              # compare (exit 1 on a breach)
    uv run python scripts/import_budget.py --check --warn-only  # compare, always exit 0
    uv run python scripts/import_budget.py --update             # rewrite measured_ms only
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

# ``parents[1]`` — this file lives at <root>/scripts/, so the repo root is one
# level up. Used only to locate the committed budget file; the *target list* is
# derived by searching upward from the caller's working directory (see
# _find_repo_root).
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

BUDGET_PATH: Final[Path] = (
    REPO_ROOT / "design" / "async-performance-patterns" / "measurements" / "import-budget.json"
)

#: How many subprocess runs feed the best-of-N. Five is importtime-waterfall's
#: own default and is the number §D-P1-oq4 commits to.
RUNS: Final[int] = 5

#: ``import time:   <self us> | <cumulative us> | <name>`` — CPython's own
#: ``-X importtime`` line format, emitted on **stderr**.
_IMPORTTIME_LINE: Final[re.Pattern[str]] = re.compile(r"^import time:\s+(\d+)\s*\|\s*\d+\s*\|")


class BudgetError(RuntimeError):
    """A budget run could not be performed at all (as opposed to being over budget).

    Distinct from "a target exceeded its ceiling", which is an ordinary result
    reported through the exit code. This is raised when the *derivation* fails
    — no ``packages.sh``, or a ``packages.sh`` that yields nothing — because
    silently measuring an empty target list is the RL-18 failure mode this
    script exists to avoid.
    """


def _find_repo_root(start: Path) -> Path:
    """Find the nearest ancestor of ``start`` that contains ``scripts/packages.sh``.

    DESIGN: search upward from the caller's cwd, never fall back to ``__file__``
      ✅ Works from anywhere inside the workspace. ``scripts/unit_tests.sh``
         runs each package's suite with the cwd set to that package's directory,
         and a plain ``cd varco_core && pytest`` is an everyday thing to do —
         a cwd-only lookup would make this script fail there for no real reason.
      ✅ Still fails loudly outside the tree, which is the Edge-case the plan
         names: a ``__file__``-based fallback would quietly succeed against
         whatever tree this file happens to live in, which is precisely the
         "silently measure the wrong thing" failure being avoided.

    Args:
        start: Directory to begin the upward walk from (normally ``Path.cwd()``).

    Returns:
        The first ancestor (``start`` included) containing ``scripts/packages.sh``.

    Raises:
        BudgetError: If no ancestor has one.
    """
    for candidate in (start.resolve(), *start.resolve().parents):
        if (candidate / "scripts" / "packages.sh").is_file():
            return candidate
    raise BudgetError(
        f"cannot derive the target list: no scripts/packages.sh found in {start} "
        "or any parent directory. Run this from inside the varco workspace."
    )


def discover_targets(repo_root: Path) -> list[str]:
    """Derive the list of importable distribution packages to measure.

    Executes ``scripts/packages.sh`` rather than carrying a list, per RL-18 and
    the precedent set by ``scripts/api_surface.py`` and ``scripts/bump.py``: a
    new workspace member must be picked up by every consumer from one edit to
    ``[tool.uv.workspace] members``. ``packages.sh`` distinguishes a
    distribution from a plain member structurally (``<m>/<m>/__init__.py``
    exists), which is what keeps the non-importable ``examples`` member out.

    Args:
        repo_root: The workspace root — the directory containing ``scripts/``.

    Returns:
        One package name per line of ``packages.sh``'s output, in
        ``members`` order. Never empty.

    Raises:
        BudgetError: If ``scripts/packages.sh`` is missing, exits non-zero, or
            prints nothing. All three are the same defect from this script's
            point of view — the target list could not be derived — and all
            three must be loud, never a silent zero-target success.

    Example:
        >>> discover_targets(Path("/home/edoardo/projects/varco"))[0]
        'varco_core'
    """
    script = repo_root / "scripts" / "packages.sh"
    if not script.is_file():
        raise BudgetError(
            f"cannot derive the target list: {script} does not exist. "
            "Run this script from the workspace root."
        )
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise BudgetError(
            f"scripts/packages.sh exited {result.returncode}: {result.stderr.strip()}"
        )
    targets = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not targets:
        raise BudgetError(
            "scripts/packages.sh produced no packages — refusing to report an empty "
            "target list as a pass (the RL-18 failure mode)."
        )
    return targets


def _total_import_ms(statement: str) -> float:
    """Run one ``-X importtime`` subprocess and total its self-times, in ms.

    Args:
        statement: Python source passed to ``-c`` (e.g. ``"import varco_core"``).

    Returns:
        The sum of the *self* column over every ``import time:`` line, in
        milliseconds. Summing self-times rather than reading one cumulative
        figure is what makes the number a true total: ``-X importtime`` emits
        several independent trees (site initialisation and the requested
        import), so no single cumulative value covers everything.

    Raises:
        BudgetError: If the subprocess fails — an unimportable target is a
            defect, not a zero-cost import.
    """
    result = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", statement],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BudgetError(f"`{statement}` failed:\n{result.stderr.strip()}")
    micros = sum(
        int(match.group(1))
        for line in result.stderr.splitlines()
        if (match := _IMPORTTIME_LINE.match(line))
    )
    return micros / 1000.0


def _best_of(statement: str, runs: int = RUNS) -> float:
    """Return the minimum ``_total_import_ms`` over ``runs`` fresh subprocesses."""
    return min(_total_import_ms(statement) for _ in range(runs))


def measure_delta_ms(target: str) -> float:
    """Measure a module's import cost above a bare interpreter, in milliseconds.

    Args:
        target: An importable module name.

    Returns:
        ``best-of-5(import <target>) - best-of-5(import sys)``. The subtraction
        is what makes the figure comparable across machines and across CI
        runners of different speeds — see §D-P1-oq4. It can go slightly
        negative for a zero-cost import, purely from run-to-run drift in the
        baseline; callers treat that as "free", not as an error.

    Raises:
        BudgetError: Propagated from ``_total_import_ms`` if the target cannot
            be imported.

    Example:
        >>> round(measure_delta_ms("varco_core"))  # doctest: +SKIP
        7
    """
    # ``import sys`` is already in sys.modules before the -c statement runs, so
    # this measures interpreter + site startup and nothing else.
    baseline = _best_of("import sys")
    return _best_of(f"import {target}") - baseline


def _load_budget() -> dict[str, dict[str, object]]:
    """Read the committed budget file.

    Reads the module global ``BUDGET_PATH`` at call time rather than capturing
    it, so tests (and a future ``--budget PATH`` flag) can redirect it.

    Raises:
        BudgetError: If the file is absent. A missing budget is a missing gate,
            never an implicit pass.
    """
    if not BUDGET_PATH.is_file():
        raise BudgetError(f"no budget file at {BUDGET_PATH}")
    data: dict[str, dict[str, object]] = json.loads(BUDGET_PATH.read_text())
    return data


def _write_budget(data: dict[str, dict[str, object]]) -> None:
    """Write the budget file back, preserving key order and trailing newline."""
    BUDGET_PATH.write_text(json.dumps(data, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` on success (or under ``--warn-only``, or for ``--update``), ``1``
        when ``--check`` finds a target over its ceiling, ``2`` when the target
        list could not be derived.

    Raises:
        SystemExit: From ``argparse`` on a usage error — notably ``--check``
            together with ``--update``, which are mutually exclusive because
            one compares against the values the other rewrites.

    Example:
        >>> main(["--check", "--warn-only"])  # doctest: +SKIP
        0
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="compare each target's measured delta against its committed ceiling (default)",
    )
    mode.add_argument(
        "--update",
        action="store_true",
        help="rewrite each target's measured_ms; never touches ceilings or observations",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="report breaches but always exit 0 (Plan 028 Step 9; Step 14 drops this)",
    )
    args = parser.parse_args(argv)

    # The target list is derived by walking up from the *caller's* working
    # directory, not from this file's location: deriving from __file__ would
    # let the script quietly succeed when invoked against an unrelated tree.
    try:
        targets = discover_targets(_find_repo_root(Path.cwd()))
        budget = _load_budget()
    except BudgetError as exc:
        print(f"import-budget: {exc}", file=sys.stderr)
        return 2

    missing = [t for t in targets if t not in budget]
    for target in missing:
        # Not a failure: a fresh package legitimately has no ceiling yet. The
        # real guard is the unit test asserting the committed budget covers
        # every derived target, which fails in `make test` instead.
        print(f"import-budget: NOTE  {target} has no budget entry — not measured")

    measured = [t for t in targets if t in budget]

    if args.update:
        for target in measured:
            budget[target]["measured_ms"] = round(measure_delta_ms(target), 1)
            print(f"import-budget: {target} measured_ms -> {budget[target]['measured_ms']} ms")
        _write_budget(budget)
        return 0

    breaches: list[str] = []
    for target in measured:
        entry = budget[target]
        ceiling = float(entry["ceiling_ms"])  # type: ignore[arg-type]
        delta = measure_delta_ms(target)
        over = delta > ceiling
        status = "OVER " if over else "ok   "
        print(f"import-budget: {status} {target}: {delta:.1f} ms (ceiling {ceiling:.1f} ms)")
        if over:
            breaches.append(
                f"{target}: {delta:.1f} ms exceeds its ceiling of {ceiling:.1f} ms — "
                "a new eager top-level import, or a genuinely slower dependency"
            )

    if not breaches:
        return 0
    for breach in breaches:
        print(f"import-budget: FAIL  {breach}", file=sys.stderr)
    if args.warn_only:
        print(
            "import-budget: warn-only, exiting 0 (Plan 028 Step 14 flips this to a gate)",
            file=sys.stderr,
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
