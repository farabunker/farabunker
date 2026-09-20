"""Unit tests for foundation/ops/restore.py + management/commands/restore.py
(W3, ADR 0014 §18).

`@pytest.mark.django_db` at class level (repo convention); no
`conftest.py`. Most tests build a REAL backup first (via `manage.py
backup`, against a "source" root pair) and then restore it into a
separate, freshly-empty "target" root pair -- exercising the actual
manifest shape `foundation.ops.backup` writes rather than a hand-built one.
`_roots()` is a plain context manager (not a fixture) so a single test can
open two independent root sets in sequence.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

import foundation.files as core_files
from models.queue.models import RUNNING, InferenceJob


def _dump_bytes(n: int = 200) -> bytes:
    return b"PGDMP" + b"\x00" * n


@contextmanager
def _roots(tmp_path, name: str):
    base = tmp_path / name
    documents_dir = base / "documents"
    generated_dir = base / "generated"
    inbox_dir = base / "inbox"
    upload_tmp_dir = base / "tmp"
    for d in (documents_dir, generated_dir, inbox_dir, upload_tmp_dir):
        d.mkdir(parents=True)
    with override_settings(
        DATA_DIR=base,
        DOCUMENTS_DIR=documents_dir,
        GENERATED_DIR=generated_dir,
        INGEST_INBOX_DIR=inbox_dir,
        FILE_UPLOAD_TEMP_DIR=upload_tmp_dir,
    ):
        yield {
            "data": base,
            "documents": documents_dir,
            "generated": generated_dir,
            "inbox": inbox_dir,
            "tmp": upload_tmp_dir,
        }


def _manifest_path(backup_dir):
    return backup_dir / "manifest.json"


def _read_manifest(backup_dir):
    return json.loads(_manifest_path(backup_dir).read_text())


def _write_manifest(backup_dir, manifest):
    _manifest_path(backup_dir).write_text(json.dumps(manifest, indent=2))


def _make_backup(tmp_path, *, with_dump: bool = True):
    """Build a real backup (one document, one generated output) under
    `tmp_path / "backup"`, returning its path."""
    with _roots(tmp_path, "source") as src:
        (src["documents"] / "1").mkdir()
        (src["documents"] / "1" / "report.pdf").write_bytes(b"hello world")
        (src["generated"] / "abc-uuid").mkdir()
        (src["generated"] / "abc-uuid" / "out.png").write_bytes(b"png-bytes")

        backup_dir = tmp_path / "backup"
        args = [str(backup_dir)]
        if with_dump:
            dump = tmp_path / "db.dump"
            dump.write_bytes(_dump_bytes())
            args += ["--dump", str(dump)]
        call_command("backup", *args)
    return backup_dir


@pytest.mark.django_db
class TestManifestGuards:
    def test_missing_manifest_refused(self, tmp_path):
        empty_src = tmp_path / "nowhere"
        empty_src.mkdir()
        with _roots(tmp_path, "target"):
            with pytest.raises(CommandError):
                call_command("restore", str(empty_src))

    def test_manifest_version_2_refused(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        manifest = _read_manifest(backup_dir)
        manifest["manifest_version"] = 2
        _write_manifest(backup_dir, manifest)

        with _roots(tmp_path, "target"):
            with pytest.raises(CommandError) as exc:
                call_command("restore", str(backup_dir))
        assert "newer farabunker" in str(exc.value)


@pytest.mark.django_db
class TestVersionSkew:
    def test_unknown_migration_refused_naming_it(self, tmp_path):
        from foundation.ops import restore

        manifest = {"migrations": ["rag.9999_future"]}
        with pytest.raises(CommandError) as exc:
            restore.check_version_skew(manifest)
        assert "rag.9999_future" in str(exc.value)

    def test_strict_subset_of_disk_migrations_restores_without_complaint(self, tmp_path):
        from foundation.ops import restore

        backup_dir = _make_backup(tmp_path)
        manifest = _read_manifest(backup_dir)
        manifest["migrations"] = manifest["migrations"][:-1]  # drop one -- a subset
        restore.check_version_skew(manifest)  # must not raise


@pytest.mark.django_db
class TestTargetGuards:
    def test_nonempty_documents_dir_refused(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        with _roots(tmp_path, "target") as tgt:
            (tgt["documents"] / "stray.txt").write_bytes(b"x")
            with pytest.raises(CommandError):
                call_command("restore", str(backup_dir))

    def test_force_overwrites_differing_hash_leaves_stale_sibling(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        with _roots(tmp_path, "target") as tgt:
            (tgt["documents"] / "1").mkdir()
            (tgt["documents"] / "1" / "report.pdf").write_bytes(b"STALE CONTENT")
            (tgt["documents"] / "1" / "unrelated.txt").write_bytes(b"leave me alone")

            call_command("restore", str(backup_dir), "--force")

            assert (tgt["documents"] / "1" / "report.pdf").read_bytes() == b"hello world"
            assert (tgt["documents"] / "1" / "unrelated.txt").read_bytes() == b"leave me alone"


@pytest.mark.django_db
class TestVerification:
    def test_corrupted_source_file_aborts_naming_it(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        (backup_dir / "files" / "documents" / "1" / "report.pdf").write_bytes(b"CORRUPTED")

        with _roots(tmp_path, "target") as tgt:
            with pytest.raises(CommandError) as exc:
                call_command("restore", str(backup_dir))
        assert "documents/1/report.pdf" in str(exc.value)
        assert not (tgt["documents"] / "1").exists()  # nothing written -- abort before copy

    def test_dump_hash_mismatch_aborts_before_pg_restore_line(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        (backup_dir / "db.dump").write_bytes(_dump_bytes(n=999))  # still valid PGDMP, different bytes

        with _roots(tmp_path, "target"):
            with pytest.raises(CommandError):
                call_command("restore", str(backup_dir))

    def test_missing_file_aborts_before_any_write(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        (backup_dir / "files" / "documents" / "1" / "report.pdf").unlink()

        with _roots(tmp_path, "target") as tgt:
            with pytest.raises(CommandError) as exc:
                call_command("restore", str(backup_dir))
        assert "missing file" in str(exc.value).lower()
        assert "documents/1/report.pdf" in str(exc.value)
        assert not (tgt["documents"] / "1").exists()  # nothing written -- abort before copy


@pytest.mark.django_db
class TestOutput:
    def test_prints_stop_single_transaction_exit_on_error_start(self, tmp_path, capsys):
        backup_dir = _make_backup(tmp_path)
        with _roots(tmp_path, "target"):
            call_command("restore", str(backup_dir))
        out = capsys.readouterr().out
        assert "docker compose stop web worker watcher" in out
        assert "--single-transaction" in out
        assert "--exit-on-error" in out
        assert "docker compose start web worker watcher" in out

    def test_no_dump_prints_files_only_warning_no_pg_restore_no_hashing(self, tmp_path, capsys):
        backup_dir = _make_backup(tmp_path, with_dump=False)
        # A stray, corrupt db.dump physically present beside a present:false
        # manifest must not be touched or cause an abort.
        (backup_dir / "db.dump").write_bytes(b"not a real dump")

        with _roots(tmp_path, "target"):
            with patch("foundation.ops.restore.sha256_file", wraps=core_files.sha256_file) as mock_hash:
                call_command("restore", str(backup_dir))

        for c in mock_hash.call_args_list:
            assert "db.dump" not in str(c.args[0])

        out = capsys.readouterr().out
        assert "pg_restore" not in out
        assert "no database dump" in out.lower() or "has no database dump" in out.lower()


@pytest.mark.django_db
class TestFollowUps:
    def test_no_dump_no_reencode_instruction(self, tmp_path, capsys):
        backup_dir = _make_backup(tmp_path, with_dump=False)
        with _roots(tmp_path, "target"):
            call_command("restore", str(backup_dir))
        out = capsys.readouterr().out
        assert "reencode --role rag.embed" not in out
        assert "the source box had" not in out.lower()
        assert "manage.py ingest" in out

    def test_dump_present_zero_chunks_instructs_reencode(self, tmp_path, capsys):
        backup_dir = _make_backup(tmp_path)
        manifest = _read_manifest(backup_dir)
        manifest["counts"]["chunk_rows"] = 0
        manifest["counts"]["chunk_bytes"] = 0
        _write_manifest(backup_dir, manifest)

        with _roots(tmp_path, "target"):
            call_command("restore", str(backup_dir))
        out = capsys.readouterr().out
        assert "had no search index" in out
        assert "reencode --role rag.embed" in out

    def test_dump_present_nonzero_chunks_source_box_wording(self, tmp_path, capsys):
        backup_dir = _make_backup(tmp_path)
        manifest = _read_manifest(backup_dir)
        manifest["counts"]["chunk_rows"] = 41208
        manifest["counts"]["chunk_bytes"] = 612 * 1024 * 1024
        _write_manifest(backup_dir, manifest)

        with _roots(tmp_path, "target"):
            call_command("restore", str(backup_dir))
        out = capsys.readouterr().out
        assert "the source box had 41,208 chunks" in out.lower()
        assert "if your dump excluded" in out.lower()
        assert "reencode --role rag.embed" in out
        assert "your dump contain" not in out.lower()

    def test_active_documents_named_in_followup(self, tmp_path, capsys):
        backup_dir = _make_backup(tmp_path)
        manifest = _read_manifest(backup_dir)
        manifest["active"]["pending_documents"] = [7]
        manifest["active"]["processing_documents"] = [9]
        _write_manifest(backup_dir, manifest)

        with _roots(tmp_path, "target"):
            call_command("restore", str(backup_dir))
        out = capsys.readouterr().out
        assert "7" in out and "9" in out
        assert "Re-ingest" in out


@pytest.mark.django_db
class TestNeverTouchesInbox:
    def test_inbox_byte_identical_after_restore(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        with _roots(tmp_path, "target") as tgt:
            inbox_file = tgt["inbox"] / "untouched.pdf"
            inbox_file.write_bytes(b"do not touch me")
            before = inbox_file.read_bytes()

            call_command("restore", str(backup_dir))

            assert inbox_file.read_bytes() == before


@pytest.mark.django_db
class TestActiveWork:
    def test_running_job_refuses_naming_stop_command(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        InferenceJob.objects.create(kind="test.job", priority=100, state=RUNNING)
        with _roots(tmp_path, "target"):
            with pytest.raises(CommandError) as exc:
                call_command("restore", str(backup_dir))
        assert "docker compose stop web worker watcher" in str(exc.value)

    def test_force_overrides_and_restore_proceeds(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        InferenceJob.objects.create(kind="test.job", priority=100, state=RUNNING)
        with _roots(tmp_path, "target") as tgt:
            call_command("restore", str(backup_dir), "--force")
            assert (tgt["documents"] / "1" / "report.pdf").exists()


@pytest.mark.django_db
class TestDirectoriesVsFiles:
    def test_empty_dir_preserved_on_clean_restore(self, tmp_path):
        with _roots(tmp_path, "source") as src:
            (src["documents"] / "1").mkdir()
            (src["documents"] / "1" / "report.pdf").write_bytes(b"hi")
            empty_doc = src["documents"] / "2"
            empty_doc.mkdir()
            (empty_doc / "work").mkdir()  # filtered by backup -- dir itself must still survive

            backup_dir = tmp_path / "backup"
            call_command("backup", str(backup_dir))

        with _roots(tmp_path, "target") as tgt:
            call_command("restore", str(backup_dir))
            assert (tgt["documents"] / "2").is_dir()
            assert not (tgt["documents"] / "2" / "work").exists()

    def test_extra_regular_file_aborts_naming_it(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        extra = backup_dir / "files" / "documents" / "1" / "extra-not-in-manifest.txt"
        extra.write_bytes(b"surprise")

        with _roots(tmp_path, "target") as tgt:
            with pytest.raises(CommandError) as exc:
                call_command("restore", str(backup_dir))
            assert "extra-not-in-manifest.txt" in str(exc.value)
            assert not (tgt["documents"] / "1").exists()  # atomic -- nothing written


@pytest.mark.django_db
class TestPathPrefixValidation:
    """W3 review M5: the "must start with documents/ or generated/" shape
    check must be validated BEFORE any write happens, not discovered
    lazily mid-copy -- a manifest entry naming a top-level path outside
    both roots must leave the target completely empty on abort."""

    def test_manifest_entry_outside_documents_or_generated_aborts_before_write(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        data = b"zz-bytes"
        (backup_dir / "files" / "zz.txt").write_bytes(data)
        manifest = _read_manifest(backup_dir)
        manifest["files"].append(
            {"path": "zz.txt", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
        _write_manifest(backup_dir, manifest)

        with _roots(tmp_path, "target") as tgt:
            with pytest.raises(CommandError) as exc:
                call_command("restore", str(backup_dir))
            assert "zz.txt" in str(exc.value)
            assert not any(tgt["documents"].iterdir())
            assert not any(tgt["generated"].iterdir())


@pytest.mark.django_db
class TestLivePostgresWarningHeader:
    def test_printed_as_the_first_line(self, tmp_path, capsys):
        from foundation.ops.backup import LIVE_POSTGRES_WARNING

        backup_dir = _make_backup(tmp_path, with_dump=False)
        with _roots(tmp_path, "target"):
            call_command("restore", str(backup_dir))
        out = capsys.readouterr().out
        assert LIVE_POSTGRES_WARNING in out
        assert out.index(LIVE_POSTGRES_WARNING) < out.index("Restored")


@pytest.mark.django_db
class TestRenderRestoreSequencePaths:
    """W3 review M2: interpolate a real host-relative path into the
    printed `pg_restore`/recovery lines when `src` resolves inside
    `settings.BASE_DIR`; otherwise keep the docs' `<name>` placeholder and
    add a sentence naming the already-verified dump's in-container path."""

    def test_src_under_base_dir_interpolates_host_relative_path(self, tmp_path, capsys, settings):
        settings.BASE_DIR = tmp_path
        backup_dir = _make_backup(tmp_path)  # tmp_path / "backup" -- under BASE_DIR
        with _roots(tmp_path, "target"):
            call_command("restore", str(backup_dir))
        out = capsys.readouterr().out
        assert "./backup/db.dump" in out
        assert "<name>" not in out
        assert "path inside the container" not in out

    def test_src_outside_base_dir_keeps_name_and_adds_note(self, tmp_path, capsys, settings):
        other_base = tmp_path / "elsewhere"
        other_base.mkdir()
        settings.BASE_DIR = other_base
        backup_dir = _make_backup(tmp_path)
        with _roots(tmp_path, "target"):
            call_command("restore", str(backup_dir))
        out = capsys.readouterr().out
        assert "./data/backups/<name>/db.dump" in out
        assert f"{backup_dir}/db.dump" in out
        assert "path inside the container" in out


@pytest.mark.django_db
class TestChunkBytesNoneGuard:
    """NIT: `human_bytes(None)` must not crash when a manifest carries a
    non-zero `chunk_rows` but a null `chunk_bytes` (a hand-edited or
    otherwise malformed manifest)."""

    def test_null_chunk_bytes_renders_unknown_size_not_a_crash(self, tmp_path, capsys):
        backup_dir = _make_backup(tmp_path)
        manifest = _read_manifest(backup_dir)
        manifest["counts"]["chunk_rows"] = 5
        manifest["counts"]["chunk_bytes"] = None
        _write_manifest(backup_dir, manifest)

        with _roots(tmp_path, "target"):
            call_command("restore", str(backup_dir))  # must not raise
        out = capsys.readouterr().out
        assert "unknown size" in out


@pytest.mark.django_db
class TestManifestEntryMissingRequiredField:
    """NIT: a manifest file entry missing `size`/`sha256` must raise
    `CommandError`, not an uncaught `KeyError`."""

    def test_missing_sha256_raises_command_error_not_key_error(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        manifest = _read_manifest(backup_dir)
        del manifest["files"][0]["sha256"]
        _write_manifest(backup_dir, manifest)

        with _roots(tmp_path, "target") as tgt:
            with pytest.raises(CommandError) as exc:
                call_command("restore", str(backup_dir))
            assert "missing required field" in str(exc.value)
            assert not any(tgt["documents"].iterdir())


@pytest.mark.django_db
class TestRestorePermissions:
    """H13 review round 1, finding 9: restore is backup's mirror writer,
    and gets the same 0700/0600 discipline through the same
    `foundation.files` helpers."""

    def test_a_restored_documents_own_directory_is_owner_only(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        with _roots(tmp_path, "target") as tgt:
            call_command("restore", str(backup_dir))
            assert stat.S_IMODE((tgt["documents"] / "1").stat().st_mode) == 0o700

    def test_a_restored_file_is_owner_read_write_only(self, tmp_path):
        backup_dir = _make_backup(tmp_path)
        with _roots(tmp_path, "target") as tgt:
            call_command("restore", str(backup_dir))
            restored = tgt["documents"] / "1" / "report.pdf"
            assert stat.S_IMODE(restored.stat().st_mode) == 0o600

    def test_documents_dir_itself_a_pre_existing_shared_root_is_not_forcibly_tightened(
        self, tmp_path
    ):
        """`_roots` pre-creates `DOCUMENTS_DIR`/`GENERATED_DIR` at the test
        runner's own default mode before restore ever runs -- restore must
        not force-chmod either, the same `only_if_created=True` rule as
        `tools.rag.views.document_upload`'s inbox root."""
        backup_dir = _make_backup(tmp_path)
        with _roots(tmp_path, "target") as tgt:
            before = stat.S_IMODE(tgt["documents"].stat().st_mode)
            call_command("restore", str(backup_dir))
            assert stat.S_IMODE(tgt["documents"].stat().st_mode) == before
