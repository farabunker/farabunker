"""Unit tests for the S14/B-8 permission discipline in
`tools/vision/store.py` -- `_write_upload` (exercised through
`store_input`, its one public caller with a stable, simple signature) and,
per the H13 review round-1 vision-steward grant, `store_output` -- a NEW
module rather than an addition to test_store.py, so these swaps (the only
changes permitted in this peer-stewarded module) have their own
regression coverage without touching a file another session also works
in.
"""
from __future__ import annotations

import stat
import uuid
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from tools.vision import store


class TestWriteUploadPermissions:
    def test_the_inputs_directory_is_owner_only(self, tmp_path):
        job_id = uuid.uuid4()
        upload = SimpleUploadedFile("seed.png", b"IMG", content_type="image/png")
        with override_settings(GENERATED_DIR=tmp_path):
            store.store_input(job_id, "init_image", upload)

        inputs_dir = tmp_path / str(job_id) / "inputs"
        assert stat.S_IMODE(inputs_dir.stat().st_mode) == 0o700

    def test_the_written_file_is_owner_read_write_only(self, tmp_path):
        job_id = uuid.uuid4()
        upload = SimpleUploadedFile("seed.png", b"IMG", content_type="image/png")
        with override_settings(GENERATED_DIR=tmp_path):
            path = store.store_input(job_id, "init_image", upload)

        assert stat.S_IMODE(Path(path).stat().st_mode) == 0o600


class TestStoreOutputPermissions:
    """H13 review round 1, finding 3 (vision-steward grant): `store_output`
    now goes through the same two helpers as `_write_upload`."""

    def test_the_job_directory_is_owner_only(self, tmp_path):
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            store.store_output(job_id, 0, "0-job_00001_.png", b"PNG-BYTES")

        assert stat.S_IMODE((tmp_path / str(job_id)).stat().st_mode) == 0o700

    def test_the_written_file_is_owner_read_write_only(self, tmp_path):
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            path = store.store_output(job_id, 0, "0-job_00001_.png", b"PNG-BYTES")

        assert stat.S_IMODE(Path(path).stat().st_mode) == 0o600


@pytest.mark.django_db
class TestStoreOutputServesForReal:
    """H13 review round 1, finding 3 (vision-steward grant): a WIRE
    assertion, not just mode bits -- a freshly `store_output`-written file
    must still serve through the real `/vision/outputs/<id>/file/` route
    (`tools.vision.views.output_file` -> `_serve_stored_file`), proving
    readback rather than only checking `stat()`."""

    @pytest.fixture
    def client(self):
        return Client()

    def test_a_freshly_stored_output_is_readable_through_the_real_route(
        self, client, settings, tmp_path
    ):
        from tools.vision.models import GeneratedOutput, GenerationJob

        settings.GENERATED_DIR = tmp_path
        job = GenerationJob.objects.create(
            operation="txt2img",
            params={
                "prompt": "a lighthouse", "negative_prompt": "", "width": 512, "height": 512,
                "steps": 20, "cfg_scale": 7.0, "seed": 42, "sampler": "euler",
                "scheduler": "normal", "batch_size": 1,
            },
            seed=42, engine="stubengine", model_id="stub.safetensors",
            endpoint="http://stub:9999",
            model_fingerprint="stubengine:stub.safetensors:None",
            status=GenerationJob.Status.DONE,
        )
        content = b"\x89PNG\r\n\x1a\nfake-but-real-bytes"
        path = store.store_output(job.id, 0, "0-job_00001_.png", content)
        output = GeneratedOutput.objects.create(
            job=job, index=0, path=path, media_type="image/png",
        )

        response = client.get(reverse("vision-output-file", args=[output.id]))

        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert b"".join(response.streaming_content) == content
