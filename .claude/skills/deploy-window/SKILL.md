---
name: deploy-window
description: Use when deploying origin/dev to the live box at :8000 -- fast-forwarding the root checkout, running migrations, restarting containers, or announcing/closing a deploy window.
---

# Deploy window

## Purpose

The root checkout is production (`docs/DEV.md` rung 3, `AGENTS.md` "The working
loop"). It bind-mounts into `web`/`watcher`/`worker`, so a bad merge there takes
the live site down the moment the autoreloader sees it. This is the exact,
ordered procedure for moving it forward -- no step skipped, no step reordered.

## Steps

1. **Announce the window** to every peer session: what lands (the PR/SHA),
   expected downtime, whether migrations run. Wait for their ack before any
   root-checkout or container operation.
2. **Ancestry guard.** `git rev-list --max-parents=0 <ref>` on the ref you're
   about to deploy must print exactly one line -- the repository's single root
   commit. More than one line, or the wrong SHA, means you have the wrong ref
   or a foreign history: stop.
3. **Clean tree.** `git status --short` in the root checkout must be empty.
   Anything staged or modified gets triaged, never overwritten.
4. **Fast-forward**, from the root checkout, as two plain commands (never
   piped or chained -- the worktree guard refuses compound forms):
   ```bash
   git fetch origin dev
   git merge --ff-only origin/dev
   ```
   A merge that is not a fast-forward means someone pushed to `dev` outside a
   PR. Stop and report; do not force anything.
5. **Migrate**, only if the delta carries migrations:
   `docker compose exec web python manage.py migrate` (the exec form; bare
   `manage.py migrate` in a runbook is shorthand for this same command).
6. **Restart, always including `web`.** `web` auto-reloads under the dev
   override but not on a production-style stack (`docker compose -f
   compose.yaml`, no auto-reloader) -- restarting it is a no-op either way, so
   every restart set names it (`docs/OPERATIONS.md`, "Deploying the chat
   cluster to a live box"):
   - runtime/queue/ingest code, or anything web-visible: `docker compose restart web watcher worker`
   - template/CSS only: `docker compose restart web`
   - docs-only: no restart.
7. **Probe.** `docker compose exec web python manage.py check` (a known
   `W003` on an open-posture box is acceptable, nothing else is). Then HTTP
   200 on the front pages.
8. **Fresh pixels.** Verify the shipped feature, live, on `:8000`, in a
   browser, before any success language (`docs/DEV.md` rung 4).
9. **Close the window**: "WINDOW CLOSED" to peers, with the deployed SHA.
10. **Ledger entry** recording what landed and at what SHA.

A session working from a worktree leaves it for the root checkout **only**
inside the announced window, and returns to its own worktree afterward.

## Failure modes

- **Conflicted root checkout.** Never leave it that way -- it is picked up
  live. Resolve immediately or restore from the last known-good SHA.
- **Container has no git.** All git operations here are host-side, against
  the root checkout; there is no git inside `web`/`worker`/`watcher`.
- **Non-fast-forward merge.** Someone pushed straight to `dev`. Stop; this is
  a process violation to report, not something to force through.
- **Skipping the ack wait.** Deploying before every peer has acknowledged the
  window is exactly the collision this procedure exists to prevent.

## See also

`AGENTS.md` "The working loop" and "Never touch, without that authorization";
`docs/DEV.md` §8 rungs 3-4; `docs/OPERATIONS.md` for migration-specific deploy
notes (e.g. the Identity & Auth migration sequence, or the chat cluster's two
dependent migrations) when a change needs more than a plain `migrate`.
