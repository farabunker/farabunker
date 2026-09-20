# Agent Standards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make this repository self-describing for any contributor — human or AI agent, any
vendor — by distilling the operating rules that currently live only in one session's private
memory into committed, vendor-neutral documentation, with guard tests that keep it true.

**Architecture:** One root `AGENTS.md` is the single source of truth; `CLAUDE.md` is a
four-line pointer plus the Claude-specific paragraph; `docs/superpowers/README.md` explains
what the planning archive is; `CONTRIBUTING.md`/`SECURITY.md`/`README.md` stop contradicting
it. Every claim any of those files makes that can be mechanically checked is pinned by one
new gate module, `foundation/ops/tests/test_agent_standards.py`, which sits beside the
repository's existing structural gates (`test_import_law.py`, `test_column_boundaries.py`,
`test_css_ownership.py`, `test_docs_sync.py`) for the same reason they live there:
`foundation/ops/` is the column with no dependents, so a repo-wide walk cannot become a
cyclic import.

**Tech Stack:** Markdown, `.gitignore`, one committed `.claude/settings.json`, and
`pytest` + `pytest-django` for the gates. No production Python is touched.

**Spec:** There is no separate spec document. This plan is the spec: the rules it commits
were gathered from (a) the orchestrating session's own operating memory, (b) written replies
from three concurrently active peer sessions, and (c) the repository's existing standards
documents — `docs/adr/0008-engineering-standards.md`, `docs/DEV.md` §7–8,
`docs/ARCHITECTURE.md` §4–5, `docs/EXTENDING.md`. Where this plan and those documents
disagree, those documents win and this plan is the bug.

---

## Global Constraints

Every task's requirements implicitly include this section.

1. **Worktree only.** All work happens in the `agent-standards` worktree at
   `.claude/worktrees/agent-standards` (referred to below as `<worktree>`). **Never** edit,
   stage, or run git commands against the repository root checkout: that checkout is
   bind-mounted into the live containers and is production. Prefix every git command with
   `-C <worktree>`.
2. **Docs and config only. No production code changes.** The only non-Markdown files this
   plan creates or modifies are `.gitignore`, `.claude/settings.json`, and one new test
   module under `foundation/ops/tests/`. No file under `agents/`, `models/`, `tools/`,
   `identity/`, `config/`, or `scripts/` is touched.
3. **Tests may be added only as gates.** New tests live in exactly one file,
   `foundation/ops/tests/test_agent_standards.py`, and assert facts about the repository
   tree — never about application behaviour.
4. **No AI model or vendor-model names in committed prose.** The repository is going public
   and describes capabilities generically ("the chat model", "a distilled few-step family").
   Engine names (the inference server, the image-generation server, the transcription
   server) are fine; model family names and checkpoint filenames are not. This binds every
   word this plan commits.
5. **No absolute machine paths in committed files.** Not `/Users/...`, not `/home/...`.
   Use `<repo>`, `<worktree>`, `<home>` placeholders. This binds this plan file itself —
   it is committed under `docs/` and Task S3's gate will scan it.
6. **No personal data.** No owner name, email address, machine model, hardware inventory,
   port numbers tied to one machine, or incident narratives naming a person. Rules are
   stated as rules, not as stories about who broke what.
7. **Docs and tests ship in the same commit** as the change they describe
   ([ADR 0008](docs/adr/0008-engineering-standards.md)). This plan's own commits obey it.
8. **Do not commit to `main`, do not push, do not open the PR** as part of executing this
   plan. Commit on the `agent-standards` branch only; the merge decision is the owner's.
9. **Never run a test suite inside a container** and never against a shared test database.
   Run natively, foreground, one process at a time, with `DATABASE_URL` pointed at this
   branch's own preview Postgres and a database name nobody else is using.
10. **Do not touch any other worktree**, any other session's preview containers, or any
    database other than your own test database.

---

## Relationship to the hardening plan

`docs/superpowers/plans/2026-09-10-hardening.md` (which lives on the `worktree-hygiene-sweep`
branch, not on `main`) contains two tasks that overlap this plan. **This plan takes them
over; they must be reduced, not executed twice.** Whoever executes the hardening plan should
apply these reductions first:

- **H2 — "the build context and the commit context stop carrying secrets"** is taken over by
  **Task S2** here, *except for its `.dockerignore` half*. S2 lands the `.gitignore` rules
  (`.claude/*` + the `!.claude/settings.json` negation, `.superpowers/`, `.DS_Store`), the
  `git rm --cached` untracking of `.superpowers/owner-requirements/`, and the two gates that
  pin both. **H2 reduces to:** create `.dockerignore` and the single test
  `test_the_docker_build_context_excludes_every_secret_and_data_path`, in
  `foundation/ops/tests/test_repo_hygiene.py`, **plus** its
  `test_the_repo_hygiene_sweep_is_reading_real_files` with the `_tracked_files()` half
  dropped — that half is implemented here, but its `len(_dockerignore_lines()) >=
  len(_REQUIRED_DOCKERIGNORE_PATTERNS)` half is the only anti-vacuity pin the
  `.dockerignore` parse has, and this plan implements no `.dockerignore` check at all. Its
  `_gitignore_lines` / `_tracked_files` helpers go with the tests that leave.
  **H2 must delete `test_the_agent_working_directories_are_gitignored` outright**: it asserts
  the bare `.claude/` form and Task S2 installs `.claude/*`. That is not duplication that
  drifts, it is a test that fails the moment both land.
  `.dockerignore` is deliberately left with H2: it changes what a `docker build` actually
  copies into the image, which is a production-behaviour change and outside this plan's
  Global Constraint 2.
- **H19 — "the public front door"** is taken over **entirely**, split across **Task S4** (the
  `CONTRIBUTING.md` and `SECURITY.md` Phase-0 banners, the `README.md` quickstart and its
  `docs/DEV.md` link) and **Tasks S3 + S5** (the absolute-path gate and the scrub of the
  historical planning documents). **H19 reduces to nothing and should be struck**, with one
  carry-over: H19 correctly noted that the three plan files being executed on the hygiene
  branch (`2026-09-09-hygiene-sweep.md`, `2026-09-10-hygiene-sweep-wp10.md`,
  `2026-09-10-hardening.md`) carry *operative* absolute paths and must be scrubbed when that
  branch closes. Those three files are not on `main` and are therefore not in this plan's
  scope; the hygiene branch must scrub them before it merges, **or Task S3's gate will fail
  on that branch the moment it merges `main`.** Say so in that branch's own follow-up list.
  **Second carry-over:** H19 also flagged that H3 (`POSTGRES_PASSWORD` in `.env.example`)
  and H14 (a `chown` on the data directories) change the first-run experience. Task S4's
  `README.md` quickstart is correct against the tree as it stands today; if H3 or H14 lands
  afterwards, that quickstart and
  `test_the_readme_offers_a_way_to_run_the_thing` must be re-verified **in the same commit
  that lands them**. Put that on the hardening branch's follow-up list too.

Nothing else in the hardening plan is affected.

---

## File structure

| File | Status | Responsibility |
|---|---|---|
| `AGENTS.md` | create | The single source of truth. Vendor-neutral. Links out; duplicates nothing. |
| `CLAUDE.md` | create | A pointer to `AGENTS.md` plus the Claude-specific paragraph. |
| `.claude/settings.json` | create | The committed, conservative, read-only permission allowlist every session inherits. |
| `.gitignore` | modify | Ignore `.claude/*` except `settings.json`; ignore `.superpowers/`; ignore `.DS_Store`. |
| `docs/superpowers/README.md` | create | What the planning archive is, and what it is not. |
| `CONTRIBUTING.md` | modify | Replace the stale Phase-0 banner; link `AGENTS.md` as the working-standards reference. |
| `SECURITY.md` | modify | Replace the stale Phase-0 supported-versions paragraph. |
| `README.md` | modify | A `## Quickstart` section, a `docs/DEV.md` link, and a one-line pointer to `AGENTS.md`. |
| `foundation/ops/tests/test_agent_standards.py` | create, then extended by S2–S5 | Every mechanically checkable claim the files above make. |
| 11 files under `docs/superpowers/plans` and `specs` | modify (S5) | Absolute local paths replaced with placeholders. |

---

## Peer-contributed rules

Three concurrently active sessions were asked what rules they operate under, and replied in
writing on 2026-09-11. Their rules are **already merged into the Task S1 draft below** —
there is no placeholder left to fill. Two mechanical cautions from those replies are carried
into Task S2 and are called out there:

1. A bare `.claude/` ignore rule cannot be negated for a file inside it — use `.claude/*`
   plus `!.claude/settings.json`. (Verified empirically; see S2 Step 4.)
2. The committed `.claude/settings.json` binds every session's tooling posture, so the PR
   body must call that file's diff out explicitly for a line-by-line read. No session should
   inherit permission-adjacent configuration silently.

Their reply also settles Task S5's skip list: **it is empty for files tracked on `main`.**
The peer branches' own plan and spec files are branch-only and their owners will scrub them
on rebase, and nothing in any peer worktree depends on a tracked path under `.superpowers/`
or `docs/superpowers/`. So S5 scrubs every one of the 11 merged files.

---

### Task S1: `AGENTS.md` — the single source of truth

**Files:**
- Create: `AGENTS.md` (repo root)
- Create: `foundation/ops/tests/test_agent_standards.py`
- Modify: `docs/DEV.md` (§8 rung 1 — state the four-run branch gate explicitly, so the
  document `AGENTS.md` defers to actually says the thing `AGENTS.md` defers about)

**Interfaces:**
- Produces: `AGENTS.md` at the repo root, containing the nine `## ` headings listed in
  `_REQUIRED_AGENTS_HEADINGS` below. Tasks S2–S5 extend the same test module; do not rename
  it, and do not add a `conftest.py` (this repository forbids them anywhere — see
  `foundation/ops/tests/_helpers.py`'s docstring).
- Consumes: `foundation.ops.tests._helpers.REPO_ROOT`, which already exists and resolves to
  `Path(settings.BASE_DIR)`.

**Why one file at the root.** `AGENTS.md` at the repository root is the convention a growing
number of agent harnesses read first, and a human opening the repo sees it beside `README.md`.
Everything vendor-specific is pushed into `CLAUDE.md` (Task S2) so that this file never has
to be read past by a contributor using different tooling.

**Keep it short.** The draft below is ~230 lines. It links out rather than duplicating:
`docs/DEV.md` owns the gates and the run matrix, `docs/ARCHITECTURE.md` owns the tree,
`docs/EXTENDING.md` owns how to add a tool, ADR 0008 owns the tests-and-docs rule. If you
find yourself explaining one of those here, link it instead.

- [ ] **Step 1: Write the failing test**

Create `foundation/ops/tests/test_agent_standards.py`:

```python
"""Invariants about the repository's own standards documents.

A sibling of `test_import_law.py`, `test_column_boundaries.py`,
`test_css_ownership.py` and `test_docs_sync.py`: those walk the tree to
enforce a rule about the code; this one walks it to enforce a rule about
what the tree TELLS a new contributor. All of them live in
`foundation/ops/tests/` because `foundation/ops/` is the column with no
dependents, so a repo-wide walk cannot become a cyclic import.

These are gates, not behaviour tests -- nothing here imports application
code or touches the database.
"""
from __future__ import annotations

import re

from foundation.ops.tests._helpers import REPO_ROOT

# An absolute path out of somebody's home directory -- the thing no
# committed document may name, because it is noise to every reader on a
# different machine and it names a person and a machine.
#
# Two guards make it usable on this repository's prose:
#
# * The LOOKBEHIND requires the separator to BEGIN a path. `tools/home/`
#   is a column here and `modules/home/` appears all over the planning
#   archive; in both, `/home/` FOLLOWS a word character, so neither
#   matches.
# * The TRAILING SEGMENT requires a real user name followed by more path.
#   So a document may still name the shape it is forbidding -- a
#   placeholder (`/Users/<owner>/src`), a bare mention (`/Users/`), or an
#   ellipsis (`/Users/...`) all pass, and only a genuine path fails.
#
# The `:` in the lookbehind means a `file:///Users/name/...` URL form is
# deliberately NOT matched -- no document here uses one, and excluding
# `:` is what keeps `http://…/home/…` style text out.
_ABSOLUTE_HOME_PATH = re.compile(r"(?<![\w/.:-])/(?:Users|home)/[A-Za-z0-9_.-]+/")

# The branch gate, declared once here and quoted in two documents.
# `docs/DEV.md` §8 rung 1 owns it; `AGENTS.md` repeats it because it is the
# one thing a newcomer needs before their first commit. This constant is
# what stops the two copies drifting -- the same trick `test_docs_sync.py`
# plays on the backup and restore sequences.
_FOUR_RUN_MATRIX = "\n".join((
    "FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q",
    "FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q",
    "FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools",
    "FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools",
))

# The spine of AGENTS.md. A heading may gain text after it, but the
# section must exist: each one is a promise some other document makes
# ("see AGENTS.md for the working loop") and a heading that quietly
# disappears turns that promise into a dead reference.
_REQUIRED_AGENTS_HEADINGS = (
    "## What this repository is",
    "## The non-negotiables",
    "## The working loop",
    "## Subagent-driven development",
    "## Merge readiness",
    "## Working alongside other sessions",
    "## Where things live",
    "## Adding a tool",
    "## If you are not Claude",
)


def _read(relative: str) -> str:
    path = REPO_ROOT / relative
    assert path.exists(), f"{relative} is missing"
    return path.read_text(encoding="utf-8")


class TestAgentsFile:
    def test_agents_md_exists_and_has_every_required_section(self):
        text = _read("AGENTS.md")
        missing = [h for h in _REQUIRED_AGENTS_HEADINGS if h not in text]
        assert missing == [], missing

    def test_agents_md_names_no_absolute_local_path(self):
        """Global Constraint 5. This file is read by strangers on other
        machines; a path out of one developer's home directory is noise
        to every one of them."""
        text = _read("AGENTS.md")
        offenders = [
            f"{number}: {line.strip()}"
            for number, line in enumerate(text.splitlines(), 1)
            if _ABSOLUTE_HOME_PATH.search(line)
        ]
        assert offenders == [], offenders

    def test_agents_md_stays_short_enough_to_be_read(self):
        """It is the single source of truth only while people actually
        read it to the end. Past roughly 250 lines it becomes a reference
        document nobody opens, and the rules move back into folklore.

        The ceiling is 275, not the ~240 the file ships at: this file
        invites additions in its own closing section, and a cap that two
        new rules trip is a cap that gets raised thoughtlessly instead of
        making anyone cut. When it does trip, CUT -- summarise and link
        out. Do not raise it again."""
        assert len(_read("AGENTS.md").splitlines()) <= 275

    def test_the_branch_gate_is_identical_in_both_places(self):
        """AGENTS.md quotes docs/DEV.md rung 1 verbatim. A gate command
        that drifts between the two is worse than one that lives in a
        single place, because both look authoritative."""
        assert _FOUR_RUN_MATRIX in _read("docs/DEV.md")
        assert _FOUR_RUN_MATRIX in _read("AGENTS.md")


class TestTheLocalPathPattern:
    def test_it_distinguishes_a_real_path_from_a_directory_name(self):
        """`tools/home/` is a column in this repository and appears on
        hundreds of lines; matching it would make every gate built on
        this pattern unusable.

        The two positive fixtures are assembled from pieces on purpose,
        so that this module -- and the plan document that quotes it --
        do not themselves contain the thing they forbid."""
        user_path = "/Users/" + "someone/src/project"
        home_path = "/home/" + "someone/.venv/bin/pytest"

        assert not _ABSOLUTE_HOME_PATH.search("see tools/home/README.md")
        assert not _ABSOLUTE_HOME_PATH.search("`modules/home/README.md` moves")
        assert not _ABSOLUTE_HOME_PATH.search("run it from /Users/<owner>/src")
        assert not _ABSOLUTE_HOME_PATH.search("no /Users/... paths, please")

        assert _ABSOLUTE_HOME_PATH.search(f"cd {user_path}")
        assert _ABSOLUTE_HOME_PATH.search(f"`{home_path}`")
```

`TestTheLocalPathPattern` is the only proof the pattern is not vacuous — a regex that
matched nothing would make `test_agents_md_names_no_absolute_local_path` and Task S3's
whole-`docs/` walk both pass forever. It lands here, with the pattern, rather than in S3.

- [ ] **Step 2: Run the test and watch it fail**

```bash
export DATABASE_URL='postgres://farabunker:farabunker@localhost:<your-preview-db-port>/farabunker_<your-role>'
.venv/bin/pytest -q foundation/ops/tests/test_agent_standards.py
```

Expected: the three `TestAgentsFile` tests FAIL with `AGENTS.md is missing`;
`TestTheLocalPathPattern` PASSES, since it depends on no file at all. If the pattern test
fails, fix the pattern before writing a line of prose — every later gate is built on it.

- [ ] **Step 3: Write `AGENTS.md`**

Create `AGENTS.md` at the repository root with exactly this content:

````markdown
# AGENTS.md — how work is done in this repository

This is the single source of truth for anyone picking this repository up: a human
contributor, or an AI agent of any vendor. Read it before your first edit. It links out
rather than repeating; where a linked document and this one disagree, the linked document
wins and this file is the bug.

## What this repository is

farabunker is a self-contained, offline-first platform for running capable AI services with
no dependency on the public internet. Documents go in and grounded, cited answers come out;
images are generated locally; a conversational agent drives every capability through one
tool contract — all on hardware the operator controls. It is a Django application over
Postgres with a vector extension, talking to model servers that run natively on the host
(containers on some platforms cannot reach the GPU), with a Postgres-backed execution queue
admitting every model run so nothing oversubscribes the machine. The premise is *assume the
network is compromised, so remove the network*: a change that needs a live outbound
connection in order to function will not be accepted. See [README.md](README.md) for the
pitch and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

The tree is five columns, base first, plus the project scaffolding:

```
identity/     who exists, what security posture the box is in, what has been done to it
foundation/   shared, feature-agnostic platform code: format/files, ops, setup, the page shell
models/       the model-management framework: contracts, registry, execution queue
agents/       the tool contract, the bounded turn runtime, and the /chat/ surface
tools/        the plug-in services — rag/ (documents), vision/ (images), home/ (planned)
config/       Django settings and the URL root
scripts/      developer and operator tooling (preview stacks, test helpers)
posture/      network and OS posture profiles
deploy/       provisioning for a box
docs/         architecture, decisions, roadmap, operations, and the planning archive
```

**The import law.** A column may import from the columns above it in that list and never
from the ones below. `agents/` never imports `tools.*` — it reaches a tool through the
registry and a dotted-path seam. `models.registry.models`, `models.registry.views` and
`models.queue.models` are off-limits to every column outside `models/` — with one scoped
exception, named and argued at its own call site — and `models.registry.bindings` is the one
sanctioned door.
These are not conventions, they are enforced: `foundation/ops/tests/test_import_law.py` and
`test_column_boundaries.py` fail the build. Read those two files before arguing with them —
each rule is documented at its own call site with the reason it exists.

## The non-negotiables

1. **Tests and documentation ship in the same commit as the change.** Not a follow-up, not
   a separate PR. Unit tests for the logic, the error paths, and the specific thing added or
   fixed; plus the docstrings, module README, ADR, and `docs/DEV.md` section the change makes
   stale. See [ADR 0008](docs/adr/0008-engineering-standards.md). A change with missing tests
   or stale docs is not done.
2. **The import law and the other guard tests hold.** Import law and column boundaries, CSS
   ownership (a selector's home is the deepest template ancestral to every consumer), route
   classification (every new route gets a class in `identity/routes.py`; unknown fails
   closed), the sanctioned-script budget, and progressive enhancement — the app works with
   JavaScript off, and no feature depends on browser storage. Every one of these has a test.
   When a gate has a dated exemption list, prefer removing the exempted code over growing
   the list.
3. **No AI model or model-family names in committed prose.** This repository is going
   public and describes capabilities generically — "the chat model", "the embedding model",
   "a distilled few-step family". Engine names are fine; checkpoint filenames and vendor
   model names are not. Code identifiers are code, not prose, and are out of scope.
4. **No absolute local paths, and no personal data,** in anything committed. Use `<repo>`,
   `<worktree>`, `<home>`. No names, no email addresses, no hardware inventories.
5. **Offline by default.** No runtime dependency on the public internet, in any code path,
   in any posture.
6. **No hand edits to a database.** Every schema or bookkeeping change is a checked-in
   migration or a tested management command, so the platform rebuilds from `manage.py
   migrate` on a fresh machine. Read-only queries are fine. Nothing auto-creates rows for an
   operator: shipped defaults are a catalogue, installed on consent
   (`manage.py install_defaults`). Creating an account with a password is the operator's own
   action, never an agent's, and an agent never types, stores, or reuses an operator's
   credentials.

## The working loop

**One worktree per task.** Branch work happens in `.claude/worktrees/<branch>` — never in
the repository root checkout. **The root checkout is production**: it is bind-mounted into
the running containers, and the development server reloads whatever is in that working tree,
so any intermediate state it passes through — a conflicted merge, a partial checkout, a
stray staged edit — is live immediately. It never carries branch work, and it only ever
moves to a merged `main` commit, fast-forward.

**A preview stack per branch.** `scripts/preview up <branch> --port <p> --db-port <q>`
brings up an isolated compose project with its own database and its own ports. Check for
port collisions first. Never stop, restart, or rebuild another branch's preview containers —
they are bind-mounted onto someone else's working tree.

**A private test database per session and per role.** `pytest-django` creates and drops its
test database on every run, so two runs pointed at the same one race each other's lifecycle:
that fails spuriously at best and can take the Postgres server down under load. Point
`DATABASE_URL` at *your* branch's preview Postgres with a database name nobody else is
using, and never at a bare shared test database on the primary port:

```bash
export DATABASE_URL='postgres://farabunker:farabunker@localhost:<your-db-port>/farabunker_<your-role>'
.venv/bin/pytest -q
```

Run natively, in the foreground, and wait for it to finish. **Never run the suite inside a
container** — git is not in the image and the git-dependent gates false-fail. **At most two
full suites run concurrently across the whole machine**, and never while a live proof run is
using the same stack.

**The four runs that make a branch green.** Feature flags gate role registration, URL
mounting, and accepted file types; registries are module-global and populated at app-ready
time, so a test can pass only because something earlier in the collection happened to
register for it. A branch is green when all four of these pass:

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
```

Work touching identity, posture, or any column's visibility functions adds the two
posture-sweep runs. [docs/DEV.md](docs/DEV.md) §8 rung 1 owns the supported flag states and
the reversed collection order, and states the same four commands; running them as a 2×2 is
this project's branch gate.

**Restart the background services after a code change.** The web service auto-reloads; the
ingest watcher and the job worker do not. A change to ingest, queue, turn-runtime, or
job-kind code does not reach them until `docker compose restart watcher worker`. A change to
`Dockerfile` or `requirements.txt` needs `docker compose up -d --build`. Forgetting this is
the most common way a working change appears broken.

**The verification ladder, and the vocabulary rule.** `docs/DEV.md` §8 is the ladder every
change climbs: host services up → the four test runs → the branch's own preview stack →
deployed → seen working in a browser. **No success language before the last rung.** "Done",
"works", "fixed", "passing", "complete" are claims about the deployed box, not about a green
terminal. Mid-ladder reports use progress phrasing. An edit that needs a second step to take
effect (an environment file that needs a container recreate) is "edited, not yet live". A
status answer names what it is about and its per-item state — planned, built, tested,
merged, deployed — rather than letting an unqualified "yes" attach to the wrong subject.

**Deploying.** The flow is one-directional: pull request → review → the owner's explicit
merge word → merge to `main` → move the live checkout to that `main` commit on the host →
restart (or recreate, if environment variables changed) → verify in a browser. Never push to
`main` directly. Never merge a branch into the live checkout. Announce a deploy window to
every active session and confirm the announcement actually went out before touching the live
checkout or its containers — one window at a time. Authorization for a merge, a deploy, or
anything destructive must arrive in the acting session's own conversation; a relayed "the
owner said" is not authorization.

## Subagent-driven development

Implementation on this project is subagent-driven: an orchestrating session plans, dispatches,
reviews, and rules, and delegated implementers write the code, the tests, and the docs. The
orchestrator does not write production code itself.

- Every implementation plan is written before the code and reviewed adversarially before it
  is executed.
- Every task gets its own review pass against the real tree, not against the plan's claims.
  When two implementers work the same branch in sequence, the whole-branch review re-verifies
  file citations, because each one's edits invalidate the other's line numbers.
- Prefer citing code by anchor or grep pattern over line number, for exactly that reason.
- Every delegated prompt that edits files names the worktree explicitly and forbids the root
  checkout. Before any deploy, check `git status --short` in the root checkout and treat
  anything staged or modified as a red flag to triage, never to overwrite — staged changes
  do not appear in a plain `git diff`.
- Pick the cheapest capable tier for each delegated task and escalate only when a fix loop
  stalls; reserve the most capable tier for architectural judgment and final whole-branch
  reviews.
- One plan, one ledger. Per-plan working notes — briefs, reports, rulings with their
  cost-if-wrong, deferred-minor triage — live in that worktree's `.superpowers/<plan>/`
  directory, which is gitignored session state.
- A change that deliberately re-pins an existing test says so, by name, in its commit message.

## Merge readiness

A branch is merge-ready only when **every** owner-reported issue on it is closed and
verified, the whole feature has passed an end-to-end walk in a browser on the preview stack —
the real workflows, not just the diff — and the branch has had a whole-branch review covering
everything added since its last one. Per-increment review verifies a diff; it does not verify
a feature as a user experiences it. If you are unsure whether an issue is closed, it is open.
Never offer or even mention merge while one is open. The merge decision itself is the owner's.

## Working alongside other sessions

Several sessions and contributors work this repository concurrently, on separate worktrees
against one machine and one `main`.

- **Zones and slices.** Each session has zones nobody else edits. Crossing into one needs an
  explicit slice grant from that zone's steward, scoped to named files with named invariants.
  A file someone is mid-change on is fenced: ask before touching it.
- **Landing order is negotiated before branching** when two branches will touch the same
  file. The second to land merges `main` into its branch, resolves there, and re-runs its
  gates — never the other way round.
- **Announce before you touch a shared surface**: the root checkout, `main`, the live
  containers, the shared page shell, a settings registration, or anything another session has
  flagged as theirs. Announce additive out-of-zone touches before they merge.
- **The stash stack is shared across worktrees.** Never a bare `git stash` or `git stash
  pop`; prefer a work-in-progress commit, and if you must stash, tag it and apply it by
  commit hash. Run one git command per shell invocation.
- **Before removing any worktree**, check both `git -C <worktree> log --branches --not
  --remotes` for unpushed commits and whether it contains a `.superpowers/` workspace. A
  clean-looking checkout can be another session's live desk. Another session's ledger
  directory is never yours to delete.

## Where things live

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — the layered design, the posture
  spectrum, the module contract, the columns.
- **[docs/DEV.md](docs/DEV.md)** — how to run the stack, run the tests, bring up a preview,
  and the verification ladder. The operational companion to this file.
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — backup, restore, and running-box procedures.
- **[docs/adr/](docs/adr/)** — the decision record. An ADR is a point-in-time decision: it is
  amended with dated amendments, not silently rewritten.
- **[docs/ROADMAP.md](docs/ROADMAP.md)** — what is planned and in what order.
- **[docs/superpowers/](docs/superpowers/)** — the archive of implementation plans and design
  specs, one per phase. Historical records, not current documentation; see that directory's
  own [README](docs/superpowers/README.md).
- **`.superpowers/`** (untracked) — per-session, per-plan working ledgers. Gitignored on
  purpose: it is live state for one session, not a project artifact.
- **[CONTRIBUTING.md](CONTRIBUTING.md)**, **[SECURITY.md](SECURITY.md)**,
  **[LICENSING.md](LICENSING.md)**, **[CLA.md](CLA.md)** — the contribution, disclosure, and
  licensing terms.

## Adding a tool

Every capability reaches the agent layer as a tool registered against one contract, and the
three steps — the spec and runner in your column's `tools.py`, one line in
`AppConfig.ready()`, two guard-list entries in the same commit — are written out in
**[docs/EXTENDING.md](docs/EXTENDING.md)**, together with what you get for free, how a tool
is granted to an agent, how to register a panel, and how to test it. Start there; do not
reverse-engineer an existing tool.

## If you are not Claude

Everything above applies to you unchanged. It is deliberately vendor-neutral: no rule here
depends on a particular assistant, harness, or model. `CLAUDE.md` exists only because one
harness reads that filename by convention — it is a pointer back to this file plus a short
paragraph about the tooling that harness happens to use, and it is not a second, competing
set of rules. If your harness reads a different filename, add a pointer file of the same
shape rather than a second copy of these rules; a rule that lives in two files will be true
in one of them.

If your harness keeps per-user memory, note that memory is private to one user on one
machine and is never a substitute for this file. Any rule that should bind the next
contributor belongs here, in the repository, with a test where one is possible.
````

- [ ] **Step 4: State the branch gate in `docs/DEV.md`, the document `AGENTS.md` defers to**

`AGENTS.md` says `docs/DEV.md` §8 rung 1 owns the flag states and the collection orders — and
it does, but it states them as two separate paragraphs and never says that a branch's gate is
the 2×2 of them. `AGENTS.md` must not be stricter than the document it defers to, so make
`docs/DEV.md` say it. In §8 rung 1, immediately after the existing "**And under both
collection orders.**" paragraph's code block (the one ending
`.venv/bin/pytest -q scripts identity agents foundation models tools  # reversed`) and before
the "**And, for Identity & Auth work, under both non-open postures.**" paragraph, insert:

````markdown
**The two axes compose: four runs make a branch green.** The flag states and
the collection orders are independent, so a branch's gate is the 2×2 of them,
not three commands:

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
```

[AGENTS.md](../AGENTS.md) quotes this block verbatim;
`foundation/ops/tests/test_agent_standards.py` pins that the two copies stay
identical.
````

The four command lines must be byte-identical to `_FOUR_RUN_MATRIX` — including the column
alignment padding after `'vision'`. That is what the new test checks.

- [ ] **Step 5: Run the tests and verify they pass**

```bash
.venv/bin/pytest -q foundation/ops/tests/test_agent_standards.py
```

Expected: 5 passed. If `test_agents_md_stays_short_enough_to_be_read` fails, cut — do not
raise the ceiling.

- [ ] **Step 6: Read it once as a stranger.** Open `AGENTS.md` top to bottom and check two
things by eye, since no test can: nothing names a model family or a checkpoint, and no
sentence promises something the linked document does not actually say.

Then check every relative link mechanically:

```bash
grep -o "](\([^)]*\))" AGENTS.md
```

Each target must exist, with exactly one forward reference: `docs/superpowers/README.md`,
which Task S3 creates — **which is why S3 must land before this branch is reviewed.** The
rest (`docs/ARCHITECTURE.md`, `docs/DEV.md`, `docs/OPERATIONS.md`, `docs/adr/`,
`docs/ROADMAP.md`, `docs/EXTENDING.md`, `docs/adr/0008-engineering-standards.md`,
`CONTRIBUTING.md`, `SECURITY.md`, `LICENSING.md`, `CLA.md`, `README.md`) all exist today.

- [ ] **Step 7: Commit**

```bash
git -C <worktree> add AGENTS.md docs/DEV.md foundation/ops/tests/test_agent_standards.py
git -C <worktree> commit -m "$(cat <<'EOF'
docs: AGENTS.md — one set of working rules, for any agent or contributor

The rules this project actually runs on lived in one session's private,
per-user memory: worktree-per-task, the root checkout being production,
the private test database, the four runs that make a branch green, the
verification ladder's vocabulary rule, merge readiness, and the
coordination norms between concurrent sessions. None of that was
readable by a new contributor, human or otherwise.

AGENTS.md states them once, vendor-neutrally, in ~230 lines that link
out to docs/ARCHITECTURE.md, docs/DEV.md, docs/EXTENDING.md and ADR 0008
rather than restating them. Rules contributed in writing by three
concurrently active sessions are merged in.

docs/DEV.md rung 1 gains the four-run branch gate explicitly. It already
stated both flag states and both collection orders, in two separate
paragraphs, but never that a branch is gated on the 2x2 of them --
and AGENTS.md must not be stricter than the document it defers to.

foundation/ops/tests/test_agent_standards.py is the new gate module
beside the existing structural gates: the nine required section headings
must exist, no absolute local path may appear, the four gate commands
must be byte-identical in AGENTS.md and docs/DEV.md, and the file must
stay under 275 lines -- past which it stops being read and the rules move
back into folklore.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task S2: `CLAUDE.md`, the committed permission allowlist, and the ignore rules

**Files:**
- Create: `CLAUDE.md` (repo root)
- Create: `.claude/settings.json`
- Modify: `.gitignore`
- Untrack (files stay on disk): `.superpowers/owner-requirements/*.md` (7 files)
- Modify: `foundation/ops/tests/test_agent_standards.py` (extend)

**Interfaces:**
- Consumes: `AGENTS.md` from Task S1, and `_read` / `REPO_ROOT` from the same test module.
- Produces: the literal pointer line asserted below, which nothing may reword without
  updating the gate; and `.claude/settings.json`, which every session in this repository
  inherits.

**The two mechanical cautions from the peer replies.**

1. **A bare `.claude/` ignore cannot be negated.** Git will not re-include a file whose
   parent directory is excluded, so `.claude/` + `!.claude/settings.json` leaves
   `settings.json` ignored and the committed file silently invisible. Use `.claude/*` plus
   `!.claude/settings.json`. This was verified empirically, not assumed — Step 4 reproduces
   the check.
2. **The committed `settings.json` binds every session's tooling posture.** When this branch
   becomes a pull request, its body must call that file's diff out explicitly, by name, for
   a line-by-line read. Nobody should inherit permission-adjacent configuration silently.
   (Recorded here because the PR is written after this plan finishes; it is not a step.)

**Why the allowlist is only read-only verbs.** The allowlist's job is to remove prompts for
things that cannot damage anything — not to pre-authorize work. Every entry below either
reads the repository or runs the test suite. Nothing in it can push, merge, delete, rewrite
history, start or stop a container, or reach a database other than through pytest. `find` and
`cat` are deliberately absent: `find -exec` and a shell redirection past `cat` are both
mutation vectors that a prefix match cannot see. Nothing machine-specific appears: no
absolute path, no port, no database URL, no credential. A session that needs anything beyond
this list gets a prompt, which is the intended behaviour.

Note that a suite run is normally prefixed with an inline `DATABASE_URL=...` assignment and
will therefore **not** match `Bash(.venv/bin/pytest:*)` and will still prompt. That is
deliberate: the prefix carries a host, a port, and a database name, and those are exactly the
values that must not be frozen into a committed file.

- [ ] **Step 1: Write the failing tests**

Append to `foundation/ops/tests/test_agent_standards.py`:

```python
import json
import subprocess

# The one line CLAUDE.md exists to carry. Asserted literally: the whole
# point of that file is that it does not become a second, drifting copy
# of AGENTS.md, and a paraphrase is how that starts.
_POINTER_LINE = (
    "**Read [AGENTS.md](AGENTS.md) first.** It is the single source of truth for how "
    "work is done in this repository, and it applies to every agent and every "
    "contributor regardless of vendor."
)

# `.claude/*` and NOT `.claude/`: git will not re-include a file whose
# parent directory is excluded, so the bare-directory form would leave
# the committed settings.json permanently invisible.
_REQUIRED_GITIGNORE_LINES = (
    ".claude/*",
    "!.claude/settings.json",
    ".superpowers/",
    ".DS_Store",
)


def _gitignore_lines() -> list[str]:
    return [
        line.strip()
        for line in _read(".gitignore").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _tracked_files(*paths: str) -> list[str]:
    """What git has in its INDEX -- not what is on disk.

    `test_import_law.py` and `test_column_boundaries.py` each carry their
    own copy of this call. Consolidating all three into `_helpers.py` is
    the right cleanup and deliberately NOT this docs-only plan's business;
    it would churn two large gate modules for no behaviour change. Use
    `splitlines()`, as both of those do: `split()` would break a path
    containing a space.
    """
    out = subprocess.run(
        ["git", "ls-files", *paths],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return out.stdout.splitlines()


class TestClaudeFile:
    def test_claude_md_exists_and_points_at_agents_md(self):
        assert _POINTER_LINE in _read("CLAUDE.md")

    def test_claude_md_is_a_pointer_not_a_second_rulebook(self):
        """Two files carrying the same rules means one of them is wrong
        and nobody knows which."""
        assert len(_read("CLAUDE.md").splitlines()) <= 40


class TestIgnoreRules:
    def test_gitignore_carries_the_agent_directory_rules(self):
        lines = _gitignore_lines()
        missing = [rule for rule in _REQUIRED_GITIGNORE_LINES if rule not in lines]
        assert missing == [], missing

    def test_the_settings_negation_follows_its_directory_rule(self):
        """Order matters in .gitignore: a negation is only honoured after
        the pattern it negates."""
        lines = _gitignore_lines()
        assert lines.index(".claude/*") < lines.index("!.claude/settings.json")

    def test_the_committed_settings_file_is_actually_visible_to_git(self):
        """The whole reason for the `.claude/*` form. If this fails, the
        ignore rule ate the file this task exists to commit."""
        assert _tracked_files(".claude/settings.json") == [".claude/settings.json"]

    def test_no_session_ledger_is_tracked(self):
        """`.superpowers/` is per-session working state written for one
        reader -- directives, incident notes, local paths. It stays on
        disk and out of the history."""
        assert _tracked_files(".superpowers") == []

    def test_the_tracked_file_walk_is_reading_a_real_repository(self):
        """Anti-vacuous pin: the two assertions above both pass trivially
        if `git ls-files` returns nothing at all."""
        assert len(_tracked_files()) > 500


class TestCommittedPermissions:
    def test_the_allowlist_grants_nothing_that_mutates(self):
        """A committed allowlist is inherited by every session in this
        repository. It removes prompts for reads; it never pre-authorizes
        a push, a merge, a delete, or a container operation."""
        settings = json.loads(_read(".claude/settings.json"))
        allow = settings["permissions"]["allow"]
        # `stash list` is a read verb, so the forbidden token names the
        # mutating subcommands rather than the word `stash`.
        forbidden = (
            "push", "commit", "merge", "rebase", "reset", "checkout", "clean",
            "rm", "restore", "stash push", "stash pop", "stash apply",
            "stash drop", "branch -d", "branch -D", "worktree add",
            "worktree remove", "docker compose up", "docker compose down",
            "docker compose restart", "docker compose exec", "docker compose build",
            "psql", "createdb", "dropdb", "curl", "pip install", "chmod", "sudo",
        )
        offenders = [
            entry for entry in allow
            if any(word in entry for word in forbidden)
        ]
        assert offenders == [], offenders

    def test_the_allowlist_names_no_machine(self):
        """Machine-neutral: no absolute path, no port, no credential."""
        settings = json.loads(_read(".claude/settings.json"))
        blob = json.dumps(settings)
        for needle in ("/Users/", "/home/", "localhost:", "postgres://", "password"):
            assert needle not in blob, needle

    def test_the_allowlist_is_not_empty(self):
        settings = json.loads(_read(".claude/settings.json"))
        assert len(settings["permissions"]["allow"]) >= 10
```

- [ ] **Step 2: Run and watch them fail**

```bash
.venv/bin/pytest -q foundation/ops/tests/test_agent_standards.py
```

Expected: the three `TestAgentsFile` tests pass; everything added in Step 1 fails
(`CLAUDE.md is missing`, `.claude/settings.json is missing`, the four missing gitignore
lines, and seven tracked `.superpowers/` paths).

- [ ] **Step 3: Write `CLAUDE.md`**

Create `CLAUDE.md` at the repository root with exactly this content:

```markdown
# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) first.** It is the single source of truth for how work is done in this repository, and it applies to every agent and every contributor regardless of vendor.

This file exists only because this harness reads this filename by convention. It adds
nothing to the rules — it records the tooling this harness happens to use, and nothing here
overrides `AGENTS.md`.

## Claude-specific notes

- **Skills.** Work on this repository runs through the superpowers skill set:
  `brainstorming` before design, `writing-plans` for any multi-step change,
  `subagent-driven-development` or `executing-plans` to execute one,
  `test-driven-development` inside each task, `requesting-code-review` and
  `receiving-code-review` at the task and branch gates, and
  `verification-before-completion` before any claim of completion. Every plan gets one
  adversarial `plan-hygiene-review` pass before it is executed.
- **The orchestrator does not write code.** The main session plans, dispatches, reviews and
  rules; delegated implementers write the code, its tests and its docs. The main session may
  write short orchestration artifacts — a spec, a task brief, a ledger entry — and says so
  when it does.
- **Model tiering.** Pick the cheapest tier that can carry a delegated task; escalate one
  tier when a fix loop stalls; reserve the most capable tier for architectural judgment and
  final whole-branch reviews.
- **Memory is per-user, and never a substitute for `AGENTS.md`.** Anything in a session's
  private memory that should bind the next contributor belongs in the repository, with a
  test where one is possible. If you find yourself relying on remembered context to follow a
  rule, that rule is missing from `AGENTS.md` — add it there.
- **Permissions.** `.claude/settings.json` is committed and read-only by design; it is the
  posture every session in this repository inherits. `.claude/settings.local.json` is
  yours, is gitignored, and is where anything machine-specific belongs.
```

- [ ] **Step 4: Verify the `.gitignore` semantics before writing them**

Do not take the negation form on trust. Reproduce it in a throwaway repository outside this
tree:

```bash
cd "$(mktemp -d)"
git init -q .
mkdir -p .claude
printf '{}\n'  > .claude/settings.json
printf 'local\n' > .claude/settings.local.json
printf '.claude/\n!.claude/settings.json\n' > .gitignore
git status --porcelain --untracked-files=all   # settings.json ABSENT — the bad form
printf '.claude/*\n!.claude/settings.json\n'  > .gitignore
git status --porcelain --untracked-files=all   # settings.json PRESENT — the good form
```

Expected: the first `git status` lists only `.gitignore`; the second additionally lists
`.claude/settings.json`, and never `.claude/settings.local.json`. If your run disagrees with
that, stop and report it rather than proceeding.

- [ ] **Step 5: Add the ignore rules**

Add one new block **at the end of `.gitignore`**, matching the file's existing
`# --- Section ---` comment style. Do **not** add a `.DS_Store` line: it is already the first
entry of the `# --- OS / editor ---` block at the top, and the gate asserts that the line is
present, not that it appears only once.

```gitignore
# --- Agent working directories ---
# `.claude/` holds this repository's own worktrees and per-machine session
# state; `.superpowers/` holds per-session, per-plan working ledgers written
# for one reader. Neither belongs in the history.
#
# `.claude/*` and NOT `.claude/`: git will not re-include a file whose PARENT
# DIRECTORY is excluded, so the bare-directory form would leave the committed
# settings.json below permanently invisible. The negation must follow the
# pattern it negates. `foundation/ops/tests/test_agent_standards.py` pins both.
.claude/*
!.claude/settings.json
.superpowers/
```

- [ ] **Step 6: Write `.claude/settings.json`**

```bash
mkdir -p .claude
```

Create `.claude/settings.json` with exactly this content:

```json
{
  "permissions": {
    "allow": [
      "Bash(git status:*)",
      "Bash(git log:*)",
      "Bash(git show:*)",
      "Bash(git diff:*)",
      "Bash(git grep:*)",
      "Bash(git blame:*)",
      "Bash(git ls-files:*)",
      "Bash(git rev-parse:*)",
      "Bash(git branch --list:*)",
      "Bash(git worktree list:*)",
      "Bash(git stash list:*)",
      "Bash(git remote -v)",
      "Bash(gh pr list:*)",
      "Bash(gh pr view:*)",
      "Bash(gh pr diff:*)",
      "Bash(gh pr checks:*)",
      "Bash(gh issue list:*)",
      "Bash(gh issue view:*)",
      "Bash(ls:*)",
      "Bash(grep:*)",
      "Bash(rg:*)",
      "Bash(wc:*)",
      "Bash(.venv/bin/pytest:*)",
      "Bash(docker compose ps:*)",
      "Bash(docker compose logs:*)"
    ]
  }
}
```

Then read `.claude/settings.local.json` if one exists on this machine and confirm you have
**not** copied anything from it: it contains absolute paths, ports, and database URLs, all of
which Global Constraint 5 and the `test_the_allowlist_names_no_machine` gate forbid here.

- [ ] **Step 7: Move the index into the state the gates assert**

Two of the gates in Step 1 read `git ls-files`, which reports the **index**, not the working
tree — so both halves of this step must happen before the tests can pass.

First, stage the new settings file. This is also the real proof of Step 4's negation: an
ignored file cannot be added without `--force`, so if this command succeeds without one, the
`!.claude/settings.json` rule is working.

```bash
git -C <worktree> add .claude/settings.json
```

Then untrack the seven session-ledger notes:

```bash
git -C <worktree> rm --cached -r .superpowers/owner-requirements
```

`--cached` removes the seven files from the index only; **they stay on disk, unchanged, in
this worktree** — `git rm --cached` never touches the working tree. Confirm both halves
before moving on:

```bash
git -C <worktree> ls-files .superpowers        # expected: no output
ls <worktree>/.superpowers/owner-requirements  # expected: the same 7 .md files
```

- [ ] **Step 7b: Preserve the seven notes before this branch can merge**

**They will NOT survive in other checkouts.** These files are tracked on `main` today, so
every checkout that later fast-forwards from a commit where they were tracked to one where
they are not has **git delete them from its working tree**. `.gitignore` does not protect
them: git removes a path that existed in the commit it is leaving and not in the commit it is
arriving at, ignore rules or no. That includes the live production checkout and every peer
worktree.

That is not a reason to keep tracking them — their content (local paths, hardware detail,
checkpoint filenames, internal incident timelines) is exactly why they must leave the
history. It is a reason to copy them out first.

- Copy `.superpowers/owner-requirements/` from the live checkout to a location **outside the
  repository** before this branch merges.
- Tell every active session to do the same for their own worktree, and confirm each one
  replied — this is a shared-surface change under `AGENTS.md`'s coordination rules.
- Record both in the merge-readiness note on the pull request. **This branch is not
  merge-ready until that copy exists and the sessions have answered.**

This is a pre-merge action, not a commit step: do it, then say plainly in your task report
whether it is done or still owed.

- [ ] **Step 8: Run the tests and verify they pass**

```bash
.venv/bin/pytest -q foundation/ops/tests/test_agent_standards.py
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git -C <worktree> add CLAUDE.md .claude/settings.json .gitignore foundation/ops/tests/test_agent_standards.py
git -C <worktree> commit -m "$(cat <<'EOF'
chore: a pointer file, a read-only permission floor, and the agent dirs out of the history

CLAUDE.md is a pointer to AGENTS.md plus the paragraph that is genuinely
harness-specific (which skills are in use, that the orchestrator does not
write code, model tiering, and that per-user memory is never a substitute
for a committed rule). Capped at 40 lines by a gate, because a second
rulebook is how the first one stops being true.

.claude/settings.json is committed on purpose: it is the permission
posture every session in this repository inherits. It grants read-only
git and gh verbs, ls/grep/rg/wc, the test runner, and two docker compose
read verbs -- nothing that pushes, merges, deletes, mutates a container,
or reaches a database. `find` and `cat` are deliberately absent; -exec
and a redirection are both mutation vectors a prefix match cannot see.
No absolute path, port or credential appears, and a gate asserts all of
that.

.gitignore gains `.claude/*` + `!.claude/settings.json` (NOT the bare
directory form: git will not re-include a file whose parent directory is
excluded, which would leave the committed settings file invisible --
verified against a throwaway repository, and pinned by two tests) and
`.superpowers/`.

The seven .superpowers/owner-requirements notes are untracked with
`git rm --cached`: directives and incident notes written for one reader,
carrying local paths and hardware detail. They stay on disk in THIS
worktree -- but any checkout that later moves onto this commit has git
delete them from its working tree, because they were tracked in the
commit it is leaving. They must be copied out of the repository before
this branch merges; that is on the merge-readiness note, not in here.

This supersedes task H2 of the 2026-09-10 hardening plan except for its
.dockerignore half.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task S3: `docs/superpowers/README.md` and the absolute-path gate

**Files:**
- Create: `docs/superpowers/README.md`
- Modify: `foundation/ops/tests/test_agent_standards.py` (extend)

**Interfaces:**
- Consumes: `_read`, `REPO_ROOT`, `_ABSOLUTE_HOME_PATH` from Task S1, and `_tracked_files`
  from Task S2. **Do not redefine the pattern** — it is defined once, in Task S1, and
  `TestTheLocalPathPattern` is already its non-vacuity proof.
- Produces: `_SCRUB_PENDING`, a dated tuple of files exempt from the absolute-path gate.
  **Task S5 empties it.** No other task may add to it.

**Why the gate lands before the scrub.** The gate is the thing that keeps the rule true
forever; the scrub is a one-off cleanup of 11 historical documents. Landing the gate first,
with a dated exemption list, means the rule binds every *new* document immediately — including
this plan file — while the cleanup proceeds as its own reviewable change. It also means the
exemption list is the honest, visible record of what is still dirty, rather than a silent gap.

**Hard ordering dependency: S2 must land first.** The walk covers every tracked `.md` in the
repository, and the seven `.superpowers/owner-requirements/*.md` notes are full of absolute
local paths. Task S2 untracks them. Run this task on a tree where S2 is committed, or the
gate fails on seven files that are not on the exemption list and are not yours to scrub.

**The two false positives Task S1's pattern already handles.** A naive `"/home/" in line`
check fires on `tools/home/README.md` and `modules/home/README.md`, which appear throughout
the planning archive and in `docs/ARCHITECTURE.md`; and a naive `"/Users/" in line` check
fires on every document that *discusses* the rule, including this plan. `_ABSOLUTE_HOME_PATH`
handles both — the separator must begin a path, and a real user segment plus more path must
follow — and `TestTheLocalPathPattern` from Task S1 pins exactly those distinctions. Reuse it.

- [ ] **Step 1: Write the failing tests**

Append to `foundation/ops/tests/test_agent_standards.py`:

```python
# Tracked documents that still carry absolute local paths, as of
# 2026-09-11. Historical planning records from before this rule existed.
# Task S5 of docs/superpowers/plans/2026-09-11-agent-standards.md scrubs
# every one of them and empties this tuple. NOTHING may be added here:
# a new document with a local path in it is a document to fix, not to
# exempt.
_SCRUB_PENDING = (
    "docs/superpowers/plans/2026-08-22-image-generation.md",
    "docs/superpowers/plans/2026-08-23-vision-enhancements.md",
    "docs/superpowers/plans/2026-08-23-vision-pre-expansion-cleanup.md",
    "docs/superpowers/plans/2026-08-24-vision-architecture-adjustments.md",
    "docs/superpowers/plans/2026-08-25-comfyui-memory-seams.md",
    "docs/superpowers/plans/2026-08-25-vision-edit-capability.md",
    "docs/superpowers/plans/2026-08-25-vision-distilled-fewstep.md",
    "docs/superpowers/plans/2026-08-25-vision-ui-cleanup.md",
    "docs/superpowers/plans/2026-08-27-vision-constant-form.md",
    "docs/superpowers/plans/2026-08-30-identity-ia2-entitlements-and-sharing.md",
    "docs/superpowers/specs/2026-08-25-agents-and-tools-design.md",
)


def _scannable_documents() -> list[str]:
    """Every tracked Markdown file in the repository, minus the pending
    scrub list.

    EVERY tracked `.md`, not just `docs/`: a module README beside the code
    is exactly where a local path gets pasted while someone is debugging,
    and narrowing this walk to `docs/` would leave all 23 of them ungated.
    They are all clean today, so widening it is free.
    """
    return [p for p in _tracked_files()
            if p.endswith(".md") and p not in _SCRUB_PENDING]


class TestDocumentsCarryNoLocalPaths:
    def test_no_tracked_document_names_a_local_home_directory(self):
        """A path out of one developer's home directory is noise to every
        other reader, and it names a person and a machine. Files listed
        in _SCRUB_PENDING are exempt until Task S5 clears them."""
        offenders = []
        for relative in _scannable_documents():
            text = (REPO_ROOT / relative).read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                if _ABSOLUTE_HOME_PATH.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()[:100]}")
        assert offenders == [], offenders

    def test_the_document_walk_is_reading_real_files(self):
        """Anti-vacuous pin: the assertion above passes trivially if the
        walk finds nothing, or if every file got exempted."""
        assert len(_scannable_documents()) > 40

    def test_every_pending_file_still_exists(self):
        """An exemption for a file that has been renamed or deleted is a
        silent hole in the gate."""
        missing = [p for p in _SCRUB_PENDING if not (REPO_ROOT / p).exists()]
        assert missing == [], missing


class TestPlanningArchiveExplainsItself:
    def test_the_archive_has_a_readme_that_says_what_it_is_not(self):
        text = _read("docs/superpowers/README.md")
        assert "AGENTS.md" in text
        assert "docs/adr/" in text
        assert "not current documentation" in text
```

- [ ] **Step 2: Run and watch them fail**

```bash
.venv/bin/pytest -q foundation/ops/tests/test_agent_standards.py
```

Expected: `test_the_archive_has_a_readme_that_says_what_it_is_not` fails
(`docs/superpowers/README.md is missing`). The three path tests should already **pass**,
because every dirty file is exempted — confirm that, and confirm Task S1's
`TestTheLocalPathPattern` still passes, since it is the only proof the pattern is not
vacuous.

- [ ] **Step 3: Prove the gate actually bites**

Before writing the README, verify the gate is not asleep on a real file. The `%s` here is
what keeps this plan document itself clean under the rule it is installing — `printf`
assembles the offending path at run time:

```bash
printf '\nrun `/Users/%s/src/x`\n' someone >> docs/ARCHITECTURE.md
.venv/bin/pytest -q foundation/ops/tests/test_agent_standards.py -k local_home_directory
git -C <worktree> checkout -- docs/ARCHITECTURE.md
git -C <worktree> status --short docs/ARCHITECTURE.md
```

Expected: FAIL naming `docs/ARCHITECTURE.md` and the line number, then an empty `status`
line. Do not proceed until you have seen it fail for the right reason, and do not leave
that line in the file.

- [ ] **Step 4: Write `docs/superpowers/README.md`**

```markdown
# The planning archive

This directory is a **historical record**, not current documentation.

Each file under `plans/` is the implementation plan for one phase of work, written before
that phase was executed and left untouched afterwards. Each file under `specs/` is the
design document a plan argues from. They are dated, and they describe the tree as it stood
on that date: paths, module names, line numbers and file counts in here are frequently
stale, deliberately so. **Nothing in this directory is a statement about how the system
works today.** For that, read [ARCHITECTURE.md](../ARCHITECTURE.md), the decision record in
[docs/adr/](../adr/), and the module READMEs beside the code.

## Why keep them

Three reasons, in order of how often they matter:

1. **Resuming a phase.** A plan is executable: its tasks carry the tests, the exact file
   paths, and the commits in order. Work that stopped halfway can be picked up by reading
   the plan and finding the last checked box, without reconstructing the reasoning from the
   diff.
2. **Explaining a decision that never earned an ADR.** An ADR records what was decided; a
   plan records the twenty smaller choices made while carrying it out, and the constraints
   that forced them. When a piece of code looks arbitrary, the plan that produced it usually
   says why.
3. **Auditability.** This is a security product, and being able to show how a given
   behaviour got there is part of what the project sells.

## How to read one

A plan opens with its goal, its architecture, and its global constraints, then a numbered
task list. Every task names the files it touches and carries its own tests and its own
commit. A `## Smoke Checklist`, where present, is the browser-level walk a human drove
against a preview stack before the merge decision.

Amendments are appended and dated rather than edited in place, for the same reason ADRs
are: the record is worth more than the tidiness.

## What does not belong in here

- **Anything current.** If a statement needs to stay true, it belongs in `docs/` proper, in
  an ADR, or in a module README — with a test where one is possible.
- **Absolute local paths, personal data, or model names.** Use `<repo>`, `<worktree>`,
  `<home>`. `foundation/ops/tests/test_agent_standards.py` fails the build on the first two.
- **Live session state.** Per-session, per-plan working ledgers live in the untracked
  `.superpowers/` directory at the repository root, not here.

The working rules these plans are executed under are in
[AGENTS.md](../../AGENTS.md).
```

- [ ] **Step 5: Run the tests and verify they pass**

```bash
.venv/bin/pytest -q foundation/ops/tests/test_agent_standards.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git -C <worktree> add docs/superpowers/README.md foundation/ops/tests/test_agent_standards.py
git -C <worktree> commit -m "$(cat <<'EOF'
docs(superpowers): say what the planning archive is, and gate local paths out of docs/

docs/superpowers/ is two dozen dated implementation plans and specs with
no explanation attached, which reads to a new visitor as internal scratch
left in a public repository. Its README says what it is (a historical
record: how a phase is resumed, why a decision that never earned an ADR
was made, auditability), what it is not (current documentation -- paths
and counts in there are stale on purpose), and what must never be written
into it.

The gate: no tracked .md anywhere in the repository may name an absolute
local home directory, using the pattern Task S1 already defined and
pinned. Every tracked .md, not just docs/ -- a module README beside the
code is exactly where a local path gets pasted mid-debug, and all 23 of
them are clean today, so widening the walk is free. Three tests: the walk
itself, an anti-vacuous count of what was actually scanned, and an
existence check on every exemption, because an exemption for a renamed
file is a silent hole.

_SCRUB_PENDING carries the 11 historical files that still carry real
paths, dated. The next task empties it. Nothing may be added to it.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task S4: the front door stops contradicting itself

**Files:**
- Modify: `CONTRIBUTING.md` (the Phase-0 status blockquote; the "Developer setup" section; a
  new link to `AGENTS.md`)
- Modify: `SECURITY.md` (the "Supported versions" paragraph)
- Modify: `README.md` (a `## Quickstart` section; `docs/DEV.md` in the links list; a one-line
  `AGENTS.md` pointer)
- Modify: `foundation/ops/tests/test_agent_standards.py` (extend)

**Interfaces:**
- Consumes: `_read` from Task S1.
- Produces: nothing later tasks depend on.

**What is stale.** `CONTRIBUTING.md` opens with "**Status:** Phase 0 (architecture &
design). There is no runtime code yet" and closes with a "Developer setup — to be documented
when the Phase 1 walking skeleton lands". `SECURITY.md` closes with "The project is
pre-release (Phase 0)". `README.md`'s own Status section two files away says "Phase 1 — the
walking skeleton runs" and lists six working capability areas. Meanwhile `README.md` has no
quickstart and never links `docs/DEV.md` at all, so a visitor reads the pitch and then has
no path to running anything.

**Scope discipline.** Read both governance files fully while you are in them — a document
whose opening claim is that stale is worth a paragraph-by-paragraph check. But **report, do
not fix, anything beyond the four edits named above.** Two of the things you will find
(`LICENSE` shipping operative AGPL text while ADR 0002 is still "Proposed"; `CLA.md`'s
"DRAFT — not legally reviewed" banner while `CONTRIBUTING.md` asks every contributor to sign
it) are open owner decisions, tracked as tasks H20 and H21 of the hardening plan. Do not
touch either.

- [ ] **Step 1: Write the failing tests**

Append to `foundation/ops/tests/test_agent_standards.py`:

```python
class TestTheFrontDoor:
    def test_no_governance_document_still_claims_there_is_no_code(self):
        """Phase 0 ended hundreds of commits ago, and README's own Status
        section two files away says so."""
        for relative in ("CONTRIBUTING.md", "SECURITY.md"):
            text = _read(relative)
            assert "Phase 0" not in text, relative
            assert "no runtime code yet" not in text, relative

    def test_the_readme_offers_a_way_to_run_the_thing(self):
        readme = _read("README.md")
        assert "## Quickstart" in readme
        assert "docs/DEV.md" in readme
        assert "docker compose up" in readme

    def test_every_front_door_document_points_at_the_working_standards(self):
        """A contributor should reach AGENTS.md from whichever of the two
        files they opened first."""
        for relative in ("README.md", "CONTRIBUTING.md"):
            assert "AGENTS.md" in _read(relative), relative
```

- [ ] **Step 2: Run and watch them fail**

```bash
.venv/bin/pytest -q foundation/ops/tests/test_agent_standards.py -k FrontDoor
```

Expected: all three fail.

- [ ] **Step 3: Replace the `CONTRIBUTING.md` status banner**

Replace the blockquote that currently reads `> **Status:** Phase 0 (architecture & design).
There is no runtime code yet, so today's most valuable contributions are to the design docs
in [`docs/`](docs/). Code-contribution tooling (tests, CI, linters) will be added as the
walking-skeleton lands — see [docs/ROADMAP.md](docs/ROADMAP.md).` with:

```markdown
> **Status:** Phase 1 — the platform is running code, not a plan. Offline document Q&A, a
> model registry, an execution queue, media ingestion, a conversational agent with tools,
> and accounts with entitlements all work today; see the
> [README](README.md#status) for what that covers and [docs/ROADMAP.md](docs/ROADMAP.md)
> for what is next. Code contributions are welcome, and every one of them ships with tests
> and the documentation it affects — see [ADR 0008](docs/adr/0008-engineering-standards.md).
>
> **[AGENTS.md](AGENTS.md) is the working-standards reference** — the branch and worktree
> model, how the tests are run, the verification ladder, and what "ready to merge" means. It
> is written for human contributors and AI agents alike; read it before your first change.
```

- [ ] **Step 4: Replace the `CONTRIBUTING.md` "Developer setup" section**

Replace the whole section — heading and body, currently `## Developer setup` followed by "To
be documented when the Phase 1 walking skeleton lands…" — with:

```markdown
## Developer setup

[docs/DEV.md](docs/DEV.md) is the full path from a clone to a running stack: the model
server, the Python environment, the environment file, the containers, assigning a model to
each role on first run, and the test suite. [README.md](README.md#quickstart) has the short
version.
```

- [ ] **Step 5: Replace the `SECURITY.md` supported-versions paragraph**

Replace `The project is pre-release (Phase 0). A supported-versions table will be published
once there are releases to support.` with:

```markdown
The project is pre-1.0 and has no tagged releases yet, so there is no supported-versions
table to publish: **`main` is the supported version.** Report against `main`, and a table
will appear here with the first release.
```

- [ ] **Step 6: Add the `README.md` quickstart**

Insert a new section immediately **before** `## Status` — a visitor should be able to run the
thing before reading about it. The block below is fenced with **four** backticks because its
content contains a three-backtick block of its own; paste everything between the four-backtick
fences, inner fence included:

````markdown
## Quickstart

You need Docker and a local model server for inference. **The box ships no models and
presumes none** — that is deliberate ([ADR 0010](docs/adr/0010-model-management-framework.md));
you choose what to install and which role it backs.

```bash
git clone <this repository> && cd farabunker
cp .env.example .env      # set SECRET_KEY before exposing the box to anything
docker compose up -d --build
open http://localhost:8000/
```

That brings up four containers — the database, the web app, the ingest watcher, and the job
worker — with durable data on the host under `./data`, outside the containers, where a
rebuild cannot touch it. Your model server stays native on the host and must listen on all
interfaces for the containers to reach it.

First run: assign a model to each role at `/inference/`, then drop a document into the
library and ask a question. A surface whose role has nothing assigned says so plainly rather
than pretending.

**[docs/DEV.md](docs/DEV.md) is the full guide** — the environment variables, running
natively against the containerised database, the test suite, the verification ladder, and
the optional image-generation and transcription engines.

**AI agents and contributors: read [AGENTS.md](AGENTS.md)** — the working standards for any
change to this repository.
````

- [ ] **Step 7: Add `docs/DEV.md` to the README links list**

In the "Next up" links list (which names `ARCHITECTURE.md`, `ROADMAP.md`, `EXTENDING.md`,
`OPERATIONS.md`, `BUSINESS.md`, `BRAND.md`), add a `docs/DEV.md` entry first — it is the one
a visitor needs soonest and the only one currently missing.

- [ ] **Step 8: Verify the quickstart's commands against the tree — do not guess**

```bash
grep -n "^  [a-z]*:" compose.yaml            # four services: db, web, watcher, worker
grep -vE "^\s*#|^$" .env.example             # the variables .env actually needs
grep -n -A2 "ports:" compose.yaml            # web publishes 8000:8000
```

`compose.override.yaml` is the wrong file for the port — it has no `ports:` block at all; its
only `8000` is inside the dev `runserver` command. The published port lives in
`compose.yaml`.

Every claim in the quickstart must match what you see. If you cannot actually run the
sequence in a clean directory, say plainly in your report which steps you did not run rather
than implying you did. Do **not** invent an environment variable that is not in
`.env.example` today.

- [ ] **Step 9: Read both governance files end to end and report**

Read `CONTRIBUTING.md` and `SECURITY.md` in full. Fix nothing beyond Steps 3–5. List, in
your task report, every other claim you believe is stale, with the line, so the orchestrator
can route it. Do not touch anything about `LICENSE`, ADR 0002, or `CLA.md`.

- [ ] **Step 10: Run the tests and verify they pass**

```bash
.venv/bin/pytest -q foundation/ops/tests/test_agent_standards.py
```

Expected: all tests pass.

- [ ] **Step 11: Commit**

```bash
git -C <worktree> add README.md CONTRIBUTING.md SECURITY.md foundation/ops/tests/test_agent_standards.py
git -C <worktree> commit -m "$(cat <<'EOF'
docs: a front door that matches the code, and a way in for contributors

CONTRIBUTING.md opened with "Phase 0 ... there is no runtime code yet"
and closed with a developer-setup section promising documentation "when
the walking skeleton lands"; SECURITY.md closed with "pre-release (Phase
0)". README's own Status section, one file away, lists six working
capability areas. Both banners now describe the phase the repository is
actually in, and CONTRIBUTING points at AGENTS.md for the working
standards and docs/DEV.md for setup.

SECURITY.md's supported-versions paragraph says the honest thing: no
tagged releases yet, main is the supported version.

README gains a Quickstart before Status -- clone, .env, compose up, open
the browser, assign a model -- with docs/DEV.md named as the full guide
and added to the links list it was missing from entirely, plus a
one-line pointer to AGENTS.md.

Three gates pin all of it. Deliberately untouched: the LICENSE-vs-ADR-
0002 and CLA-draft inconsistencies, which are open owner decisions.

This supersedes task H19 of the 2026-09-10 hardening plan.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task S5: scrub the absolute paths out of the planning archive

**Files:**
- Modify: the 11 files listed below, under `docs/superpowers/plans/` and
  `docs/superpowers/specs/`
- Modify: `foundation/ops/tests/test_agent_standards.py` (empty `_SCRUB_PENDING`)

**Interfaces:**
- Consumes: `_SCRUB_PENDING` from Task S3.
- Produces: `_SCRUB_PENDING = ()`.

**The skip list.** Task S5 was written to consult a skip list of files that a concurrently
active branch is also modifying, so those could be left to the hardening plan instead of
conflicting. **That list came back empty for files tracked on `main`** (peer replies,
2026-09-11): the peer branches' own plan and spec files are branch-only and their authors
will scrub them on rebase, and nothing in any peer worktree depends on a tracked path under
`docs/superpowers/`. **So this task scrubs all 11 files and empties the exemption tuple
completely.**

If, when you start, the orchestrator hands you a non-empty skip list after all, then: leave
those files unscrubbed, leave exactly those entries in `_SCRUB_PENDING` with a dated comment
naming the branch that owns each one and the reason, and say so in your task report. Do not
invent an exemption on your own judgment.

**The 11 files and their hit counts**, measured on this branch:

| File | `/Users/` lines |
|---|---|
| `docs/superpowers/plans/2026-08-25-vision-edit-capability.md` | 49 |
| `docs/superpowers/plans/2026-08-24-vision-architecture-adjustments.md` | 40 |
| `docs/superpowers/plans/2026-08-23-vision-pre-expansion-cleanup.md` | 39 |
| `docs/superpowers/plans/2026-08-25-vision-ui-cleanup.md` | 28 |
| `docs/superpowers/plans/2026-08-27-vision-constant-form.md` | 23 |
| `docs/superpowers/plans/2026-08-25-comfyui-memory-seams.md` | 10 |
| `docs/superpowers/plans/2026-08-25-vision-distilled-fewstep.md` | 6 |
| `docs/superpowers/plans/2026-08-23-vision-enhancements.md` | 3 |
| `docs/superpowers/plans/2026-08-22-image-generation.md` | 1 |
| `docs/superpowers/plans/2026-08-30-identity-ia2-entitlements-and-sharing.md` | 1 |
| `docs/superpowers/specs/2026-08-25-agents-and-tools-design.md` | 1 |

**NEVER scrub this plan document.** `docs/superpowers/plans/2026-09-11-agent-standards.md`
legitimately contains `/Users/` and `/home/` in its own text — Global Constraint 5, the
regex's docstring, the two positive test fixtures, and the `forbidden`/needle tuples. Task
S3's pattern does not match any of them, by design: they are the proof the pattern is not
vacuous. A plain `grep` for `/Users/` *will* list this file. Exclude it, and leave it alone.

**Re-measure before you start** — this plan may have sat for a while:

```bash
cd <worktree>
git grep -c "/Users/" -- docs/superpowers ':!docs/superpowers/plans/2026-09-11-agent-standards.md'
```

If the list differs from the table, use what you measure and say so in your report. No
tracked `.md` anywhere carries a genuine absolute `/home/` path — every `/home/` hit in the
archive is `tools/home/` or `modules/home/`, which is exactly the false positive Task S3's
regex was built to skip.

- [ ] **Step 1: Read the full inventory before changing anything**

```bash
git grep -n "/Users/" -- docs/superpowers ':!docs/superpowers/plans/2026-09-11-agent-standards.md'
```

The paths in there take four shapes. Replace them in this order — **longest first**, or a
shorter pattern eats the longer one and leaves a mangled tail:

1. `<absolute repo path>/.claude/worktrees/<branch-name>` → `<worktree>`
2. `<absolute repo path>/.venv` → `<repo>/.venv`
3. `<absolute repo path>` → `<repo>`
4. any remaining `/Users/<name>/…` or `/home/<name>/…` → `<home>/…`

Most of the volume is two shapes — a venv interpreter path and a venv pytest path — which
become `<repo>/.venv/bin/python` and `<repo>/.venv/bin/pytest`.

- [ ] **Step 2: Scrub the three largest files, one at a time, reading each diff**

Work file by file, not with one sweeping regex across all eleven. These are historical
records: a mangled sentence in one is a small permanent loss, and a sed expression that
over-matches in a 2,000-line plan is not visible in a summary.

For each of `2026-08-25-vision-edit-capability.md`, `2026-08-24-vision-architecture-adjustments.md`,
and `2026-08-23-vision-pre-expansion-cleanup.md`:

```bash
# then, after each file:
git -C <worktree> diff -- docs/superpowers/plans/<file>
```

Read every hunk. Check specifically for: a replacement inside a fenced command block that
leaves the command unrunnable-looking in a *new* way (a placeholder is fine — a half-path is
not); a path that was part of a prose sentence and now reads ungrammatically; and any line
where the same absolute path appeared twice and only one was replaced.

- [ ] **Step 3: Commit the three large files**

```bash
git -C <worktree> add docs/superpowers/plans/2026-08-25-vision-edit-capability.md docs/superpowers/plans/2026-08-24-vision-architecture-adjustments.md docs/superpowers/plans/2026-08-23-vision-pre-expansion-cleanup.md
git -C <worktree> commit -m "$(cat <<'EOF'
docs(superpowers): local paths out of the three largest historical plans

128 lines across three vision-track plans named an absolute path out of
one developer's home directory -- mostly a venv interpreter and a venv
pytest, plus worktree paths. Replaced with <repo>, <worktree> and <home>
placeholders, file by file with each diff read, because these are
historical records and a mangled sentence in one is a permanent loss.

No prose meaning changed; only the paths.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

- [ ] **Step 4: Scrub the remaining eight files the same way, reading each diff**

`2026-08-25-vision-ui-cleanup.md`, `2026-08-27-vision-constant-form.md`,
`2026-08-25-comfyui-memory-seams.md`, `2026-08-25-vision-distilled-fewstep.md`,
`2026-08-23-vision-enhancements.md`, `2026-08-22-image-generation.md`,
`2026-08-30-identity-ia2-entitlements-and-sharing.md`, and
`specs/2026-08-25-agents-and-tools-design.md`.

- [ ] **Step 5: Verify nothing is left — with the gate, not with grep**

A plain `git grep "/Users/"` is the wrong instrument here: it reports this plan document's
own fixtures and constraint text, and an implementer who trusts a "no output" instruction
will start scrubbing the very lines that prove the pattern works. Ask the gate instead — it
knows the difference:

```bash
.venv/bin/pytest -q foundation/ops/tests/test_agent_standards.py -k local_home_directory
```

Expected: PASS. If it fails, the failure message names every remaining file and line.

For a human read of what changed, scope the grep and exclude this plan:

```bash
git grep -n "/Users/" -- docs/superpowers ':!docs/superpowers/plans/2026-09-11-agent-standards.md'
```

Expected: no output.

- [ ] **Step 6: Empty the exemption tuple**

In `foundation/ops/tests/test_agent_standards.py`, replace the `_SCRUB_PENDING` tuple and
its comment with:

```python
# Every tracked document in the repository is clean as of 2026-09-11, and
# this gate keeps it that way. The tuple stays so a future scrub can be
# staged the same way -- landing the gate first, with the dirty files
# named and dated, rather than silently skipped. It is not a place to put
# a new document that has a local path in it: that is a document to fix.
_SCRUB_PENDING: tuple[str, ...] = ()
```

**Also delete `test_every_pending_file_still_exists`** in the same edit. With the tuple
empty it can never fail, and a test that cannot fail is worse than no test: it reads like
coverage. Whoever stages the next scrub restores it along with the entries it guards.

- [ ] **Step 7: Run the tests and verify they pass**

```bash
.venv/bin/pytest -q foundation/ops/tests/test_agent_standards.py
```

Expected: all tests pass, including `test_the_document_walk_is_reading_real_files`, which
now counts every tracked `.md` in the repository with no exemptions subtracted.

- [ ] **Step 8: Commit**

```bash
git -C <worktree> add docs/superpowers foundation/ops/tests/test_agent_standards.py
git -C <worktree> commit -m "$(cat <<'EOF'
docs(superpowers): the last local paths go, and the exemption list empties

The remaining eight historical plans and one spec lose their absolute
home-directory paths, replaced with <repo>/<worktree>/<home>
placeholders, each diff read on its own.

_SCRUB_PENDING is now empty: every tracked .md in the repository is
clean, and test_no_tracked_document_names_a_local_home_directory scans
all of them with no exemptions. The tuple stays, with a comment saying
what it is for -- staging a future scrub, never excusing a new document.
test_every_pending_file_still_exists goes, because with an empty tuple it
can no longer fail.

The skip list the task was written to consult came back empty for files
tracked on main: the concurrently active branches' own plan and spec
files are branch-only and their authors scrub them on rebase.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

## The branch gate, before review

After Task S5, run the four-run matrix on the whole suite — not just the new module. Task S2
changed `.gitignore` and the index, and Task S3's gate walks `git ls-files`, so a stale
assumption anywhere in `foundation/ops/tests/` would surface here and nowhere else.

```bash
export DATABASE_URL='postgres://farabunker:farabunker@localhost:<your-preview-db-port>/farabunker_<your-role>'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
```

One at a time, in the foreground, never inside a container. Record the four counts.

Then: a whole-branch review, and — because this branch changes no runtime behaviour and has
no user-visible surface — **no preview stack and no browser walk is required, and none should
be claimed.** The verification ladder's rung 2 and rung 4 do not apply to a change that
renders no pixels. Say exactly that in the report rather than skipping the rungs silently.
`AGENTS.md`'s own vocabulary rule still binds the report: this branch is "written and gated",
not "done", until it is merged.

---

## Self-review

**1. Coverage.** Every item the brief named has a task: `AGENTS.md` with all nine sections
(S1); `CLAUDE.md`, the committed allowlist, the three `.gitignore` rules, and the
`git rm --cached` (S2); `docs/superpowers/README.md` and the absolute-path gate with a dated
exemption list (S3); the two Phase-0 banners, the README quickstart and the `AGENTS.md`
pointer (S4); the path scrub with the skip-list mechanism (S5). The "Relationship to the
hardening plan" section states exactly what H2 and H19 reduce to. The peer-contributed rules
arrived before this plan was finished, so the placeholder section the brief allowed for is
not present — the rules are merged into the S1 draft and the S2 cautions instead, and the
"Peer-contributed rules" section above records that.

**2. Placeholders.** No step says "TBD", "add error handling", or "similar to Task N". Every
file that is created has its full text in the plan; every test has its full source; every
commit has its full message.

**3. Consistency.** `_read`, `REPO_ROOT`, `_ABSOLUTE_HOME_PATH`, `_FOUR_RUN_MATRIX`,
`_tracked_files`, `_gitignore_lines`, `_SCRUB_PENDING` and `_scannable_documents` are each
defined once, in the task that first needs them, and used with the same signature everywhere
after. The nine headings in `_REQUIRED_AGENTS_HEADINGS` match the nine `## ` headings in the
S1 draft exactly, in order. `_POINTER_LINE` matches the first bold line of the S2 `CLAUDE.md`
draft exactly, including punctuation. `_FOUR_RUN_MATRIX`'s four lines match the S1 `AGENTS.md`
draft and the S1 Step 4 `docs/DEV.md` insertion byte for byte, padding included.

**4. Ordering.** S1 → S2 → S3 → S4 → S5, and two of those arrows are hard: S2's `CLAUDE.md`
gate needs S1's test module to exist, and **S3's walk needs S2 to have untracked
`.superpowers/owner-requirements/`** — those seven notes are full of absolute paths and would
fail a gate they are not on the exemption list for. S4 and S5 are independent of each other
but both assume S3's gate is in place.

**5. Three things I deliberately did not do.** `.dockerignore` stays with hardening task H2
(it changes what a build copies, which is a production-behaviour change, outside this plan's
Global Constraint 2). The `LICENSE`-vs-ADR-0002 and `CLA.md`-draft inconsistencies stay open
owner decisions (hardening H20/H21), touched by nothing here. And `_tracked_files` is left as
a third local copy of a call `test_import_law.py` and `test_column_boundaries.py` already
make, rather than consolidated into `_helpers.py` — the consolidation is right, and churning
two large gate modules for no behaviour change is not a docs-only plan's business.

---

## Plan review

**Round 1 — 2026-09-11 — VERDICT: AMEND, all findings applied.** One adversarial reviewer
(most-capable tier, read-only) checked the plan against the real tree rather than against its
own quotes. It independently re-derived and confirmed: the nine heading strings, the
three-part pointer-line concatenation, both line caps, all three `.claude/settings.json`
gates (25 entries, zero forbidden substrings, no machine needles), the path regex run over
every tracked `.md` in the worktree (hits exactly the 11 `_SCRUB_PENDING` files and never the
plan document), all 11 S5 per-file counts, every relative link target, the reversed testpath
order against `pytest.ini`, and the README quickstart's claims against `compose.yaml` and
`.env.example`.

Nine correctness findings, all applied:

1. *(high)* S5's "expected: no output" grep verifications fire on this plan document's own
   fixtures — 22 lines. Replaced with the gate itself, plus a pathspec exclusion on the
   re-measure and an explicit "never scrub this file" warning.
2. *(high)* "They remain on disk in every other checkout" was wrong: the seven
   `.superpowers/owner-requirements` notes are tracked on `main`, so any checkout that moves
   onto the merge commit has git delete them. Corrected, and a pre-merge Step 7b added —
   copy them out of the repository and confirm every active session has too, before this
   branch is merge-ready.
3. *(med-high)* S3's gate walked `docs/` only, narrowing hardening H19's coverage; a local
   path in any of the 23 module READMEs would have been ungated. Widened to every tracked
   `.md` (all clean today, so it is free), with the S2-must-land-first dependency stated.
4. *(med)* The H2 reduction stripped H2's `.dockerignore` anti-vacuity pin and described its
   bare-`.claude/` assertion as drift rather than a hard failure against S2's `.claude/*`.
   Reduction rewritten.
5. *(med)* `AGENTS.md`'s import-law paragraph omitted `models.queue.models`. Added, with its
   one scoped exception.
6. *(med)* `AGENTS.md` claimed `docs/DEV.md` rung 1 authorises a four-run matrix; rung 1
   states the two axes separately and never composes them. `docs/DEV.md` now states the 2×2
   (new S1 Step 4), `AGENTS.md`'s deference is reworded, and `_FOUR_RUN_MATRIX` pins the two
   copies identical in the style of `test_docs_sync.py`.
7. *(med)* S4's port verification grepped `compose.override.yaml`, which has no `ports:`
   block. Repointed at `compose.yaml`.
8. *(med)* S4's README draft used a three-backtick fence that its own inner ` ```bash ` block
   closed. Raised to four backticks, matching S1's treatment.
9. *(med)* Striking H19 dropped its caveat that H3 and H14 change the first-run experience.
   Carried over as a second follow-up for the hardening branch.

Five of six optional items also applied: the line cap raised to 275 with a "cut, don't raise"
docstring; the redundant `git add -u .superpowers` removed; `_tracked_files` switched to
`splitlines()` with a WHY comment on the two existing copies; `test_every_pending_file_still_exists`
deleted in S5 once its tuple empties; the `.gitignore` placement ambiguity resolved to one
instruction; the archive count reworded to avoid arithmetic drift. The sixth (moving
`tracked_files()` into `_helpers.py`) was declined and recorded in the self-review as
deliberate — it would churn two large gate modules for no behaviour change.
