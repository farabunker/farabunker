# ADR 0011 — Branch preview stacks

**Status:** Accepted
**Date:** 2026-08-20

## Context

`compose.yaml` bind-mounts the MAIN repo checkout — `web.volumes: - .:/app`,
and `web.build.context: .` (ADR 0006). Both `.` are resolved relative to
wherever `docker compose` is invoked from, which for the primary stack is
always the main repo root. That means the primary stack can never run a
`git worktree` branch: even started from inside a worktree checkout, the
running container's `context` for `docker build` and the `.:/app` mount at
runtime still point at the *main* repo's files, so the container serves
main's code regardless of which branch's `docker compose` you typed the
command in.

Before this ADR, "does this branch actually work" was answered by reading
the diff and running the unit suite — never by exercising the running
app in a browser. Two additional problems compound this:

1. **Shared state.** If a worktree branch's stack somehow *did* attach to
   the primary compose project, it would share the primary stack's single
   Postgres instance (`./data/postgres`) — a branch under test could
   corrupt or migrate the same database backing whatever the primary stack
   is doing, and a branch with a bad migration would need a manual
   data restore to recover from.
2. **No signal on a cold start.** The primary stack's Postgres has been
   migrated forward incrementally since Wave 1 — it has never seen a
   branch's migrations run against a genuinely empty database. A migration
   that only works if some earlier, already-applied migration happened to
   leave a compatible row in place would pass on the primary stack and
   fail on a fresh install.

This ADR records the design of `compose.preview.yaml` + `scripts/preview`
(Tasks 1–2 of this plan), which close both gaps: an isolated,
disposable stack that runs a worktree branch's actual code against its own
database, side by side with the primary stack, torn down with one command.

## Decision

### 1. A standalone compose file, parameterized by environment variable, never auto-merged

`compose.preview.yaml` is invoked only ever with `-f compose.preview.yaml`
— `compose.override.yaml` (the primary stack's dev auto-reload override)
does not apply to it, so its `web` command duplicates the override's
`migrate && runserver` verbatim rather than relying on a second file to
merge it in. Every path that differs per-branch is an environment
variable the launcher script sets, never a hardcoded value:

- `FARABUNKER_SRC` — absolute path to the worktree checkout, used as
  **both** the Docker build context and the `:/app` bind mount. Using the
  worktree as the build context (not the main repo) is what makes branch-
  added dependencies actually install — this very branch adds `httpx` to
  `requirements.txt`; a preview that built from main's image and just
  bind-mounted the worktree's code on top would import-error on startup
  because `httpx` was never installed into that image.
- `PREVIEW_DATA_DIR` — absolute path to a host directory dedicated to this
  preview's durable data, distinct from the primary stack's `./data`.
- `PREVIEW_WEB_PORT` / `PREVIEW_DB_PORT` — host ports, defaulted in the
  compose file (8001/5433) so the file works even if the launcher forgets
  to set them, but always overridable per-preview.

### 2. A fresh, empty database is the default — not a copy of the primary stack's data

Each preview gets its own Postgres data directory under
`data/preview/<branch>/postgres`, created empty by `scripts/preview up`
the first time a branch is previewed. This is a deliberate choice, not
just a side effect of isolation: starting from an empty database is what
proves a branch's migrations actually run correctly on a virgin install,
and exercises the cold-start path (seed data, first-migrate behavior) that
the primary stack's long-lived, incrementally-migrated database can no
longer exercise. A preview that only ever copied the primary stack's data
forward would hide exactly the migration bugs this stack exists to catch.

### 3. Ollama is deliberately shared with the primary stack, not isolated

Every other piece of state is isolated per preview; Ollama is the one
exception, and it is intentional. farabunker's offline invariant is that
**the platform itself never downloads a model** (ADR 0010 §5) — every
`ollama pull` is an operator-run, out-of-band action. If preview stacks
isolated Ollama the way they isolate Postgres, standing up a preview would
either require re-pulling every model the operator already has (violating
the offline rule, and slow) or leave the preview with no models bound at
all. Instead, `compose.preview.yaml`'s `web` and `watcher` services point
`OLLAMA_BASE_URL` at the SAME native host Ollama the primary stack uses
(`http://host.docker.internal:11434`, ADR 0006 §4) — a preview reuses
whatever the operator has already pulled, exactly like the primary stack
does.

### 4. Isolation contract: pinned project name, dedicated ports, dedicated data dir

- **Project name** — `scripts/preview` always runs `docker compose -p
  farabunker-preview-<branch> ...`, where `<branch>` is the sanitized
  worktree directory basename. Pinning the project name (rather than
  letting Compose derive one from the current directory) is what
  guarantees a preview's containers, network, and volumes never collide
  with the primary stack's default project name or with another branch's
  preview — two previews for two different branches run concurrently
  without either seeing the other.
- **Ports** — default 8001 (web) / 5433 (db), one above the primary
  stack's 8000/5432, overridable via `--port`/`--db-port` when those
  collide with something else already running (see the port table in
  `docs/DEV.md`).
- **Data directory** — `data/preview/<branch>/{postgres,documents,inbox}`,
  asserted by the launcher script to always resolve under `data/preview/`
  before any create or delete, so a bug in branch-name handling can never
  make preview cleanup touch the primary stack's `./data`.
- **Cookie jar** *(amendment, 2026-09-16)* — **a dedicated port buys no
  isolation in a browser.** A cookie's scope is scheme + host + path, and
  the port is explicitly not part of it (RFC 6265 §8.5, "cookies do not
  provide isolation by port"), so `localhost:8000` and `localhost:8001`
  share one jar: with every stack writing `sessionid` and `csrftoken`,
  whichever tab rolls its session last wins and the others are silently
  signed out mid-click — while their server-side session rows stay alive,
  which is what makes the symptom read as an identity bug rather than a
  browser one. Each preview therefore names both cookies after its own web
  port (`sessionid_8001`, `csrftoken_8001`): `config/settings.py` reads
  `FARABUNKER_SESSION_COOKIE_NAME` / `FARABUNKER_CSRF_COOKIE_NAME`,
  defaulting to Django's own names so the primary stack and CI are
  untouched, and `compose.preview.yaml` keys both on `PREVIEW_WEB_PORT` —
  the one thing already unique per stack, since two previews cannot publish
  the same host port.

  **Recorded as an amendment because the omission is the whole story.**
  This section's own heading enumerates three dimensions, and this is a
  fourth; it was discovered when four stacks ran at once on one machine and
  the enumerated three turned out not to include the one that mattered. A
  reader who takes "dedicated ports" as sufficient draws exactly the
  conclusion that produced the collision, which is why the gap is named
  here rather than left to `docs/DEV.md` — that file tells an operator what
  happens, and this is where the contract lives.

### 5. Non-destructive by default; destruction is opt-in and scoped to one branch

`scripts/preview down <branch>` stops and removes the preview's containers
but leaves `data/preview/<branch>/` in place, so re-running `up` on the
same branch resumes with the same data rather than re-migrating from
scratch every time. `scripts/preview reset <branch> --yes` is the only
command that deletes data, requires the explicit `--yes` flag, and is
scoped — by both the branch-name validation and a second resolved-path
assertion — to exactly `data/preview/<branch>/`, never the preview root or
the primary stack's data.

## Consequences

- A worktree branch can be smoke-tested end-to-end in a browser, against
  its own code and its own database, without ever touching the primary
  stack — closing the gap this ADR opens with (§Context).
- Running a preview costs a full `docker compose ... up -d --build` per
  branch (its own `web`/`watcher` images, its own Postgres instance), so
  previews are heavier than the unit suite and are expected to run one or
  two at a time locally, not as a CI matrix.
- Because previews share the host's Ollama, a preview cannot validate a
  branch that itself changes model-pulling behavior — that gap is
  accepted, since the platform is never supposed to pull models in the
  first place (ADR 0010 §5).
- A fresh database on every first `up` means a preview cannot be used to
  reproduce a bug that only manifests against long-accumulated primary-
  stack data; that still requires debugging against a copy of the primary
  stack's own `./data`.
- The port/project-name/data-dir isolation contract is enforced by
  `scripts/preview`'s own validation, not by Docker or the filesystem — a
  future change to that script must preserve the guards documented in §4
  and §5 (see the script's own comments for the specific defenses) or the
  isolation this ADR promises quietly breaks.
