"""
`manage.py backup <dest>` -- copy the two file roots the platform owns
(`settings.DOCUMENTS_DIR`, `settings.GENERATED_DIR`) into `<dest>/files/`,
write `<dest>/manifest.json` describing what was copied, and (when
`--dump PATH` names a `pg_dump --format=custom` file) fold it in as
`<dest>/db.dump` (W3, ADR 0014 §18).

**Everything reproducible from originals + a database dump is NOT copied.**
The one table that *is* derivable-but-expensive -- `data_rag_chunks`, the
pgvector chunk store -- is left inside the `pg_dump` by default; an
operator wanting a smaller dump excludes it themselves
(`pg_dump --exclude-table-data=data_rag_chunks`) and re-encodes after
restore. See `docs/OPERATIONS.md` for the full truth table and the
documented backup sequence.

**Column-privacy, drawn in the other direction.** `tools.rag`/`tools.vision`
own the Postgres tables this module counts rows in and the `data_rag_chunks`
table `tools.rag.index` creates (`LIVE_TABLE_NAME`), but `foundation.ops`
never imports either app -- the import law's rule 2 states a `tools/*` or
`agents/*` app may only reach into `models.registry.bindings`, never the
reverse; the same restriction is enforced here in the other direction. Row
counts and the active-work check therefore run as raw
SQL against each table's known Django-default name
(`f"{app_label}_{model_name.lower()}"`) rather than importing the models
that own them. `_CHUNK_TABLE` below is a plain string literal, duplicated
from (not imported from) `tools/rag/index.py:26,30`
(`VECTOR_TABLE_NAME = "rag_chunks"`, `LIVE_TABLE_NAME = f"data_{
VECTOR_TABLE_NAME}"`) -- if that constant ever changes on the RAG side,
this literal needs updating by hand; there is no shared import to keep the
two in sync automatically. `models.queue.models.InferenceJob` IS imported
directly, under import-law rule 2's second named exception: `foundation.ops`
may import `models.queue.models`, and only for the active-work refusal
(RUNNING jobs), and nothing else (see `models/README.md` and this column's
own `README.md`).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import django
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from models.queue.models import RUNNING as JOB_RUNNING
from models.queue.models import InferenceJob
from foundation.files import create_locked_file, create_owner_only_dir, lock_down_file, sha256_file
from foundation.format import human_bytes

MANIFEST_VERSION = 1

# Known Django-default table names for the Postgres tables W3 reads --
# never imported model classes, see module docstring.
_TABLE_DOCUMENT = "rag_document"
_TABLE_CATEGORY = "rag_category"
_TABLE_ASKRECORD = "rag_askrecord"
_TABLE_GENERATIONJOB = "vision_generationjob"
_TABLE_MODELCONNECTION = "inference_modelconnection"
_TABLE_ROLEBINDING = "inference_rolebinding"

# Duplicated literal, not imported -- see module docstring.
_CHUNK_TABLE = "data_rag_chunks"

# Read while streaming a file for copy+hash together (§2 copy semantics: a
# single pass, so the recorded hash always describes the bytes actually
# written). Matches `foundation.files.sha256_file`'s own pinned block size for
# consistency of intent, though SHA-256 is block-size-independent -- any
# chunk size here yields an identical digest.
_READ_BLOCK_SIZE = 1024 * 1024

# The documented sequence (docs/OPERATIONS.md, §2 of the W3 design) --
# printed verbatim (with the `<name>` placeholder, matching the docs) when
# `--dump` was not given, so the operator can run it and re-fold the dump
# in on a later invocation. Reproduced identically in `docs/OPERATIONS.md`.
# `mkdir -p` is line one (W3 review MAJOR 1): a fresh checkout has no
# `data/backups/` at all, and the very next line redirects `pg_dump`'s
# stdout into a file under it -- a shell redirect never creates its own
# parent directory, so without this the documented sequence fails on its
# second line on a box that has never taken a backup before.
#
# S14: `./data/backups` here is `settings.BACKUP_DIR`'s DEFAULT value
# (`DATA_DIR/backups`), spelled out as a literal HOST-relative path
# because the `pg_dump` line runs on the host, before any Django settings
# module is in scope. An operator who has pointed `FARABUNKER_BACKUP_DIR`
# elsewhere (`docs/OPERATIONS.md` §"A database dump is now a credential
# store") substitutes their own directory for `./data/backups` in every
# line below; this sequence documents the un-configured default, not a
# hardcoded requirement.
DOCUMENTED_BACKUP_SEQUENCE = """\
mkdir -p ./data/backups/<name>
docker compose stop worker watcher
docker compose exec -T db pg_dump -U farabunker -d farabunker \\
    --format=custom --no-owner --no-privileges > ./data/backups/<name>/db.dump
docker compose exec web python manage.py backup /app/data/backups/<name> \\
    --dump /app/data/backups/<name>/db.dump
docker compose start worker watcher"""

STOP_QUEUE_COMMAND = "docker compose stop worker watcher"

# The bold callout from docs/OPERATIONS.md's "The one thing you must never
# do" section, verbatim (W3 review M1) -- this module is the source; the
# docs quote this exact string inside a longer blockquote paragraph, and
# `foundation/ops/tests/test_docs_sync.py` asserts it stays a substring of
# `docs/OPERATIONS.md` byte-for-byte. Printed as the first line of output
# by both `run_backup` and `foundation.ops.restore.run_restore` -- a header
# reminder every single invocation, not just something buried in the docs
# an operator has to already know to go read.
LIVE_POSTGRES_WARNING = "**Never copy `./data/postgres` while the `db` container is running.**"


def relative_to_base_dir(path: Path, base_dir: Path) -> str | None:
    """POSIX-style path of `path` relative to `base_dir`, or `None` if
    `path` does not resolve inside it (W3 review M2). Interpolating a real
    path into the printed sequence in place of the docs' `<name>`
    placeholder only makes sense when the operator followed the
    `<base_dir>/data/backups/<name>` convention the docs recommend --
    outside that convention there is no way to know what the equivalent
    HOST path looks like (the container's filesystem and the host's are
    only known to agree under the bind-mounted `base_dir`), so `<name>`
    stays a placeholder there."""
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return None


def render_backup_sequence(dest: Path, dest_arg: str, base_dir: Path) -> tuple[str, str | None]:
    """`(sequence, note)` -- `DOCUMENTED_BACKUP_SEQUENCE` with `<name>`
    interpolated to real paths where that's knowable (W3 review M2): the
    HOST-side redirect line (`> ./data/backups/<name>/db.dump`, a shell
    redirect a HOST shell interprets) only gets a real path when `dest`
    resolves inside `base_dir` (`./<relative>/db.dump`) -- see
    `relative_to_base_dir`. The in-container `docker compose exec web
    python manage.py backup /app/data/backups/<name> --dump
    /app/data/backups/<name>/db.dump` lines always get the literal
    argument the operator is about to type, unconditionally: inside the
    container that value is exactly `dest_arg`, known regardless of where
    `dest` resolves. `docs/OPERATIONS.md` itself keeps the generic
    `<name>` placeholder -- only the command's own printed output is
    interpolated.

    When `dest` does NOT resolve inside `base_dir`, the HOST-side lines
    keep the `<name>` placeholder (there is no way to know what an
    equivalent HOST path looks like outside that convention -- same
    reasoning as `foundation.ops.restore.render_restore_db_sequence`) and
    `note` is a sentence telling the operator to run `pg_dump` into a path
    reachable from the host and pass it with `--dump`, so the printed
    sequence doesn't silently mix a HOST-relative `<name>` line with an
    in-container `dest_arg` line without explanation. `None` when `dest`
    resolves inside `base_dir` -- nothing to explain there."""
    sequence = DOCUMENTED_BACKUP_SEQUENCE
    rel = relative_to_base_dir(dest, base_dir)
    if rel is not None:
        sequence = sequence.replace("./data/backups/<name>/db.dump", f"./{rel}/db.dump")
        sequence = sequence.replace("mkdir -p ./data/backups/<name>", f"mkdir -p ./{rel}")
        note = None
    else:
        note = (
            "This dest is outside the repo; run pg_dump into a path you can reach from the host and "
            "pass it with --dump."
        )
    sequence = sequence.replace("/app/data/backups/<name>", dest_arg)
    return sequence, note


def running_inference_jobs() -> list[InferenceJob]:
    """RUNNING rows in `models.queue.models.InferenceJob` -- imported
    directly under import-law rule 2's second named exception:
    `foundation.ops` may import `models.queue.models`, and only for the
    active-work refusal (RUNNING jobs), and nothing else. Shared with
    `foundation.ops.restore`'s own active-work refusal (restore calls this
    function rather than importing `models.queue.models` itself)."""
    return list(InferenceJob.objects.filter(state=JOB_RUNNING).order_by("id"))


def _resolve_git_sha(base_dir: Path) -> str | None:
    """Read the current commit SHA from the filesystem -- never by
    shelling out to `git` (no `git` binary in the `web`/`worker`/`watcher`
    image; the repo has no `.dockerignore`, so `COPY . .` bakes `.git` in
    regardless).

    Handles the linked-worktree case (every preview stack mounts one, ADR
    0011): `<base_dir>/.git` is there a *file* reading `gitdir: <path>`,
    not a directory, so a naive `open(".git/HEAD")` would raise
    `NotADirectoryError` -- an `OSError`, not a `FileNotFoundError`.
    Algorithm: resolve `.git`'s `gitdir:` indirection if it is a file, then
    resolve that gitdir's `commondir` file (relative, e.g. `../..`) if
    present -- falling back to the gitdir itself when `commondir` is
    absent -- and read `refs/heads/<branch>` from THAT directory (a linked
    worktree's own `HEAD`/`gitdir` are per-worktree, but its refs live in
    the shared common dir). Falling back to `packed-refs` is deliberately
    not attempted: `None` is a fine answer for a decorative field nothing
    downstream verifies. The whole function is wrapped in `except OSError`
    for exactly that reason.

    W3 review M4 -- qualifying the claim above: the `gitdir:` line inside a
    linked worktree's `.git` file is written by `git worktree add` as an
    ABSOLUTE HOST path (e.g. `/Users/.../Farabunker/.git/worktrees/<name>`).
    Inside a container that bind-mounts that same worktree checkout (every
    preview stack, ADR 0011), that path is not reachable -- it names a
    location on the HOST filesystem, not anything mounted into the
    container -- so `(gitdir / "HEAD")` above raises `FileNotFoundError`
    and this function honestly returns `None` there rather than resolving a
    SHA. There is no in-container fallback for this case: the common dir a
    linked worktree needs lives outside the single directory that gets
    bind-mounted, so nothing under `base_dir` can substitute for it. A
    preview stack showing `git_sha: null` in its own manifest is therefore
    expected, not a bug -- see `TestGitSha.test_worktree_gitdir_unreachable_in_container_returns_none_honestly`.
    """
    try:
        git_path = base_dir / ".git"
        if git_path.is_file():
            raw = git_path.read_text().strip()
            if not raw.startswith("gitdir:"):
                return None
            gitdir = Path(raw[len("gitdir:") :].strip())
            if not gitdir.is_absolute():
                gitdir = (base_dir / gitdir).resolve()
        else:
            gitdir = git_path

        commondir_file = gitdir / "commondir"
        if commondir_file.is_file():
            common_raw = commondir_file.read_text().strip()
            common_dir = Path(common_raw)
            if not common_dir.is_absolute():
                common_dir = (gitdir / common_dir).resolve()
        else:
            common_dir = gitdir

        head_raw = (gitdir / "HEAD").read_text().strip()
        if head_raw.startswith("ref:"):
            ref = head_raw[len("ref:") :].strip()
            sha = (common_dir / ref).read_text().strip()
        else:
            sha = head_raw
        return sha or None
    except OSError:
        return None


def _database_info() -> dict:
    """`connection.settings_dict` subset the manifest records -- never the
    password (finding 9)."""
    d = connection.settings_dict
    return {
        "name": d.get("NAME"),
        "host": d.get("HOST") or "localhost",
        "port": d.get("PORT") or 5432,
        "user": d.get("USER"),
    }


def _applied_migrations() -> list[str]:
    """`"app.name"` for every row `django_migrations` holds -- the one
    machine-checked field besides `files[]`/`dump` (restore's version-skew
    check compares each of these against `MigrationLoader(None).
    disk_migrations`)."""
    recorder = MigrationRecorder(connection)
    return sorted(f"{m.app}.{m.name}" for m in recorder.migration_qs.all())


def _row_count(cursor, table: str) -> int:
    cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 -- literal, not user input
    return cursor.fetchone()[0]


def _documents_by_status(cursor) -> dict[str, int]:
    cursor.execute(f"SELECT status, count(*) FROM {_TABLE_DOCUMENT} GROUP BY status")
    return dict(cursor.fetchall())


def _document_ids_by_status(cursor, status: str) -> list[int]:
    cursor.execute(f"SELECT id FROM {_TABLE_DOCUMENT} WHERE status = %s ORDER BY id", [status])
    return [row[0] for row in cursor.fetchall()]


def _chunk_stats(cursor) -> tuple[int | None, int | None]:
    """`(row_count, total_bytes)` for the live `data_rag_chunks` table, or
    `(None, None)` when it doesn't exist yet (a fresh install that has
    never embedded anything -- `PGVectorStore` creates the table lazily).
    Two queries, not one: Postgres plans a `SELECT ... FROM <table>`
    against the literal table name at parse time regardless of a `CASE`
    guard around it, so a single conditional query can't skip erroring on
    a table that doesn't exist -- the existence check has to run first."""
    cursor.execute("SELECT to_regclass(%s)", [_CHUNK_TABLE])
    if cursor.fetchone()[0] is None:
        return None, None
    cursor.execute(f"SELECT count(*), pg_total_relation_size('{_CHUNK_TABLE}') FROM {_CHUNK_TABLE}")
    rows, size = cursor.fetchone()
    return rows, size


def _count_inbox_files(inbox_dir: Path) -> int:
    if not inbox_dir.exists():
        return 0
    return sum(1 for p in inbox_dir.rglob("*") if p.is_file())


def check_active_work(cursor, *, force: bool) -> dict:
    """Build `manifest.active` and, unless `force`, refuse
    (`CommandError`) when a RUNNING `InferenceJob` or a PROCESSING
    `Document` exists (finding 2). PENDING is the normal staged state of
    every document until a worker claims it and is never a refusal."""
    running = running_inference_jobs()
    pending_ids = _document_ids_by_status(cursor, "pending")
    processing_ids = _document_ids_by_status(cursor, "processing")
    inbox_count = _count_inbox_files(settings.INGEST_INBOX_DIR)

    active = {
        "running_jobs": [
            {
                "id": j.id,
                "kind": j.kind,
                "started_at": j.started_at.isoformat() if j.started_at else None,
            }
            for j in running
        ],
        "pending_documents": pending_ids,
        "processing_documents": processing_ids,
        "inbox_files": inbox_count,
    }

    if not force and (running or processing_ids):
        parts = []
        if running:
            names = ", ".join(f"#{j.id} ({j.kind})" for j in running)
            parts.append(f"{len(running)} job(s) still running ({names})")
        if processing_ids:
            parts.append(f"{len(processing_ids)} document(s) still processing (ids {processing_ids})")
        raise CommandError(
            "Cannot back up while work is in progress: "
            + "; ".join(parts)
            + f". Stop the queue first: `{STOP_QUEUE_COMMAND}`. Pass --force to back up anyway."
        )

    return active


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_dest_bounds(dest: Path) -> None:
    """`<dest>` must not resolve inside either walked root -- walking
    `DATA_DIR` itself is never done (finding 16) precisely so the
    recommended `data/backups/<name>` dest can't recurse into its own
    write. Unconditional -- not overridden by `--force`, a structural
    error rather than an "existing content" one."""
    resolved = dest.resolve()
    for root in (settings.DOCUMENTS_DIR, settings.GENERATED_DIR):
        if _is_within(resolved, root.resolve()):
            raise CommandError(
                f"<dest> ({dest}) must not be inside {root} -- backup would recurse into its own output."
            )


def validate_dest_emptiness(dest: Path, dump: Path | None, *, force: bool) -> None:
    """`<dest>` must be empty or non-existent unless `--force`, with one
    exemption (finding 1): a `--dump` file already sitting alone at
    `<dest>` (the documented sequence's own `> ./data/backups/<name>/
    db.dump` redirect) never trips this check."""
    if force or not dest.exists():
        return
    if not dest.is_dir():
        raise CommandError(f"{dest} exists and is not a directory.")

    entries = list(dest.iterdir())
    if not entries:
        return

    if dump is not None and len(entries) == 1:
        only = entries[0]
        try:
            if only.is_file() and os.path.samefile(only, dump):
                return
        except OSError:
            pass

    raise CommandError(f"{dest} is not empty. Pass --force to back up into it anyway.")


def validate_dump_source(dump: Path) -> None:
    """`--dump` must exist, be non-empty, and begin with the `pg_dump
    --format=custom` magic `PGDMP` (finding 5)."""
    if not dump.exists():
        raise CommandError(f"--dump {dump} does not exist.")
    size = dump.stat().st_size
    if size == 0:
        raise CommandError(f"--dump {dump} is empty -- re-run `pg_dump --format=custom` and try again.")
    with open(dump, "rb") as f:
        magic = f.read(5)
    if magic != b"PGDMP":
        raise CommandError(
            f"--dump {dump} does not begin with the pg_dump custom-format magic (found {magic!r}) -- "
            "this looks like a plain-SQL dump or a truncated file; re-run `pg_dump --format=custom`."
        )


def fold_in_dump(dest: Path, dump: Path | None) -> dict:
    """Copy `dump` to `<dest>/db.dump` (or, if it already IS that file,
    hash it in place without moving it -- `os.path.samefile`, finding 1)
    and return the `manifest.dump` dict. Caller must have already run
    `validate_dump_source` when `dump` is not `None`."""
    if dump is None:
        return {"present": False, "format": "custom", "path": "db.dump", "size": None, "sha256": None}

    dest_dump = dest / "db.dump"
    same = False
    try:
        same = dest_dump.exists() and os.path.samefile(dump, dest_dump)
    except OSError:
        same = False

    if not same:
        create_owner_only_dir(dest)
        shutil.copy2(dump, dest_dump)

    # H13 review round 1, finding 1 (CRITICAL): tighten UNCONDITIONALLY,
    # outside the `if not same` above -- the documented in-place sequence
    # (an operator who ran `pg_dump` straight into `<dest>/db.dump` and
    # passes that same path to `--dump`) takes the `same` branch and
    # skips the `mkdir`/`copy2` entirely; tightening only inside that
    # `if` used to skip the chmod right along with them, leaving a dump
    # placed there directly at whatever mode produced it (`pg_dump`'s own
    # redirect is default-umask, exactly the exposure this task closes).
    lock_down_file(dest_dump)

    return {
        "present": True,
        "format": "custom",
        "path": "db.dump",
        "size": dest_dump.stat().st_size,
        "sha256": sha256_file(dest_dump),
    }


def _walk_root(root: Path, *, filter_work: bool) -> tuple[list[Path], list[Path]]:
    """Return `(dirs, files)`, both relative to `root`, for everything
    under it. `filter_work=True` (DOCUMENTS_DIR only) skips a directory
    literally named `work` when it sits directly under a document-id dir
    -- the one filter the walk has (finding 16). Raises `CommandError`
    naming any symlink encountered anywhere under `root` (finding 17)."""
    dirs: list[Path] = []
    files: list[Path] = []
    if not root.exists():
        return dirs, files

    def _walk(dir_path: Path, rel: Path, depth: int) -> None:
        for entry in sorted(dir_path.iterdir(), key=lambda p: p.name):
            entry_rel = rel / entry.name
            if entry.is_symlink():
                raise CommandError(
                    f"Refusing to back up a symlink: {entry_rel} -- the managed store never creates one."
                )
            if entry.is_dir():
                if filter_work and depth == 1 and entry.name == "work":
                    continue
                dirs.append(entry_rel)
                _walk(entry, entry_rel, depth + 1)
            elif entry.is_file():
                files.append(entry_rel)
            else:
                raise CommandError(f"Refusing to back up a non-regular file: {entry_rel}")

    _walk(root, Path("."), 0)
    return dirs, files


def _copy_and_hash(src: Path, dest: Path) -> tuple[int, str]:
    """Single pass: read -> hash -> write, so the recorded size/sha256
    always describe the bytes actually written, even if `src` changes
    mid-walk (finding 17). Aborts naming `src` on any read error (never
    silently skipped)."""
    # H13 review round 1, finding 8: every directory a backup creates
    # under `<dest>/files/**` is exclusively this backup's own -- not a
    # shared root -- so the default (aggressive) `create_owner_only_dir`
    # applies here too, same as `<dest>` itself.
    create_owner_only_dir(dest.parent)
    digest = hashlib.sha256()
    size = 0
    try:
        # H13 review round 1, finding 7: created at 0600 directly, so
        # there's no window where this file sits at the OS default
        # create mode before the `lock_down_file` below runs.
        with open(src, "rb") as fsrc, create_locked_file(dest) as fdst:
            for block in iter(lambda: fsrc.read(_READ_BLOCK_SIZE), b""):
                digest.update(block)
                fdst.write(block)
                size += len(block)
    except OSError as exc:
        raise CommandError(f"Cannot read {src}: {exc}") from exc
    shutil.copystat(src, dest)
    # S14: `copystat` just copied SRC's mode onto `dest` -- tightening
    # BEFORE this line would be silently undone by it (true regardless of
    # `create_locked_file` above creating it at 0600: `copystat` still
    # runs after and still overwrites that). This call, AFTER, is the
    # only order that sticks; the order is load-bearing and a test pins
    # it (`TestBackupPermissions.
    # test_copystat_does_not_restore_the_sources_wider_mode`).
    lock_down_file(dest)
    return size, digest.hexdigest()


def _source_roots() -> tuple[tuple[Path, str, bool], ...]:
    """The two roots + subdir names + `filter_work` flags this module ever
    walks, shared between `copy_files` (which copies them) and
    `planned_file_paths` (which only needs the resulting relative paths,
    read-only, so the two stay in sync by construction rather than by two
    hand-written literals matching each other)."""
    return (
        (settings.DOCUMENTS_DIR, "documents", True),
        (settings.GENERATED_DIR, "generated", False),
    )


def planned_file_paths() -> list[str]:
    """The `manifest.files[].path` set `copy_files` is about to produce,
    computed from a read-only walk of the source roots -- no writes.
    Called by `run_backup` BEFORE `copy_files`/`fold_in_dump` touch
    `<dest>` at all, so `check_no_stale_files` can refuse (leaving `<dest>`
    exactly as it was found) instead of discovering the problem after
    bytes have already been overwritten (W3 follow-up review: the
    two-pass fix -- see `check_no_stale_files`)."""
    paths: list[str] = []
    for root, subdir, filter_work in _source_roots():
        _, files = _walk_root(root, filter_work=filter_work)
        for rel_file in files:
            paths.append((Path(subdir) / rel_file).as_posix())
    return paths


def copy_files(dest: Path) -> tuple[list[dict], dict]:
    """Copy both roots (`DOCUMENTS_DIR`, `GENERATED_DIR`) into
    `<dest>/files/{documents,generated}/`, returning `(manifest.files,
    manifest.totals)`. Adds and overwrites only -- NEVER prunes anything
    already sitting at `<dest>/files/` (a `--force` reuse of a dest that
    held an earlier, different backup can therefore leave stale files
    behind; `check_no_stale_files` is the caller's job of catching that,
    W3 review MAJOR 2, called BEFORE this function runs -- see its own
    docstring)."""
    files_root = dest / "files"
    manifest_files: list[dict] = []
    total_bytes = 0

    for root, subdir, filter_work in _source_roots():
        dirs, files = _walk_root(root, filter_work=filter_work)
        # H13 review round 1, finding 8: every directory under
        # `<dest>/files/**` is this backup's own -- including an EMPTY
        # one that survives only to preserve shape, never reached by
        # `_copy_and_hash`'s own `create_owner_only_dir(dest.parent)` --
        # so it is tightened here too.
        for rel_dir in dirs:
            create_owner_only_dir(files_root / subdir / rel_dir)
        create_owner_only_dir(files_root / subdir)
        for rel_file in files:
            src = root / rel_file
            dest_file = files_root / subdir / rel_file
            size, sha = _copy_and_hash(src, dest_file)
            manifest_files.append(
                {"path": (Path(subdir) / rel_file).as_posix(), "size": size, "sha256": sha}
            )
            total_bytes += size

    manifest_files.sort(key=lambda f: f["path"])
    totals = {"file_count": len(manifest_files), "bytes": total_bytes}
    return manifest_files, totals


def _walk_all_files(root: Path) -> list[str]:
    """Every regular file under `root`, as posix paths relative to `root`.
    Used only by `check_no_stale_files` -- unlike `_walk_root`, this never
    raises on a symlink/non-regular entry (that's `copy_files`'s job on the
    write side); it just needs to know what's physically there now."""
    if not root.exists():
        return []
    return [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]


# Cap on how many stale filenames `check_no_stale_files` names individually
# before collapsing the rest into a "...and N more" tail -- a real stale
# set can be an entire earlier backup's file tree, and a CommandError
# message that long is unreadable rather than helpful.
_STALE_FILES_SHOWN = 20


def check_no_stale_files(dest: Path, planned_paths: list[str]) -> None:
    """BEFORE `copy_files`/`fold_in_dump` write anything (two-pass, W3
    follow-up review), walk `<dest>/files/**` as it exists right now and
    refuse (`CommandError`), naming every regular file found there that
    `planned_paths` -- this run's about-to-be-written manifest paths,
    computed read-only by `planned_file_paths` from the source roots --
    does NOT list. `copy_files` only ever adds and overwrites -- it never
    prunes -- so a `--force` reuse of a `<dest>` that already holds a
    *different* backup (one with a file this run's source no longer has,
    e.g. a document deleted between two nights' backups into the same
    dest) leaves that old file sitting there unlisted in the new manifest.
    `restore`'s own extras check (`validate_files_tree`) would refuse that
    exact file at restore time anyway -- this catches it here, at backup
    time, BEFORE any bytes move, so nothing is mutated on refusal: an
    aborted `--force` run never leaves new bytes sitting under the old
    (now-stale) manifest at `<dest>/manifest.json`, which is what a
    post-write check (this function's earlier shape) would do -- the
    write already happened by the time the refusal fired, so `<dest>` was
    left with fresher files on disk than the manifest describing them,
    and a later restore would fail with a confusing "corrupted backup"
    hash mismatch instead of this refusal's clear explanation. Named list
    capped at `_STALE_FILES_SHOWN` entries plus a "...and N more" tail."""
    files_root = dest / "files"
    known = set(planned_paths)
    stale = sorted(p for p in _walk_all_files(files_root) if p not in known)
    if stale:
        shown = stale[:_STALE_FILES_SHOWN]
        names = ", ".join(shown)
        remaining = len(stale) - len(shown)
        if remaining:
            names += f", ...and {remaining} more"
        raise CommandError(
            "--force reused a dest that already holds a different backup -- restore would refuse it; "
            "use an empty dest. Stale file(s) not in this backup's manifest: " + names
        )


def build_manifest(
    *,
    dest: Path,
    dump_info: dict,
    active: dict,
    files: list[dict],
    totals: dict,
) -> dict:
    with connection.cursor() as cursor:
        documents = _row_count(cursor, _TABLE_DOCUMENT)
        by_status = _documents_by_status(cursor)
        categories = _row_count(cursor, _TABLE_CATEGORY)
        ask_records = _row_count(cursor, _TABLE_ASKRECORD)
        generation_jobs = _row_count(cursor, _TABLE_GENERATIONJOB)
        model_connections = _row_count(cursor, _TABLE_MODELCONNECTION)
        role_bindings = _row_count(cursor, _TABLE_ROLEBINDING)
        chunk_rows, chunk_bytes = _chunk_stats(cursor)

    inference_jobs = InferenceJob.objects.count()

    return {
        "manifest_version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "farabunker": {
            "git_sha": _resolve_git_sha(Path(settings.BASE_DIR)),
            "django_version": django.get_version(),
            "features": sorted(settings.FARABUNKER_FEATURES),
        },
        "migrations": _applied_migrations(),
        "database": _database_info(),
        "counts": {
            "documents": documents,
            "documents_by_status": by_status,
            "categories": categories,
            "ask_records": ask_records,
            "generation_jobs": generation_jobs,
            "model_connections": model_connections,
            "role_bindings": role_bindings,
            "inference_jobs": inference_jobs,
            "chunk_rows": chunk_rows,
            "chunk_bytes": chunk_bytes,
        },
        "active": active,
        "files": files,
        "totals": totals,
        "dump": dump_info,
        "complete": dump_info["present"],
    }


def _default_backup_name() -> str:
    """A fresh, sortable directory name for an un-named `manage.py backup`
    run, nested under `settings.BACKUP_DIR` (H13 review round 1, finding
    6). Microsecond precision: `run_backup` calls this once per
    invocation, and two invocations run back-to-back (a script, a test)
    must not collide into the SAME directory -- `validate_dest_emptiness`
    would then refuse the second one outright, defeating the entire point
    of a destination an operator doesn't have to name by hand."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _ensure_backup_dest_dir(dest: Path) -> None:
    """`create_owner_only_dir(dest)`, turning a bare `PermissionError`
    into a refusal that names the fix -- same shape as
    `config.settings._ensure_file_upload_temp_dir`.

    The DEFAULT destination (`_default_backup_name`, above) nests under
    `settings.BACKUP_DIR`, which lives under `./data` like every other
    durable root this platform writes to -- so it inherits the exact S12
    ownership problem `_ensure_file_upload_temp_dir` was written for: a
    box whose `./data` still carries root ownership from before this box
    ran the container's non-root user hits `mkdir`'s bare
    `PermissionError` here, at the first line of `run_backup` that
    touches the filesystem, with no mention of `chown` or the uid this
    box now runs as. Naming the fix here, rather than only in
    `config.settings`, matters because this call site is reached by
    `manage.py backup` directly -- an operator running a scheduled or
    ad hoc backup never imports `config.settings.FILE_UPLOAD_TEMP_DIR`'s
    own already-created directory, so its refusal never fires for this
    path."""
    try:
        create_owner_only_dir(dest)
    except PermissionError as exc:
        raise CommandError(
            f"Cannot create backup destination ({dest}): {exc}. This is "
            "almost always ./data still being owned by root from before "
            "this box ran the container's non-root user (S12, uid/gid "
            "10001) -- see docs/OPERATIONS.md §\"Upgrading to the "
            "non-root container user\" for the chown sequence."
        ) from exc


def run_backup(dest_arg: str | None, *, dump_arg: str | None, force: bool, stdout, style) -> None:
    """The whole `manage.py backup` flow -- see module docstring. Writes
    output via `stdout`/`style` (a Django `Command`'s own) so the command
    module stays a thin wrapper.

    S14: `dest_arg` is optional. An operator who doesn't care to name a
    destination gets a fresh, timestamped directory under
    `settings.BACKUP_DIR` (`_default_backup_name`) -- so "just run
    `manage.py backup`" and "point backups at the mounted, encrypted
    volume" compose with no path arithmetic on the operator's part, AND
    running it again (by hand, or on a schedule) produces a second
    backup rather than a `--force`-required collision with the first.
    Naming a destination explicitly (the documented sequence above, which
    coordinates the in-container destination with a host-side `pg_dump`
    redirect decided before this command ever runs) still works exactly
    as before and always takes priority.
    """
    stdout.write(style.WARNING(LIVE_POSTGRES_WARNING))

    dest_arg = dest_arg or str(settings.BACKUP_DIR / _default_backup_name())
    dest = Path(dest_arg)
    dump = Path(dump_arg) if dump_arg else None

    validate_dest_bounds(dest)
    if dump is not None:
        validate_dump_source(dump)

    with connection.cursor() as cursor:
        active = check_active_work(cursor, force=force)

    validate_dest_emptiness(dest, dump, force=force)

    # S14: a backup destination is a credential store waiting to happen
    # (docs/OPERATIONS.md's own words) -- 0700, not the container's
    # default-umask 0755. H13 review round 1, finding 2: through the one
    # shared helper (also used by `copy_files`, `_copy_and_hash`, and
    # `fold_in_dump` below) rather than a hand-rolled `mkdir`+`chmod`
    # pair repeated at each site -- see `foundation.files.
    # create_owner_only_dir`'s own docstring for why `mode=` alone isn't
    # enough and why this is a per-site explicit call rather than a
    # process-wide `os.umask` (this runs inside a management command in a
    # process that may go on to do other work; a global umask change
    # would be a side effect on all of it).
    _ensure_backup_dest_dir(dest)
    check_no_stale_files(dest, planned_file_paths())
    files, totals = copy_files(dest)
    dump_info = fold_in_dump(dest, dump)

    manifest = build_manifest(dest=dest, dump_info=dump_info, active=active, files=files, totals=totals)
    manifest_path = dest / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n")
    # S14: it enumerates every document path and every applied migration --
    # owner-only, same as the dump and the copied files.
    lock_down_file(manifest_path)

    stdout.write(f"Copied {totals['file_count']} file(s), {totals['bytes']:,} bytes, to {dest}/files/")

    if active["inbox_files"]:
        stdout.write(
            style.WARNING(
                f"WARNING: {active['inbox_files']} file(s) are still waiting in the inbox and are "
                "NOT in this backup."
            )
        )

    chunk_rows = manifest["counts"]["chunk_rows"]
    chunk_bytes = manifest["counts"]["chunk_bytes"]
    if chunk_rows is not None:
        size = human_bytes(chunk_bytes) if chunk_bytes is not None else "unknown size"
        if dump_info["present"]:
            stdout.write(
                f"search index: {chunk_rows:,} chunks, {size} -- included in your dump unless you "
                "excluded it."
            )
        else:
            stdout.write(
                f"search index: {chunk_rows:,} chunks, {size} -- and is NOT in this files-only backup "
                "(pass --dump to include it)."
            )

    if dump_info["present"]:
        stdout.write(style.SUCCESS(f"Backup complete: {dest} (includes a database dump)."))
    else:
        stdout.write(
            style.WARNING(
                "WARNING: no --dump was given -- manifest.complete is false. Your files were copied, "
                "but the library, Ask history, and settings are NOT backed up. Run the full sequence "
                "next time:"
            )
        )
        sequence, note = render_backup_sequence(dest, dest_arg, Path(settings.BASE_DIR))
        stdout.write(sequence)
        if note:
            stdout.write("")
            stdout.write(note)
