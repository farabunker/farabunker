# Operations: backup and restore

This is the operator's home for backing up and restoring a farabunker box (W3,
[ADR 0014 §18](adr/0014-media-ingestion.md)). It assumes the stack from
[docs/DEV.md](DEV.md) is already up; it does not repeat that setup.

## What a backup contains

**Your original files plus a database dump reproduce everything else.**

- **Your original files are irreplaceable — backed up.** Every document you
  ingested (`data/documents/<id>/…`, including its cached `extract.json`
  extraction) and every image-generation output (`data/generated/<job-uuid>/…`)
  is a file the platform owns and cannot re-derive: re-running extraction means
  hours of GPU time and, if the model has since changed, *different* text; a
  generation job's output is a non-deterministic engine artifact no re-run
  reproduces.
- **Consolidated workstream notes are backed up exactly like any other
  document, not as a directory of their own.** `rag.consolidate` distils a
  stream conversation into a note and stages it through the SAME
  `stage_document`/managed-store path every ingested file takes (Workstreams,
  spec §10.4): the durable copy retrieval actually reads lands at
  `data/documents/<doc-id>/…`, already covered by the bullet above, and the
  `Document` row (`origin=notes`, `workstream` set) is a normal Postgres row
  your `pg_dump` already carries.
- **Backups now contain documents no non-admin library page ever lists.** A
  contained document — a note, or an ordinary upload placed into a stream —
  is out of `/rag/documents/`, out of Ask and out of Search for every
  principal but an administrator: containment is a corpus rule, not a
  permission rule (spec §13), and it binds even for a member who could
  otherwise see everything. Your `pg_dump` still carries the row and your
  file copy still carries the bytes: a restore does not lose a stream's notes
  or its contained documents just because the ordinary library page never
  showed you they existed. They surface on the stream's own page instead.
- **Everything else lives in Postgres and is backed up as a `pg_dump`, never
  as a file copy** — the document library's status/metadata, categories, Ask
  history, operator settings, the model registry, the execution queue's own
  tables, the agents column's own tables (`Agent`, `Conversation`,
  `Turn`, `ToolInvocation`), and — as of IA-1 — the identity column's own
  tables (`User`, `IdentitySettings`, `AuditEvent`; see below).
- **P2 dropped the old `rag_chatsession`/`rag_chatmessage` tables**
  (`rag.0013_retire_chat_tables` — write-only, never read back by anything
  shipped, so nothing loses a feature). A dump taken before that migration
  may still contain rows in them; restoring it recreates the tables
  harmlessly (`pg_restore --clean --if-exists` brings back whatever the
  dump's own schema was), and the next `manage.py migrate` drops them again.
  There is no data migration into `agents.Turn` for those old rows — see
  the migration's own docstring for why fabricating one was rejected.
- **The search index (`data_rag_chunks`, the pgvector chunk table) is
  derivable, but not free.** It ships inside your `pg_dump` by default. If you
  want a smaller dump, exclude it yourself
  (`pg_dump --exclude-table-data=data_rag_chunks`) and rebuild it after
  restore with `manage.py reencode --role rag.embed` — that re-embeds every
  document, which can take a while. Rule of thumb: roughly 4 bytes per
  embedding dimension per chunk for the vector itself (a 768-dim model is
  about 3 KB/chunk), plus the chunk text and its metadata; `backup` prints the
  actual row count and size of your own index every time it runs, so you don't
  have to estimate.
- **Never copied, on purpose:**
  - `data/documents/<id>/work/` — transcode/extraction scratch space, cleaned
    up on success and only ever a resume aid.
  - `data/tmp/`, `data/postgres/`, `data/preview/` — never walked at all (see
    below).
  - `data/inbox/` — files dropped for the watcher but not yet ingested aren't
    the platform's yet; `backup` warns you how many are waiting instead of
    silently including or dropping them.
  - `data/notes/` — the STAGING original of a consolidated note, one
    Markdown file per distilled stream conversation
    (`<conversation-uuid>.md`), written by the `rag.consolidate` job. Never
    walked or copied: the copy that matters is the managed one under
    `data/documents/<doc-id>/…`, already covered above, and the original in
    `data/notes/` is left in place afterwards purely so a directory the
    watcher never looks at closes a question rather than opening one — see
    below.

## `data/notes/`: why the watcher must never be pointed at it

`config/settings.py::NOTES_DIR` (`DATA_DIR / "notes"`) is **deliberately NOT
under `data/inbox/`.** The watcher (`manage.py ingest_watch`) polls the inbox
directory and stages whatever it finds there as a new, universal document
with a service principal for an actor. A note written into the inbox would
therefore be staged **twice** — once by the consolidation job that produced
it (correctly, as a **contained** document under its own stream) and a
second time by the watcher a moment later (as a loose, universal duplicate
nobody asked for, under a different actor). Pointing the watcher at
`data/notes/` — by editing its configured directory, by a symlink, or by any
other means — reintroduces exactly that double-stage, for every note the box
has ever consolidated. `NOTES_DIR` costs one settings constant and the
watcher's own inbox scan never looks at it; keep it that way.

`data/notes/` itself needs no backup attention: it is never walked (the
bullet above), and the content that matters — the note's durable copy and
its `Document` row — is backed up exactly like any other document.

## The watch inbox's trust boundary (B-3, round-3 hardening)

`data/inbox/` is a **host directory this platform does not own** — the watcher
(`manage.py ingest_watch`) only polls it, the way any other process on the box could.
On a single-owner box with no file share, writing into it is operator trust, and this
platform treats it accordingly: the watcher indexes **regular files only** and
**symlinks are ignored** outright, never followed, so a link planted in the inbox
can never make its target's bytes a new document, and can never be moved out of
wherever it actually lives.

That trust assumption stops holding the moment the inbox is exposed as a drop
folder — a network share, a shared volume mounted into more than one account's reach
— which is the exact workflow the watch folder exists for. **Exposing `data/inbox/`
as a network share makes every account on that share a library author**: anything
any of them can write there, the watcher will stage as a new, universal,
unlabelled document under the service principal, exactly as if it had come through
the browser upload form. Restrict who can write into the inbox to the same set of
people you would trust with the browser upload form itself.

## A database dump is now a credential store

IA-1 puts three new tables in Postgres: `identity_user` (usernames and password
hashes), `django_session` (already existed, but now holds real signed-in sessions
rather than an unauthenticated box's near-empty table), and `identity_auditevent`
(who did what, when — logins, failed logins, password changes, posture switches).
A `pg_dump` you already took for the reasons above **now also contains password
hashes, session keys and the audit log** — and, as of IA-2, **entitlement
grants, document labels, tool labels and share rows**
(`identity_entitlement`, `identity_entitlementgrant`, the `agents`
column's `ToolEntitlement`/`Share`, and the `tools.rag` column's
`DocumentEntitlement`) — and must be handled the way you would
handle any other credential store from this point on:

- **Encrypted at rest.** The plain folder `manage.py backup` writes is not
  encrypted by anything in this platform (see "Offline by design" below) — put
  it on an encrypted volume, or encrypt the folder yourself, before it leaves
  the machine that made it.
- **Never mailed.** A dump attached to an email or dropped in a cloud-synced
  folder puts every account's password hash and every session key on a server
  this project never asked you to trust.
- **Never committed.** A dump belongs nowhere near a git repository, this one
  included — `git log` remembers a committed file long after it is deleted.

None of this is new *behaviour*; it is a new *consequence* of behaviour this
document already described. The backup and restore commands below are
unchanged by IA-1 — only what is riding inside the dump they produce.

**The code now enforces the file-permission half of "encrypted at rest"
directly.** `manage.py backup` writes its destination directory `0700`
and every file inside it — the copied documents and generated media,
`manifest.json`, and `db.dump` — `0600`: owner read/write only, nothing
for group or other, regardless of the container's umask. That closes the
part of the exposure code can close on its own; it is not a substitute
for the other two bullets above. (An earlier ruling asked for the default
destination to live entirely outside the repo checkout; that has been
WITHDRAWN — inside the container `/app` *is* the checkout and `/app/data`
is the host-mounted durable volume, so a default anywhere else would be
container-local and ephemeral, losing every backup on `docker compose
down`, which is worse than the exposure it would be fixing. The file
modes above are the actual fix; `BACKUP_DIR` staying inside the durable
volume is what makes a backup survive the container it was taken from.)
What an operator should still do:

- **Point the destination at an encrypted volume.** Set
  `FARABUNKER_BACKUP_DIR` (see `.env.example`) to a mounted, encrypted
  path — `manage.py backup` run with NO destination argument now writes
  into a fresh, timestamped directory under it
  (`settings.BACKUP_DIR/<timestamp>`, `BACKUP_DIR` default
  `<FARABUNKER_DATA_DIR>/backups`, the same durable host-mounted volume
  the document and generated-media stores already use — never merely
  "outside the checkout", which inside the container would mean
  container-local and ephemeral, losing every backup on
  `docker compose down`), so running it again produces a SECOND backup
  rather than colliding with the first. Naming a destination explicitly,
  the way the documented sequences above always have, still works
  exactly as before and still takes priority.
- **An existing backup directory keeps its old, wider modes.** "The destination directory" here
  means this RUN's own leaf — `BACKUP_DIR/<timestamp>` (or whatever path was named explicitly) —
  not `BACKUP_DIR` itself. That leaf is always retightened to `0700` on every run into it,
  including a `--force` reuse of one that already existed — but the CONTENTS of an earlier backup
  already sitting at that destination are never re-walked: a file this run doesn't touch (left
  over from a run made before this code existed) keeps whatever mode it already had.
  `BACKUP_DIR` — the shared root every run's leaf lives under — is a different case again: it is
  created with `mkdir(parents=True)` the first time a leaf is written under it, and a `mkdir`
  never applies a mode to the PARENT directories it creates along the way (only to the leaf) — so
  a freshly created `BACKUP_DIR` sits at whatever the container's default umask produces
  (typically `0755`), never retightened by this code on any later run, because it is not the
  destination this code owns. Fix either by hand once: `chmod -R go-rwx <dest>` for stale
  contents inside a leaf, `chmod 0700 <BACKUP_DIR>` for the shared root itself.

## Rotating the database password

The password is `POSTGRES_PASSWORD` in `.env` and nowhere else: both compose files interpolate it
into the `db` service and into every app service's `DATABASE_URL`, so one value is the whole
credential. Changing the line in `.env` alone is **not** a rotation — Postgres already stores the
old one, and the next `docker compose up` brings a database that refuses the new password.

Any box that has ever run with the shipped default (`farabunker`) should be rotated, because that
value is in this repository's history and this repository is going public.

**Every `.env` created before this change is missing `POSTGRES_PASSWORD` entirely — add it
before running *any* compose subcommand, not only before rotating.** The `${POSTGRES_PASSWORD:?…}`
guard lives in the top-level `x-engine-env` anchor, so it renders for every subcommand
(`up`, `down`, `logs`, `ps`, `scripts/preview down`, …), and compose refuses to run at all until
the variable exists — even a `docker compose logs` on an otherwise-untouched box. Add
`POSTGRES_PASSWORD=<the password this box's Postgres was initialised with>` to the existing
`.env` first; that line by itself changes nothing (Postgres already has that password), it only
tells compose the value it already needs to render. Only once that line is present does the
rotation sequence below apply.

Do it in this order. It takes the box down for the duration.

```bash
# 1. Stop everything that holds a connection. The db keeps running.
docker compose stop web watcher worker

# 2. Change the password INSIDE Postgres, using the current credential.
docker compose exec -T db psql -U farabunker -d farabunker \
    -c "ALTER USER farabunker WITH PASSWORD '<the new password>';"

# 3. Put the same value in .env (the file, not the shell) -- in BOTH
#    places. POSTGRES_PASSWORD is what the containers compose from; the
#    native DATABASE_URL line (used by anyone running Django outside the
#    containers) points at the SAME Postgres user and ALTER USER just
#    changed the password it needs, so it goes stale here too. Percent-
#    encode the password inside the URL (see Notes below).
#    POSTGRES_PASSWORD=<the new password>
#    DATABASE_URL=postgres://farabunker:<the new password, percent-encoded>@localhost:5432/farabunker

# 4. Recreate the app services so they read the new value. `up -d`
#    alone will not: compose only re-reads .env for containers it
#    recreates.
docker compose up -d --force-recreate web watcher worker

# 5. Prove it.
docker compose exec web python manage.py check
docker compose logs --tail=20 web
```

Notes:

- **Pick the password with a generator**, not by hand: `python -c "import secrets;
  print(secrets.token_urlsafe(32))"`. It never has to be typed by a person.
- **A `%`, `@`, `/`, `:` or `#` in the password breaks `DATABASE_URL`**, which is a URL — those
  characters must be percent-encoded there. `token_urlsafe` produces none of them, which is why it
  is the recommendation.
- **`data/postgres/` is not affected.** The password lives in Postgres's own catalogue inside that
  directory; `ALTER USER` rewrites it in place. No dump, no restore, no data movement.
- **A backup taken before the rotation still carries the old hash.** That is correct and expected: a
  restore of that dump restores the old password too, and this runbook is then re-run against it.
- **Re-seeding a preview's `.env` does NOT rotate that preview's own database.** `scripts/preview`
  only writes `.env` for a worktree that doesn't already have one, and even a fresh `.env` would
  not help: Postgres reads `POSTGRES_PASSWORD` once, at first initialization of its data
  directory, and a preview whose data directory was already initialized keeps the password it
  started with regardless of what `.env` says afterward -- same rule as the main box's
  `data/postgres/`. To rotate a running preview's password, either run the same `ALTER USER`
  sequence above against that preview's own `db` container
  (`docker compose -p farabunker-preview-<branch> exec -T db psql -U farabunker -d farabunker
  -c "ALTER USER farabunker WITH PASSWORD '<the new password>';"`, then update that preview's own
  `.env`), or run `scripts/preview reset <branch> --yes` (stops the preview and permanently
  deletes its data directory) followed by `scripts/preview up <branch>`, which re-seeds `.env`
  from the main repo's current one and re-initializes Postgres with that value.

## Upgrading to the non-root container user (S12)

**Before this change**, `Dockerfile` ran the `web`, `watcher`, and `worker` processes as `root`;
combined with `compose.yaml`'s read-write `.:/app` bind mount, a compromise of the Django process
was root **inside** the container and write access to the host's application source, which the
next restart executes. **After**, `Dockerfile` creates a fixed user (`farabunker`, uid/gid
`10001`) and everything from `COPY . .` onward runs as that user; every compose service also sets
`security_opt: [no-new-privileges:true]`, so a process that does gain root inside the container
cannot use a setuid binary to climb back out of the user drop.

**Any box that ran the stack before this deploy wrote `./data` as root.** uid `10001` cannot write
there until ownership is reconciled. Run this once, before the next rebuild:

```bash
docker compose stop web watcher worker
sudo chown 10001:10001 ./data
sudo find ./data -mindepth 1 -maxdepth 1 ! -name postgres -exec chown -R 10001:10001 {} +
docker compose up -d --build
```

**`sudo chown 10001:10001 ./data` (no `-R`) comes first and touches only the `./data` directory
entry itself, not its contents.** The `find` line already reconciles every existing child
directory recursively; the plain `chown` on `./data` is what lets uid `10001` create BRAND-NEW
top-level entries under it later — `./data/backups`, for instance, does not exist at chown time
and the `find` line cannot reach a directory that isn't there yet. Non-recursive is required for
the same reason the `find` line excludes `postgres` by name: `./data/postgres` sits one level
below `./data` and must never be `chown`'d, and a `-R` here would reach it.

**`./data/postgres` is deliberately EXCLUDED from that command, and must never be `chown`'d.**
The `db` service's own container runs as whatever uid the upstream `pgvector/pgvector` image
ships, and Postgres owns its data directory's ownership itself — it checks the ownership of its
data directory against the uid actually running the `postgres` process on every start, and refuses
(or, worse, on a directory `chown`'d out from under an already-running cluster, `PANIC`s) the
moment the two disagree. This task does not touch that image or that uid at all; only `web`,
`watcher`, and `worker` change user, so only their directories under `./data` need reconciling —
`documents`, `generated`, `inbox`, `tmp`, and anything else that is not `postgres`. `db` does not
need stopping for this step.

**Skip this and the failure mode is NOT uniform across `./data`.** `FILE_UPLOAD_TEMP_DIR`
(`./data/tmp`) is created eagerly, at Django settings-import time, in every one of `web`,
`watcher`, and `worker` — so a root-owned `./data/tmp` makes **every one of those containers
refuse to start at all**: `docker compose ps` reports the affected service(s) `Restarting`, not
`Up`, and `docker compose logs` names the directory and the fix directly:

```
django.core.exceptions.ImproperlyConfigured: Cannot create FILE_UPLOAD_TEMP_DIR
(/app/data/tmp): [Errno 13] Permission denied: '/app/data/tmp'. This is almost
always ./data still being owned by root from before this box ran the
container's non-root user (S12, uid/gid 10001) -- see docs/OPERATIONS.md
§"Upgrading to the non-root container user" for the chown sequence.
```

Every OTHER directory under `./data` (`documents`, `generated`, `inbox`) is created lazily, on
first write, so a root-owned one of those is quieter: the containers stay `Up`, and the failure
surfaces later, as a bare

```
PermissionError: [Errno 13] Permission denied: '/app/data/<path>'
```

on the first ingest, browser upload, or generation job that reaches it — or `[Errno 1] Operation
not permitted` instead of `Errno 13` if the directory already exists and only the attempt to
create a file or subdirectory inside it is refused. Either way, the fix is the same `chown` above,
run once; no rebuild is needed for a data-ownership fix by itself afterward, only `docker compose
restart web watcher worker`.

**`manage.py backup`'s default destination (`./data/backups/<timestamp>`) names the fix directly,
the same way `FILE_UPLOAD_TEMP_DIR` above does, instead of surfacing that bare `PermissionError`.**
`./data/backups` is exactly the "brand-new top-level entry" the `chown 10001:10001 ./data` line
above exists for — an operator who has run the `find` command but never the plain `chown ./data`
sees this refusal, naming the same `chown` fix, the first time they run `manage.py backup` with no
destination argument.

**A fresh Linux install needs the same `chown`, before the very first `docker compose up`, not
just an upgrade.** `docker compose up -d --build` creates `./data`'s subdirectories owned by
whatever uid runs inside each container the first time each one writes there — uid `10001` now,
not root — so a *brand-new* box has nothing to reconcile **provided `./data` itself does not
already exist as a root-owned directory** (e.g. from `mkdir data` run by hand, or an `rsync`/`git`
checkout done as root). If it does, run the same `chown 10001:10001 ./data` (non-recursive) plus
`find … -exec chown …` above once, before the first `docker compose up`, to be certain — the
plain `chown` on `./data` itself is what lets uid `10001` create new top-level entries there,
such as `./data/backups`, that neither command has reconciled yet. macOS dev boxes never hit this: Docker Desktop's virtiofs
maps host/container ownership transparently, which is why this whole section is Linux-only.

**A preview stack (`compose.preview.yaml`) needs the same treatment** on its own
`PREVIEW_DATA_DIR`, the first time it is rebuilt after this change lands — `scripts/preview` does
not chown it for you:

```bash
sudo chown 10001:10001 <PREVIEW_DATA_DIR>
sudo find <PREVIEW_DATA_DIR> -mindepth 1 -maxdepth 1 ! -name postgres -exec chown -R 10001:10001 {} +
```

The plain, non-recursive `chown` on `<PREVIEW_DATA_DIR>` itself comes first for the same reason as
the upgrade block above: it is what lets uid `10001` create brand-new top-level entries there later.

**The engine bind mounts (`COMFYUI_OUTPUT_DIR`/`COMFYUI_INPUT_DIR`) are a separate case when an
operator points them outside `./data`.** Their compose default (`./data/engine/output`,
`./data/engine/input`) is covered by the `chown` above like any other `./data` subdirectory, but an
operator who instead points them at the engine's own real host directories (its native `output`/
`input` folders, owned by whatever user runs that engine natively) has handed the container a path
uid `10001` was never reconciled against — either add uid `10001` to that directory's group with
write permission, or accept that `/vision/engine-files/`'s admin delete action is unavailable for
files under those paths until it is.

**A root shell for debugging** (inspecting permissions, reading a file the non-root user cannot
open, anything that genuinely needs it):

```bash
docker compose exec -u root web sh
```

**What this task deliberately leaves for Phase 4** (`deploy/README.md`): dropped capabilities,
seccomp, and a read-only root filesystem. Only the non-root user and `no-new-privileges` land now
— both cost nothing today, and the rest needs more design work (exactly which capabilities the
document parsers need, what a read-only rootfs breaks).

## Pinning the Python base image by digest (owner step)

`Dockerfile`'s `db` counterpart (`pgvector/pgvector:pg16` in `compose.yaml` and
`compose.preview.yaml`) is already pinned by digest, so two rebuilds of the same commit use the
same Postgres image — see the comment above that line. `Dockerfile`'s own base,
`python:3.12-slim`, is deliberately left tag-only, marked with a `# digest: TODO(owner)` comment:
resolving its current digest needs a registry pull, which the environment that wrote this pin was
not permitted to make.

**Close it once, from a box that can reach the registry:**

```bash
docker buildx imagetools inspect python:3.12-slim
```

The output names the manifest digest for the platform you build for. Put it in `Dockerfile`,
replacing the tag-only line:

```dockerfile
FROM python:3.12-slim@sha256:<digest>
```

and remove the `# digest: TODO(owner) …` comment above it. Rebuild and run the suite before
committing — a wrong digest fails the pull outright, so there is no silent failure mode here.
`docs/DEV.md` "Bumping a dependency" covers the same rebuild-and-verify step for the ordinary case
of a moved dependency; this is the one-time step for the base image itself.

`foundation/ops/tests/test_repo_hygiene.py`'s
`test_the_python_base_image_is_tag_pinned_with_an_owner_digest_todo` already accepts the
digest-pinned form above (`FROM python:3.12-slim@sha256:<64 hex>`, TODO comment removed) as well
as today's tag-only form — closing this step does not need a test change alongside it.

## Accounts: the pages

Two admin-only pages, plus one break-glass surface, once accounts are on
(`posture != open`):

- **`/identity/users/`** (`identity-users`) — list every account, create
  one, deactivate/reactivate, promote/demote an administrator, and reset
  a password. In the `personal` posture the superuser toggle is hidden
  outright: every account there is an administrator, so the page offers
  no choice it does not have. Reached from the app bar's Settings entry, under
  the sidebar's Access group, as "Accounts".
- **`/identity/settings/`** (`identity-settings`) — posture, library
  posture, the session idle window, and the **`admin_sees_content`**
  toggle: whether an administrator may additionally read other users'
  conversations, Ask history, generated images and document bytes.
  **Off by default** — administering this box already shows every job's
  and document's row-level facts (kind, owner, state, status) with this
  off. It carries no posture branch (present in `personal` as well as
  `enterprise`), unlike the users page's superuser toggle. In the `open`
  posture this page is reachable from a "Turn accounts on" link on
  [Setup](/setup/); once accounts are on, it is linked from the shared
  nav as "Settings", beside "Accounts", for administrators only.
- **`/admin/`** — Django's own admin, re-registered for the swapped-in
  user model as a **break-glass tool only, never a product surface**
  (Django's `AdminSite.register` silently ignores a model that has been
  swapped out via `AUTH_USER_MODEL`, so without this re-registration
  `/admin/` would show nothing an operator could act on at all). Its
  `save_model` delegates every guarded write — promote/demote,
  deactivate/reactivate, create — to the same `identity.services`
  functions the pages above use, so the last-admin guard and the audit
  trail apply here exactly as they do there; the per-user permission
  catalogue (`user_permissions`) and the per-user password-change form
  are both unreachable from this surface, and accounts are never
  deleted from it (or anywhere else). **`auth.Group` is unregistered
  outright, not re-registered and not left as Django ships it** (A-2,
  security round 3): the identity page's own groups page is the one
  door for a group's create and delete (groups have no rename
  primitive), and it writes the audit row each of those actions gets;
  the stock `Group` admin wrote none of them while also being able to
  cascade-delete every entitlement grant and every share whose subject
  was that group. So `/admin/` reaches `identity.User` and nothing
  else.

## Login lockout

Sign-in refuses further attempts for a username after **5 failures within 15 minutes**, counted
from the audit rows the login page already writes on every failed attempt — no separate lockout
table, and the count is per username rather than per network address. The refusal message is the
same whether or not the username belongs to a real account, and it names how many minutes remain
(a fixed number for any locked-out username, so this adds no way to tell one account from
another).

**A failed attempt is counted under its canonical username** — whitespace stripped, Unicode
normalised (NFKC) — the same key the lockout check reads back with, so a run of attempts spelled
with stray whitespace or an alternate Unicode spelling of the same name still trips the same
account's lockout rather than silently missing it.

**A successful sign-in does not reset the count.** The audit trail is append-only, so failures
from just before a correct password are still inside the window and can still contribute to a
lockout a few minutes later; there is no separate "good standing" flag a successful login sets.

**There is no unlock command, deliberately.** An operator clears a lockout by waiting out the
15-minute window; a command that lifted it early would be a second authentication path onto the
box. `/identity/users/` (see "Accounts: the pages" above) shows whether an account is otherwise
in good standing. The attempts that triggered a lockout are the `identity.login_failed` rows in
the `identity_auditevent` table (there is no page for this yet — the table is append-only and
queried directly, the same way you would read any other audit row on this box).

**Accepted trade-off:** anyone who knows a username can keep it locked out indefinitely by sending
5 requests every 15 minutes — a small, deliberate denial-of-service cost against being able to
guess a password at all, or exhaust CPU hashing one, at any real rate.

## Deploying Identity & Auth (IA-1) to a live box

**This deploy runs migrations, including a `RunPython` step.**
`identity.0001_initial` creates the `identity_user`/`identity_
identitysettings`/`identity_auditevent` tables and swaps `AUTH_USER_
MODEL`; `identity.0002_repoint_admin_log_fk` repoints `django_admin_log.
user_id` off the retired `auth_user` table and onto `identity_user`,
behind an **emptiness precondition** on both `auth_user` and `django_
admin_log` — it refuses rather than guesses whenever either table holds
a row it does not know how to move (see "If `identity.0002_repoint_
admin_log_fk` refuses" below for the remedy).

Run the following, **in this order**, on any box that existed before
IA-1:

**1. Take a backup first.** This migration's reversal is restoring that
backup — there is no reverse migration that safely undoes the FK
repoint once other rows may depend on it. See "Back up" below for the
exact commands.

**2. Run `SELECT count(*) FROM auth_user; SELECT count(*) FROM
django_admin_log;`** against the live box, so a refusal from
`identity.0002_repoint_admin_log_fk` is anticipated rather than a
surprise — a nonzero count in either table means that migration will
refuse, and you want the remedy ("If `identity.0002_repoint_admin_log_fk`
refuses" below) already decided before it does.

**3. STEP 0: run `manage.py identity_repair_migration_history`, dry-run
first, then `manage.py migrate`.** Every box that ever ran `migrate`
before this column existed already has `django.contrib.admin`'s
migrations applied, and `admin.0001_initial` carries a
`swappable_dependency` on the user model's own first migration — which,
on such a box, has never run. Plain `manage.py migrate` refuses
outright, before applying anything, with:

```
django.db.migrations.exceptions.InconsistentMigrationHistory: Migration
admin.0001_initial is applied before its dependency identity.
0001_initial on database 'default'.
```

```bash
manage.py identity_repair_migration_history --dry-run
manage.py identity_repair_migration_history
manage.py migrate
```

`identity_repair_migration_history` forgets the already-applied
`admin.*` rows in `django_migrations`, applies `identity.0001_initial`
for real (its tables are new and empty), then re-creates the `admin.*`
rows with their ORIGINAL `applied` timestamps, all inside one
transaction. It writes no row to `auth_user` or `django_admin_log` —
repointing `django_admin_log`'s foreign key stays `identity.
0002_repoint_admin_log_fk`'s job, run by the plain `manage.py migrate`
that follows. It is not otherwise data-free: the nested `migrate` it
runs fires Django's own `post_migrate` signal exactly as any `migrate`
would, so it creates the `ContentType` and `Permission` rows `identity`'s
three new models need — the same rows a fresh install's first `migrate`
creates for every app.

**It refuses, rather than guessing, on any other shape**, naming exactly
what it found:

```
CommandError: identity.0001_initial is already applied on this database
-- nothing to repair. Run `manage.py migrate` directly.
```

```
CommandError: This database has no applied admin.* migrations, so it is
not the pre-IA-1 shape this command repairs -- nothing to repair (a
fresh install applies identity.0001_initial before admin's own
migrations, with no help needed). Run `manage.py migrate` directly.
```

```
CommandError: identity.0002_repoint_admin_log_fk recorded as applied
without identity.0001_initial itself -- this is not a shape this
command can safely repair. A human needs to look at django_migrations
by hand before anything runs `migrate` again.
```

```
CommandError: identity_user exists but no identity.* migration is
recorded as applied -- this looks like a half-run migration, not the
pre-IA-1 shape this command repairs. A human needs to look at
django_migrations and the schema by hand before anything runs `migrate`
again.
```

The last two both refuse in `--dry-run` too — they name shapes this
command was never written to guess about, not ones where reporting a
plan is safe.

**A fresh install needs none of this.** Nothing has applied `admin.
0001_initial` yet, so `migrate`'s own plan applies `identity.
0001_initial` FIRST — exactly what the swappable dependency demands —
and `identity_repair_migration_history` has nothing to do.

**4. Run `manage.py migrate`** (STEP 0 above already tells you to, as
its own last line). This applies `identity.0002_repoint_admin_log_fk`
and everything after it, including the FK repoint's own emptiness
precondition described above.

**The live box stays in `open` posture on merge.** This deploy adds a
column and a set of pages; it does not flip anything for the box's
existing users. Nothing changes until an operator *deliberately* runs
the four-command sequence below.

**First-admin adoption is a separate, deliberate step, run by hand after
the deploy — never automated:**

```bash
manage.py createsuperuser
manage.py adopt_open_rows --user <name> --dry-run
manage.py adopt_open_rows --user <name>
manage.py identity_posture personal        # or: enterprise
```

Read the `--dry-run` counts before running the claiming pass for real —
they are the only preview of what "every row owned by the open
principal, or left blank" actually means on THIS box's data.

**`SECRET_KEY` and `DEBUG` must be real before the posture is switched.**
`identity.services.set_posture` refuses the switch away from `open`
otherwise, at run time as well as at boot (`manage.py check`'s
`identity.E001`/`identity.E002`) — see "Switching posture: the three
refusals" below for the exact messages.

**Restart `watcher` and `worker` after this deploy.** `identity/
middleware.py`, `identity/access.py`, and the acting-principal changes to
the job-kind runtime (every payload now carrying an `actor_kind`/`actor_key`
pair, and the turn loop deriving its principal from them) are code those
two processes hold in memory for the life of the process — a plain
`docker compose restart watcher worker` after the deploy, same as any
other job-kind change (see [docs/DEV.md](DEV.md)'s restart rule).

## Turning on accounts

A box that has been running `open` (no accounts) has conversations, ask
history and other owned rows sitting under `("open", "box")`, or under
nothing at all (`("", "")`, for rows written before the owner columns
existed). Switching the posture does not move them — that is a switch,
not a migration — so a first administrator who wants to own the box's
existing history runs this exact sequence, in this order:

**Coming from a fresh install** (`DEBUG=1`, no accounts yet)?
`createsuperuser` and `adopt_open_rows` below run regardless of posture,
but the last line, `identity_posture`, refuses until `DEBUG=0`, a real
`SECRET_KEY`, and an active superuser are all in place. Those three —
**not** `ALLOWED_HOSTS` — are the whole of what `set_posture` checks
(see "Switching posture: the three refusals" below). `ALLOWED_HOSTS`
belongs in the same `.env` edit regardless, because `identity.E003`/
`identity.E004` refuse to boot the box at all once `DEBUG` is off — see
"Leaving the open posture" below, which folds this whole sequence in as
its own step.

```bash
manage.py createsuperuser
manage.py adopt_open_rows --user <username> --dry-run
manage.py adopt_open_rows --user <username>
manage.py identity_posture personal        # or: enterprise
```

Each line runs inside the `web` container: the exec form is
`docker compose exec web python manage.py <command>`, and the bare
`manage.py` above is shorthand for it.

**Order matters.** Create the superuser, adopt, *then* switch the
posture. Switching first leaves the new administrator looking at their
own empty box until they adopt — confusing, not dangerous, and
`adopt_open_rows` prints this same guidance after it runs.

`adopt_open_rows --user <username>`:

- **Refuses** unless `<username>` names an account that exists, is
  active, and is a superuser — adoption gives one account every unowned
  row on the box, which is an administrator's decision.
- **Claims** every row owned by `("open", "box")` **or** left blank
  (`("", "")`), rewriting it to that superuser, in one transaction.
- **Leaves alone** every row already owned by a person, and every row
  owned by `("service", "local")` — a row a shell path (`manage.py
  agent_turn`, `ingest`, `ask`) made is not a row the open box made, and
  claiming it would attribute an automated action to a person who never
  performed it. See `manage.py reassign_owner --from service` below for
  the deliberate, explicit way to hand one of those to a person instead.
- **`--dry-run`** prints the per-table counts and writes nothing.
- Is idempotent — running it again claims nothing — and writes exactly
  one audit event (`identity.adopted`) with the per-table counts,
  whether or not anything was actually claimed.

### Handing rows to somebody else: `reassign_owner`

The documented follow-up to deactivating somebody who owned rows (see
"Deactivating an account" below, which reports the counts worth acting
on):

```bash
manage.py reassign_owner --from <leaver> --to <somebody>
```

`--from` also accepts the literal `open` (move rows currently owned by
the open principal) or the literal `service` (move rows a shell path
made — the one case `adopt_open_rows` deliberately never touches on its
own). `--to` accepts a **username only** — never `open` or `service` —
so a `--from open` move is undone by switching the posture back, not by
running `reassign_owner` a second time toward `open`. `--kind
<owned-rows key>` narrows the move to one registered table (e.g.
`agents.conversation`); an unrecognized key is refused, naming the valid
ones. `--to` must be an active account. Like `adopt_open_rows`, it
walks every registered owned-rows table in one transaction and writes
exactly one audit event (`identity.owner_reassigned`) recording the
`from`, `to`, `kind` and per-table counts.

## Deactivating an account

`identity.services.deactivate_user` — reached by the identity pages —
never deletes a `User` row; it only sets `is_active=False`. It is
idempotent (deactivating an already-inactive account is a no-op, not a
second audit row). Two consequences follow, and neither is an accident:

- **Owned rows are left in place.** Their conversations, documents and
  other owned rows still exist and still belong to them; the deactivated
  account is simply no longer reachable to sign in and use them. The
  operation returns a per-table count of what that account owns, so you
  know whether `manage.py reassign_owner` is worth running afterwards. A
  deleted user with owned rows would be an orphan nobody could reason
  about.
- **Every signed-in session dies for free, with no sweep run.** Django's
  `ModelBackend.get_user` calls `user_can_authenticate`, which is
  `False` for an inactive user — so on the very next request that
  presents an old session cookie, `request.user` resolves to anonymous.
  There is no `Session`-table cleanup step to run and none is missing:
  writing one would be a second, weaker copy of a mechanism Django
  already maintains, and this is stated here so you don't go looking for
  it.

Reactivating an account (`is_active=True` again) does not restore any
entitlement grant IA-2 may have revoked in the meantime — a dropped
grant was a decision somebody made, and undoing it silently on
reactivation would undo that decision without anybody choosing to.

One exception belongs here: `manage.py changepassword <username>` (see
[docs/DEV.md](DEV.md)'s "Accounts, and the three postures") is Django's
own command, not `identity.services.set_password` — it bypasses this
column's guarded writes entirely and writes no audit row.

## Switching posture: the three refusals

`identity.services.set_posture` — reached identically by the posture
page, by `manage.py identity_posture`, and by nothing else — refuses to
switch this box away from `open` (into `personal` or `enterprise`)
unless all three of the following hold. **Reducing** a posture (moving
back towards `open`) is never refused by any of them: an operator whose
box is misconfigured must always be able to make it less strict.

- **No active superuser.**

  > This box has no active superuser, so switching away from the open
  > posture would lock everybody out. Run `manage.py createsuperuser`
  > first.

- **`DEBUG` is on.**

  > DEBUG is on. A box with accounts must not render tracebacks -- with
  > its settings and environment in them -- to any visitor. Set DEBUG=0,
  > together with a real SECRET_KEY and this box's ALLOWED_HOSTS, then
  > recreate the containers before switching posture. The full order is
  > in docs/OPERATIONS.md under “Leaving the open posture”.

- **`SECRET_KEY` is still the shipped development key.**

  > This box is still using the shipped development signing key. That
  > key signs every session cookie, and it is published in a public
  > repository -- anybody who can read it could mint a session for any
  > account here. Set a real SECRET_KEY and recreate the containers
  > before switching posture.

Two of these three conditions — `DEBUG` and the default `SECRET_KEY` —
are also checked once per boot by `manage.py check` (`identity.E001` and
`identity.E002` respectively). The third, no active superuser, has no
boot-time equivalent: whether an admin exists can change between one
request and the next, so only `set_posture` itself can enforce it. A
check that runs only at startup is not enough on its own regardless,
because this image starts with `migrate && <server>`: flipping the
posture on a *running* box with `DEBUG=1` or the default key would
otherwise change nothing until the next restart, which is precisely the
window in which the operator believes accounts are enforced.
`set_posture` enforcing all three conditions at request time (and from
the CLI) is what closes that window.

**The boot refusal travels with the application, not only with
`migrate` (S13).** `identity.E001`, `identity.E002` and `identity.E003`/
`identity.E004` are enforced at boot today because `manage.py migrate`
runs system checks and `migrate` happens to precede the server in the
container `CMD` (`migrate && uvicorn`) — real protection, but an
accident of that command chain: any deployment that migrates
separately (a CI step, a Kubernetes init container, `docker compose run
web migrate` followed by a plain `uvicorn`) loses it silently, and an
enterprise-posture box then serves Django's technical-500 page — with
its settings and environment in it — to any visitor. Both
`config/asgi.py` (uvicorn) and `config/wsgi.py` (the
`compose.override.yaml` dev `runserver` path) now call the one shared
helper, `identity.checks.refuse_if_boot_problems()`, which asks
`identity.checks.serious_boot_problems()` — the same three checks
above, named directly rather than the whole check registry — as soon as
the application object exists, and refuses to serve
(`django.core.exceptions.ImproperlyConfigured`, naming exactly which
conditions failed) if any of them holds, regardless of how the process
was started. It composes with the refusals above rather than adding a
fourth condition: `DEBUG` off with accounts, a real `SECRET_KEY` with
accounts, and no wildcard or empty `ALLOWED_HOSTS`, are the whole list —
the same list `manage.py check` already enforces, asked again at both
points — ASGI and WSGI alike — every deployment shape passes through.
Like every check it calls, it is silent when the settings row cannot be
read (a box mid-migration is not misconfigured) — the refusal travels
with the application, but only as far as the application can see.

A fourth check, `identity.W001`, warns when accounts are on and
`SECURE_COOKIES` is unset (see [docs/DEV.md](DEV.md)'s environment
table) — it is **not** one of the three refusals above, it is only a
warning, and it never blocks a posture switch.

A fifth check, `identity.W002`, warns the opposite direction: the
posture is `open` but at least one `identity_user` row already exists.
That combination is what a lost or restored `IdentitySettings` row
looks like from the outside — the accounts an operator created are
still in the database, but the row that used to require signing in to
reach them has reverted to open, and every caller can now act as any of
those accounts with no password. It is silent on a genuinely open box
(no accounts yet), and it never blocks anything — like `identity.W001`,
it is only a warning surfaced by `manage.py check`.

A sixth check, `identity.W003`, warns about the box's *fresh-install*
state instead: the posture is `open` **and** `DEBUG` is on, with no
accounts on either side of that combination. `identity.E001` is silent
here (it fires only once accounts are required) and `identity.W002` is
silent here too (it fires only once accounts already exist) — so a
genuinely fresh box in this exact state said nothing at all before this
check existed, even though every caller on the network is answered as
an administrator and any error renders this box's settings and
environment to them. Like `identity.W001` and `identity.W002` it is
only a warning: an open box is a posture an operator may choose, and a
check that refused to boot it would make the fresh-install path
impossible. See "Leaving the open posture" below for the way out it
points at.

## Leaving the open posture

`identity.W003` (above) is what `manage.py check` says about the state
the fresh-install default produces: posture `open`, `DEBUG=1`, no
accounts yet. **Reachability before an administrator exists comes from
the open posture, not from `DEBUG=1`** — an open box with `DEBUG=0` is
exactly as reachable and strictly safer, and nothing about creating the
first administrator (step 3 below) needs tracebacks. What `DEBUG=1`
adds is only the second half of the finding: with it on, every caller
on the network is answered as an administrator, and any error renders
this box's settings and environment to them. The check names that
combination once per boot; this section is the runbook that leaves it.

**The order below is fixed, not a preference.** `identity.services.
set_posture` — reached identically by the settings page and by
`manage.py identity_posture` — refuses to switch this box away from
`open` while `DEBUG` is on or while no active superuser exists (see
"Switching posture: the three refusals" above), so step 3's posture
switch cannot succeed before steps 1–2 have taken effect and an
administrator exists.

1. **One `.env` edit, all together**: `DEBUG=0`; a generated
   `SECRET_KEY` (`python -c "import secrets;
   print(secrets.token_urlsafe(50))"`, never typed by hand); and
   `ALLOWED_HOSTS` naming every address this box must answer to on its
   LAN (see "The names this box answers to" below for the syntax and
   examples). **All three in the same edit — never `ALLOWED_HOSTS`
   later, once `DEBUG` is already off**: `manage.py migrate` runs
   `identity.E003`/`identity.E004` the moment `DEBUG` is off, so a box
   recreated with `DEBUG=0` but a still-incomplete `ALLOWED_HOSTS`
   either refuses to come up at all (an empty or wildcard list) or
   locks out exactly the LAN name the operator is using to reach it
   (`Bad Request (400)`, `DisallowedHost` in the log) before step 3 can
   run. While you're in the file, confirm `POSTGRES_PASSWORD` is
   already present — the `${POSTGRES_PASSWORD:?…}` guard (see "Rotating
   the database password" above) refuses every compose subcommand,
   including the recreate in the next step, until it exists.
2. **Recreate the containers**: `docker compose up -d --force-recreate
   web watcher worker`. An `.env` edit only takes effect on a recreated
   container — never a restart of a process already running inside one,
   and never a bare `docker compose up -d` for a service that was
   already up (Compose only re-reads `.env` for containers it
   recreates).
3. **Create the first administrator, and adopt any existing open-box
   rows** — see "Turning on accounts" above for the exact sequence
   (`createsuperuser`, `adopt_open_rows --dry-run`, `adopt_open_rows`,
   then `identity_posture personal` or `enterprise` to switch the
   posture, in that order). That section's own sequence *is* this step.
4. **Verify the names**: from a LAN browser, visit the box using every
   hostname and IP named in step 1. `Bad Request (400)` with
   `DisallowedHost` in the log for any of them means step 1's list was
   incomplete, not that anything is broken (see "The names this box
   answers to" below).

## The names this box answers to

`ALLOWED_HOSTS` is the setting that makes every other control on this box mean what it says. A
browser decides two pages are *same-origin* from the URL in its address bar, not from anything the
server says — so a DNS name an attacker controls, re-pointed at this box's LAN address, produces a
page that can read this box's responses and its CSRF cookie unless Django refuses the request on
the `Host:` header first. That is what this list does.

- **Default:** loopback (`localhost`, `127.0.0.1`, `[::1]`) plus this machine's own hostname.
- **In the containers:** `compose.yaml` sets it to `localhost,127.0.0.1,[::1],web` — **loopback and
  the service name only.** A container's own hostname is a random hex id, not this machine's real
  name, so the compose default cannot include it the way the bare-metal default does.
- **LAN access is a deploy step, not something that works out of the box.** A browser on another
  machine on the LAN reaches this box by an address the container's default does not know about —
  its LAN IP, or a name that resolves to it. **Set `ALLOWED_HOSTS` in `.env` before the first
  `docker compose up -d` after adopting this setting**, naming every such address, for example:
  `ALLOWED_HOSTS=localhost,127.0.0.1,[::1],web,192.0.2.10,box.lan` (`192.0.2.10` here stands in for
  this box's own LAN IP; `box.lan` for whatever name resolves to it — neither is a real address).
- **Add a name** when a browser reaches this box by something else — an mDNS `.local` alias, a
  CNAME, the name a reverse proxy presents, or a bare LAN IP. Comma-separated, in `.env`.
- **An `.env` change takes effect only after the containers are recreated** — `docker compose up -d`
  again, not a request, not a restart of a process inside the running container. Compose reads
  `.env` when it builds the container's environment, not while the container is already up.
- **Never `*`.** `manage.py check` refuses a wildcard (`identity.E003`) whenever `DEBUG` is `0`,
  and `manage.py migrate` runs the checks, so a container that boots with one does not come up at
  all.
- **An empty list is the wildcard's mirror image** — instead of admitting every name it admits
  none, including the operator's own — and `identity.E004` refuses it on the same terms. **On
  compose you cannot actually reach it**: `compose.yaml` passes
  `${ALLOWED_HOSTS:-localhost,127.0.0.1,[::1],web}`, and `:-` substitutes that default for a
  variable that is unset *or empty*, so `ALLOWED_HOSTS=` written blank in `.env` yields loopback
  and the service name, not an empty list. The symptom is therefore not a refusal to boot but a
  `Bad Request (400)` from every LAN name the blank line was supposed to carry. `identity.E004`
  is the guard for the boxes that run outside compose (`manage.py` on the host with
  `ALLOWED_HOSTS=` exported), and it stays because that path has no such default behind it.
- **Behind TLS termination**, also set `CSRF_TRUSTED_ORIGINS` to the browser-facing origins, scheme
  included. Left empty on a direct plain-HTTP box, which is the supported LAN posture, Django's own
  same-origin comparison already covers every POST.

A symptom worth recognising: `Bad Request (400)` on every page, with `DisallowedHost` in the log,
means the name in the address bar is not in this list — not that anything is broken.

## The request-body cap

`FARABUNKER_MAX_REQUEST_BYTES` (default 4 GiB) is the one bound on how many bytes a single request
may *declare* in its `Content-Length` header. `foundation.uploads.RequestBodyLimitMiddleware`
refuses anything over it with a plain-text `413`, before anything — CSRF, the session, an upload
handler — has read a byte of the body.

- **What a `413` means.** The caller's request declared a body larger than this box currently
  accepts. For an ordinary upload this usually means the cap is set too low for what the box is
  actually being asked to hold — raise it, or point the operator at whatever produced the oversize
  request.
- **To raise or lower it**, set `FARABUNKER_MAX_REQUEST_BYTES` (bytes) in `.env` and recreate the
  containers (`docker compose up -d`) the same way `ALLOWED_HOSTS` above needs a recreate — Compose
  reads `.env` when it builds the environment, not on every request. Lower it on a box with a small
  data volume; the default already leaves headroom for one large legitimate upload over the
  per-file cap the document library enforces on its own.
- **The two caps must move together.** The document library's own per-file upload limit
  (Settings → Library → "Maximum upload size (GB)") is a *separate* setting, and this
  middleware runs first: raising the per-file cap above `FARABUNKER_MAX_REQUEST_BYTES` without
  also raising this one does not make the larger upload succeed — it makes an otherwise-valid
  upload at the new, higher per-file limit fail with the same `413` this section describes,
  before the per-file check ever runs. Raise both, or raise only this one to keep the existing
  headroom.
- **What this does not close.** A caller who *lies* in `Content-Length`, or sends a chunked body
  with none at all, is not caught by a header check — enforcing the real byte count as it streams
  needs the server or a reverse proxy in front of it to do that while reading. There is no reverse
  proxy anywhere in front of this box today (`deploy/` is a README), so a proxy's own body-size
  limit is the complete answer this cap is a cheap first cut toward, not a substitute for one. This
  cap costs a legitimate request nothing and closes the case where the declared size alone is
  enough to refuse before any disk or memory is spent.

## Private content never gets cached (B-8, round-3 hardening)

Every entitlement-gated byte route — a document's original file (`GET /rag/documents/<id>/
file/`, both the plain response and the 206 Range slice), its transcript (`GET /rag/
documents/<id>/transcript/`), a vision job's generated output (`GET /vision/outputs/<id>/
file/`), and a job's stored input (`GET /vision/inputs/<id>/file/`) — answers `Cache-Control:
private, no-store, max-age=0` with `Cookie` added to `Vary`, set by one shared helper
(`foundation.http.mark_private`) so the four routes can never drift from each other.

- **What this closes.** None of the four routes sent any cache directive before this — the
  framework adds none by default. On a shared appliance browser the next person to use it
  could pull a labelled document back out of the disk cache after the session that fetched
  it is gone; behind any caching proxy on the LAN, a response cached without `Vary: Cookie`
  could reach a principal who never held the entitlement that produced it. No proxy is
  deployed today (`deploy/` is a README) — this closes the browser-cache case now and the
  LAN-proxy case in advance.
- **`Vary` is additive, never assigned wholesale** — the helper patches `Cookie` onto
  whatever `Vary` a response already carries, never clobbering a header a view set itself.

## Media limits: page count, media duration, and a rendered page's pixel ceiling

Three operator-relevant bounds keep the media/vision-extraction path (`tools/rag/`,
[ADR 0014](adr/0014-media-ingestion.md) §18) from doing unbounded work on one file:

- **`RagSettings.max_document_pages`** (Settings → Library) — how many *textless* pages
  a scanned/mixed PDF may have before extraction refuses it, checked at job-start
  (round-3 hardening B-5/H28 moved this off upload time — see `tools/rag/README.md`'s
  own page-cap section). One consequence worth naming: an over-cap upload now leaves a
  `Document` row, a managed-store copy, and a queue slot behind before the refusal is
  discovered — bounded today by the request-level upload cap (S9/H10: bytes and file
  count per POST) and by the per-principal queued-job cap below (C-7/H39) — never
  unbounded, but no longer refused before anything is written either.
- **`RagSettings.max_media_seconds`** — how long an audio/video source may run before
  transcription refuses it, checked at stage-time.
- **`tools.rag.transcode.MAX_RENDER_PIXELS`** (round-3 hardening B-4/H29, review round 1
  findings 1–2; not an operator-editable setting, a module constant) — 25,000,000 (25
  megapixels), the ceiling on a single rendered PDF page's pixel count. Neither
  `pypdfium2` nor this platform's own code checked a PDF page's declared size
  (`MediaBox`) against anything before rendering it — a hand-built page can declare any
  size, and the PDF format's own 14,400-point (200in) page-size ceiling goes unenforced
  by the renderer, so a page lying about its own size could force an allocation sized to
  that lie. `rasterize_pdf_page` now reads the page's declared size first and chooses a
  GRADUATED render scale from it (`min(the shipped fixed multiplier, sqrt(25,000,000 /
  declared area))`) rather than a step function: **a very large but legitimate page (an
  architectural drawing, a poster) still renders — at a smaller, continuously-derived
  scale that lands at, up to rounding, exactly the pixel ceiling — rather than being
  refused.** Only once that chosen scale would fall below `transcode.RENDER_SCALE_FLOOR`
  (0.25× — an already-illegible ~18 DPI, past which paying for the render buys nothing
  usable) does `rasterize_pdf_page` refuse outright (naming the page's declared size, the
  ceiling, and the floor) before ever calling into `pypdfium2`, rather than rendering
  first and discovering the problem in memory pressure.
  **Worst-case peak for one page at the ceiling: `pypdfium2`'s own render buffer is BGRA
  (4 bytes/pixel) and `bitmap.to_pil()` produces a second, RGB (3 bytes/pixel) copy
  before the first is released — both can coexist, ~100MB (BGRA) + ~75MB (RGB) ≈ 175MB
  transient, a bound this platform chose rather than inherited from Pillow's own
  default.** The same 25,000,000 is also set as Pillow's own `Image.MAX_IMAGE_PIXELS`
  (at the `_require_pillow` choke point both rasterization and upload normalization pass
  through, rather than left at Pillow's own ~89.5-megapixel default), so an arbitrary
  uploaded image is held to the identical ceiling by Pillow's own decompression-bomb
  guard. See `tools/rag/README.md`'s media section for the exact figure and
  `tools/rag/tests/test_transcode_bounds.py` for the crafted-file reproductions (a
  `MediaBox` far beyond the floor; a small PNG whose header alone claims a size chosen
  specifically to fail without this explicit override).

## The execution queue's own limits: a per-principal queued-job cap, and a turn's character cap

Two operator-relevant bounds close round-3 hardening's C-7 finding: nothing stopped one
principal from filling the execution queue with unlimited jobs, and a turn's own text had no
upper bound at all.

- **`JobSettings.max_queued_per_principal`** — how many QUEUED-or-RUNNING jobs one principal
  (a signed-in member, or every anonymous caller on an open box collectively, since they share
  one identity) may hold in the execution queue at once, across every job kind. Null (the
  shipped default) means no cap — a box that never sets one behaves exactly as it always has.
  Enforced once, at the seam every job kind's `enqueue()` call already goes through
  (`models.queue.backend.enqueue`), so `rag.ask`, `agent.turn` and every `vision.*` operation
  all draw against the SAME per-principal count rather than each carrying an independent one. A
  refusal raises `models.contracts.queue.QueueQuotaExceeded` — deliberately not the existing
  `QueueUnavailable` a caller might already catch, since the two mean different things: the
  queue is working fine, this caller has simply asked for too much of it. **Every door answers
  it as a 429**, naming the counts the queue itself named, never a 500 and never the 503 that
  means the queue is broken — including the vision create page, the last one to get its own
  clause, which additionally KEEPS the images an operator just attached rather than discarding
  them the way its other refusals do: a quota refusal is the try-again-in-a-minute case, and
  the files are still staged when they do. Jobs stamped
  `SERVICE_PRINCIPAL` (the watch inbox, the registry's rematerialize action, every `manage.py`
  shell command) are exempt — that identity is the operator's own work, not a principal's, and a
  large bulk operation must never be throttled by a cap sized for one human's ordinary use.
  **To set it** (fix round 1: this is now a form field, not a row edit), use the "Job
  execution" settings page's own form -- the "Retention, default priority & per-account cap"
  section (fix round 2: renamed to name all three controls it now carries), beside
  `retention_limit`/`default_priority` -- the same page every other queue-wide setting is
  already set on. Blank clears it back to no cap. On a multi-member box, a small number (single digits) is enough to
  stop one account from crowding out everyone else's turns and Ask questions while still
  leaving room for a person's own follow-up messages to queue normally; an open box with few or
  no members can leave it unset. **On an open box**, an unstamped payload (every job enqueued
  before this cap existed, or a hand-built one) resolves to the same shared `OPEN_PRINCIPAL`
  identity every anonymous caller's jobs already collapse onto for visibility purposes, and a
  QUEUED or RUNNING one counts against that shared total the same as a stamped one does (fix
  round 1, C-7 review) -- so a box with old, still-QUEUED rows from before a cap was set should
  clear them from the Queue page first, or set a cap that accounts for them.
- **`agents.limits.MAX_TURN_CHARS`** (20,000 characters) — a REQUEST-SIZE CEILING ON ONE TURN'S
  OWN TEXT, checked in `agents.chat.service.start_turn` beside the existing blank-text refusal.
  Before this, the only check was non-empty, so a turn's length was bounded solely by the
  framework's in-memory upload size. NOT a bound on the replay window (fix round 2: this entry
  previously read as if it were one): `agents.limits.HISTORY_TURNS` already caps how many prior
  turns replay into the prompt — a fixed count, not a size — and the actual size bound on what
  the bound model can accept for that whole replayed conversation is
  `models.registry.models.ModelConnection.context_window` (an operator's own per-connection
  setting), not this constant; see `agents/limits.py`'s own comment for the full reasoning. Not
  an operator-editable setting — a module constant, matching `tools.rag.transcode.
  MAX_RENDER_PIXELS`'s own "a platform-wide bound, not a per-box tuning knob" reasoning.
- **`JobSettings.response_timeout_seconds`** (the one-timeout task, 2026-09-17) — how long a
  single agent/chat turn may take end to end, 60–7200 seconds, default 1800 (30 minutes),
  raised from the ollama engine adapter's own previously-invisible 300s default
  (`models.contracts.engines.ollama.DEFAULT_REQUEST_TIMEOUT`). This is now the ONE firing
  authority for a turn's wall clock: the chat engine's own inner request timeout is built
  from this same value (`models.contracts.gateway.get_llm_for`), so it can never end a turn
  before this setting does — the SAME bound also reaches the `rag.ask`/`rag.search` tool
  runners' own in-turn clients (`tools/rag/tools.py`, fix round 3), derived from what's left
  of the turn's own budget, so those cannot compete with it either. It governs an agent/chat
  turn ONLY — a `rag.ingest` job, a page-submitted (not in-turn) RAG ask/search, or a
  `vision.generate` job never reads it, unlike the other five values on this page, which
  every queued job obeys. **Worst case, a turn can run up to roughly TWICE this value**: the
  deadline is only checked between steps, never during a request already in flight, so one
  that starts just before the deadline can still run the full length again before it is
  caught. **To set it**, use the "Job execution" settings page's own
  "Response timeout" form, the same page every other queue-wide setting is already set on. A
  turn that ends because this limit expired is marked failed with a generic sentence; an
  administrator viewing it additionally sees a hint that the limit can be raised here — on a
  full page load as a link, and on the live poll page as the same plain-text sentence
  appended to the failure (the poller's own JS never swaps in the linked version for a failed
  turn, a held-file limitation recorded in ADR 0013's fix-round-1 amendment). A generated
  image's own composed text and a chat model's own last-known words are both preserved on the
  turn's card in this case too (reload-only, same held-file limitation).
  **Sibling limit, fix round 1**: the no-JS poller's own client-side give-up point
  (`agents.chat.service.MAX_POLL_DURATION_MS`) is a DIFFERENT axis — it stops the page from
  polling, never the turn itself — raised to 7500 seconds specifically so it can never fire
  before this setting's own 7200-second ceiling could. The poller's own on-page copy still
  reads "10 minutes" (`agents/chat/templates/chat/conversation.html`, held by PR #84); an
  operator who raises the response timeout should read that wording as stale until that file
  is next touched.
  **Sibling limit, fix round 3**: an in-turn image generation
  (`tools.vision.tools`'s own generate runner) is bounded by THIS setting, not by vision's own
  `GENERATE_WAIT_TIMEOUT_SECONDS` (4 hours) — that constant governs a DIFFERENT thing, a
  detached, page-submitted generation with nothing waiting on it; the in-turn tool clamps its
  own wait to whatever remains of the turn's budget. An operator who wants a longer in-turn
  generation raises THIS number, not vision's.

## Document limits: an archive's uncompressed size, and a CSV's row count

**Not gated on the `"media"` feature flag** — `.docx`, `.xlsx` and `.csv` are all in the
base document allowlist, unlike the three limits above.

The document library's per-file upload cap (Settings → Library → "Maximum upload size
(GB)", `RagSettings.max_upload_bytes`, 2 GiB by default) is a **compressed**-size cap —
it compares the bytes the browser actually sent against its ceiling, nothing more. `.docx`
and `.xlsx` are both ZIP containers, and the libraries this platform hands them to
materialize a member's FULL *uncompressed* size in memory to parse it — an ordinary
office document reaches maybe 5–20:1 compression, but a crafted or merely repetitive one
(a worksheet of the same few values repeated) reaches 100:1 to 1000:1+ without looking
unusual on disk. A small, easily-accepted upload can therefore expand to gigabytes before
either parser has read enough to notice the content is malformed — and the request-body
cap and the per-file upload cap above are both sized in the bytes that were SENT, not the
bytes an accepted file expands to once parsed, so neither one catches this.

`tools.rag.readers.assert_archive_is_sane` (round-3 hardening B-6/H34) closes that gap,
for both formats, BEFORE either parser ever runs: it reads only the ZIP's own central
directory (`zipfile.ZipInfo.file_size`/`compress_size` — the declared sizes an archive's
own index already carries) and refuses if either ceiling is exceeded — never
decompresses a byte to check, so the check itself can never become the bomb it exists to
refuse:

- **`readers.MAX_UNCOMPRESSED_RATIO`** — 200:1, per archive member. Catches a small file
  with an outrageous ratio.
- **`readers.MAX_UNCOMPRESSED_BYTES`** — 200 MiB, the summed uncompressed size across
  the members the parser actually opens. Catches a large, only mildly-compressible
  archive the ratio check alone would miss.

For a `.docx` that sum is the whole archive: `python-docx` builds the entire package —
document part, styles, relationships, embedded media — to make its in-memory model, so
every member really is loaded. For an `.xlsx` it excludes `xl/media/` and `xl/drawings/`,
the embedded pictures and their anchors: pandas opens the workbook through openpyxl's
read-only mode, which streams the worksheet XML and never builds the drawing model those
parts belong to. Counting them refused a legitimate workbook carrying a few hundred
megabytes of photographs for an expansion that never happens. The per-member **ratio**
ceiling still covers every member either way — a part declaring 200x its compressed size
is crafted wherever it sits, and real image or drawing parts never approach it.

A `.csv` is not an archive — nothing is compressed, so there is no declared-vs-actual
size to compare — but an unbounded row count is its own way to turn a small file into an
outsized in-memory cost: `pandas.read_csv` builds one Python object per cell, and
ingest's own row-writing step (`tools.rag.ingest._ingest_tabular`) round-trips the whole
frame through a JSON encoder and writes one `DocumentRow` per row, both scaling with row
count rather than file bytes. **`readers.MAX_CSV_ROWS`** (1,000,000) bounds it instead,
checked with a cheap streamed line count before `pandas.read_csv` ever builds a frame.

All three refusals raise `readers.ArchiveExpansionExceededError` (a `ValueError`
subclass, naming the ceiling it hit in its message) — an accepted-then-refused document
surfaces exactly like any other parse failure: an ordinary FAILED row, the reason named
in `status_detail`, Retry available once the source file is fixed. See
`tools/rag/README.md`'s own account (beside the upload cap) for the implementation side,
and `tools/rag/tests/test_readers_archive_bounds.py` for the crafted-archive
reproductions — every fixture there lies about its own declared size rather than
actually containing that many bytes, so no test on this host ever decompresses a real
bomb.

## The model console's endpoint override

`GET /inference/?endpoint=<url>` (and the same value carried through a form's hidden
`endpoint_override` field) lets an administrator point the console at a different model server for
one page load, without registering it first — a scan hit's "Use this endpoint" link is the normal
way this gets set. Two things gate it, but only one of them is doing real work here:

- **The route itself requires an administrator.** `/inference/` and every action on it are an
  admin-only route: in `personal`/`enterprise` posture, a signed-in account that is not an
  administrator gets a `403` for the whole page, before any of its code runs — the endpoint
  override is never reached. This does nothing on an `open` box: with no accounts, there is nobody
  for it to refuse, and the page is reachable by anyone.
- **The host allowlist, in every posture including `open`, is what actually closes this.** The
  requested host must be this box's own loopback interface (`localhost`, an address in
  `127.0.0.0/8`, or `::1` — reachable by design, no registration needed, **at any port**), or the
  exact scheme+host+port of a `ModelConnection` already registered here or a configured default
  engine endpoint — the port match is exact for everything that is not loopback. Everything else
  is refused, silently: the page just falls back to the default endpoint.
  A private LAN address (a colleague's machine, an unregistered engine on your own network) is
  refused exactly like a public one unless it has been registered — this is deliberate, not a bug:
  on an `open` box the admin check above does nothing, so this allowlist is the *only* thing
  standing between a link on any page a LAN user visits and this box probing an arbitrary address.

A symptom worth recognising: a scan turns up a server, but clicking "Use this endpoint" (or
retyping its address) leaves the console showing the old endpoint instead — the host is outside the
allowlist (this is expected for anything other than loopback or an address already registered here).
The fix is to **register the connection** (the manual form, or "Add to registered" on a detected
model), which adds that exact scheme+host+port to the allowlist from then on; it is not a setting
to edit.

## Engine responses are read against a byte budget

Three specific reads from a model-inference engine's HTTP API go through a byte budget rather than
pulling the whole response into memory first: the transcription engine's health probe, and the
image engine's job-output and job-history reads. A compromised, hostile, or simply overloaded
engine that starts streaming an oversize body on one of these three is refused outright (or, for
the health probe specifically, sniffed on only its first few kilobytes rather than refused — a
health check only ever needed a prefix of the body); either way it cannot turn one of these
requests into unbounded memory growth on a box whose available memory is otherwise the binding
constraint. The image engine's job-supplied job id, used to build the job-history request's own
path, is also checked against a strict shape before it is used, so a misbehaving engine cannot
redirect that request at a different path on itself.

**What this does not (yet) cover.** Every OTHER engine response read in the platform — model
listings, submission responses, the transcription engine's own transcription result, and every
call to the local text-generation engine — still reads its full body unbounded. Closing the
remaining reads is tracked as a follow-up (H11b), not a setting an operator can turn on early:
there is nothing to configure here either way, this paragraph describes fixed platform behaviour.

## Expected orphan tables: `auth_user`, `auth_user_groups`, `auth_user_user_permissions`

On a box that was already deployed and had applied `django.contrib.auth`'s
own migrations before IA-1 landed, a `pg_dump` (and `\dt` on the live
database) shows `auth_user`, `auth_user_groups` and
`auth_user_user_permissions` sitting empty, alongside the identity column's
own `identity_user`. **This is expected and deliberate — it is not a
half-finished migration and nothing needs to be done about it.**

`identity.0002_repoint_admin_log_fk` moves `django_admin_log.user_id`'s
foreign key off `auth_user` and onto `identity_user` (looking up the
constraint's hash-suffixed name from the Postgres catalogue rather than
guessing it), but it deliberately does **not** drop `auth_user` or its two
sibling tables. Dropping them would put `django.contrib.auth`'s own
migration state — which still believes it created and owns those tables —
out of agreement with the database, the same class of problem the FK
repoint exists to avoid. On a genuinely fresh install these three tables
are never created at all: `admin.0001_initial`'s own
`swappable_dependency(settings.AUTH_USER_MODEL)` already points
`django_admin_log` at `identity_user` from the start, so the migration has
nothing to do and the orphan tables simply don't exist.

If you see these three tables empty in a dump or a live database, that is
the expected post-IA-1 state on an upgraded box — leave them.

### If `identity.0002_repoint_admin_log_fk` refuses

This migration refuses rather than guessing whenever `auth_user` or
`django_admin_log` holds a row it does not know how to move — an operator
question, not a bug. Whichever table triggers it, the box is left in a
**half-applied state**: `identity.0001_initial` has already applied, so
`AUTH_USER_MODEL` is `identity.User`, but `django_admin_log.user_id` still
points at the old `auth_user` table until `0002` completes. That is safe to
sit in — nothing else depends on `django_admin_log`'s FK target — but it is
not a state to leave indefinitely.

- **A row in `auth_user`** means a pre-swap account exists that IA-1's
  user-model swap abandons — not merely an empty legacy table left behind.
  Take a backup, then decide deliberately: migrate that account by hand into
  `identity_user` before re-running `manage.py migrate`, or accept it is
  gone, delete the `auth_user` row(s), re-run `manage.py migrate`, and
  `manage.py createsuperuser` against the new user model.
- **A row in `django_admin_log`** means an existing audit-log entry still
  references `auth_user` and this migration has no way to reassign it to a
  new user id. Take a backup, then decide deliberately whether to clear
  `django_admin_log` (its rows are audit trail, not application data) before
  re-running `manage.py migrate`.

Either way: back up first, decide deliberately, then re-run `migrate` — do
not force the migration past its own refusal.

## Back up

```bash
mkdir -p ./data/backups/<name>
docker compose stop worker watcher
docker compose exec -T db pg_dump -U farabunker -d farabunker \
    --format=custom --no-owner --no-privileges > ./data/backups/<name>/db.dump
docker compose exec web python manage.py backup /app/data/backups/<name> \
    --dump /app/data/backups/<name>/db.dump
docker compose start worker watcher
```

This copies `DOCUMENTS_DIR` and `GENERATED_DIR` (nothing else — never
`DATA_DIR` itself, so the backup can't recurse into its own output) into
`<name>/files/`, writes `<name>/manifest.json`, and folds the dump in as
`<name>/db.dump`. Stopping `worker`/`watcher` first means nothing is mid-
transcode while the file copy runs; the command itself refuses if any job is
still `RUNNING` or any document is still `processing` (pass `--force` to
override, e.g. for a controlled backup while you accept that risk).

**The one gap even this leaves.** `web` keeps serving between the `pg_dump`
and the file copy. An upload in that window ends up as an orphan file with no
database row (harmless — re-ingest it if you want it back); a delete in that
window leaves a database row whose file is gone (the library will show a
document whose link 404s). For a **strictly consistent** backup, stop `web`
too and run the command as a one-off container (a stopped service can't be
`exec`'d into):

```bash
mkdir -p ./data/backups/<name>
docker compose stop web worker watcher
docker compose exec -T db pg_dump -U farabunker -d farabunker \
    --format=custom --no-owner --no-privileges > ./data/backups/<name>/db.dump
docker compose run --rm web python manage.py backup /app/data/backups/<name> \
    --dump /app/data/backups/<name>/db.dump
docker compose start web worker watcher
```

Once written, `<name>/` is a plain folder — copy it to external media, another
machine, wherever you keep backups. There is no built-in remote/cloud target;
see [Offline by design](#offline-by-design) below.

If you ever run `manage.py backup` *without* `--dump` (e.g. you only want a
files-only snapshot), it still copies your files, but it prints a warning and
sets `manifest.complete: false` — the library, Ask history, and settings are
NOT in that backup. It never claims success it didn't earn.

## Restore

```bash
docker compose stop web worker watcher
docker compose exec -T db pg_restore -U farabunker -d farabunker \
    --clean --if-exists --no-owner --no-privileges \
    --single-transaction --exit-on-error \
    < ./data/backups/<name>/db.dump
docker compose start web worker watcher
```

Run `manage.py restore /app/data/backups/<name>` (inside the `web` container,
same as `backup`) first — it copies your files back into `DOCUMENTS_DIR`/
`GENERATED_DIR`, verifies `db.dump` against the manifest's recorded sha256,
and then prints the `pg_restore` command above for you to run. It refuses
(unless you pass `--force`) into a target that already has files, or while any
job is `RUNNING` in the *current* database — naming the stop command either
way. If the sha256 check fails (the dump doesn't match what `manifest.json`
recorded — a corrupted or partial copy), fix the copy of `db.dump` at
`<name>/` and re-run with `--force`: your files already restored cleanly the
first time, and `--force` only re-copies/overwrites, it never prunes.

**Why `--single-transaction`.** `--clean --if-exists` drops each table right
before recreating it. Without `--single-transaction`, an interrupted restore
can leave you with tables dropped and never recreated — and the dump you just
consumed is your only copy. `--single-transaction` wraps the whole restore in
one transaction: it either lands completely or rolls back to exactly the
state before it started. (It implies `--exit-on-error`; both are on the line
above for explicitness.) It's incompatible only with `--jobs`, a parallel-
restore flag this procedure doesn't use.

**If the single-transaction restore itself fails** (a genuinely corrupt dump,
say — not the ordinary case), your database is still untouched, but a fresh
attempt needs a clean target. With `web`/`worker`/`watcher` still stopped:

```bash
docker compose exec -T db psql -U farabunker -d postgres \
    -c 'DROP DATABASE farabunker WITH (FORCE)' -c 'CREATE DATABASE farabunker OWNER farabunker'
docker compose exec -T db pg_restore -U farabunker -d farabunker \
    --no-owner --no-privileges \
    --single-transaction --exit-on-error \
    < ./data/backups/<name>/db.dump
```

**Superuser / external Postgres caveat.** `--clean --if-exists` also drops and
recreates the `vector` extension. That works here only because `farabunker`
is the `db` container's own bootstrap superuser. Against an external Postgres
(a supported alternative, [ADR 0006](adr/0006-containerization-and-isolation.md)
§3) where your role isn't a superuser, `CREATE EXTENSION` will fail instead —
restore into a freshly created database with the extension pre-installed by an
actual superuser, and drop `--clean --if-exists` from the line.

**After `pg_restore` finishes**, start everything back up, run
`manage.py migrate` (a dump older than your current code may be missing a
migration or two — see the note on `rag.0013` above) and, if your current
code offers a shipped default the dump predates, `manage.py
install_defaults` (create-if-absent and idempotent — it never touches a
row the dump already restored, so running it costs nothing when nothing
is missing), then confirm the library at `/rag/`.
`manage.py restore` itself already told you, from the manifest:

- whether the source box's search index (`data_rag_chunks`) needs rebuilding
  with `manage.py reencode --role rag.embed` — only relevant if your dump
  excluded it, and only printed when a dump was actually restored; a files-
  only restore skips this because re-ingesting builds the index as it goes;
- which document ids, if any, were still pending/processing when the backup
  was taken, so you know to re-ingest them.

If you restored files without a dump at all (`manifest.dump.present: false`),
there is no database step — your originals are back, but the library, Ask
history, and settings are not. Re-ingest with `manage.py ingest`.

## Repairing entitlement labels on chunks: `manage.py relabel_chunks`

Document entitlement labels live on `DocumentEntitlement` rows, but
retrieval filters on a copy stamped into each document's pgvector
chunks. `tools/rag/labels.py::restamp_document_chunks` writes that copy
on every labelling change, and at ingest time it **logs rather than
raises** if the store is briefly unreachable — which can leave a
document whose tables say "Finance" and whose chunks say nothing.

`manage.py relabel_chunks [--document <id>]` re-runs the same stamp with
`raising=True`, one document at a time (or every document, with no
`--document`). It is cheap — no embedding, no engine, one `UPDATE` per
document — and is also the documented repair for the rarer case of an
`Entitlement` row deleted straight from `/admin/` or a shell, bypassing
the cascade that would otherwise have re-stamped its documents itself.

## Deleting an entitlement widens access

`/identity/entitlements/<id>/`'s (`identity-entitlement-edit`) delete
confirmation names the counts **first** — grants, document labels, tool labels, and anything else the
registered cascades touch (`identity.services.entitlement_delete_counts`)
— and the operator should read them before confirming. Every document
that entitlement was the **last** label on becomes unlabelled and
follows the library posture from that point on: `open` or `personal`
makes it visible to everyone again, `enterprise` with the library posture
locked makes it visible only to an administrator with the content
setting on (`sees_all_content`) — an administrator with that setting off
does not read it either. This is the one
administrator action in this column that **widens** access rather than
narrowing it, which is why deleting an entitlement is itself an
administrator-only action.

**Deleting an entitlement also un-taints, and it is the one path a taint tag is
ever removed in v1 (Workstreams).** The same delete cascades a stream's WALL
rows (`agents.WorkstreamScopeEntitlement`) and every conversation- and
stream-level taint tag naming that entitlement
(`agents.workstreams.workstream_entitlement_cascade`), inside the same
transaction as the grant and label cascades above. The trail says so both
ways: `WORKSTREAM_UNTAINTED`/`CONVERSATION_UNTAINTED` audit rows are written
for every tag removed, so `for_target("workstream", pk)` shows the tag
arriving and, now, leaving — never a tag that silently outlives the
entitlement it named. There is no separate "untaint" action for an operator
to reach for; the entitlement's own delete confirmation is the one door.

## The one thing you must never do

> **Never copy `./data/postgres` while the `db` container is running.** A
> running Postgres holds committed data in shared buffers and in the write-
> ahead log that has not yet reached the files on disk, and it rewrites those
> files continuously. `cp`/`rsync`/Time Machine over a live data directory
> therefore captures a torn, mid-write snapshot: the cluster may refuse to
> start, or start and be silently missing or corrupting rows and indexes.
> There is no way to tell which by looking at it. A `pg_dump` is a
> transactionally consistent logical snapshot and is always safe to take
> while the database is running — that is why this command asks you for one.
> The only safe file-level copy is a **cold** one: `docker compose stop db`,
> copy the whole directory, `docker compose start db`.

`pg_basebackup`/WAL archiving is a real answer to online physical backup and
is deliberately not what this document offers — the `pg_dump` sequence above
is the supported path.

## Offline by design

There is no cloud target, no scheduler, and no built-in encryption beyond
whatever `pg_dump -Fc`'s own compression gives you. `manage.py backup` writes
a plain folder on disk; moving it anywhere else — external media, another
machine, an encrypted volume you manage yourself — is on you, matching the
platform's offline-first posture. There is also no retention pruning, no
incremental/differential backup, and no dedup: each `manage.py backup` run is
a complete, independent snapshot.
