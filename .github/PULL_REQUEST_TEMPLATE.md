## Summary

<!-- What does this PR change, and why? -->

## Checklist

- [ ] Docs updated **in this commit** (README.md / CLAUDE.md / a `technical_docs/features/*.md`
      page), not a follow-up — see CLAUDE.md's doc-update rule.
- [ ] Tests added for every new code path (unit tests via `InMemoryEventBus`/
      `InMemoryDeadLetterQueue`; integration tests, marked `@pytest.mark.integration`, for
      anything touching a real broker/DB).
- [ ] `make lint` passes (`ruff check .` + `ruff format --check .`).
- [ ] `make type-check` passes (`mypy`, ten source dirs).
- [ ] `make test` passes (all eleven unit-test suites).
- [ ] `CHANGELOG.md` entry added under `## [Unreleased]`, in the right section
      (`Added`/`Changed`/`Fixed`/`BREAKING`).
- [ ] Relevant `BACKLOG.md` row referenced (or a new one filed) if this closes or advances a
      tracked item.
- [ ] If this changes a public `__all__` or a function's signature:
      `uv run python scripts/api_surface.py` was re-run and the regenerated snapshot is included.
- [ ] If this is a breaking change: `removed_in=` is set per `CONTRIBUTING.md`'s versioning policy
      and a `deprecated`/`deprecated_alias` shim is in place unless a plain code comment in this PR
      explains why one is impossible (e.g. a changed default value).
