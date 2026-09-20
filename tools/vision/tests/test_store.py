"""Unit tests for tools/vision/store.py (spec §5)."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from tools.vision import store
from tools.vision.tests._helpers import PNG, png_bytes


class TestJobDir:
    def test_lives_under_the_generated_dir(self, tmp_path):
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            assert store.job_dir(job_id) == tmp_path / str(job_id)

    def test_does_not_create_the_directory(self, tmp_path):
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            store.job_dir(job_id)
            assert not (tmp_path / str(job_id)).exists()


class TestStoreOutput:
    def test_writes_the_bytes_and_returns_the_path(self, tmp_path):
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            path = store.store_output(job_id, 0, "job_00001_.png", b"PNG-BYTES")

        assert (tmp_path / str(job_id) / "0-job_00001_.png").read_bytes() == b"PNG-BYTES"
        assert path.endswith("0-job_00001_.png")

    def test_engine_supplied_paths_cannot_escape_the_job_directory(self, tmp_path):
        """The filename comes from the ENGINE, so it is treated as untrusted:
        only its basename is ever used."""
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            path = store.store_output(job_id, 0, "../../etc/passwd", b"x")

        assert str(tmp_path / str(job_id)) in path
        assert not (tmp_path / "etc").exists()


class TestStoreInput:
    def test_writes_an_uploaded_file_under_the_job_inputs_dir(self, tmp_path):
        job_id = uuid.uuid4()
        upload = SimpleUploadedFile("seed.png", b"IMG", content_type="image/png")
        with override_settings(GENERATED_DIR=tmp_path):
            path = store.store_input(job_id, "init_image", upload)

        assert (tmp_path / str(job_id) / "inputs" / "init_image-seed.png").read_bytes() == b"IMG"
        assert path.endswith("init_image-seed.png")


class TestRemoveJobFiles:
    def test_deletes_the_whole_job_tree(self, tmp_path):
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            store.store_output(job_id, 0, "a.png", b"x")
            store.remove_job_files(job_id)

        assert not (tmp_path / str(job_id)).exists()

    def test_missing_directory_is_not_an_error(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            store.remove_job_files(uuid.uuid4())


class TestPngDimensions:
    def test_reads_width_and_height_from_the_header(self):
        assert store.png_dimensions(png_bytes(832, 1216)) == (832, 1216)

    def test_non_png_content_is_unknown_not_an_error(self):
        assert store.png_dimensions(b"not a png at all") == (None, None)

    def test_truncated_png_is_unknown(self):
        assert store.png_dimensions(b"\x89PNG\r\n\x1a\n") == (None, None)


class TestPngHelper:
    def test_the_shared_constant_is_the_builder_at_512(self):
        """One piece of PNG-header knowledge in the suite, not two: the
        fixed constant every other module uses IS this builder's output."""
        assert PNG == png_bytes(512, 512)


class TestStoredFile:
    """A file already in the managed store, presented as the small slice
    of Django's upload API `store_input` uses -- so a caller that has one
    reaches `submit_job(files=...)` with no second code path."""

    def test_it_reads_back_in_chunks_and_keeps_its_name(self, tmp_path):
        source = tmp_path / "0-job_00001_.png"
        source.write_bytes(b"abcdef")
        stored = store.StoredFile(source, name="beach.png", content_type="image/png")
        assert stored.name == "beach.png"
        assert stored.content_type == "image/png"
        assert b"".join(stored.chunks(chunk_size=4)) == b"abcdef"

    def test_the_name_defaults_to_the_files_own_basename(self, tmp_path):
        source = tmp_path / "0-job_00001_.png"
        source.write_bytes(b"x")
        assert store.StoredFile(source).name == "0-job_00001_.png"

    def test_store_input_accepts_one_exactly_like_an_upload(self, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path / "generated"
        source = tmp_path / "beach.png"
        source.write_bytes(b"abcdef")
        path = store.store_input("job-1", "init_image", store.StoredFile(source))
        assert Path(path).read_bytes() == b"abcdef"
        assert Path(path).name == "init_image-beach.png"


class TestStageInput:
    """An upload with no job yet: the page has the bytes, and the
    GenerationJob that will own them is created later, on a worker."""

    def test_it_writes_under_the_staging_directory(self, tmp_path):
        upload = SimpleUploadedFile("beach.png", b"bytes", content_type="image/png")
        with override_settings(GENERATED_DIR=tmp_path):
            path = Path(store.stage_input(upload))
        assert path.parent.parent == tmp_path / store.STAGING_DIRNAME
        assert path.read_bytes() == b"bytes"

    def test_each_staged_upload_gets_its_own_directory(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            first = Path(store.stage_input(SimpleUploadedFile("a.png", b"1")))
            second = Path(store.stage_input(SimpleUploadedFile("a.png", b"2")))
        assert first.parent != second.parent
        assert first.read_bytes() == b"1"
        assert second.read_bytes() == b"2"

    def test_only_the_basename_is_kept(self, tmp_path):
        upload = SimpleUploadedFile("beach.png", b"bytes")
        upload.name = r"C:\Users\op\beach.png"
        with override_settings(GENERATED_DIR=tmp_path):
            path = Path(store.stage_input(upload))
        assert path.name == "beach.png"

    def test_a_staged_upload_is_removed_with_its_directory(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            path = store.stage_input(SimpleUploadedFile("a.png", b"1"))
            store.remove_staged_input(path)
        assert not Path(path).exists()
        assert not Path(path).parent.exists()

    def test_removing_a_path_outside_the_staging_directory_does_nothing(self, tmp_path):
        """A guard, not politeness: this function takes a path off a
        database row, and a job's own input must never be deletable
        through it."""
        job_file = tmp_path / "some-job" / "inputs" / "init_image-beach.png"
        job_file.parent.mkdir(parents=True)
        job_file.write_bytes(b"1")
        with override_settings(GENERATED_DIR=tmp_path):
            store.remove_staged_input(str(job_file))
        assert job_file.exists()


@pytest.mark.parametrize(("filename", "expected"), [
    ("a.png", "image/png"), ("a.PNG", "image/png"),
    ("a.jpg", "image/jpeg"), ("a.jpeg", "image/jpeg"),
    ("a.webp", "image/webp"), ("a.gif", "image/gif"),
    ("a.svg", ""), ("a", ""), ("a.exe", ""),
])
def test_media_type_comes_from_the_extension_not_the_client(filename, expected):
    """C-01. `SimpleUploadedFile` lets its caller declare any
    `content_type` it likes -- exactly what a browser does, and exactly
    what this platform used to record and re-serve. The stored media type
    must be derived from the name whose parsing we control."""
    uploaded = SimpleUploadedFile(filename, b"x", content_type="image/svg+xml")
    assert store.media_type_for_upload(uploaded) == expected
