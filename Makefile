# Makefile — varco monorepo
# All commands run from the workspace root. Requires: uv (https://docs.astral.sh/uv/)
#
# Quick reference:
#   make install          — sync all workspace deps
#   make lint              — ruff check (whole repo; PKG= narrows to one package's source dirs)
#   make format             — ruff format + fix (same PKG= narrowing as lint)
#   make type-check        — mypy (all ten source dirs; PKG= narrows to one package)
#   make test              — unit tests, all ten packages + the example suite
#                            (scripts/unit_tests.sh — accumulates pass/fail/skip
#                            instead of aborting on the first red package)
#   make test PKG=varco_core — unit tests for one package
#   make integration-test — integration tests (requires Docker; honors any
#                            VARCO_TEST_*_URL override present in the shell)
#   make integration-test PKG=varco_redis — integration tests for one package
#   make integration-test-clean — integration tests, guaranteed clean-room
#                            (unsets every VARCO_TEST_*_URL override first)
#   `.github/workflows/integration.yml` runs `make integration-test-clean`
#   (push:main + nightly + workflow_dispatch) — always the clean-room entry
#   point, so every CI integration run is a genuine clean-room run. It is
#   NOT a required check; `.github/workflows/test.yml`'s `all-green` job is
#   the only one (RL-5, plans/017-ci-green-workflows-and-lint-type-gates.md).
#   make build            — build wheels for all packages
#   make build PKG=varco_redis — build one package
#   make publish          — publish all dist/* to PyPI (requires UV_PUBLISH_TOKEN)
#   make docs-deps        — install documentation tooling (mkdocs + mkdocstrings)
#   make docs             — build the static HTML docs site into ./site
#   make docs-serve       — live-reload docs preview at http://127.0.0.1:8000
#   make clean            — remove all dist/ directories and the built docs site

.DEFAULT_GOAL := help

# ── Package list ──────────────────────────────────────────────────────────────
PACKAGES := \
	varco_core \
	varco_kafka \
	varco_nats \
	varco_redis \
	varco_sa \
	varco_beanie \
	varco_memcached \
	varco_ws \
	varco_fastapi \
	varco_casbin

# Optional single-package override: make test PKG=varco_redis
PKG ?=

# ── Helpers ───────────────────────────────────────────────────────────────────
# Resolve actual target list: single package when PKG is set, all otherwise.
ifeq ($(PKG),)
  _TARGETS := $(PACKAGES)
else
  _TARGETS := $(PKG)
endif

# Derive source dirs (package-name/package-name) for lint/format/type-check.
_SRC_DIRS := $(foreach p,$(_TARGETS),$(p)/$(p))

# ── Formatting ────────────────────────────────────────────────────────────────
# RL-6: `uv run ruff` resolves the pinned version from uv.lock (root
# `[dependency-groups] lint`) — the previous ephemeral-resolve invocation
# picked up whatever ruff release was newest at invocation time, so a local
# green said nothing about CI.
RUFF  := uv run ruff
MYPY  := uv run mypy

# ─────────────────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@echo ""
	@echo "  varco — available targets"
	@echo ""
	@echo "  make install                 sync all workspace deps"
	@echo "  make sync                    alias for install"
	@echo "  make lint                    ruff check (whole repo)"
	@echo "  make lint PKG=varco_redis    ruff check (one package's source dirs)"
	@echo "  make format                  ruff format + fix (whole repo)"
	@echo "  make format PKG=varco_redis  ruff format + fix (one package's source dirs)"
	@echo "  make type-check              mypy (all ten source dirs)"
	@echo "  make test                    unit tests, all ten packages + example suite"
	@echo "                                (scripts/unit_tests.sh; same script CI runs)"
	@echo "  make test PKG=varco_core     unit tests (one package)"
	@echo "  make integration-test        integration tests (requires Docker; honors"
	@echo "                                VARCO_TEST_*_URL overrides if set)"
	@echo "  make integration-test PKG=varco_redis  integration tests (one package)"
	@echo "  make integration-test-clean  integration tests, clean-room (unsets every"
	@echo "                                VARCO_TEST_*_URL override first)"
	@echo "                                — .github/workflows/integration.yml runs"
	@echo "                                exactly this target (push:main + nightly +"
	@echo "                                workflow_dispatch); not a required check"
	@echo "  make build                   build wheels + sdists (all packages)"
	@echo "  make build PKG=varco_redis   build one package"
	@echo "  make publish                 publish dist/* to PyPI"
	@echo "  make docs-deps               install documentation tooling"
	@echo "  make docs                    build static HTML docs into ./site"
	@echo "  make docs-serve              live-reload docs preview"
	@echo "  make clean                   remove all dist/ directories + ./site"
	@echo ""

# ── Install / sync ────────────────────────────────────────────────────────────
.PHONY: install sync
install sync:
	uv sync

# ── Lint ──────────────────────────────────────────────────────────────────────
# §RL-6-ruff: whole repo (`.`) by default — covers tests/, testkit/, examples/,
# scripts/ and varco_casbin too, none of which the old $(_SRC_DIRS)-only scope
# reached. `PKG=` still narrows to that one package's source dirs for local
# iteration speed.
_LINT_TARGET := $(if $(PKG),$(_SRC_DIRS),.)

.PHONY: lint
lint:
	$(RUFF) check $(_LINT_TARGET)

# ── Format ────────────────────────────────────────────────────────────────────
.PHONY: format
format:
	$(RUFF) format $(_LINT_TARGET)
	$(RUFF) check --fix $(_LINT_TARGET)

# ── Type check ────────────────────────────────────────────────────────────────
.PHONY: type-check
type-check:
	$(MYPY) $(_SRC_DIRS)

# ── Unit tests ────────────────────────────────────────────────────────────────
# §RL-5-parity: delegates to scripts/unit_tests.sh, which accumulates
# pass/fail/skip across every suite instead of aborting on the first red
# package — the same script CI runs (bash scripts/unit_tests.sh), so a green
# `make test` means a green CI `unit` leg. `PKG=` narrows to one package
# (forwarded as a positional arg); unset runs all ten packages + the example
# suite.
.PHONY: test
test:
	@bash scripts/unit_tests.sh $(PKG)

# ── Integration tests ─────────────────────────────────────────────────────────
# `integration-test` honors any VARCO_TEST_*_URL override present in the
# environment (see scripts/integration_tests.sh's header and Open Question 1
# in plans/012-r3-reliability-and-regression-proofing.md). This is the
# developer-facing, override-honouring target — CI instead always calls
# `integration-test-clean` below via .github/workflows/integration.yml
# (push:main + nightly + workflow_dispatch), so a stray shell env var can
# never point a CI run at a non-clean-room broker.
.PHONY: integration-test
integration-test:
	@if [ -n "$(PKG)" ]; then \
		bash scripts/integration_tests.sh $(PKG); \
	else \
		bash scripts/integration_tests.sh; \
	fi

# `integration-test-clean` is the guaranteed clean-room entry point: every
# VARCO_TEST_*_URL override name is explicitly unset first, so the run always
# exercises fresh testcontainers-managed brokers/databases regardless of what
# is set in the calling shell.
.PHONY: integration-test-clean
integration-test-clean:
	@if [ -n "$(PKG)" ]; then \
		env -u VARCO_TEST_REDIS_URL -u VARCO_TEST_MONGO_URL -u VARCO_TEST_POSTGRES_URL \
			-u VARCO_TEST_KAFKA_URL -u VARCO_TEST_MEMCACHED_URL -u VARCO_TEST_NATS_URL \
			bash scripts/integration_tests.sh $(PKG); \
	else \
		env -u VARCO_TEST_REDIS_URL -u VARCO_TEST_MONGO_URL -u VARCO_TEST_POSTGRES_URL \
			-u VARCO_TEST_KAFKA_URL -u VARCO_TEST_MEMCACHED_URL -u VARCO_TEST_NATS_URL \
			bash scripts/integration_tests.sh; \
	fi

# ── Build ─────────────────────────────────────────────────────────────────────
.PHONY: build
build:
	$(foreach pkg,$(_TARGETS),uv build --package $(pkg) --out-dir $(pkg)/dist;)

# ── Publish ───────────────────────────────────────────────────────────────────
# Requires UV_PUBLISH_TOKEN to be set (or --token flag).
# Publishes wheels from every package's dist/ directory.
.PHONY: publish
publish:
	$(foreach pkg,$(_TARGETS), \
		if [ -d $(pkg)/dist ] && ls $(pkg)/dist/*.whl 1>/dev/null 2>&1; then \
			uv publish $(pkg)/dist/*; \
		fi; \
	)

# ── Docs ──────────────────────────────────────────────────────────────────────
# MkDocs Material + mkdocstrings. The API reference is generated from package
# docstrings at build time (scripts/gen_ref_pages.py); hand-written feature docs
# live in technical_docs/features/. Output goes to ./site (gitignored).
#
# DOCS_ENV silences the MkDocs-2.0 banners injected by mkdocs-material
# (NO_MKDOCS_2_WARNING) and by the properdocs fork pulled in transitively by the
# gen-files/literate-nav/section-index plugins (DISABLE_MKDOCS_2_WARNING). We pin
# mkdocs<2 in the docs dependency group so a future v2 can't silently break the build.
DOCS_ENV := NO_MKDOCS_2_WARNING=1 DISABLE_MKDOCS_2_WARNING=true

.PHONY: docs-deps
docs-deps:
	uv sync --group docs

# Normal build — produces ./site even while docstring coverage is still improving.
.PHONY: docs
docs: docs-deps
	$(DOCS_ENV) uv run mkdocs build

# Completeness gate — fails on any docstring/reference warning. This is the target
# the api-docs-maintainer agent drives toward (clean strict build = docs complete).
.PHONY: docs-strict
docs-strict: docs-deps
	$(DOCS_ENV) uv run mkdocs build --strict

.PHONY: docs-serve
docs-serve: docs-deps
	$(DOCS_ENV) uv run mkdocs serve

# ── Clean ─────────────────────────────────────────────────────────────────────
.PHONY: clean
clean:
	rm -rf $(foreach pkg,$(_TARGETS),$(pkg)/dist) dist/ site/
