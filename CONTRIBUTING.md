# Contributing to farabunker

Thanks for your interest in farabunker — a self-contained, offline-first platform for
running capable AI services with no dependency on the public internet. This guide covers how
to contribute and the one legal step (the CLA) that keeps the project sustainable.

> **[AGENTS.md](AGENTS.md) is the working-standards reference** — the branch and worktree
> model, how the tests are run, the verification ladder, the commit convention, and what
> "ready to merge" means. It is written for human contributors and AI agents alike; read it
> before your first change.

## Ground rules

- Be respectful — this project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- Never introduce a dependency on the public internet at runtime. farabunker's entire premise
  is offline operation and **default-deny egress** (see
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#2-security-posture-the-offline-spectrum)); a
  change that requires a live outbound connection to function will not be accepted.
- Keep modules within the [module contract](docs/ARCHITECTURE.md#5-the-module-contract):
  capability-scoped, sandboxed, talking only to the core.
- For security issues, do **not** open a public issue — see [SECURITY.md](SECURITY.md).

## Tests & documentation are required (not optional)

Every change ships with unit tests and the documentation it makes stale, in the same
commit — a core project standard, argued in
[ADR 0008](docs/adr/0008-engineering-standards.md).

- Test framework: **pytest + pytest-django**. Tests live under each app (e.g.
  `tools/rag/tests/`); external services are mocked, and DB-backed tests run against
  Postgres+pgvector (`docker compose up -d db`).
- The exact commands — an explicit `DATABASE_URL` against your own database, and the four
  runs that make a branch green — are [docs/DEV.md](docs/DEV.md) §8 rung 1, quoted in
  [AGENTS.md](AGENTS.md#quick-reference). A bare `.venv/bin/pytest` on the ambient database
  is not the branch gate.
- Some rules are enforced by tests rather than by review: `test_import_law.py`,
  `test_column_boundaries.py`, `test_css_ownership.py` and `test_agent_standards.py` under
  `foundation/ops/tests/` will fail your build if a change crosses one.

## The Contributor License Agreement (CLA)

farabunker is **dual-licensed** (open-source AGPL + a commercial license — see
[LICENSING.md](LICENSING.md)). For the project to offer both, every contribution must be
covered by our Contributor License Agreement.

By submitting a contribution you agree to the terms in [CLA.md](CLA.md). In short: **you keep
the copyright to your work, and you grant the project a broad, irrevocable license to use and
relicense it** (including under the commercial license). You are *not* assigning your
copyright away.

> **Why:** without this grant the project could not sustainably fund development, because
> contributed code could not be included in the commercial license. See
> [docs/adr/0003-dual-licensing-and-cla.md](docs/adr/0003-dual-licensing-and-cla.md).

**How it will work:** a CLA-assistant bot will ask first-time contributors to sign the CLA on
their first pull request (a one-time click, recorded against your account). Until that
automation is live, note in your PR that you have read and agree to [CLA.md](CLA.md).

> **Not yet, though.** [CLA.md](CLA.md) is a draft awaiting legal review, so there is no
> agreement in place for the project to accept outside work under. **External contributions
> are held, not merged, until that review finishes** — issues, bug reports and discussion are
> very welcome in the meantime, and a held pull request is not a rejected one.

## How to contribute

1. **Discuss first for anything non-trivial.** Open an issue describing the change so we can
   agree on the approach before you invest time — especially while the architecture is still
   settling.
2. **Fork and branch.** Create a topic branch off `dev` (e.g. `docs/clarify-airlock`,
   `feat/inference-gateway`) — `dev` is the long-lived integration branch; pull requests land
   there, never directly against `main`.
3. **Make focused changes.** One logical change per pull request, and commit subjects in this
   repository's `type(scope): subject` form — see
   [AGENTS.md](AGENTS.md#code-style-and-commits).
4. **Smoke-test on a preview stack before opening the PR.** Bring up an isolated preview of
   your branch (`scripts/preview up <branch>` — see
   [docs/DEV.md](docs/DEV.md#testing-a-branch-before-merge) and
   [ADR 0011](docs/adr/0011-branch-preview-stacks.md)) and drive its plan's `## Smoke
   Checklist` by hand in a browser.
5. **Open a pull request** when the checklist in [AGENTS.md](AGENTS.md#merge-readiness) is
   satisfied. Describe *what* and *why*, link the issue, and confirm CLA agreement. Note its
   last item in particular — the consolidation audit.

## Developer setup

[docs/DEV.md](docs/DEV.md) is the full path from a clone to a running stack: the model
server, the Python environment, the environment file, the containers, assigning a model to
each role on first run, and the test suite. [README.md](README.md#quickstart) has the short
version.
