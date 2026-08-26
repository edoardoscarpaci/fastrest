#!/usr/bin/env bash
# unit_tests.sh — Run unit tests for one package or all workspace members,
# accumulating pass/fail/skip across all of them instead of aborting on the
# first red package (§RL-5-parity, plans/017-ci-green-workflows-and-lint-type-gates.md).
#
# `Makefile:test`'s previous `foreach ... || exit 1` loop aborted on the first
# failing package, hiding every package after it — exactly the failure-isolation
# property §RL-5-shape depends on to justify NOT fanning out per-package CI jobs.
# This script fixes that for both `make test` and CI simultaneously, and fixes
# two live drift bugs at once: `varco_casbin` and `examples` were never in
# `Makefile`'s PACKAGES list.
#
# ⚠️ KNOWN MAINTENANCE POINT (plan 017 Edge cases table): the package list is
# duplicated in three places — Makefile's PACKAGES, this script's array, and
# scripts/integration_tests.sh's ALL_INTEGRATION_PACKAGES. A new workspace
# member must be added to all three by hand; deriving them from
# [tool.uv.workspace] members is the obvious future fix and is scope creep here.
#
# Usage:
#   scripts/unit_tests.sh                  # run all ten packages + example
#   scripts/unit_tests.sh varco_redis      # run one package
#   scripts/unit_tests.sh varco_kafka varco_redis  # run specific packages
#   make test                              # same as the bare invocation
#   make test PKG=varco_redis              # same as passing one package
#
# Environment:
#   PYTEST_EXTRA_ARGS — passed verbatim to every pytest invocation,
#                        e.g. PYTEST_EXTRA_ARGS="-x -s" ./scripts/unit_tests.sh
#
# This script does NOT select the integration marker — integration tests are
# opt-in (guarded by VARCO_RUN_INTEGRATION, see varco_kafka/tests/conftest.py)
# and already skip themselves when that guard is absent. The "not integration"
# marker expression is passed explicitly anyway, belt-and-braces, so a marker
# misconfiguration can never accidentally pull a slow/Docker-requiring test
# into this suite.
#
# Exit codes:
#   0 — all selected suites passed (or had no tests to collect)
#   1 — one or more suites failed

set -euo pipefail

# ── Resolve workspace root regardless of where the script is called from ──────
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── Colour helpers (fall back gracefully when stdout is not a tty) ────────────
if [[ -t 1 ]]; then
  RED="\033[0;31m"; GREEN="\033[0;32m"; YELLOW="\033[1;33m"
  CYAN="\033[0;36m"; BOLD="\033[1m"; RESET="\033[0m"
else
  RED=""; GREEN=""; YELLOW=""; CYAN=""; BOLD=""; RESET=""
fi

# ── All workspace member packages (mirrors Makefile's PACKAGES) ──────────────
ALL_PACKAGES=("varco_core" "varco_kafka" "varco_nats" "varco_redis" "varco_sa" "varco_beanie" "varco_memcached" "varco_ws" "varco_fastapi" "varco_casbin")

# ── Extra suites — the example app is not a workspace member (see
# scripts/integration_tests.sh's DESIGN comment for why run_from="root" is
# required for it: `uv run` from inside examples/00-full-stack-post-api
# resolves a separate, non-workspace environment with no varco_core installed).
EXTRA_SUITES=("examples/00-full-stack-post-api:example/tests")

# ── Determine which suites to run ──────────────────────────────────────────────
SUITES=()
if [[ $# -eq 0 ]]; then
  for pkg in "${ALL_PACKAGES[@]}"; do
    SUITES+=("$pkg:tests:$pkg:cd")
  done
  for extra in "${EXTRA_SUITES[@]}"; do
    extra_dir="${extra%%:*}"
    extra_testpath="${extra#*:}"
    SUITES+=("$extra_dir:$extra_testpath:$extra_dir:root")
  done
else
  for pkg in "$@"; do
    SUITES+=("$pkg:tests:$pkg:cd")
  done
fi

# ── Validate suite paths ────────────────────────────────────────────────────────
for suite in "${SUITES[@]}"; do
  suite_dir="${suite%%:*}"
  rest="${suite#*:}"
  suite_testpath="${rest%%:*}"
  if [[ ! -d "$ROOT/$suite_dir/$suite_testpath" ]]; then
    echo -e "${RED}ERROR: '$suite_dir' does not have a '$suite_testpath' directory under $ROOT/${RESET}" >&2
    exit 1
  fi
done

# ── Sync the workspace ONCE, up front ──────────────────────────────────────────
# RL-21: each suite below runs its own `uv run`, and `uv run` will re-resolve
# and re-sync the environment if it thinks anything is stale.  Eleven of those,
# interleaved with pytest collection, produced intermittent false failures on
# trees that were green on the very next run (seen on both the examples suite
# and varco_fastapi).  A false red here is expensive: this script is CI's `unit`
# job, and `all-green` — the single required status check — sits downstream.
#
# So: sync deliberately once, then forbid every per-suite `uv run` from touching
# the environment again via --no-sync.  A suite now either runs against a fully
# prepared venv or fails loudly at this step, instead of racing a sync.
#
#   ✅ One sync instead of up to eleven; removes the race entirely rather than
#      papering over it with a retry (which would hide real failures).
#   ✅ Faster — the per-suite staleness check is skipped.
#   ❌ A dependency changed mid-run is not picked up. Correct: a unit run should
#      test one fixed environment, not a moving one.
echo -e "${BOLD}── Syncing workspace ──────────────────────────────────────────────────────${RESET}"
uv sync --all-packages --all-extras
echo

# ── Run pytest for each suite, accumulating results ────────────────────────────
FAILED_SUITES=()
PASSED_SUITES=()
SKIPPED_SUITES=()

echo -e "\n${BOLD}── Running unit tests ─────────────────────────────────────────────────────${RESET}\n"

for suite in "${SUITES[@]}"; do
  suite_dir="${suite%%:*}"
  rest="${suite#*:}"
  suite_testpath="${rest%%:*}"
  rest2="${rest#*:}"
  suite_label="${rest2%%:*}"
  suite_run_from="${rest2#*:}"

  echo -e "${CYAN}▶  $suite_label${RESET}"
  # `set -e` must not abort the loop on a non-zero pytest exit — every suite
  # must run and be summarised at the end, so the status is captured explicitly.
  # shellcheck disable=SC2086  # PYTEST_EXTRA_ARGS intentionally word-splits
  status=0
  if [[ "$suite_run_from" == "root" ]]; then
    (cd "$ROOT" && uv run --no-sync pytest \
        "$suite_dir/$suite_testpath/" \
        -m "not integration" \
        -v \
        ${PYTEST_EXTRA_ARGS:-}) || status=$?
  else
    (cd "$ROOT/$suite_dir" && uv run --no-sync pytest \
        "$suite_testpath/" \
        -m "not integration" \
        -v \
        ${PYTEST_EXTRA_ARGS:-}) || status=$?
  fi

  # pytest exit code 5 (EXIT_NOTESTSCOLLECTED) means "no tests matched the
  # filter" — not a failure (mirrors scripts/integration_tests.sh:193-201).
  if [[ $status -eq 0 ]]; then
    PASSED_SUITES+=("$suite_label")
    echo -e "${GREEN}✔  $suite_label passed${RESET}\n"
  elif [[ $status -eq 5 ]]; then
    SKIPPED_SUITES+=("$suite_label")
    echo -e "${YELLOW}○  $suite_label has no tests — skipped${RESET}\n"
  else
    FAILED_SUITES+=("$suite_label")
    echo -e "${RED}✘  $suite_label FAILED${RESET}\n"
  fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo -e "${BOLD}── Summary ────────────────────────────────────────────────────────────────${RESET}"
for suite_label in "${PASSED_SUITES[@]+"${PASSED_SUITES[@]}"}"; do
  echo -e "  ${GREEN}✔  $suite_label${RESET}"
done
for suite_label in "${SKIPPED_SUITES[@]+"${SKIPPED_SUITES[@]}"}"; do
  echo -e "  ${YELLOW}○  $suite_label (no tests)${RESET}"
done
for suite_label in "${FAILED_SUITES[@]+"${FAILED_SUITES[@]}"}"; do
  echo -e "  ${RED}✘  $suite_label${RESET}"
done

if [[ ${#FAILED_SUITES[@]} -gt 0 ]]; then
  echo -e "\n${RED}${BOLD}${#FAILED_SUITES[@]} suite(s) failed.${RESET}" >&2
  exit 1
fi

echo -e "\n${GREEN}${BOLD}All unit tests passed.${RESET}"
