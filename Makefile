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
#   make integration-test — integration tests (requires Docker)
#   make build            — build wheels for all packages
#   make build PKG=varco_redis — build one package
#   make publish          — publish all dist/* to PyPI (requires UV_PUBLISH_TOKEN)
#   make clean            — remove all dist/ directories

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
	@echo "  make integration-test        integration tests (requires Docker)"
	@echo "  make build                   build wheels + sdists (all packages)"
	@echo "  make build PKG=varco_redis   build one package"
	@echo "  make publish                 publish dist/* to PyPI"
	@echo "  make clean                   remove all dist/ directories"
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
.PHONY: integration-test
integration-test:
	@if [ -n "$(PKG)" ]; then \
		bash scripts/integration_tests.sh $(PKG); \
	else \
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

# ── Clean ─────────────────────────────────────────────────────────────────────
.PHONY: clean
clean:
	rm -rf $(foreach pkg,$(_TARGETS),$(pkg)/dist) dist/
