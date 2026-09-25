---
name: deploy-window
description: Use when deploying origin/dev to the live box at :8000 -- fast-forwarding the root checkout, running migrations, restarting containers, or announcing/closing a deploy window.
---

# Deploy window

## Purpose

The root checkout is production and bind-mounts into `web`/`watcher`/`worker` -- a bad merge
there takes the live site down the moment the autoreloader sees it. Exact, ordered procedure.

## Steps

1. **Announce the window** to every peer session: what lands (the PR/SHA),
   expected downtime, whether migrations run. Wait for their ack before any
   root-checkout or container operation. **One window at a time, machine-wide**:
   a second window waits for "WINDOW CLOSED".
2. **Ancestry guard.** `git rev-list --max-parents=0 <ref>` on the ref you're
   about to deploy must print exactly one line -- the repository's single root
   commit (`a4d1033b5a61320f5efb4666ae8fdb0bede88450` in this repository). More
   than one line, or a different SHA, means you have the wrong ref or a
   foreign history: stop.
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
   `docker compose -p farabunker exec -T web python manage.py migrate --noinput`.
   `-T` because an agent shell has no TTY; `--noinput` so a prompt can never
   hang the window; `-p farabunker` pins the project regardless of cwd --
   `docs/OPERATIONS.md` shows the shorter interactive form of the same
   commands.
6. **Restart, always including `web`.** `web` auto-reloads under the dev
   override but not on a production-style stack (`docker compose -f
   compose.yaml`, no auto-reloader) -- restarting it is a no-op either way, so
   every restart set names it (`docs/OPERATIONS.md`, "Deploying the chat
   cluster to a live box"). Every command is pinned to the project with
   `-p farabunker`, for the same cwd-independence reason as steps 5 and 7:
   - runtime/queue/ingest code, or anything web-visible:
     `docker compose -p farabunker restart web watcher worker`
   - template/CSS only: `docker compose -p farabunker restart web`
   - environment variables changed: `docker compose -p farabunker up -d
     --force-recreate web watcher worker` -- `restart` does not re-read
     `.env`, only a recreated container does; never a bare `docker compose up
     -d` for a service already running (`docs/OPERATIONS.md`).
   - docs-only: no restart.
7. **Probe.** `docker compose -p farabunker exec -T web python manage.py check`
   (a known `W003` on an open-posture box is acceptable, nothing else is).
   Then HTTP 200 on the front pages.
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
- **Non-fast-forward merge.** Never push to `dev` directly or merge into it
  outside the PR flow -- a non-fast-forward here means someone did. Stop;
  this is a process violation to report, not something to force through.
- **Skipping the ack wait.** Deploying before every peer has acknowledged the
  window is exactly the collision this procedure exists to prevent.

## See also

`AGENTS.md` "The working loop" and "Never touch, without that authorization";
`docs/DEV.md` §8 rungs 3-4; `docs/OPERATIONS.md` for migration-specific deploy
notes (e.g. the Identity & Auth migration sequence, or the chat cluster's two
dependent migrations) when a change needs more than a plain `migrate`, and for
the interactive (human-at-a-terminal) form of the compose commands above.
