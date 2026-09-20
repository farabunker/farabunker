"""Unit tests for foundation/ops/backup.py + management/commands/backup.py
(W3, ADR 0014 §18).

`@pytest.mark.django_db` at class level (repo convention); no
`conftest.py` (the repo forbids one anywhere) -- each test class defines
its own `roots` fixture via `override_settings`, listing every one of
`DATA_DIR`/`DOCUMENTS_DIR`/`GENERATED_DIR`/`INGEST_INBOX_DIR`/
`FILE_UPLOAD_TEMP_DIR` individually (never recomputed from `DATA_DIR`).
No `pg_dump`/`pg_restore` ever runs here -- `_dump_bytes()` below fakes a
`pg_dump --format=custom` file by starting it with the `PGDMP` magic.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from models.queue.models import RUNNING, InferenceJob
from foundation.ops.backup import LIVE_POSTGRES_WARNING, _ensure_backup_dest_dir, _resolve_git_sha
from tools.rag.models import Category, Document


def _dump_bytes(n: int = 200) -> bytes:
    return b"PGDMP" + b"\x00" * n


def _doc(**overrides) -> Document:
    fields = dict(
        title="report.pdf",
        source_path="/irrelevant/report.pdf",
        original_path="/inbox/report.pdf",
        file_hash="a" * 64,
        doc_type=Document.DocType.PROSE,
        status=Document.Status.READY,
    )
    fields.update(overrides)
    return Document.objects.create(**fields)


@pytest.fixture
def roots(tmp_path):
    data_dir = tmp_path / "data"
    documents_dir = data_dir / "documents"
    generated_dir = data_dir / "generated"
    inbox_dir = data_dir / "inbox"
    upload_tmp_dir = data_dir / "tmp"
    for d in (documents_dir, generated_dir, inbox_dir, upload_tmp_dir):
        d.mkdir(parents=True)
    with override_settings(
        DATA_DIR=data_dir,
        DOCUMENTS_DIR=documents_dir,
        GENERATED_DIR=generated_dir,
        INGEST_INBOX_DIR=inbox_dir,
        FILE_UPLOAD_TEMP_DIR=upload_tmp_dir,
    ):
        yield {
            "data": data_dir,
            "documents": documents_dir,
            "generated": generated_dir,
            "inbox": inbox_dir,
            "tmp": upload_tmp_dir,
        }


@pytest.fixture
def a_backup_run(roots):
    """`a_backup_run(dest, dump=None)` -- run `manage.py backup` into
    `dest` (creating one document with one file first, so there is always
    at least one copied file to assert a mode on) and return `dest` as a
    `Path`. S14's `TestBackupPermissions` below only cares about the
    resulting modes, not the content, so the document is a minimal
    fixed one; tests that need a specific source file's own mode
    (`a_world_readable_document`) create it before calling this."""
    def run(dest, *, dump=None):
        dest = Path(dest)
        if not any((roots["documents"]).rglob("*")):
            doc_dir = roots["documents"] / "1"
            doc_dir.mkdir(exist_ok=True)
            (doc_dir / "report.txt").write_bytes(b"hello")
        args = [str(dest)]
        if dump is not None:
            args += ["--dump", str(dump)]
        call_command("backup", *args)
        return dest

    return run


@pytest.fixture
def a_dump_file(tmp_path):
    """An external `pg_dump --format=custom` file, OUTSIDE any backup
    destination -- `a_backup_run`'s `dump=` takes the copy path
    (`fold_in_dump`'s `not same` branch), not the hash-in-place one."""
    dump = tmp_path / "external.dump"
    dump.write_bytes(_dump_bytes())
    return dump


@pytest.fixture
def a_world_readable_document(roots):
    """A document source file at a wide, world-readable mode --
    `test_copystat_does_not_restore_the_sources_wider_mode` pins that
    `_copy_and_hash`'s own `os.chmod` (after `shutil.copystat`) is what
    actually tightens the COPY despite the SOURCE staying wide."""
    doc_dir = roots["documents"] / "1"
    doc_dir.mkdir(exist_ok=True)
    src = doc_dir / "report.txt"
    src.write_bytes(b"hello")
    os.chmod(src, 0o644)
    return src


@pytest.mark.django_db
class TestManifestContent:
    def test_manifest_shape(self, roots, tmp_path, capsys):
        doc_dir = roots["documents"] / "1"
        doc_dir.mkdir()
        (doc_dir / "report.pdf").write_bytes(b"hello")
        _doc()
        Category.objects.create(name="Finance")
        expected_categories = Category.objects.count()  # 0004 seeds 4 defaults

        dest = tmp_path / "backup"
        call_command("backup", str(dest))

        manifest = json.loads((dest / "manifest.json").read_text())
        assert manifest["manifest_version"] == 1
        assert "T" in manifest["created_at"]  # ISO8601
        assert manifest["counts"]["documents"] == 1
        assert manifest["counts"]["categories"] == expected_categories
        assert manifest["migrations"]
        assert manifest["farabunker"]["features"] == sorted(manifest["farabunker"]["features"])
        assert manifest["farabunker"]["git_sha"] is None or isinstance(manifest["farabunker"]["git_sha"], str)
        assert manifest["database"]["user"] is not None
        assert "password" not in manifest["database"]

        entry = next(f for f in manifest["files"] if f["path"] == "documents/1/report.pdf")
        assert entry["size"] == 5
        import hashlib

        assert entry["sha256"] == hashlib.sha256(b"hello").hexdigest()

        assert "password" not in json.dumps(manifest).lower()
        assert "mtime" not in manifest["files"][0]
        assert "store_shape" not in manifest


@pytest.mark.django_db
class TestTwoRootsOnly:
    def test_work_tmp_postgres_preview_excluded(self, roots, tmp_path):
        doc_dir = roots["documents"] / "1"
        doc_dir.mkdir()
        (doc_dir / "report.pdf").write_bytes(b"hi")
        work_dir = doc_dir / "work"
        work_dir.mkdir()
        (work_dir / "scratch.wav").write_bytes(b"scratch")

        (roots["data"] / "tmp").mkdir(exist_ok=True)
        (roots["data"] / "tmp" / "upload.part").write_bytes(b"x")
        (roots["data"] / "postgres").mkdir()
        (roots["data"] / "postgres" / "PG_VERSION").write_bytes(b"16")
        (roots["data"] / "preview").mkdir()
        (roots["data"] / "preview" / "stray.txt").write_bytes(b"x")
        (roots["data"] / "stray_top_level").mkdir()
        (roots["data"] / "stray_top_level" / "x.txt").write_bytes(b"x")

        dest = tmp_path / "backup"
        call_command("backup", str(dest))

        manifest = json.loads((dest / "manifest.json").read_text())
        paths = [f["path"] for f in manifest["files"]]
        assert paths == ["documents/1/report.pdf"]
        assert not (dest / "files" / "documents" / "1" / "work").exists()
        assert not any((dest / "files").rglob("scratch.wav"))
        assert not any((dest / "files").rglob("upload.part"))
        assert not any((dest / "files").rglob("PG_VERSION"))
        assert not any((dest / "files").rglob("stray.txt"))
        assert not any((dest / "files").rglob("x.txt"))


@pytest.mark.django_db
class TestInclusions:
    def test_original_extract_and_generated_copied_empty_dir_survives(self, roots, tmp_path):
        doc_dir = roots["documents"] / "1"
        doc_dir.mkdir()
        (doc_dir / "report.pdf").write_bytes(b"hi")
        (doc_dir / "extract.json").write_bytes(b"{}")

        empty_doc_dir = roots["documents"] / "2"
        empty_doc_dir.mkdir()
        (empty_doc_dir / "work").mkdir()  # only work/ -- gets filtered, dir itself must survive

        gen_dir = roots["generated"] / "abc-uuid"
        gen_dir.mkdir()
        (gen_dir / "output.png").write_bytes(b"png-bytes")

        dest = tmp_path / "backup"
        call_command("backup", str(dest))

        assert (dest / "files" / "documents" / "1" / "report.pdf").read_bytes() == b"hi"
        assert (dest / "files" / "documents" / "1" / "extract.json").read_bytes() == b"{}"
        assert (dest / "files" / "generated" / "abc-uuid" / "output.png").read_bytes() == b"png-bytes"
        assert (dest / "files" / "documents" / "2").is_dir()
        assert not (dest / "files" / "documents" / "2" / "work").exists()


@pytest.mark.django_db
class TestCopySemantics:
    def test_symlink_aborts_naming_it(self, roots, tmp_path):
        doc_dir = roots["documents"] / "1"
        doc_dir.mkdir()
        real = tmp_path / "outside.txt"
        real.write_bytes(b"x")
        (doc_dir / "link.txt").symlink_to(real)

        dest = tmp_path / "backup"
        with pytest.raises(CommandError) as exc:
            call_command("backup", str(dest))
        assert "link.txt" in str(exc.value)

    def test_unreadable_file_aborts_naming_it(self, roots, tmp_path):
        doc_dir = roots["documents"] / "1"
        doc_dir.mkdir()
        bad = doc_dir / "secret.pdf"
        bad.write_bytes(b"x")
        os.chmod(bad, 0o000)
        dest = tmp_path / "backup"
        try:
            with pytest.raises(CommandError) as exc:
                call_command("backup", str(dest))
            assert "secret.pdf" in str(exc.value)
        finally:
            os.chmod(bad, 0o644)


@pytest.mark.django_db
class TestInbox:
    def test_inbox_not_copied_warning_names_count(self, roots, tmp_path, capsys):
        (roots["inbox"] / "cat").mkdir()
        (roots["inbox"] / "cat" / "new.pdf").write_bytes(b"x")
        (roots["inbox"] / "cat" / "new2.pdf").write_bytes(b"x")

        dest = tmp_path / "backup"
        call_command("backup", str(dest))
        out = capsys.readouterr().out
        assert "2" in out
        assert "inbox" in out.lower()
        assert not any((dest / "files").rglob("new.pdf"))


@pytest.mark.django_db
class TestDestRules:
    def test_nonempty_dest_refused(self, roots, tmp_path):
        dest = tmp_path / "backup"
        dest.mkdir()
        (dest / "stray.txt").write_bytes(b"x")
        with pytest.raises(CommandError):
            call_command("backup", str(dest))

    def test_force_proceeds_into_nonempty_dest(self, roots, tmp_path):
        dest = tmp_path / "backup"
        dest.mkdir()
        (dest / "stray.txt").write_bytes(b"x")
        call_command("backup", str(dest), "--force")
        assert (dest / "manifest.json").exists()

    def test_dest_inside_documents_dir_refused(self, roots):
        dest = roots["documents"] / "nested-backup"
        with pytest.raises(CommandError):
            call_command("backup", str(dest))


@pytest.mark.django_db
class TestForceNeverPrunes:
    """W3 review MAJOR 2: `--force` reusing a `<dest>` that already holds a
    DIFFERENT backup (fewer files than before) must refuse, naming the
    stale file left behind -- `copy_files` only ever adds/overwrites, it
    never prunes, and restore's own extras check would refuse that exact
    file later anyway."""

    def test_force_into_dest_with_stale_file_from_earlier_backup_refuses_naming_it(self, roots, tmp_path):
        doc1_dir = roots["documents"] / "1"
        doc1_dir.mkdir()
        (doc1_dir / "one.pdf").write_bytes(b"one")
        doc2_dir = roots["documents"] / "2"
        doc2_dir.mkdir()
        (doc2_dir / "two.pdf").write_bytes(b"two")

        dest = tmp_path / "backup"
        call_command("backup", str(dest))  # night 1: both docs
        assert (dest / "files" / "documents" / "2" / "two.pdf").exists()

        shutil.rmtree(doc2_dir)  # doc 2 is gone from the live root by night 2

        with pytest.raises(CommandError) as exc:
            call_command("backup", str(dest), "--force")  # night 2: reuses the same dest
        assert "documents/2/two.pdf" in str(exc.value)
        assert "--force reused a dest" in str(exc.value)

    def test_force_into_dest_holding_only_a_subset_of_this_run_never_refuses(self, roots, tmp_path):
        """The normal, intended `--force` use (a truly empty-or-matching
        dest, or one only missing a stray top-level file outside
        `files/`) must not be affected."""
        doc_dir = roots["documents"] / "1"
        doc_dir.mkdir()
        (doc_dir / "report.pdf").write_bytes(b"hello")

        dest = tmp_path / "backup"
        call_command("backup", str(dest))
        call_command("backup", str(dest), "--force")  # identical re-run, nothing stale
        assert (dest / "manifest.json").exists()

    def test_force_refuses_before_any_write_dest_still_matches_old_manifest(self, roots, tmp_path):
        """W3 follow-up review: `check_no_stale_files` must run BEFORE
        `copy_files` writes anything. Previously it ran AFTER `copy_files`
        had already overwritten `<dest>/files/**`, so an aborted `--force`
        run could leave NEW bytes for files it reached sitting under the
        OLD manifest (which was never rewritten, since the abort happens
        before `manifest.json` is written) -- a later restore would then
        fail with a confusing "corrupted backup" hash mismatch. The fix:
        the stale-file diff runs against a read-only source walk, before
        any write, so a refusal here leaves `<dest>` untouched -- every
        file still matches the OLD manifest byte-for-byte."""
        doc1_dir = roots["documents"] / "1"
        doc1_dir.mkdir()
        (doc1_dir / "one.pdf").write_bytes(b"one-original")
        doc2_dir = roots["documents"] / "2"
        doc2_dir.mkdir()
        (doc2_dir / "two.pdf").write_bytes(b"two")

        dest = tmp_path / "backup"
        call_command("backup", str(dest))  # night 1: both docs
        old_manifest = json.loads((dest / "manifest.json").read_text())

        (doc1_dir / "one.pdf").write_bytes(b"one-EDITED")  # source doc edited
        shutil.rmtree(doc2_dir)  # and another doc is gone entirely, by night 2

        with pytest.raises(CommandError) as exc:
            call_command("backup", str(dest), "--force")  # night 2: reuses the same dest
        assert "documents/2/two.pdf" in str(exc.value)

        # Nothing mutated: dest still matches the OLD manifest exactly, even
        # for the file (one.pdf) copy_files would otherwise have reached and
        # overwritten with the edited source bytes before the old post-write
        # check fired.
        assert (dest / "files" / "documents" / "1" / "one.pdf").read_bytes() == b"one-original"
        assert (dest / "files" / "documents" / "2" / "two.pdf").read_bytes() == b"two"
        assert json.loads((dest / "manifest.json").read_text()) == old_manifest

    def test_more_than_20_stale_files_caps_named_list_and_counts_the_rest(self, roots, tmp_path):
        doc_dir = roots["documents"] / "1"
        doc_dir.mkdir()
        (doc_dir / "keep.pdf").write_bytes(b"keep")

        dest = tmp_path / "backup"
        call_command("backup", str(dest))

        stale_dir = dest / "files" / "documents" / "stale"
        stale_dir.mkdir()
        for i in range(25):
            (stale_dir / f"extra{i}.txt").write_bytes(b"x")

        with pytest.raises(CommandError) as exc:
            call_command("backup", str(dest), "--force")
        message = str(exc.value)
        assert message.count("extra") == 20
        assert "...and 5 more" in message


@pytest.mark.django_db
class TestLivePostgresWarningHeader:
    def test_printed_as_the_first_line(self, roots, tmp_path, capsys):
        dest = tmp_path / "backup"
        call_command("backup", str(dest))
        out = capsys.readouterr().out
        assert LIVE_POSTGRES_WARNING in out
        assert out.index(LIVE_POSTGRES_WARNING) < out.index(str(dest))


@pytest.mark.django_db
class TestRenderBackupSequencePaths:
    """W3 review M2: the printed fallback sequence interpolates real paths
    where they're knowable, and never touches `docs/OPERATIONS.md`'s own
    generic `<name>` placeholder."""

    def test_dest_under_base_dir_interpolates_host_relative_path(self, roots, tmp_path, capsys, settings):
        settings.BASE_DIR = tmp_path
        dest = tmp_path / "data" / "backups" / "2026-08-24"
        call_command("backup", str(dest))
        out = capsys.readouterr().out
        assert "./data/backups/2026-08-24/db.dump" in out
        assert "mkdir -p ./data/backups/2026-08-24" in out
        assert str(dest) in out  # in-container line still gets the literal argument
        assert "<name>" not in out

    def test_dest_outside_base_dir_keeps_name_placeholder(self, roots, tmp_path, capsys, settings):
        other_base = tmp_path / "elsewhere"
        other_base.mkdir()
        settings.BASE_DIR = other_base
        dest = tmp_path / "backup"
        call_command("backup", str(dest))
        out = capsys.readouterr().out
        assert "./data/backups/<name>/db.dump" in out
        assert str(dest) in out  # in-container line always gets the literal argument
        assert (
            "This dest is outside the repo; run pg_dump into a path you can reach from the host and "
            "pass it with --dump." in out
        )


@pytest.mark.django_db
class TestSearchIndexMessageGating:
    """W3 review M3: the "search index: N chunks" line must say whether
    the chunks are actually in THIS backup -- gated on whether --dump was
    given, not printed as though every backup includes the dump."""

    def test_with_dump_says_included(self, roots, tmp_path, capsys):
        dump = tmp_path / "db.dump"
        dump.write_bytes(_dump_bytes())
        dest = tmp_path / "backup"
        with patch("foundation.ops.backup._chunk_stats", return_value=(10, 2048)):
            call_command("backup", str(dest), "--dump", str(dump))
        out = capsys.readouterr().out
        assert "search index: 10 chunks" in out
        assert "included in your dump" in out
        assert "NOT" not in out

    def test_without_dump_says_not_included(self, roots, tmp_path, capsys):
        dest = tmp_path / "backup"
        with patch("foundation.ops.backup._chunk_stats", return_value=(10, 2048)):
            call_command("backup", str(dest))
        out = capsys.readouterr().out
        assert "search index: 10 chunks" in out
        assert "NOT" in out
        assert "files-only backup" in out
        assert "included in your dump" not in out


@pytest.mark.django_db
class TestDumpPlumbing:
    def test_no_dump_incomplete_and_prints_sequence(self, roots, tmp_path, capsys):
        dest = tmp_path / "backup"
        call_command("backup", str(dest))
        manifest = json.loads((dest / "manifest.json").read_text())
        assert manifest["complete"] is False
        assert manifest["dump"]["present"] is False

        out = capsys.readouterr().out
        assert "docker compose exec -T db pg_dump -U farabunker -d farabunker" in out
        assert "--format=custom --no-owner --no-privileges" in out
        assert "docker compose stop worker watcher" in out
        assert "docker compose start worker watcher" in out
        assert "Backup complete" not in out  # never claims completeness without a dump

    def test_dump_outside_dest_copied_and_hashed(self, roots, tmp_path):
        dump = tmp_path / "outside" / "db.dump"
        dump.parent.mkdir()
        data = _dump_bytes()
        dump.write_bytes(data)

        dest = tmp_path / "backup"
        call_command("backup", str(dest), "--dump", str(dump))

        manifest = json.loads((dest / "manifest.json").read_text())
        assert manifest["complete"] is True
        assert manifest["dump"]["present"] is True
        import hashlib

        assert manifest["dump"]["sha256"] == hashlib.sha256(data).hexdigest()
        assert (dest / "db.dump").read_bytes() == data

    def test_dump_already_at_dest_hashed_in_place_not_moved(self, roots, tmp_path):
        dest = tmp_path / "backup"
        dest.mkdir()
        dump = dest / "db.dump"
        data = _dump_bytes()
        dump.write_bytes(data)
        before_inode = dump.stat().st_ino

        call_command("backup", str(dest), "--dump", str(dump))

        manifest = json.loads((dest / "manifest.json").read_text())
        assert manifest["dump"]["present"] is True
        assert dump.stat().st_ino == before_inode  # never moved onto itself


@pytest.mark.django_db
class TestDumpValidation:
    def test_empty_dump_refused(self, roots, tmp_path):
        dump = tmp_path / "db.dump"
        dump.write_bytes(b"")
        dest = tmp_path / "backup"
        with pytest.raises(CommandError):
            call_command("backup", str(dest), "--dump", str(dump))

    def test_plain_sql_dump_refused_naming_format_custom(self, roots, tmp_path):
        dump = tmp_path / "db.dump"
        dump.write_bytes(b"--\n-- PostgreSQL database dump\n--\n")
        dest = tmp_path / "backup"
        with pytest.raises(CommandError) as exc:
            call_command("backup", str(dest), "--dump", str(dump))
        assert "--format=custom" in str(exc.value)


@pytest.mark.django_db
class TestActiveWork:
    def test_running_job_refuses_naming_stop_command(self, roots, tmp_path):
        InferenceJob.objects.create(kind="test.job", priority=100, state=RUNNING)
        dest = tmp_path / "backup"
        with pytest.raises(CommandError) as exc:
            call_command("backup", str(dest))
        assert "docker compose stop worker watcher" in str(exc.value)

    def test_processing_document_refuses(self, roots, tmp_path):
        _doc(status=Document.Status.PROCESSING)
        dest = tmp_path / "backup"
        with pytest.raises(CommandError):
            call_command("backup", str(dest))

    def test_pending_document_does_not_refuse_and_is_recorded(self, roots, tmp_path):
        doc = _doc(status=Document.Status.PENDING)
        dest = tmp_path / "backup"
        call_command("backup", str(dest))
        manifest = json.loads((dest / "manifest.json").read_text())
        assert doc.id in manifest["active"]["pending_documents"]

    def test_force_overrides_both_refusals(self, roots, tmp_path):
        InferenceJob.objects.create(kind="test.job", priority=100, state=RUNNING)
        _doc(status=Document.Status.PROCESSING)
        dest = tmp_path / "backup"
        call_command("backup", str(dest), "--force")
        assert (dest / "manifest.json").exists()


@pytest.mark.django_db
class TestBackupPermissions:
    def test_the_destination_directory_is_owner_only(self, tmp_path, a_backup_run):
        dest = a_backup_run(tmp_path / "b1")
        assert stat.S_IMODE(dest.stat().st_mode) == 0o700

    def test_every_copied_file_is_owner_read_write_only(self, tmp_path, a_backup_run):
        dest = a_backup_run(tmp_path / "b2")
        for path in (dest / "files").rglob("*"):
            if path.is_file():
                assert stat.S_IMODE(path.stat().st_mode) == 0o600, path

    def test_the_manifest_is_owner_read_write_only(self, tmp_path, a_backup_run):
        """It enumerates every document path and every applied migration."""
        dest = a_backup_run(tmp_path / "b3")
        assert stat.S_IMODE((dest / "manifest.json").stat().st_mode) == 0o600

    def test_the_dump_is_owner_read_write_only(self, tmp_path, a_backup_run, a_dump_file):
        """The credential store itself: password hashes, session keys, the
        audit log, entitlement grants."""
        dest = a_backup_run(tmp_path / "b4", dump=a_dump_file)
        assert stat.S_IMODE((dest / "db.dump").stat().st_mode) == 0o600

    def test_copystat_does_not_restore_the_sources_wider_mode(self, tmp_path, a_backup_run,
                                                              a_world_readable_document):
        """`_copy_and_hash` ends with `shutil.copystat`, which copies the
        SOURCE's mode over the destination's -- so tightening at open()
        time alone would be undone one line later."""
        dest = a_backup_run(tmp_path / "b5")
        copied = next((dest / "files").rglob("*.txt"))
        assert stat.S_IMODE(copied.stat().st_mode) == 0o600

    def test_dump_already_at_dest_is_tightened_too(self, tmp_path, roots):
        """H13 review round 1, finding 1 (CRITICAL): the documented
        in-place sequence -- `pg_dump` redirected straight to
        `<dest>/db.dump`, then that same path passed to `--dump` -- takes
        `fold_in_dump`'s SAME-file branch, which skips the `mkdir`/
        `copy2` (and, before this fix, skipped the chmod right along with
        them, leaving a dump placed there directly at whatever mode
        produced it)."""
        dest = tmp_path / "backup"
        dest.mkdir()
        dump = dest / "db.dump"
        dump.write_bytes(_dump_bytes())
        os.chmod(dump, 0o644)

        call_command("backup", str(dest), "--dump", str(dump))

        assert stat.S_IMODE(dump.stat().st_mode) == 0o600

    def test_nested_directories_under_files_are_owner_only(self, tmp_path, roots):
        """H13 review round 1, finding 8: not just the top-level
        destination -- every directory `copy_files`/`_copy_and_hash`
        create under `<dest>/files/**` is this backup's own, including an
        EMPTY one kept only to preserve shape."""
        doc_dir = roots["documents"] / "1"
        doc_dir.mkdir()
        (doc_dir / "report.txt").write_bytes(b"hello")
        empty_doc_dir = roots["documents"] / "2"
        empty_doc_dir.mkdir()
        (empty_doc_dir / "work").mkdir()  # filtered out; doc 2's own dir still survives, empty

        dest = tmp_path / "backup"
        call_command("backup", str(dest))

        checked = 0
        for path in (dest / "files").rglob("*"):
            if path.is_dir():
                assert stat.S_IMODE(path.stat().st_mode) == 0o700, path
                checked += 1
        assert checked >= 3  # documents/, documents/1/, documents/2/ at least


class _RaisesOnMkdir(Path):
    """Same stand-in shape as
    `foundation/ops/tests/test_upload_temp_dir_permissions.py`'s: a
    `Path` whose `mkdir` raises `PermissionError` the way a real
    `Path.mkdir` does against a directory this process's uid does not
    own, without touching the filesystem at all."""

    def mkdir(self, *args, **kwargs):  # noqa: D102 - trivial
        raise PermissionError(13, "Permission denied")


class TestBackupDestPermissionRefusal:
    """S12 follow-on, same shape as
    `test_upload_temp_dir_permissions.py`'s coverage of
    `config.settings._ensure_file_upload_temp_dir`: the DEFAULT backup
    destination nests under `settings.BACKUP_DIR`, which lives under
    `./data` like every other durable root this platform writes to -- a
    box that hasn't run the S12 `chown` yet hits `create_owner_only_dir`'s
    bare `Path.mkdir` `PermissionError` here, with no mention of `chown`
    or the uid this box now runs as, unless this wrapper names the fix."""

    def test_a_permission_error_becomes_an_actionable_refusal(self):
        bad_path = _RaisesOnMkdir("/nonexistent/vault/20260101T000000000000Z")
        with pytest.raises(CommandError) as excinfo:
            _ensure_backup_dest_dir(bad_path)
        message = str(excinfo.value)
        assert str(bad_path) in message
        assert "10001" in message
        assert "chown" in message

    def test_a_normal_mkdir_is_left_alone(self, tmp_path):
        """The happy path -- an already-writable destination -- must
        still work exactly as `create_owner_only_dir` did before this
        wrapper existed."""
        target = tmp_path / "vault" / "20260101T000000000000Z"
        _ensure_backup_dest_dir(target)
        assert target.is_dir()


@pytest.fixture
def a_reloaded_settings_module(monkeypatch):
    """H13 review round 1, finding 11: `importlib.reload` mutates the
    ACTUAL `config.settings` module object sitting in `sys.modules` --
    pytest's own `monkeypatch` teardown undoes the environment variable,
    but not that mutation, so a naive `importlib.reload(shipped)` inside a
    test leaves every LATER test in the same process importing
    `config.settings` and seeing THIS test's `FARABUNKER_BACKUP_DIR`
    value. This fixture reloads the module under a caller-supplied value,
    then reloads it AGAIN, with the variable removed, once the test is
    done -- restoring the module the way `monkeypatch` already restores
    the environment."""
    import importlib

    import config.settings as shipped

    def reload_with(value: str | None):
        if value is None:
            monkeypatch.delenv("FARABUNKER_BACKUP_DIR", raising=False)
        else:
            monkeypatch.setenv("FARABUNKER_BACKUP_DIR", value)
        importlib.reload(shipped)
        return shipped

    yield reload_with
    reload_with(None)


class TestBackupDestinationSetting:
    def test_the_default_is_inside_the_durable_data_volume(self, settings):
        assert settings.BACKUP_DIR == settings.DATA_DIR / "backups"

    def test_an_operator_can_point_it_elsewhere(self, tmp_path, a_reloaded_settings_module):
        shipped = a_reloaded_settings_module(str(tmp_path / "vault"))
        assert shipped.BACKUP_DIR == tmp_path / "vault"


@pytest.mark.django_db
class TestDestArgDefaultsToBackupDir:
    """S14/H13 review round 1 finding 6: an operator who runs `manage.py
    backup` naming no destination gets a fresh, TIMESTAMPED directory
    under `settings.BACKUP_DIR`, not a `CommandError` and not
    `settings.BACKUP_DIR` itself (which would make a second run collide
    with the first) -- so pointing `FARABUNKER_BACKUP_DIR` at an
    encrypted volume and just running `manage.py backup`, repeatedly,
    compose without any path arithmetic."""

    def test_omitting_dest_backs_up_under_settings_backup_dir(self, roots, settings, tmp_path):
        settings.BACKUP_DIR = tmp_path / "vault"
        doc_dir = roots["documents"] / "1"
        doc_dir.mkdir()
        (doc_dir / "report.txt").write_bytes(b"hi")

        call_command("backup")

        manifests = list(settings.BACKUP_DIR.rglob("manifest.json"))
        assert len(manifests) == 1
        assert manifests[0].parent.parent == settings.BACKUP_DIR

    def test_two_consecutive_runs_do_not_collide(self, roots, settings, tmp_path):
        """The whole point of a timestamped subdirectory: a bare
        `manage.py backup` defaulting straight to `settings.BACKUP_DIR`
        (no subdirectory at all) would make a SECOND run refuse into the
        still non-empty first one."""
        settings.BACKUP_DIR = tmp_path / "vault"
        doc_dir = roots["documents"] / "1"
        doc_dir.mkdir()
        (doc_dir / "report.txt").write_bytes(b"hi")

        call_command("backup")
        call_command("backup")

        destinations = {p.parent for p in settings.BACKUP_DIR.rglob("manifest.json")}
        assert len(destinations) == 2

    def test_an_explicit_dest_still_overrides_the_default(self, roots, settings, tmp_path):
        settings.BACKUP_DIR = tmp_path / "vault"
        explicit = tmp_path / "elsewhere"
        doc_dir = roots["documents"] / "1"
        doc_dir.mkdir()
        (doc_dir / "report.txt").write_bytes(b"hi")

        call_command("backup", str(explicit))

        assert (explicit / "manifest.json").exists()
        assert not settings.BACKUP_DIR.exists()


class TestGitSha:
    """No `@pytest.mark.django_db` -- `_resolve_git_sha` is pure
    filesystem, no DB involved."""

    def test_plain_git_dir(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        (git_dir / "refs" / "heads").mkdir(parents=True)
        (git_dir / "refs" / "heads" / "main").write_text("a" * 40 + "\n")
        assert _resolve_git_sha(tmp_path) == "a" * 40

    def test_linked_worktree_gitdir_indirection(self, tmp_path):
        """The exact shape a `git worktree add` checkout has: `.git` is a
        FILE (`open(".git/HEAD")` would raise `NotADirectoryError`, an
        `OSError`), and refs live in the common dir via `commondir`
        (R1/R3-5)."""
        common = tmp_path / "main-repo" / ".git"
        common.mkdir(parents=True)
        (common / "refs" / "heads").mkdir(parents=True)
        (common / "refs" / "heads" / "feature").write_text("b" * 40 + "\n")

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        wt_gitdir = tmp_path / "main-repo" / ".git" / "worktrees" / "feature"
        wt_gitdir.mkdir(parents=True)
        (wt_gitdir / "commondir").write_text("../..\n")
        (wt_gitdir / "HEAD").write_text("ref: refs/heads/feature\n")
        (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n")

        assert _resolve_git_sha(worktree) == "b" * 40

    def test_no_git_dir_returns_none(self, tmp_path):
        assert _resolve_git_sha(tmp_path) is None

    def test_detached_head_reads_sha_directly(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("c" * 40 + "\n")
        assert _resolve_git_sha(tmp_path) == "c" * 40

    def test_worktree_gitdir_unreachable_in_container_returns_none_honestly(self, tmp_path):
        """W3 review M4: `git worktree add` writes the `gitdir:` line as an
        ABSOLUTE HOST path. A preview stack that bind-mounts this same
        worktree checkout into a container cannot reach that host path at
        all -- there is no in-container fallback for it (the common dir a
        linked worktree needs lives outside the single bind-mounted
        directory). This documents that `git_sha: None` in that situation
        is the honest, expected answer, not a bug -- see the qualified
        docstring on `_resolve_git_sha`."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        unreachable_host_gitdir = "/this/host/path/does/not/exist/in/the/container/.git/worktrees/feature"
        (worktree / ".git").write_text(f"gitdir: {unreachable_host_gitdir}\n")

        assert _resolve_git_sha(worktree) is None
