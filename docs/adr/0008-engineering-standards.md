# ADR 0008 — Engineering standards: tests & docs are core

**Status:** Accepted
**Date:** 2026-08-18

## Context

farabunker is a security product whose value proposition is trust and auditability. That
demands quality-first engineering: code that is verified and documented, not just "working
on my machine." This ADR makes **unit tests and documentation non-negotiable, core parts of
every change** — a standing standard, not a phase.

## Decision

Every change (feature, fix, or refactor) MUST include, in the same commit / pull request:

1. **Unit tests** covering the change's logic, error paths, and the specific behavior added
   or fixed. Tests must pass before committing. External services (Ollama, and where
   practical the LLM/embedding calls) are **mocked** so the suite runs deterministically and
   offline; tests that need the database run against Postgres+pgvector.
2. **Documentation** updated in the same change: docstrings on new code, the relevant module
   README, an ADR for any architectural decision, and run/dev docs (`docs/DEV.md`) when the
   developer workflow changes.

**Tooling:** `pytest` + `pytest-django`. Run with `pytest` (or `.venv/bin/pytest`). Test
files live in `tests/` under each app (e.g. `modules/rag/tests/`).

**Orchestration rule:** every delegated task's specification includes "write passing tests
and update docs" — never a follow-up added after the fact.

## Consequences

- Slightly more work per change, bought back many times over in trust, refactorability, and
  fewer regressions — essential for a product sold on trustworthiness.
- CI (added later) will run the suite on every push; a change with failing or missing tests
  is not done.
- Coverage is expected to grow with the codebase; the bar is "the logic and edge cases of
  what you touched are tested," not a single global percentage gate (for now).

## Amendment (2026-09-13): a pinning policy

This ADR named no policy for dependency and base-image pinning, and the hardening pass found
the gap. The standard, going forward: runtime dependencies are floored (and ceilinged with a
reason) in `requirements.txt`, with the exact resolved set captured in a generated
`constraints.txt` that ships alongside it; container base images are pinned by digest, not a
moving tag, wherever the digest can be resolved without a disallowed network pull — an
unresolved one is recorded as an explicit owner TODO rather than guessed. `docs/DEV.md`
"Which Python" and "Bumping a dependency" carry the mechanics.
