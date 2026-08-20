# Makefile — varco monorepo
# All commands run from the workspace root. Requires: uv (https://docs.astral.sh/uv/)
#
# Quick reference:
#   make install          — sync all workspace deps
#   make lint             — ruff check (all packages)
#   make format           — ruff format (all packages)
#   make type-check       — mypy (all packages)
#   make test             — unit tests (all packages)
#   make test PKG=varco_core — unit tests for one package
#   make integration-test — integration tests (requires Docker; honors any
#                            VARCO_TEST_*_URL override present in the shell)
#   make integration-test PKG=varco_redis — integration tests for one package
#   make integration-test-clean — integration tests, guaranteed clean-room
#                            (unsets every VARCO_TEST_*_URL override first)
#   Nothing under `make integration-test*` runs in CI, by design — see
#   BACKLOG.md:50-56 and the Non-goals section of
#   plans/012-r3-reliability-and-regression-proofing.md.
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
	varco_fastapi

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
RUFF  := uvx ruff
MYPY  := uv run mypy

# ─────────────────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@echo ""
	@echo "  varco — available targets"
	@echo ""
	@echo "  make install                 sync all workspace deps"
	@echo "  make sync                    alias for install"
	@echo "  make lint                    ruff check (all packages)"
	@echo "  make lint PKG=varco_redis    ruff check (one package)"
	@echo "  make format                  ruff format + fix (all packages)"
	@echo "  make format PKG=varco_redis  ruff format + fix (one package)"
	@echo "  make type-check              mypy (all packages)"
	@echo "  make test                    unit tests (all packages)"
	@echo "  make test PKG=varco_core     unit tests (one package)"
	@echo "  make integration-test        integration tests (requires Docker; honors"
	@echo "                                VARCO_TEST_*_URL overrides if set)"
	@echo "  make integration-test PKG=varco_redis  integration tests (one package)"
	@echo "  make integration-test-clean  integration tests, clean-room (unsets every"
	@echo "                                VARCO_TEST_*_URL override first)"
	@echo "                                — nothing here runs in CI by design"
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
.PHONY: lint
lint:
	$(RUFF) check $(_SRC_DIRS)

# ── Format ────────────────────────────────────────────────────────────────────
.PHONY: format
format:
	$(RUFF) format $(_SRC_DIRS)
	$(RUFF) check --fix $(_SRC_DIRS)

# ── Type check ────────────────────────────────────────────────────────────────
.PHONY: type-check
type-check:
	$(MYPY) $(_SRC_DIRS)

# ── Unit tests ────────────────────────────────────────────────────────────────
# Each package is tested in its own directory so pytest picks up the package's
# pyproject.toml (asyncio_mode = "auto", testpaths, etc.) rather than the root.
.PHONY: test
test:
	@$(foreach pkg,$(_TARGETS), \
		echo "── testing $(pkg) ──────────────────────────────────────────"; \
		(cd $(pkg) && uv run pytest tests/ -v) || exit 1; \
	)

# ── Integration tests ─────────────────────────────────────────────────────────
# `integration-test` honors any VARCO_TEST_*_URL override present in the
# environment (see scripts/integration_tests.sh's header and Open Question 1
# in plans/012-r3-reliability-and-regression-proofing.md). Nothing here runs
# in CI by design — .github/workflows/integration.yml is intentionally inert
# (BACKLOG.md:50-56).
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
