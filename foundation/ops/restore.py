"""
`manage.py restore <src>` -- the inverse of `foundation.ops.backup`: mirror
`<src>/files/{documents,generated}/**` back into `settings.DOCUMENTS_DIR`/
`settings.GENERATED_DIR`, verify (and, when present, print the restore
command for) `<src>/db.dump`, and print deterministic, manifest-derived
follow-ups (W3, ADR 0014 §18).

Order: **manifest -> refusals (version skew, non-empty target, active
work) -> files (validate, THEN copy -- two-pass, so an abort never leaves
a half-written tree) -> the database block -> follow-ups.** Files before
the database block: a DB restored without its files points at missing
paths (a broken library, a 404 on `document_file`), while files without a
DB are merely invisible and re-ingestible -- the less bad failure mode to
land on if something goes wrong partway through.

Never imports `tools.rag`/`tools.vision` (same column-privacy boundary
`foundation.ops.backup`'s module docstring explains) -- restore needs none of
their models at all: the version-skew check reads Django's own migration
ledger (`MigrationLoader`), the active-work check reads
`models.queue.models.InferenceJob` (via `foundation.ops.backup.
running_inference_jobs`, under import-law rule 2's second named exception --
`foundation.ops` may import `models.queue.models`, and only for the
active-work refusal, and nothing else; this module does not import
`models.queue.models` itself), and the file/manifest logic never touches a
Postgres table by name the way backup's counts do.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.db.migrations.loader import MigrationLoader

from foundation.ops.backup import LIVE_POSTGRES_WARNING, MANIFEST_VERSION, relative_to_base_dir, running_inference_jobs
from foundation.files import create_owner_only_dir, lock_down_file, sha256_file
from foundation.format import human_bytes

STOP_ALL_COMMAND = "docker compose stop web worker watcher"

# The documented restore sequence (docs/OPERATIONS.md) -- printed verbatim
# with the `<name>` placeholder, matching the docs, same convention as
# `foundation.ops.backup.DOCUMENTED_BACKUP_SEQUENCE`.
RESTORE_DB_SEQUENCE = """\
docker compose stop web worker watcher
docker compose exec -T db pg_restore -U farabunker -d farabunker \\
    --clean --if-exists --no-owner --no-privileges \\
    --single-transaction --exit-on-error \\
    < ./data/backups/<name>/db.dump
docker compose start web worker watcher"""

# `--single-transaction` implies `--exit-on-error` (both are kept on the
# printed line for explicitness -- an operator reading it should not have
# to know that implication). If the single-transaction restore itself
# fails, the database is untouched (the whole thing rolled back) -- the
# recovery below is for the case that ISN'T enough: `--single-transaction`
# is incompatible with `--jobs` (not used here) but a restore can still
# fail for other reasons (e.g. a genuinely corrupt dump). Precondition:
# `web`/`worker`/`watcher` must already be stopped (RESTORE_DB_SEQUENCE's
# first line) before running this -- it talks to the same database.
RECOVERY_SEQUENCE = """\
docker compose exec -T db psql -U farabunker -d postgres \\
    -c 'DROP DATABASE farabunker WITH (FORCE)' -c 'CREATE DATABASE farabunker OWNER farabunker'
docker compose exec -T db pg_restore -U farabunker -d farabunker \\
    --no-owner --no-privileges \\
    --single-transaction --exit-on-error \\
    < ./data/backups/<name>/db.dump"""

NO_DUMP_WARNING = (
    "This backup has no database dump: your original files are restored, but the library, Ask "
    "history, and settings are not. Re-ingest the originals with `manage.py ingest` -- ingesting "
    "rebuilds the search index as it goes, so a separate `reencode` run is not needed."
)

EXTENSION_CAVEAT = (
    "Note: --clean --if-exists also drops and recreates the vector extension, which works here only "
    "because farabunker is the db container's bootstrap superuser. Against an external Postgres where "
    "that role isn't a superuser, restore into a freshly created database (extension pre-installed by a "
    "superuser) and drop --clean --if-exists instead."
)


def render_restore_db_sequence(src: Path, base_dir: Path) -> tuple[str, str, str | None]:
    """`(restore_sequence, recovery_sequence, note)` with `<name>`
    interpolated to `src`'s real host-relative path where that's knowable
    (W3 review M2, same convention as
    `foundation.ops.backup.render_backup_sequence`) -- `RESTORE_DB_SEQUENCE`
    and `RECOVERY_SEQUENCE` share the same `< ./data/backups/<name>/
    db.dump` HOST-side redirect line, interpolated identically in both.
    When `src` does NOT resolve inside `base_dir`, `<name>` stays a
    placeholder in both (there is no way to know what an equivalent HOST
    path looks like outside that convention) and `note` is a sentence
    naming the exact in-container path of the `db.dump` this command just
    hashed and verified -- the one thing knowable unconditionally, so the
    operator isn't left with a bare, unresolvable `<name>`. `docs/
    OPERATIONS.md` itself keeps the generic `<name>` placeholder -- only
    the command's own printed output is interpolated."""
    rel = relative_to_base_dir(src, base_dir)
    if rel is not None:
        old = "./data/backups/<name>/db.dump"
        new = f"./{rel}/db.dump"
        return RESTORE_DB_SEQUENCE.replace(old, new), RECOVERY_SEQUENCE.replace(old, new), None
    note = f"The dump this command verified is {src}/db.dump (path inside the container)."
    return RESTORE_DB_SEQUENCE, RECOVERY_SEQUENCE, note


def load_manifest(src: Path) -> dict:
    """Parse `<src>/manifest.json`; refuse (`CommandError`) on a
    missing/unreadable file, invalid JSON, or `manifest_version >
    MANIFEST_VERSION` -- "this backup was written by a newer farabunker"."""
    manifest_path = src / "manifest.json"
    try:
        raw = manifest_path.read_text()
    except OSError as exc:
        raise CommandError(f"Cannot read manifest at {manifest_path}: {exc}") from exc
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CommandError(f"Manifest at {manifest_path} is not valid JSON: {exc}") from exc

    version = manifest.get("manifest_version")
    if not isinstance(version, int) or version > MANIFEST_VERSION:
        raise CommandError(
            f"{manifest_path} has manifest_version={version!r} -- this backup was written by a newer "
            "farabunker. Upgrade the code, then restore."
        )
    return manifest


def check_version_skew(manifest: dict) -> None:
    """Every `(app, name)` the manifest's `migrations[]` names must exist
    in `MigrationLoader(None).disk_migrations` -- the code this restore
    runs against must know every migration the backup's database applied.
    No reverse check: code ahead of the dump is normal (`web`'s start
    command already runs `migrate --noinput`)."""
    loader = MigrationLoader(None)
    unknown = []
    for entry in manifest.get("migrations", []):
        app, _, name = entry.partition(".")
        if (app, name) not in loader.disk_migrations:
            unknown.append(entry)
    if unknown:
        raise CommandError(
            "This backup was taken from a newer farabunker (it applied "
            f"{', '.join(unknown)}, which this code doesn't know about) -- upgrade the code, then restore."
        )


def check_target_empty(*, force: bool) -> None:
    """Any entry in `DOCUMENTS_DIR` or `GENERATED_DIR` -> refuse unless
    `--force`. Under `--force`, a dest file whose hash differs is
    overwritten and stale extras are never removed -- restore adds and
    replaces, it never prunes."""
    if force:
        return

    def _nonempty(root: Path) -> bool:
        return root.exists() and any(root.iterdir())

    offending = [str(r) for r in (settings.DOCUMENTS_DIR, settings.GENERATED_DIR) if _nonempty(r)]
    if offending:
        raise CommandError(
            f"Refusing to restore into a non-empty target ({', '.join(offending)}). Pass --force to "
            "restore anyway (a dest file whose hash differs is overwritten; unrelated stale files are "
            "left in place, never removed)."
        )


def check_active_work(*, force: bool) -> None:
    """RUNNING jobs in the CURRENT database -> refuse unless `--force`,
    naming the stop command restore's own database step needs anyway."""
    running = running_inference_jobs()
    if running and not force:
        names = ", ".join(f"#{j.id} ({j.kind})" for j in running)
        raise CommandError(
            f"Cannot restore while {len(running)} job(s) are running ({names}). Stop the queue first: "
            f"`{STOP_ALL_COMMAND}`. Pass --force to restore anyway."
        )


def _walk_src_files(files_root: Path) -> tuple[list[str], list[str], list[str]]:
    """Return `(dirs, files, bad)` -- posix-style paths relative to
    `files_root`, `bad` holding any non-regular entry (symlink, fifo,
    device) encountered anywhere in the tree."""
    dirs: list[str] = []
    files: list[str] = []
    bad: list[str] = []

    def _walk(dir_path: Path, rel: str) -> None:
        for entry in sorted(dir_path.iterdir(), key=lambda p: p.name):
            entry_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_symlink():
                bad.append(entry_rel)
                continue
            if entry.is_dir():
                dirs.append(entry_rel)
                _walk(entry, entry_rel)
            elif entry.is_file():
                files.append(entry_rel)
            else:
                bad.append(entry_rel)

    if files_root.exists():
        _walk(files_root, "")
    return dirs, files, bad


def validate_files_tree(src: Path, manifest_files: list[dict]) -> tuple[list[str], list[str]]:
    """Validate the WHOLE `<src>/files/**` tree against the manifest
    BEFORE any write happens (R3: validate-then-copy, so an abort is
    atomic -- nothing partially restored). Every directory encountered is
    returned for `copy_files_tree` to `mkdir` regardless of whether it
    holds files (an empty document dir has no `manifest.files[]` entry and
    must not read as an "extra"); only regular files are compared against
    the manifest.

    W3 review M5: the "must start with documents/ or generated/" path-shape
    check (`_map_rel`'s own `CommandError` branch) is run here too, on
    every directory AND file this walk found -- not just discovered lazily
    mid-`copy_files_tree`, which would leave whatever came before the bad
    entry (in iteration order) already written to disk on abort. Calling
    `_map_rel` here is pure validation (its return value is discarded);
    `copy_files_tree` calls it again, for real, once this function has
    already proven every entry maps cleanly."""
    files_root = src / "files"
    dirs, files, bad = _walk_src_files(files_root)
    if bad:
        raise CommandError(f"Backup contains non-regular file(s), refusing to restore: {', '.join(sorted(bad))}")

    by_path = {f["path"]: f for f in manifest_files}
    file_set = set(files)

    extras = sorted(file_set - by_path.keys())
    if extras:
        raise CommandError(f"Backup contains file(s) not listed in the manifest: {', '.join(extras)}")

    missing = sorted(by_path.keys() - file_set)
    if missing:
        raise CommandError(f"Backup is missing file(s) listed in the manifest: {', '.join(missing)}")

    for rel in dirs + files:
        _map_rel(rel)  # validation only -- raises CommandError on a bad-shaped path; see docstring above

    mismatched = []
    for path in files:
        entry = by_path[path]
        full = files_root / path
        try:
            expected_size = entry["size"]
            expected_sha256 = entry["sha256"]
        except KeyError as exc:
            raise CommandError(f"Manifest entry for {path!r} is missing required field {exc}.") from exc
        try:
            actual_size = full.stat().st_size
            actual_hash = sha256_file(full)
        except OSError as exc:
            raise CommandError(f"Cannot read {full}: {exc}") from exc
        if actual_size != expected_size or actual_hash != expected_sha256:
            mismatched.append(path)
    if mismatched:
        raise CommandError(
            f"File(s) do not match the manifest (corrupted backup?): {', '.join(sorted(mismatched))}"
        )

    return dirs, files


def _map_rel(rel: str) -> Path | None:
    """`"documents/12/report.pdf"` -> `DOCUMENTS_DIR/"12/report.pdf"`,
    `"generated/<uuid>/out.png"` -> `GENERATED_DIR/"<uuid>/out.png"`.
    `None` for the two bare root entries themselves (`"documents"`,
    `"generated"`) -- they map onto `DOCUMENTS_DIR`/`GENERATED_DIR`
    themselves, which are ensured to exist by the caller, not per-entry
    here. Called twice per entry (W3 review M5): once from
    `validate_files_tree`, purely for its `CommandError` branch, before any
    write happens, and again from `copy_files_tree` for the real mapped
    path once validation has passed in full."""
    if rel in ("documents", "generated"):
        return None
    if rel.startswith("documents/"):
        return settings.DOCUMENTS_DIR / rel[len("documents/") :]
    if rel.startswith("generated/"):
        return settings.GENERATED_DIR / rel[len("generated/") :]
    raise CommandError(f"Unexpected path in backup (must start with documents/ or generated/): {rel}")


def copy_files_tree(src: Path, dirs: list[str], files: list[str]) -> None:
    """Second pass: `mkdir` every directory `validate_files_tree` found,
    then copy every regular file. Only ever reached after validation
    passed in full -- see module docstring.

    H13 review round 1, finding 9: restore is backup's mirror writer, and
    gets the same 0700/0600 discipline through the same
    `foundation.files` helpers. `DOCUMENTS_DIR`/`GENERATED_DIR`
    THEMSELVES are shared roots this call does not own (same reasoning as
    `tools.rag.views.document_upload`'s inbox-root case) -- created if
    missing, but never force-chmod'd if already there
    (`only_if_created=True`). Everything under them (a restored
    document's own directory, a restored job's own directory) IS owned
    exclusively by this restore and gets the default, aggressive
    behaviour.
    """
    files_root = src / "files"
    create_owner_only_dir(settings.DOCUMENTS_DIR, only_if_created=True)
    create_owner_only_dir(settings.GENERATED_DIR, only_if_created=True)
    for rel in dirs:
        dest = _map_rel(rel)
        if dest is not None:
            create_owner_only_dir(dest)
    for rel in files:
        dest = _map_rel(rel)
        create_owner_only_dir(dest.parent)
        shutil.copy2(files_root / rel, dest)
        # `copy2` carries the SOURCE's mode across (the backup's own
        # 0600, ordinarily -- but a manually assembled/edited backup tree
        # is not guaranteed to be) -- tighten unconditionally rather than
        # trust it.
        lock_down_file(dest)


def build_db_block(manifest: dict, src: Path, base_dir: Path) -> str:
    """`manifest.dump.present` gates EVERYTHING in this step (R3): when
    false, no hash is attempted and nothing aborts -- the files-only
    warning replaces the whole block. When true, `<src>/db.dump` is
    hashed and compared to `manifest.dump.sha256` before anything is
    printed; a mismatch aborts (retry with `--force` after re-copying a
    good `db.dump` alongside `<src>` -- see docs/OPERATIONS.md). `base_dir`
    is threaded through to `render_restore_db_sequence` for W3 review M2's
    real-path interpolation."""
    dump_info = manifest.get("dump") or {}
    if not dump_info.get("present"):
        return NO_DUMP_WARNING

    dump_path = src / "db.dump"
    try:
        actual = sha256_file(dump_path)
    except OSError as exc:
        raise CommandError(f"Cannot read {dump_path}: {exc}") from exc
    expected = dump_info.get("sha256")
    if actual != expected:
        raise CommandError(
            f"{dump_path} does not match the manifest's recorded sha256 (expected {expected}, found "
            f"{actual}) -- refusing to restore a dump that may be corrupted."
        )

    restore_sequence, recovery_sequence, note = render_restore_db_sequence(src, base_dir)
    note_block = f"\n\n{note}" if note else ""

    return (
        "Your files are restored. Now restore the database:\n\n"
        + restore_sequence
        + note_block
        + "\n\n"
        + "If that pg_restore fails partway through, --single-transaction rolled it back -- your "
        "database is untouched. If it still won't come up cleanly (with the services above still "
        "stopped), recreate the database empty and restore without --clean:\n\n"
        + recovery_sequence
        + "\n\n"
        + EXTENSION_CAVEAT
    )


def build_chunk_followup(manifest: dict) -> str:
    """The three-branch, manifest-derived chunk follow-up (R2/R3): when
    there was no dump, `NO_DUMP_WARNING` already says re-ingest rebuilds
    the index and `reencode` is not needed, so this returns `""` rather
    than repeating/contradicting that. When a dump WAS restored,
    `chunk_rows`/`chunk_bytes` describe the SOURCE box's live table at
    backup time -- never a claim about what the dump itself contains
    (only the `PGDMP` magic was checked, and `--exclude-table-data` leaves
    that intact)."""
    dump_info = manifest.get("dump") or {}
    if not dump_info.get("present"):
        return ""

    counts = manifest.get("counts") or {}
    chunk_rows = counts.get("chunk_rows")
    chunk_bytes = counts.get("chunk_bytes")
    if not chunk_rows:
        return (
            "The source box had no search index when this backup was taken -- run `docker compose exec "
            "web python manage.py reencode --role rag.embed` to build it from your documents (this "
            "re-embeds everything and can take a while)."
        )
    size = human_bytes(chunk_bytes) if chunk_bytes is not None else "unknown size"
    return (
        f"The source box had {chunk_rows:,} chunks ({size}) when this backup was "
        "taken. If your dump excluded `data_rag_chunks`, run `docker compose exec web python manage.py "
        "reencode --role rag.embed` now; otherwise the index came back with the dump."
    )


def build_active_followup(manifest: dict) -> str:
    """Name any document ids `manifest.active` recorded as pending/
    processing at backup time -- they come back in that same state with
    unfinished work; the operator Re-ingests them."""
    active = manifest.get("active") or {}
    ids = sorted(set(active.get("pending_documents", [])) | set(active.get("processing_documents", [])))
    if not ids:
        return ""
    return f"Document id(s) {ids} were still being processed when the backup was taken -- Re-ingest them from the library."


def run_restore(src_arg: str, *, force: bool, stdout, style) -> None:
    """The whole `manage.py restore` flow -- see module docstring."""
    stdout.write(style.WARNING(LIVE_POSTGRES_WARNING))

    src = Path(src_arg)
    manifest = load_manifest(src)
    check_version_skew(manifest)
    check_target_empty(force=force)
    check_active_work(force=force)

    dirs, files = validate_files_tree(src, manifest.get("files", []))
    copy_files_tree(src, dirs, files)

    stdout.write(
        f"Restored {len(files)} file(s) into {settings.DOCUMENTS_DIR} and {settings.GENERATED_DIR}."
    )
    stdout.write("")
    db_block = build_db_block(manifest, src, Path(settings.BASE_DIR))
    if db_block == NO_DUMP_WARNING:
        stdout.write(style.WARNING(db_block))  # NIT: styled prominently, not just plain text
    else:
        stdout.write(db_block)

    chunk_followup = build_chunk_followup(manifest)
    if chunk_followup:
        stdout.write("")
        stdout.write(chunk_followup)

    active_followup = build_active_followup(manifest)
    if active_followup:
        stdout.write("")
        stdout.write(active_followup)

    stdout.write("")
    stdout.write("Once services are back up, open /rag/ to confirm the library.")
