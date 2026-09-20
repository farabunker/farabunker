"""Unit tests for tools/vision/models.py (spec §4.7)."""
from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from foundation.format import format_timecode
from identity.contracts.principals import OPEN_PRINCIPAL
from models.contracts import operations
from models.contracts.operations import Operation, Param
from tools.vision import services
from tools.vision.models import GeneratedOutput, GenerationJob, JobInput
from tools.vision.tests._helpers import PNG


TXT2IMG_PARAMS = {
    "prompt": "a lighthouse", "negative_prompt": "blurry", "width": 1024,
    "height": 1024, "steps": 25, "cfg_scale": 7.0, "seed": 42,
    "sampler": "euler", "scheduler": "normal", "batch_size": 1,
}


def _job(**overrides) -> GenerationJob:
    fields = {
        "operation": "txt2img",
        "params": {"prompt": "a lighthouse", "seed": 7},
        "seed": 7,
        "engine": "comfyui",
        "model_id": "sdxl.safetensors",
        "model_fingerprint": "comfyui:sdxl.safetensors:None",
        "model_config": {},
    }
    fields.update(overrides)
    return GenerationJob.objects.create(**fields)


def _ago(**delta) -> "datetime":
    """A timestamp `delta` before now -- `timedelta`'s own keywords."""
    return timezone.now() - timedelta(**delta)


def _timed_job(*, created=None, started=None, finished=None, **overrides) -> GenerationJob:
    """A job whose three lifecycle stamps are exactly what this test says.

    `created_at` is `auto_now_add`, so it CANNOT be passed to `create()` --
    Django replaces it on insert. The file's own answer (see
    `test_a_long_queued_job_reads_as_stale`) is to write the stamps with a
    queryset `.update()` afterwards, which bypasses `auto_now_add` and every
    `save()` hook, then re-read the row.
    """
    job = _job(**overrides)
    stamps = {"created_at": created, "started_at": started, "finished_at": finished}
    GenerationJob.objects.filter(pk=job.pk).update(
        **{key: value for key, value in stamps.items() if value is not None}
    )
    job.refresh_from_db()
    return job


@pytest.mark.django_db
class TestGenerationJob:
    def test_primary_key_is_a_uuid_and_defaults_to_queued(self):
        job = _job()
        assert isinstance(job.id, uuid.UUID)
        assert job.status == GenerationJob.Status.QUEUED
        assert job.error == ""
        assert job.engine_ref == ""
        assert job.engine_payload == {}

    def test_terminal_states(self):
        assert _job(status=GenerationJob.Status.DONE).is_terminal is True
        assert _job(status=GenerationJob.Status.FAILED).is_terminal is True
        assert _job(status=GenerationJob.Status.RUNNING).is_terminal is False
        assert _job(status=GenerationJob.Status.QUEUED).is_terminal is False

    @override_settings(VISION_STALE_AFTER=timedelta(minutes=10))
    def test_a_long_queued_job_reads_as_stale(self):
        job = _job()
        GenerationJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - timedelta(minutes=11)
        )
        job.refresh_from_db()
        assert job.is_stale is True

    @override_settings(VISION_STALE_AFTER=timedelta(minutes=10))
    def test_a_finished_job_is_never_stale(self):
        job = _job(status=GenerationJob.Status.DONE)
        GenerationJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        job.refresh_from_db()
        assert job.is_stale is False

    def test_newest_first_ordering(self):
        first = _job()
        second = _job()
        assert list(GenerationJob.objects.all()) == [second, first]


@pytest.mark.django_db
class TestJobChildren:
    def test_inputs_and_outputs_cascade_with_the_job(self):
        job = _job()
        JobInput.objects.create(job=job, param_key="init_image", path="/tmp/a.png", media_type="image/png")
        GeneratedOutput.objects.create(
            job=job, index=0, path="/tmp/out.png", media_type="image/png", width=1024, height=1024
        )

        assert job.inputs.count() == 1
        assert job.outputs.count() == 1

        job.delete()
        assert GeneratedOutput.objects.count() == 0
        assert JobInput.objects.count() == 0

    def test_outputs_are_ordered_by_index(self):
        job = _job()
        for index in (2, 0, 1):
            GeneratedOutput.objects.create(
                job=job, index=index, path=f"/tmp/{index}.png", media_type="image/png"
            )
        assert [output.index for output in job.outputs.all()] == [0, 1, 2]


@pytest.mark.django_db
class TestJobFacts:
    """The card and the gallery read one schema-driven line, so an
    operation the templates have never seen describes itself."""

    def _job(self, operation="txt2img", params=None, model_id="sdxl.safetensors"):
        return GenerationJob.objects.create(
            operation=operation,
            params=params if params is not None else TXT2IMG_PARAMS,
            seed=42, engine="comfyui", model_id=model_id, endpoint="http://x:8188",
            model_fingerprint="comfyui:sdxl.safetensors:None",
        )

    def test_txt2img_facts_are_labelled_from_the_schema(self):
        assert self._job().facts == [
            ("Seed", "42"),
            ("Width", "1024"),
            ("Height", "1024"),
            ("Steps", "25"),
            ("CFG scale", "7.0"),
            ("Sampler", "euler"),
            ("Scheduler", "normal"),
            ("Batch size", "1"),
            ("Model", "sdxl.safetensors"),
        ]

    def test_an_unseen_operation_describes_itself_with_no_template_change(self):
        operation = Operation(
            key="img2img", label="Image to image", capability="image-generation",
            output_media="image/png",
            params=(
                Param("prompt", "text", "Prompt", default="", required=True),
                Param("init_image", "file", "Init image", accept="image/*", required=True),
                Param("denoise", "float", "Denoise", default=0.6, min=0, max=1),
                Param("seed", "seed", "Seed", default=None),
            ),
        )
        job = self._job(
            operation="img2img",
            params={"prompt": "a lighthouse", "init_image": "beach.png", "denoise": 0.6, "seed": 42},
        )
        with patch.dict(operations._OPERATIONS, {"img2img": operation}):
            assert job.facts == [
                ("Seed", "42"),
                ("Init image", "beach.png"),
                ("Denoise", "0.6"),
                ("Model", "sdxl.safetensors"),
            ]

    def test_a_param_the_job_never_carried_is_skipped_not_rendered_blank(self):
        job = self._job(params={"prompt": "a lighthouse", "seed": 42, "steps": 25})

        assert job.facts == [("Seed", "42"), ("Steps", "25"), ("Model", "sdxl.safetensors")]

    def test_a_job_whose_operation_is_no_longer_registered_shows_its_raw_keys(self):
        """The feature was turned off, or the operation was renamed. Showing
        what was actually recorded beats showing nothing."""
        job = self._job(operation="retired", params={"steps": 25, "prompt": "a lighthouse"})

        assert job.facts == [
            ("Seed", "42"),
            ("prompt", "a lighthouse"),
            ("steps", "25"),
            ("Model", "sdxl.safetensors"),
        ]

    def test_a_list_valued_param_reads_as_a_list_not_a_repr(self):
        """An asset param holds a list. `str(["a", "b"])` on a card is a
        Python repr leaking onto a page."""
        job = _job(params={**TXT2IMG_PARAMS, "loras": ["style.safetensors", "detail.safetensors"]})
        assert ("LoRAs", "style.safetensors, detail.safetensors") in job.facts

    def test_an_empty_list_is_skipped_like_any_other_absent_value(self):
        job = _job(params={**TXT2IMG_PARAMS, "loras": []})
        assert all(label != "LoRAs" for label, _value in job.facts)

    def test_a_job_with_no_seed_shows_no_seed_fact(self):
        """"Seed None" on a card is a value the operator cannot reuse and
        a mode that never had one."""
        job = _job(operation="upscale", params={"upscale_model": "4x.pth"}, seed=None)
        assert all(label != "Seed" for label, _value in job.facts)


@pytest.mark.django_db
class TestJobHeadline:
    """The card and gallery caption's ONE heading line -- fix-wave item 6.
    An operation with no `prompt` param must not headline blank."""

    def _job(self, operation="txt2img", params=None):
        return GenerationJob.objects.create(
            operation=operation,
            params=params if params is not None else {"prompt": "a lighthouse", "seed": 42},
            seed=42, engine="comfyui", model_id="sdxl.safetensors",
            model_fingerprint="comfyui:sdxl.safetensors:None",
        )

    def test_a_job_with_a_prompt_headlines_it(self):
        assert self._job().headline == "a lighthouse"

    def test_a_prompt_less_registered_operation_headlines_its_schema_label(self):
        operation = Operation(
            key="upscale", label="Upscale", capability="image-generation",
            output_media="image/png",
            params=(Param("scale", "float", "Scale", default=2.0, min=1, max=4),),
        )
        job = self._job(operation="upscale", params={"scale": 2.0})

        with patch.dict(operations._OPERATIONS, {"upscale": operation}):
            assert job.headline == "Upscale"

    def test_a_prompt_less_unregistered_operation_falls_back_to_its_raw_key(self):
        """The feature was turned off, or the operation was renamed -- same
        honest fallback `facts` uses for an unregistered operation."""
        job = self._job(operation="retired", params={"scale": 2.0})

        assert job.headline == "retired"

    def test_an_edit_job_headlines_its_instruction(self):
        """Owner ruling 2026-08-24(b): `edit`'s schema carries `instruction`,
        not `prompt` -- before this, a job like this fell all the way
        through to the operation's own schema label ("Edit an image"),
        which is what the owner saw on the preview stack and flagged."""
        job = self._job(
            operation="edit",
            params={"instruction": "put a red hat on the woman", "guidance": 4.0},
        )

        assert job.headline == "put a red hat on the woman"


@pytest.mark.django_db
class TestFailureKind:
    """A machine-readable discriminator beside the prose: an agent has to
    be able to tell 'fix your params' from 'try again later'."""

    def test_a_new_job_carries_no_failure_kind(self):
        job = _job()
        assert job.failure_kind == ""

    def test_the_vocabulary_covers_every_failure_a_caller_can_observe(self):
        assert set(GenerationJob.FailureKind.values) == {
            "params_invalid", "role_unbound", "connection_unavailable",
            "engine_unreachable", "engine_rejected", "engine_failed", "lost",
        }


@pytest.mark.django_db
class TestQueueCorrelation:
    def test_a_job_submitted_outside_the_queue_has_no_queue_job_id(self):
        assert _job().queue_job_id is None

    def test_a_queued_generation_is_findable_by_its_queue_job_id(self):
        job = _job(queue_job_id=77)
        assert GenerationJob.objects.get(queue_job_id=77) == job


@pytest.mark.django_db
class TestStagedInput:
    """An upload the page recorded before the job that will consume it
    exists -- the JSON-safe way a browser file reaches a queue payload."""

    def test_a_job_input_can_exist_before_its_job(self, tmp_path):
        staged = JobInput.objects.create(
            job=None, param_key="init_image",
            path=str(tmp_path / "beach.png"), media_type="image/png",
        )
        assert staged.job_id is None
        assert staged.created_at is not None

    def test_it_is_reachable_by_the_ordinary_input_reference(self, tmp_path):
        source = tmp_path / "beach.png"
        source.write_bytes(PNG)
        staged = JobInput.objects.create(
            job=None, param_key="init_image", path=str(source), media_type="image/png",
        )
        assert services.stored_input(f"input:{staged.id}", OPEN_PRINCIPAL).path == source


@pytest.mark.django_db
class TestGenerationJobDurations:
    def test_a_queued_job_is_still_waiting(self):
        """The clock an operator is watching while nothing has started."""
        job = _timed_job(created=_ago(seconds=42), status=GenerationJob.Status.QUEUED)
        durations = job.durations
        assert 42 <= durations["queued"] < 45
        assert durations["processing"] is None
        assert 42 <= durations["total"] < 45

    def test_a_running_job_reports_both_halves_live(self):
        job = _timed_job(
            created=_ago(seconds=100), started=_ago(seconds=60),
            status=GenerationJob.Status.RUNNING,
        )
        durations = job.durations
        assert 39 <= durations["queued"] <= 41       # created -> started, fixed
        assert 60 <= durations["processing"] < 63    # started -> now, still moving
        assert 100 <= durations["total"] < 103

    def test_a_finished_job_stops_moving(self):
        job = _timed_job(
            created=_ago(seconds=300), started=_ago(seconds=280),
            finished=_ago(seconds=10), status=GenerationJob.Status.DONE,
        )
        assert job.durations["queued"] == pytest.approx(20.0, abs=1)
        assert job.durations["processing"] == pytest.approx(270.0, abs=1)
        assert job.durations["total"] == pytest.approx(290.0, abs=1)

    def test_a_job_that_never_ran_reports_no_processing_time(self):
        """A submission the engine refused waited and then failed. Its
        processing time is UNKNOWN, not zero -- `_fail` writes
        `finished_at` and never `started_at`."""
        job = _timed_job(
            created=_ago(seconds=30), finished=_ago(seconds=25),
            status=GenerationJob.Status.FAILED,
        )
        durations = job.durations
        assert durations["processing"] is None
        assert durations["queued"] == pytest.approx(5.0, abs=1)
        assert durations["total"] == pytest.approx(5.0, abs=1)

    def test_the_rendered_form_uses_the_codebases_one_timecode(self):
        job = _timed_job(
            created=_ago(seconds=300), started=_ago(seconds=280),
            finished=_ago(seconds=10), status=GenerationJob.Status.DONE,
        )
        assert job.durations_display["processing"] == format_timecode(270)
        assert job.durations_display["processing"] == "4:30"

    def test_an_unknown_duration_renders_as_nothing_at_all(self):
        """`format_timecode(None)` is `""` by contract -- a card must show
        no time rather than a made-up `0:00`."""
        job = _timed_job(
            created=_ago(seconds=30), finished=_ago(seconds=25),
            status=GenerationJob.Status.FAILED,
        )
        assert job.durations_display["processing"] == ""

    def test_submitted_is_none_with_no_queue_link(self):
        """A job run directly (a management command, a test, a future
        tool calling `submit_job` itself) has no `InferenceJob` to compare
        its own `created_at` against -- `submitted` is unknown, not zero,
        and `total` falls back to `created_at` exactly as before this
        fix round."""
        job = _timed_job(created=_ago(seconds=42), status=GenerationJob.Status.QUEUED)
        durations = job.durations
        assert durations["submitted"] is None
        assert durations["total"] == pytest.approx(durations["queued"], abs=1)

    def test_submitted_is_none_when_the_queue_job_has_aged_out(self):
        """`queue_job_id` naming a row the queue's own retention limit
        already pruned degrades to unknown -- `models.contracts.queue.
        get_job` answers `None` for an id it does not recognise, never an
        error."""
        job = _timed_job(
            created=_ago(seconds=30), status=GenerationJob.Status.QUEUED,
            queue_job_id=999999,
        )
        assert job.durations["submitted"] is None

    def test_submitted_is_the_platform_queue_wait_when_the_link_resolves(self):
        """`queue_job_id` names a real `InferenceJob` -- `submitted` is the
        PLATFORM's own wait (enqueue to claim: `InferenceJob.created_at` to
        this row's own `created_at`), and `total` now starts there too --
        the full, honest, end-to-end figure the owner asked for."""
        from models.queue.models import InferenceJob

        queue_job = InferenceJob.objects.create(
            kind="vision.generate", state="running", priority=200, payload={},
        )
        InferenceJob.objects.filter(pk=queue_job.pk).update(created_at=_ago(seconds=310))
        job = _timed_job(
            created=_ago(seconds=300), started=_ago(seconds=280),
            finished=_ago(seconds=10), status=GenerationJob.Status.DONE,
            queue_job_id=queue_job.pk,
        )
        durations = job.durations
        assert durations["submitted"] == pytest.approx(10.0, abs=1)
        assert durations["queued"] == pytest.approx(20.0, abs=1)     # unchanged: engine-only
        assert durations["total"] == pytest.approx(300.0, abs=1)     # from InferenceJob.created_at
