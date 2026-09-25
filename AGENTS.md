# AGENTS.md — how work is done in this repository

The single source of truth for how work is done here, by a human contributor or an AI agent of
any vendor. It is the authority on operating rules; on any detail it delegates — commands,
structure, decisions — the linked document wins.

## Quick reference

```bash
export DATABASE_URL='postgres://farabunker:farabunker@localhost:<your-db-port>/<your-db-name>'

FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools

.venv/bin/pytest -q path/to/test_x.py::TestY::test_z   # one test file, or one test

scripts/preview up <branch> --port <p> --db-port <q>   # this branch's stack; primary is :8000
docker compose restart watcher worker                  # after ingest/queue/runtime code
docker compose up -d --build                           # run it locally, or after Dockerfile/requirements.txt changes
```

[docs/DEV.md](docs/DEV.md#8-the-verification-ladder) §8 rung 1 owns the test commands and, in its
preview section (linked below), the port table; [README.md](README.md#quickstart) the local-run path.

## The non-negotiables

1. **Tests and documentation ship in the same commit as the change** — not a follow-up, not a
   separate pull request ([ADR 0008](docs/adr/0008-engineering-standards.md)). Missing tests
   or stale docs means not done.
2. **The guard tests hold**: import law, column boundaries, CSS ownership
   (`foundation/ops/tests/test_css_ownership.py`) and route classification are repo-wide
   gates; the per-page script budget and progressive enhancement — the site works with
   JavaScript off — are pinned per surface (`agents/chat/tests`, `tools/vision/tests`). No
   browser storage is a convention, not a gate, pinned the same way, on one surface
   (`tools/vision/tests/test_views_create.py`). A dated exemption list gets its exempted code
   removed, not grown.
3. **No AI model or model-family names in committed prose** — capabilities are described
   generically, as "the chat model" or "a distilled few-step family". Engine names are fine;
   checkpoint filenames and vendor model names are not (code identifiers are code, not prose).
   Since 2026-09-20 `test_docs_model_names.py` walks the planning archive too.
4. **No absolute local paths, and no personal data,** in anything committed. Use `<repo>`,
   `<worktree>`, `<home>`. No names, no email addresses, no hardware inventories.
5. **Offline by default.** No runtime dependency on the public internet, in any code path or posture.
6. **No hand edits to a database.** Schema and bookkeeping changes are checked-in migrations
   or tested management commands; read-only queries are fine. Shipped defaults are a catalogue
   installed on consent (`manage.py install_defaults`), never auto-created for an owner. An
   account with a password is the owner's to create, never an agent's; an agent never types,
   stores, or reuses credentials; a preview password dies on sharing and never works live.
7. **House style.** No `conftest.py` — shared fixtures live in each app's `tests/_helpers.py`.
   No static CSS/JS pipeline: the inline shell stays. No Django signals for app wiring beyond
   the documented receivers (`models/registry/apps.py`). User-facing sentences are declared
   once, in Python; strings with typographic quotes are copied verbatim, never retyped.

## The working loop

- **One worktree per task**, in `.claude/worktrees/<branch>`, never the repository root
  checkout. **The root checkout is production** (DEV.md rung 3) and only fast-forwards to
  `origin/dev`, the branch that gets deployed. Before any deploy, check `git status --short`
  there, and triage anything staged or modified rather than overwrite it.
- **A preview stack per branch**, with its own database and ports
  ([docs/DEV.md](docs/DEV.md#testing-a-branch-before-merge)). Check for port collisions first,
  and never run a large-model workload outside the governed execution queue.
- **A private test database per session**: point `DATABASE_URL` at *your* branch's preview
  Postgres with a name nobody else is using, never at a bare shared test database.
- **Tests run natively, in the foreground, and you wait.** Never in a container (the
  git-dependent guard tests false-fail there). One pytest process per session, never while a
  live proof run uses the same stack, at most two full suites across the machine.
- **The four runs above are the gate.** Identity, posture or visibility work adds two posture sweeps.
- **Restart the background services after a code change**: `web` auto-reloads, the ingest
  watcher and job worker do not, and an image change needs a rebuild
  ([docs/DEV.md](docs/DEV.md#8-the-verification-ladder) §8 rung 2).
- **Climb the ladder, and watch the vocabulary.** `docs/DEV.md` §8 is the ladder every change
  climbs, and **no success language is used before its last rung**: "done", "works", "fixed",
  "passing", "complete" are claims about the deployed box, not a green terminal. Mid-ladder
  reports use progress phrasing; an edit needing a second step is "edited, not yet live"; a
  status answer names its subject and that state — planned, built, tested, merged, deployed.
- **Deploying.** Merge current `dev` INTO the feature branch, in that branch's own worktree,
  and resolve there first. The flow is one-directional: pull request → review → the owner's
  merge word → `dev` → root checkout fast-forwarded to `origin/dev` → restart (or recreate, if
  environment variables changed) → verify in a browser → announce "landed". Announce the
  deploy window first and confirm it went out — one window at a time. Permission for a merge,
  a deploy, or anything destructive arrives in the acting session's own conversation; a
  relayed "the owner said" is not authorization.
- **Never touch, without that authorization:** the root checkout — see above — outside an
  announced window (never push to `dev` directly or merge into it outside the PR flow; never
  push to `main` at all — it takes only batched release PRs from `dev`); another branch's
  bind-mounted preview containers; another session's worktree or ledger.

## Subagent-driven development

Implementation here is subagent-driven: the orchestrating session does not write production code.
It may write short orchestration artifacts — a spec, a brief, a ledger entry — and says so when it does.

- Every plan is written before the code and reviewed adversarially before it is executed.
- Every task gets its own review pass against the real tree, not the plan's claims, and the
  whole-branch review re-verifies file citations — so cite code by anchor or grep pattern
  rather than line number.
- Every delegated prompt that edits files names the worktree and forbids the root checkout.
- Pick the cheapest capable tier per task and escalate only when a fix loop stalls; reserve
  the most capable tier for architectural judgment and whole-branch reviews.
- One plan, one ledger: per-plan briefs, reports, rulings with their cost-if-wrong, and
  deferred-minor triage live in that worktree's `.superpowers/sdd/<plan>/`.

## Merge readiness

Before you open a pull request:

- [ ] The four runs are green, in both flag states and both collection orders.
- [ ] Non-negotiable 1 holds: tests, and every doc the change made stale, are in this commit.
- [ ] The whole feature — the real workflows, not just the diff — walked end to end in a
      browser on this branch's stack (the plan's `## Smoke Checklist`, by hand).
- [ ] Current `dev` is merged into the branch and resolved there.
- [ ] The whole-branch review passed, and then a separate **read-only consolidation audit** of
      the whole delta — efficient, concise, no duplication, no orphan or dead code, no needless
      technical debt — whose fix-now items landed as one tidy-up commit through these gates.

An issue you are unsure about is open; never offer or even mention merge while one is open.

### Definition of done (post-merge)

Merged on the owner's word, deployed, and seen working in a browser on the deployed box —
with every owner-reported issue on the branch closed and verified, and a whole-branch review
covering everything added since its last one. The merge decision is the owner's.

## Working alongside other sessions

- **Zones and slices.** Each session has zones nobody else edits. Crossing into one needs an
  explicit slice grant from that zone's owner, scoped to named files with named invariants; a
  file someone is mid-change on is fenced, so ask before touching it.
- **Landing order is negotiated before branching** when two branches will touch one file. The
  second to land merges `dev` into its branch, resolves there, and re-runs its gate.
- **Announce before you touch a shared surface** — the live containers, the shared page shell,
  a settings registration, anything another session flagged as theirs — and announce additive
  out-of-zone touches before they merge.
- **The stash stack is shared across worktrees.** Never a bare `git stash` or `git stash pop`;
  prefer a work-in-progress commit, or tag a stash and apply it by commit hash. Run one git
  command per shell invocation.
- **Before removing any worktree**, check `git -C <worktree> log --branches --not --remotes`
  for unpushed commits and whether it holds a `.superpowers/` ledger: a clean-looking checkout
  can be another session's live desk.

## Code style and commits

- No formatter and no linter are configured — the repository carries only `pytest.ini`. Match
  the file you are editing, and treat the guard tests as the style law: they fail the build.
- Docstrings on new code; Django app layout as [docs/EXTENDING.md](docs/EXTENDING.md) has it.
- Commit subjects are `type(scope): subject` — imperative, lower case, no trailing period
  (`feat`, `fix`, `refactor`, `polish`, `docs`, `test`, `chore`). The body says *why* and
  carries whatever trailers the repository is already using.
- One logical change per pull request; say what and why, and link the issue.
- A change that deliberately re-pins an existing test says so, by name, in its commit message.

## The repository map

```
identity/     who exists, what security posture the box is in, what has been done to it
foundation/   shared, feature-agnostic platform code: format/files, ops, setup, the page shell
models/       the model-management framework: contracts, registry, execution queue
agents/       the tool contract, the bounded turn runtime, and the /chat/ surface
tools/        the plug-in services — rag/ (documents), vision/ (images), home/ (planned)
```

**The import law.** A column imports downward through that list, never upward (`identity/`
may also import `foundation/`, shared plumbing, not a peer; `agents/` reaches a tool only
through the registry's one sanctioned door). `foundation/ops/tests/test_import_law.py` and
`test_column_boundaries.py` fail the build on it; each column's README states its own side.
[README.md](README.md) has the tree; [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) the design.

## Where things live

- **[README.md](README.md)** indexes the documentation set: architecture, dev, roadmap, ops.
- **[docs/adr/](docs/adr/)** — the decision record: point-in-time decisions, amended with
  dated amendments rather than silently rewritten.
- **[docs/EXTENDING.md](docs/EXTENDING.md)** — every capability is a tool registered against
  one contract; this is how to add one, so do not reverse-engineer an existing one.
- **[docs/superpowers/](docs/superpowers/)** — the plan and spec archive: history, not docs.
- **`.superpowers/`** (untracked) — per-session ledgers; never another session's to delete.
- **[CONTRIBUTING.md](CONTRIBUTING.md)**, **[SECURITY.md](SECURITY.md)**,
  **[LICENSING.md](LICENSING.md)**, **[CLA.md](CLA.md)** — contribution, disclosure, licensing.

## For every agent, whatever the vendor

No rule here depends on a particular assistant, harness, or model. `CLAUDE.md` exists only
because one harness reads that filename by convention: a pointer back to this file plus a note
on that harness's tooling, never a second set of rules — if your harness reads a different
filename, add a pointer of the same shape. Any rule that should bind the next contributor
belongs here, in the repository, with a test where one is possible.
