"""Unit tests for the generate / poll / delete / file endpoints (spec §4.7, §6)."""
from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import ANY, call, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from models.registry.models import ModelConnection, RoleBinding
from models.contracts import operations
from models.contracts.engines import ENGINES
from models.contracts.engines.base import JobStatus
from models.contracts.operations import IMG2IMG, TXT2IMG, UPSCALE, Operation, Param
from models.contracts.queue import QueueUnavailable
from models.contracts.roles import VISION_GENERATE_ROLE
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import OPEN_PRINCIPAL
from tools.vision import jobs, services
from tools.vision.models import GenerationJob, JobInput
from tools.vision.tests._helpers import (
    PNG,
    RAW,
    StubEngine,
    StubGenerator,
    clear_bindings,
    grant,
    make_entitlement,
    make_job_ctx,
    make_user,
    posture,
    sign_in,
    stored_output,
)

FORM = {
    "prompt": "a lighthouse", "negative_prompt": "", "width": "512", "height": "512",
    "steps": "20", "cfg_scale": "7", "seed": "42", "sampler": "euler",
    "scheduler": "normal", "batch_size": "1",
}

# The same shape as FORM, but for `img2img`: no width/height (the init
# image decides the output size) and `denoise` in their place. Carries its
# own `operation` key -- img2img is not the first registered operation, so
# every test that posts this must say which schema it means.
IMG2IMG_FORM = {
    "operation": "img2img",
    "prompt": "a lighthouse", "negative_prompt": "", "denoise": "0.5",
    "steps": "20", "cfg_scale": "7", "seed": "42", "sampler": "euler",
    "scheduler": "normal", "batch_size": "1",
}

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


def _bind():
    connection = ModelConnection.objects.create(
        name="stub image model", engine="stubengine", endpoint="http://stub:9999",
        model_id="stub.safetensors", capabilities=["image-generation"],
    )
    RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)


def _engine(generator=None, healthy=True, choices=("euler",)):
    """`sampler` is restricted to `choices` (matching FORM's `sampler`); every
    other choice param -- `scheduler` here -- reports no live options, which
    degrades its form field to free text (see `forms._field_for`), so
    FORM's `scheduler="normal"` validates without needing a matching live
    option for a param this stub does not model."""
    engine = StubEngine(generator or StubGenerator(), healthy=healthy)
    engine.list_choices = lambda endpoint, key: choices if key == "sampler" else ()
    return patch.dict(ENGINES, {"stubengine": engine})


def _submitted_job(tmp_path=None, generator=None) -> GenerationJob:
    """A real `GenerationJob`, created directly through the service layer
    rather than through `POST /vision/generate/` -- that endpoint now only
    ENQUEUES (no `GenerationJob` exists until a worker runs the queued
    job), so a test that needs an existing job to poll/serve/delete builds
    one the same way `tools.vision.jobs.run_generate` does, matching the
    pattern `test_views_queue.py` already uses (`stored_output`)."""
    _bind()
    with _engine(generator):
        return services.submit_job("txt2img", dict(FORM), actor=OPEN_PRINCIPAL)


def _ago(**delta) -> "datetime":
    """A timestamp `delta` before now -- `timedelta`'s own keywords."""
    return timezone.now() - timedelta(**delta)


def _timed_job(*, created=None, started=None, finished=None, **overrides) -> GenerationJob:
    """A job whose three lifecycle stamps are exactly what this test says.

    `created_at` is `auto_now_add`, so it cannot be passed to `create()` --
    written afterwards with a queryset `.update()`, the same idiom
    `test_models.py`'s own `_timed_job` uses. No `engine_ref` is set, so
    `job_status`'s `refresh_job` call returns immediately for every one of
    these (terminal jobs always do; a QUEUED/RUNNING job with no
    `engine_ref` has nothing to poll) -- no engine mock is needed here.
    """
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
    job = GenerationJob.objects.create(**fields)
    stamps = {"created_at": created, "started_at": started, "finished_at": finished}
    GenerationJob.objects.filter(pk=job.pk).update(
        **{key: value for key, value in stamps.items() if value is not None}
    )
    job.refresh_from_db()
    return job


@pytest.mark.django_db
class TestGenerate:
    @patch("tools.vision.views.enqueue", return_value=12)
    def test_valid_post_queues_a_job_and_redirects_without_js(self, mock_enqueue, client):
        """The submission is QUEUED now, not submitted directly -- see
        TestGenerateEnqueues for the payload's own shape."""
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM)

        assert response.status_code == 302
        assert response["Location"] == (
            f"{reverse('vision-create')}?operation=txt2img&queued=12"
        )
        mock_enqueue.assert_called_once()

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_xhr_post_returns_the_job_card_fragment(self, mock_enqueue, client):
        """The XHR response is the QUEUED placeholder card now, not the
        real job card -- see TestGenerateEnqueues for the poll-URL
        assertion."""
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        body = response.content.decode()
        assert response.status_code == 202
        assert reverse("vision-queue-status", args=[12]) in body
        assert "data-job-poll" in body

    @patch("tools.vision.views.enqueue")
    def test_invalid_params_are_a_400_that_re_renders_the_form(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), dict(FORM, prompt=""))

        body = response.content.decode()
        assert response.status_code == 400
        assert GenerationJob.objects.count() == 0
        assert "This field is required" in body
        # `_create_page_response` copies the real form's errors onto the
        # union form's own field -- a duplicate copy (e.g. also landing on
        # `None`/non-field errors) would show the same message twice.
        assert body.count("This field is required") == 1
        assert "<html" in body  # non-XHR: the whole page, form and all
        mock_enqueue.assert_not_called()

    @patch("tools.vision.views.enqueue")
    def test_non_xhr_re_render_does_not_lose_an_uploaded_file(self, mock_enqueue, client):
        """Review fix 1: `_create_page_response` used to bind the union
        form to `request.POST` WITHOUT `request.FILES`, so every non-XHR
        re-render manufactured "This field is required." under a file
        field the operator genuinely uploaded -- `init_image` here, on an
        otherwise-invalid img2img submission (blank `prompt`)."""
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with _engine():
            response = client.post(
                reverse("vision-generate"),
                {**IMG2IMG_FORM, "prompt": "", "init_image": upload},
            )

        body = response.content.decode()
        assert response.status_code == 400
        assert "<html" in body  # non-XHR: the whole page, form and all
        page_form = response.context["form"]
        assert "init_image" not in page_form.errors
        assert "prompt" in page_form.errors  # the REAL refusal still shows
        # All 20 union fields still rendered, whichever mode is picked.
        assert body.count('class="field field--') == 20
        mock_enqueue.assert_not_called()

    @patch("tools.vision.views.enqueue")
    def test_xhr_invalid_params_get_the_small_errors_fragment(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), dict(FORM, prompt=""), **XHR)

        body = response.content.decode()
        assert response.status_code == 400
        assert GenerationJob.objects.count() == 0
        assert "This field is required" in body
        assert "<html" not in body  # XHR: the errors-only fragment, not the page
        mock_enqueue.assert_not_called()

    def test_card_has_no_confirm_dialog_and_carries_the_delete_form(self, client):
        """Moved off `generate()`'s own response -- that is the queued
        placeholder now, with no delete control of its own yet -- onto the
        REAL job card, fetched from its own status endpoint the way the
        page's poll would."""
        job = _submitted_job()

        response = client.get(reverse("vision-job-status", args=[job.id]))

        body = response.content.decode()
        assert "confirm(" not in body
        assert reverse("vision-job-delete", args=[job.id]) in body

    def test_unbound_role_is_a_503_with_the_honest_message(self, client):
        response = client.post(reverse("vision-generate"), FORM)

        assert response.status_code == 503
        assert "No model assigned for Image generation" in response.content.decode()

    def test_non_xhr_unbound_503_renders_the_whole_page_not_a_bare_fragment(self, client):
        """Fix-wave item 5: a plain POST (no JS) while unbound used to get
        the SAME bare `_unavailable.html` fragment the XHR path gets --
        `<div class="banner warn">...` with no page chrome at all, which a
        real browser rendered as an unstyled fragment instead of a page.
        Non-XHR must get the whole create page (nav / `<h1>`) around the
        same honest banner."""
        response = client.post(reverse("vision-generate"), FORM)

        body = response.content.decode()
        assert response.status_code == 503
        assert "<html" in body
        assert "<h1>Image generation</h1>" in body
        assert "No model assigned for Image generation" in body

    def test_xhr_unbound_503_still_gets_the_bare_fragment(self, client):
        """The XHR path is unchanged by item 5: the page's own fetch() call
        still gets just the small fragment to drop into `#form-errors`."""
        response = client.post(reverse("vision-generate"), FORM, **XHR)

        body = response.content.decode()
        assert response.status_code == 503
        assert "<html" not in body
        assert "No model assigned for Image generation" in body

    def test_unreachable_engine_is_a_503_naming_the_endpoint(self, client):
        _bind()
        with _engine(healthy=False):
            response = client.post(reverse("vision-generate"), FORM)

        assert response.status_code == 503
        assert "http://stub:9999" in response.content.decode()

    def test_get_is_not_allowed(self, client):
        assert client.get(reverse("vision-generate")).status_code == 405


@pytest.mark.django_db
class TestGenerateEnqueues:
    """One submission path: the page enqueues `vision.generate`, the same
    job kind an agent enqueues, so the scheduler sees every generation and
    both callers get identical behaviour."""

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_valid_submission_is_queued_never_submitted_directly(self, mock_enqueue, client):
        _bind()
        with _engine(), patch("tools.vision.views.services.submit_job") as mock_submit:
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        assert response.status_code == 202
        mock_submit.assert_not_called()
        kind, payload = mock_enqueue.call_args.args
        assert kind == "vision.generate"
        assert payload["operation"] == "txt2img"
        assert payload["params"]["prompt"] == "a lighthouse"
        assert payload["inputs"] == {}

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_the_payload_is_json(self, mock_enqueue, client):
        _bind()
        with _engine():
            client.post(reverse("vision-generate"), FORM, **XHR)
        payload = mock_enqueue.call_args.args[1]
        assert json.loads(json.dumps(payload)) == payload

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_an_open_box_records_the_open_principal_as_the_actor(self, mock_enqueue, client):
        """Task 10, the acting rule part 1. Not a blank: every job on an
        open box has an honest actor, which is what makes
        `models/queue/visibility.py` able to reason about it later."""
        _bind()
        with _engine():
            client.post(reverse("vision-generate"), FORM, **XHR)
        payload = mock_enqueue.call_args.args[1]
        assert (payload["actor_kind"], payload["actor_key"]) == ("open", "box")

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_signed_in_user_records_their_own_principal_as_the_actor(self, mock_enqueue, client):
        from identity.contracts.postures import POSTURE_PERSONAL
        from tools.vision.tests._helpers import make_user, posture, sign_in

        _bind()
        user = make_user()
        with posture(POSTURE_PERSONAL):
            sign_in(client, user)
            with _engine():
                client.post(reverse("vision-generate"), FORM, **XHR)
        payload = mock_enqueue.call_args.args[1]
        assert (payload["actor_kind"], payload["actor_key"]) == ("user", str(user.pk))

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_the_real_enqueue_payload_feeds_run_generate_and_survives_intact(
        self, mock_enqueue, client, tmp_path
    ):
        """End-to-end seam: the payload contract (ADR 0012) is between
        what the view hands `enqueue()` and what a worker hands
        `run_generate`. Proving both halves separately (view builds the
        right shape; `run_generate` accepts a hand-built payload) leaves a
        gap where a hand-built test payload could quietly drift from what
        the view really sends. This captures the REAL `mock_enqueue.
        call_args` payload from a live POST and feeds it, unmodified,
        straight into `jobs.run_generate` -- the same call a worker makes
        -- with the usual engine stub, and asserts the generation succeeds
        with the params intact."""
        _bind()
        generator = StubGenerator(
            states=[JobStatus(state="done")], outputs=[("a.png", PNG, "image/png")]
        )
        with _engine(generator), override_settings(GENERATED_DIR=tmp_path):
            response = client.post(reverse("vision-generate"), FORM, **XHR)
            assert response.status_code == 202
            kind, payload = mock_enqueue.call_args.args
            assert kind == "vision.generate"

            result = jobs.run_generate(payload, [], make_job_ctx())

        assert result["status"] == GenerationJob.Status.DONE
        assert result["output_ids"]

        job = GenerationJob.objects.get(pk=result["job_id"])
        assert job.params["prompt"] == FORM["prompt"]
        assert job.params["sampler"] == FORM["sampler"]
        assert job.params["seed"] == payload["params"]["seed"]

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_the_seed_is_pinned_at_submit_time(self, mock_enqueue, client):
        """Validated params, not raw ones: a blank seed becomes a real
        number here, so the queue payload records what will actually run."""
        _bind()
        blank_seed = {**FORM, "seed": ""}
        with _engine():
            client.post(reverse("vision-generate"), blank_seed, **XHR)
        assert isinstance(mock_enqueue.call_args.args[1]["params"]["seed"], int)

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_an_uploaded_file_is_staged_and_travels_as_a_reference(self, mock_enqueue, client, tmp_path):
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        form = {**IMG2IMG_FORM, "init_image": upload}
        with _engine(), override_settings(GENERATED_DIR=tmp_path):
            response = client.post(reverse("vision-generate"), form, **XHR)

        assert response.status_code == 202
        payload = mock_enqueue.call_args.args[1]
        reference = payload["inputs"]["init_image"]
        assert reference.startswith("input:")
        staged = JobInput.objects.get(pk=int(reference.split(":")[1]))
        assert staged.job_id is None
        assert Path(staged.path).read_bytes() == PNG
        assert "init_image" not in payload["params"]

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_carried_reference_travels_as_itself(self, mock_enqueue, client, tmp_path):
        _bind()
        output = stored_output(tmp_path)
        form = {**IMG2IMG_FORM, "input_init_image": f"output:{output.id}"}
        with _engine():
            client.post(reverse("vision-generate"), form, **XHR)
        assert mock_enqueue.call_args.args[1]["inputs"] == {
            "init_image": f"output:{output.id}"
        }

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_an_attached_file_still_wins_over_a_carried_reference(
        self, mock_enqueue, client, tmp_path
    ):
        _bind()
        output = stored_output(tmp_path)
        form = {
            **IMG2IMG_FORM,
            "input_init_image": f"output:{output.id}",
            "init_image": SimpleUploadedFile("new.png", PNG, content_type="image/png"),
        }
        with _engine(), override_settings(GENERATED_DIR=tmp_path):
            client.post(reverse("vision-generate"), form, **XHR)
        assert mock_enqueue.call_args.args[1]["inputs"]["init_image"].startswith("input:")

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_dead_carried_reference_is_a_400_on_its_own_field(
        self, mock_enqueue, client, tmp_path
    ):
        _bind()
        output = stored_output(tmp_path)
        os.remove(output.path)
        form = {**IMG2IMG_FORM, "input_init_image": f"output:{output.id}"}
        with _engine():
            response = client.post(reverse("vision-generate"), form, **XHR)

        assert response.status_code == 400
        assert "no longer on disk" in response.content.decode()
        mock_enqueue.assert_not_called()

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_an_xhr_submission_gets_a_queued_card_that_polls(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        body = response.content.decode()
        assert reverse("vision-queue-status", args=[12]) in body
        assert "Queued" in body

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_plain_post_redirects_with_the_queue_job_id(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM)

        assert response.status_code == 302
        assert response["Location"] == (
            f"{reverse('vision-create')}?operation=txt2img&queued=12"
        )

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_plain_post_for_a_named_operation_redirects_to_that_operation(
        self, mock_enqueue, client, tmp_path
    ):
        _bind()
        with _engine(), override_settings(GENERATED_DIR=tmp_path):
            form = {**IMG2IMG_FORM, "init_image": SimpleUploadedFile("a.png", PNG)}
            response = client.post(reverse("vision-generate"), form)

        assert response["Location"] == (
            f"{reverse('vision-create')}?operation=img2img&queued=12"
        )

    @patch("tools.vision.views.enqueue")
    def test_an_unbound_role_is_a_503_and_queues_nothing(self, mock_enqueue, client):
        response = client.post(reverse("vision-generate"), FORM, **XHR)

        assert response.status_code == 503
        mock_enqueue.assert_not_called()

    @patch("tools.vision.views.enqueue", side_effect=QueueUnavailable())
    def test_an_unmigrated_queue_is_a_503_that_says_so(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        assert response.status_code == 503
        assert "migrations" in response.content.decode()

    @patch("tools.vision.views.enqueue", side_effect=RuntimeError("planner exploded"))
    def test_a_failed_enqueue_is_a_503_and_discards_what_it_staged(
        self, mock_enqueue, client, tmp_path
    ):
        """Nothing will ever consume those bytes, so they go now rather
        than waiting out the sweep."""
        _bind()
        form = {**IMG2IMG_FORM, "init_image": SimpleUploadedFile("a.png", PNG)}
        with _engine(), override_settings(GENERATED_DIR=tmp_path):
            response = client.post(reverse("vision-generate"), form, **XHR)

        assert response.status_code == 503
        assert JobInput.objects.count() == 0

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_invalid_params_never_reach_the_queue(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), {**FORM, "steps": "9999"}, **XHR)

        assert response.status_code == 400
        mock_enqueue.assert_not_called()

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_staging_sweeps_expired_uploads_as_it_goes(self, mock_enqueue, client, tmp_path):
        _bind()
        with override_settings(GENERATED_DIR=tmp_path):
            stale = services.stage_upload("init_image", SimpleUploadedFile("old.png", PNG))
            stale_id = int(stale.split(":")[1])
            JobInput.objects.filter(pk=stale_id).update(
                created_at=timezone.now() - timedelta(hours=48)
            )
            with _engine():
                client.post(
                    reverse("vision-generate"),
                    {**IMG2IMG_FORM, "init_image": SimpleUploadedFile("new.png", PNG)},
                    **XHR,
                )
        assert not JobInput.objects.filter(pk=stale_id).exists()


@pytest.mark.django_db
class TestIgnoredParamsSubmit:
    """ADR 0012 D-EDIT-13: a field the page rendered DISABLED (this
    model's graph cannot honour it) is not submitted by the browser. The
    submission must still be QUEUED, not 400'd -- and the payload must
    carry an honest value for the engine-owned one and an honest BLANK
    for the text one."""

    IGNORES = {
        "negative_prompt": "This model has no negative-prompt input.",
        "scheduler": "This model's own noise schedule has no separate setting to choose.",
    }

    def _flux2_connection(self):
        return ModelConnection.objects.create(
            name="klein", engine="stubengine", endpoint="http://stub:9999",
            model_id="klein.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

    def _engine_with_ignores(self):
        class _Flux2(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("txt2img", "edit")

            def ignored_params(self, operation_key, config=None):
                return dict(TestIgnoredParamsSubmit.IGNORES) if operation_key == "txt2img" else {}

            def list_choices(self, endpoint, key):
                return {"sampler": ("euler",), "scheduler": ("normal", "karras")}.get(key, ())

        return patch.dict(ENGINES, {"stubengine": _Flux2()}, clear=True)

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_submission_missing_the_disabled_fields_is_queued_not_refused(
        self, mock_enqueue, client
    ):
        connection = self._flux2_connection()
        form = {key: value for key, value in FORM.items()
                if key not in ("scheduler", "negative_prompt")}
        form["connection"] = str(connection.pk)

        with self._engine_with_ignores():
            response = client.post(reverse("vision-generate"), form, **XHR)

        assert response.status_code == 202, response.content.decode()
        payload = mock_enqueue.call_args.args[1]
        assert payload["params"]["scheduler"] == "normal"
        assert payload["params"]["negative_prompt"] == ""

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_hand_crafted_value_for_an_ignored_field_is_not_trusted(
        self, mock_enqueue, client
    ):
        """The page rendered `scheduler` and `negative_prompt` DISABLED
        for this model -- devtools can still re-enable a control and post
        a value for it, but that value is one the graph never reads.
        Accepting it would record a lie in the job (ADR 0012 D-EDIT-13),
        so the view re-derives both instead of trusting the submission."""
        connection = self._flux2_connection()
        form = dict(
            FORM, scheduler="karras", negative_prompt="blurry",
            connection=str(connection.pk),
        )

        with self._engine_with_ignores():
            response = client.post(reverse("vision-generate"), form, **XHR)

        assert response.status_code == 202
        payload = mock_enqueue.call_args.args[1]
        assert payload["params"]["scheduler"] == "normal"
        assert payload["params"]["negative_prompt"] == ""

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_an_ignored_file_param_is_not_blanked_to_the_empty_string(
        self, mock_enqueue, client
    ):
        """The template IGNORES map today only ever names non-file params,
        but the blanking loop that re-derives an ignored key's value did
        not know that -- it would happily stamp the string `""` over
        `merged["reference_image"]`, an actual `UploadedFile`. Gated on
        `operation.file_param_keys()`, a file key is now POPPED instead:
        `validate_params` sees it as simply unanswered (`raw.get` -> None,
        same as any other absent optional param), never as a param that
        was "answered" with an empty string. Submitting a real upload for
        an ignored optional file param must not crash and must not carry
        `""` in the job's params for that key."""
        connection = ModelConnection.objects.create(
            name="edit stub", engine="stubengine", endpoint="http://stub:9999",
            model_id="stub.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)
        captured = {}
        real_validate_params = operations.validate_params

        def _capture(operation, raw):
            captured["raw"] = dict(raw)
            return real_validate_params(operation, raw)

        form = {
            "operation": "edit", "instruction": "add a hat",
            "init_image": SimpleUploadedFile("beach.png", PNG, content_type="image/png"),
            "reference_image": SimpleUploadedFile("style.png", PNG, content_type="image/png"),
        }
        with _engine(), \
             patch("tools.vision.services.live_ignored", return_value={"reference_image": "x"}), \
             patch(
                 "tools.vision.views.services.stage_upload", side_effect=services.stage_upload
             ) as stage_upload, \
             patch("tools.vision.views.validate_params", side_effect=_capture):
            response = client.post(reverse("vision-generate"), form, **XHR)

        assert response.status_code == 202, response.content.decode()
        # Popped, not blanked: the key that reached `validate_params` is
        # simply absent, never a live `""`.
        assert "reference_image" not in captured["raw"]
        # `payload["params"]` NEVER carries a file key, ignored or not --
        # `payload_params` excludes every `file_param_keys()` entry
        # unconditionally, so that alone would pass with or without the
        # fix. The fix is that the ignored upload is never STAGED and
        # never named in `inputs` (review, Task E-batch minor #4/#5):
        # `stage_upload` is called once, for `init_image` only, and
        # `reference_image` is absent from `inputs` even though the
        # operator did attach a file for it.
        payload = mock_enqueue.call_args.args[1]
        assert "reference_image" not in payload["params"]
        assert stage_upload.call_args_list == [call("init_image", ANY)]
        assert "init_image" in payload["inputs"]
        assert "reference_image" not in payload["inputs"]

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_model_that_honours_everything_still_refuses_a_missing_choice(
        self, mock_enqueue, client
    ):
        """The relaxation is scoped to what the engine actually said it
        ignores -- an ordinary checkpoint still gets the honest 400."""
        _bind()
        form = {key: value for key, value in FORM.items() if key != "sampler"}

        with _engine():
            response = client.post(reverse("vision-generate"), form, **XHR)

        assert response.status_code == 400
        mock_enqueue.assert_not_called()


@pytest.mark.django_db
class TestUpscaleWithNoUpscalersInstalled:
    """The S4 ruling, end to end, in BOTH the cases that empty the option
    list: a reachable engine with no upscale models installed, and no image
    model assigned at all. Either way upscaling is impossible, the page
    says so ON THE FIELD rather than accepting the submission, and the
    message names the cause honestly instead of blaming whichever one it
    is not (R2-3)."""

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_submitting_an_upscale_with_no_engine_upscalers_is_a_400_naming_the_field(
        self, mock_enqueue, client, tmp_path, settings
    ):
        settings.GENERATED_DIR = tmp_path / "generated"
        _bind()
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: ()
        engine.list_assets = lambda endpoint, kind: []
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "upscale": UPSCALE}), \
             patch.dict(ENGINES, {"stubengine": engine}):
            response = client.post(
                reverse("vision-generate"),
                {"operation": "upscale", "init_image": upload},
                **XHR,
            )

        assert response.status_code == 400
        body = response.content.decode().lower()
        assert "no upscale model" in body
        assert GenerationJob.objects.filter(operation="upscale").count() == 0
        mock_enqueue.assert_not_called()

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_an_unbound_role_is_not_blamed_on_the_engines_model_folder(
        self, mock_enqueue, client, tmp_path, settings
    ):
        """R2-3: with NOTHING bound, `services.live_options` returns `{}` without
        asking any engine -- so the field is empty for a completely
        different reason. The operator must be pointed at the binding they
        are missing, never told an engine reported something it never
        did."""
        settings.GENERATED_DIR = tmp_path / "generated"
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "upscale": UPSCALE}):
            response = client.post(
                reverse("vision-generate"),
                {"operation": "upscale", "init_image": upload},
                **XHR,
            )

        body = response.content.decode()
        assert response.status_code == 400
        assert "no image model is assigned yet" in body
        assert reverse("inference-console") in body
        assert "reports no" not in body
        assert GenerationJob.objects.filter(operation="upscale").count() == 0
        mock_enqueue.assert_not_called()
        # Deliberate consequence of the S4 ruling, recorded here so it is
        # not read as a regression: an unbound role makes the upscaler
        # field unfillable, so this POST is refused at the FORM (400) and
        # never reaches the queue. The message carries the same
        # information a 503 would -- assign a model, here is where -- so
        # nothing is hidden from the operator, and the test below pins that
        # operations needing no asset are untouched.

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_an_operation_that_needs_no_asset_still_reports_the_binding_not_a_field(
        self, mock_enqueue, client
    ):
        """The boundary this fix must not cross. The asset field's message
        is for a control that cannot be filled; it must not become the
        universal answer. txt2img declares no asset param, so an unbound
        role there still reaches the queue's own preflight check and still
        gets the honest 503 -- and a MALFORMED txt2img submission still
        gets its 400, which is B10."""
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "upscale": UPSCALE}):
            unavailable = client.post(reverse("vision-generate"), dict(FORM), **XHR)
            malformed = client.post(
                reverse("vision-generate"), {**FORM, "steps": "not a number"}, **XHR
            )

        assert unavailable.status_code == 503
        assert "No model assigned for Image generation" in unavailable.content.decode()
        assert malformed.status_code == 400
        mock_enqueue.assert_not_called()


@pytest.mark.django_db
class TestJobStatus:
    def _job(self, generator):
        return _submitted_job(generator=generator)

    def test_refreshes_and_renders_the_card(self, client):
        generator = StubGenerator(states=[JobStatus(state="running")])
        job = self._job(generator)
        with _engine(generator):
            response = client.get(reverse("vision-job-status", args=[job.id]))

        job.refresh_from_db()
        assert response.status_code == 200
        assert job.status == GenerationJob.Status.RUNNING
        assert "data-job-poll" in response.content.decode()

    def test_a_finished_card_stops_polling(self, client, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[("a.png", PNG, "image/png")])
        job = self._job(generator)
        with _engine(generator):
            response = client.get(reverse("vision-job-status", args=[job.id]))

        body = response.content.decode()
        assert "data-job-poll" not in body
        assert reverse("vision-output-file", args=[job.outputs.get().id]) in body

    def test_json_format_returns_the_job_dict(self, client, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[("a.png", PNG, "image/png")])
        job = self._job(generator)
        with _engine(generator):
            response = client.get(reverse("vision-job-status", args=[job.id]), {"format": "json"})

        payload = json.loads(response.content)
        assert payload["status"] == "done"
        assert payload["seed"] == 42
        assert payload["outputs"][0]["width"] == 512
        assert payload["unreachable"] is False

    def test_a_polled_card_for_a_flux2_pick_carries_the_connection_and_edit_link(
        self, client, tmp_path, settings
    ):
        """B2: `_render_card` used to build `_job_card.html`'s context with
        a bare `input_targets()` (registry order -- img2img before edit)
        and no `selected_connection` at all, so a card refreshed through
        `job_status`/`queue_job_status` (every poll) lost the picked
        model's own operation narrowing AND the `&connection=` a fresh
        page render carries. A poll request naming the same pick
        (`?connection=<pk>`, the same query key the create page's own
        chooser and `_output_actions.html`'s own links use) must render a
        "Use in ..." link that both carries that pick and routes to the
        operation THAT model actually supports."""
        settings.GENERATED_DIR = tmp_path
        connection = ModelConnection.objects.create(
            name="flux2 pick", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

        class _EditOnly(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        output = stored_output(tmp_path)
        job = output.job

        with patch.dict(ENGINES, {"stubengine": _EditOnly()}, clear=True):
            response = client.get(
                reverse("vision-job-status", args=[job.id]) + f"?connection={connection.pk}"
            )

        body = response.content.decode()
        assert f"&connection={connection.pk}" in body
        assert f"{reverse('vision-create')}?operation=edit" in body
        assert f"{reverse('vision-create')}?operation=img2img" not in body

    def test_unreachable_engine_shows_the_still_checking_note(self, client):
        generator = StubGenerator(states=[ConnectionError("down")])
        job = self._job(generator)
        with _engine(generator):
            response = client.get(reverse("vision-job-status", args=[job.id]))

        assert "still checking" in response.content.decode()
        job.refresh_from_db()
        assert job.status == GenerationJob.Status.QUEUED

    def test_unknown_job_is_a_404(self, client):
        response = client.get(
            reverse("vision-job-status", args=["8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"])
        )
        assert response.status_code == 404

    def test_a_waiting_card_shows_only_the_wait(self, client):
        """Each state asserts the string THAT state renders. A blanket
        "total is in the body" would pass on `0:00` in the states that
        render no total at all, and would race the clock besides."""
        job = _timed_job(created=_ago(seconds=90), status=GenerationJob.Status.QUEUED)
        body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()
        assert "Waiting" in body
        assert "total" not in body.lower().split("waiting")[1][:80]

    def test_a_running_card_shows_the_wait_and_the_run(self, client):
        job = _timed_job(
            created=_ago(seconds=90), started=_ago(seconds=30),
            status=GenerationJob.Status.RUNNING,
        )
        body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()
        assert "Waited" in body and "running" in body
        assert job.durations_display["queued"] in body    # fixed once started

    def test_a_finished_card_shows_all_three_from_fixed_stamps(self, client):
        """Fixed stamps, so every rendered string is exact -- nothing here
        depends on how long the test took to run."""
        job = _timed_job(
            created=_ago(seconds=300), started=_ago(seconds=280),
            finished=_ago(seconds=10), status=GenerationJob.Status.DONE,
        )
        body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()
        assert "total" in body
        for key in ("queued", "processing", "total"):
            assert job.durations_display[key] in body

    def test_a_failed_card_that_never_ran_shows_no_run_time(self, client):
        job = _timed_job(
            created=_ago(seconds=60), finished=_ago(seconds=55),
            status=GenerationJob.Status.FAILED,
        )
        body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()
        assert "total" in body
        assert "ran" not in body.lower().split("waited")[1][:60]

    def test_a_card_shows_the_platform_queue_wait_when_the_link_resolves(self, client):
        """Fix round 2026-08-25: `In queue <mm:ss>` prefixes the timing
        line once `queue_job_id` resolves to a real `InferenceJob` --
        the platform-side wait (enqueue to claim) that used to be
        invisible on this card entirely."""
        from models.queue.models import InferenceJob

        job = _timed_job(created=_ago(seconds=90), status=GenerationJob.Status.QUEUED)
        queue_job = InferenceJob.objects.create(
            kind="vision.generate", state="running", priority=200, payload={},
        )
        InferenceJob.objects.filter(pk=queue_job.pk).update(
            created_at=job.created_at - timedelta(seconds=15)
        )
        GenerationJob.objects.filter(pk=job.pk).update(queue_job_id=queue_job.pk)

        body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()

        assert "In queue" in body


@pytest.mark.django_db
class TestOutputFile:
    def _finished_job(self, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[("a.png", PNG, "image/png")])
        _bind()
        with _engine(generator):
            job = services.submit_job("txt2img", dict(FORM), actor=OPEN_PRINCIPAL)
            services.refresh_job(job)
        return job

    def test_streams_the_stored_bytes_inline(self, client, tmp_path, settings):
        job = self._finished_job(tmp_path, settings)
        output = job.outputs.get()

        response = client.get(reverse("vision-output-file", args=[output.id]))

        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert b"".join(response.streaming_content) == PNG

    def test_download_flag_sets_an_attachment_disposition(self, client, tmp_path, settings):
        job = self._finished_job(tmp_path, settings)
        output = job.outputs.get()

        response = client.get(reverse("vision-output-file", args=[output.id]), {"download": "1"})

        assert "attachment" in response["Content-Disposition"]

    def test_missing_file_on_disk_is_a_404_not_a_500(self, client, tmp_path, settings):
        job = self._finished_job(tmp_path, settings)
        output = job.outputs.get()
        os.remove(output.path)

        assert client.get(reverse("vision-output-file", args=[output.id])).status_code == 404


@pytest.mark.django_db
class TestJobDelete:
    def test_deletes_and_redirects(self, client, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        job = _submitted_job()

        response = client.post(reverse("vision-job-delete", args=[job.id]))

        assert response.status_code == 302
        assert GenerationJob.objects.count() == 0

    def test_get_is_not_allowed(self, client):
        job = _submitted_job()

        assert client.get(reverse("vision-job-delete", args=[job.id])).status_code == 405


@pytest.mark.django_db
class TestOperationFromThePost:
    def test_the_posted_operation_decides_which_schema_validates(self, client):
        """Now that a submission is queued, not submitted directly, this
        pins the payload the queue receives rather than a `GenerationJob`
        row -- no row exists until a worker runs the job."""
        operation = Operation(
            key="img2img", label="Image to image", capability="image-generation",
            output_media="image/png",
            params=(
                Param("prompt", "text", "Prompt", default="", required=True),
                Param("denoise", "float", "Denoise", default=0.6, min=0, max=1),
                Param("seed", "seed", "Seed", default=None),
            ),
        )
        _bind()
        with patch.dict(operations._OPERATIONS, {"img2img": operation}), _engine(), \
             patch("tools.vision.views.enqueue", return_value=12) as mock_enqueue:
            response = client.post(
                reverse("vision-generate"),
                {"operation": "img2img", "prompt": "a lighthouse", "denoise": "0.4", "seed": "42"},
                **XHR,
            )

        assert response.status_code == 202
        payload = mock_enqueue.call_args.args[1]
        assert payload["operation"] == "img2img"
        assert payload["params"] == {"prompt": "a lighthouse", "denoise": 0.4, "seed": 42}
        assert GenerationJob.objects.count() == 0

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_post_naming_an_unregistered_operation_is_a_404(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), {"operation": "controlnet", **FORM}, **XHR)

        assert response.status_code == 404
        assert GenerationJob.objects.count() == 0
        mock_enqueue.assert_not_called()

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_post_with_no_operation_field_uses_the_default(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        assert response.status_code == 202
        assert mock_enqueue.call_args.args[1]["operation"] == "txt2img"

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_no_js_post_for_a_non_default_operation_redirects_back_to_it(self, mock_enqueue, client):
        """Fix-wave item 1: a plain (no-JS) POST used to always redirect to
        bare `/vision/`, silently switching a second operation's submission
        to the default one's URL."""
        operation = Operation(
            key="img2img", label="Image to image", capability="image-generation",
            output_media="image/png",
            params=(
                Param("prompt", "text", "Prompt", default="", required=True),
                Param("denoise", "float", "Denoise", default=0.6, min=0, max=1),
                Param("seed", "seed", "Seed", default=None),
            ),
        )
        _bind()
        with patch.dict(operations._OPERATIONS, {"img2img": operation}), _engine():
            response = client.post(
                reverse("vision-generate"),
                {"operation": "img2img", "prompt": "a lighthouse", "denoise": "0.4", "seed": "42"},
            )

        assert response.status_code == 302
        assert response["Location"] == (
            f"{reverse('vision-create')}?operation=img2img&queued=12"
        )

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_no_js_post_names_its_operation_in_the_redirect(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM)

        assert response.status_code == 302
        assert response["Location"] == (
            f"{reverse('vision-create')}?operation=txt2img&queued=12"
        )


@pytest.mark.django_db
class TestGenerateFromAStoredImage:
    @patch("tools.vision.views.enqueue", return_value=12)
    def test_a_referenced_image_becomes_the_jobs_input_with_no_upload(
        self, mock_enqueue, client, tmp_path, settings
    ):
        """A submission is queued now, not submitted directly -- the carried
        reference is resolved once (to prove it is live) and travels in the
        payload's `inputs`, unconsumed, exactly as it is posted."""
        settings.GENERATED_DIR = tmp_path / "generated"
        _bind()
        output = stored_output(tmp_path)
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": IMG2IMG}), _engine():
            response = client.post(
                reverse("vision-generate"),
                {"operation": "img2img", "prompt": "a lighthouse", "negative_prompt": "",
                 "denoise": "0.5", "steps": "20", "cfg_scale": "7", "seed": "1",
                 "sampler": "euler", "scheduler": "normal", "batch_size": "1",
                 "input_init_image": f"output:{output.id}"},
                **XHR,
            )

        assert response.status_code == 202
        payload = mock_enqueue.call_args.args[1]
        assert payload["operation"] == "img2img"
        assert payload["inputs"] == {"init_image": f"output:{output.id}"}
        assert "init_image" not in payload["params"]
        # Only the pre-existing job `stored_output` created for the
        # reference itself -- the submission above only enqueued.
        assert GenerationJob.objects.count() == 1

    @patch("tools.vision.views.enqueue", return_value=12)
    def test_an_unresolvable_reference_is_a_400_that_names_it(self, mock_enqueue, client):
        _bind()
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": IMG2IMG}), _engine():
            response = client.post(
                reverse("vision-generate"),
                {"operation": "img2img", "prompt": "p", "negative_prompt": "", "denoise": "0.5",
                 "steps": "20", "cfg_scale": "7", "seed": "1", "sampler": "euler",
                 "scheduler": "normal", "batch_size": "1",
                 "input_init_image": "output:4242"},
                **XHR,
            )
        assert response.status_code == 400
        assert "does not name a stored image" in response.content.decode()
        mock_enqueue.assert_not_called()


@pytest.mark.django_db
class TestGenerateCarriesThePickedModel:
    """The per-generation model picker (Task 11): the submission's own
    `connection` field, not a session, decides which model the queued job
    resolves against at claim time."""

    def test_the_pick_travels_in_the_queue_payload_as_a_pk_string(self, client):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.safetensors", capabilities=["image-generation"],
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True), patch(
            "tools.vision.views.enqueue", return_value=7
        ) as enqueued:
            client.post(
                reverse("vision-generate"),
                {**RAW, "operation": "txt2img", "connection": str(connection.pk)},
            )

        payload = enqueued.call_args.args[1]
        assert payload["connection"] == str(connection.pk)

    def test_no_pick_enqueues_exactly_what_it_always_did(self, client):
        """Every existing caller, link, and bookmark sends no `connection`
        field, and must keep behaving identically."""
        connection = ModelConnection.objects.create(
            name="bound", engine="stubengine", endpoint="http://stub:9999",
            model_id="b.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.update_or_create(
            role_key="vision.generate", defaults={"connection": connection}
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True), patch(
            "tools.vision.views.enqueue", return_value=7
        ) as enqueued:
            client.post(reverse("vision-generate"), {**RAW, "operation": "txt2img"})

        assert "connection" not in enqueued.call_args.args[1]

    def test_an_edit_keeps_its_image_instruction_and_model_in_one_submission(self, client):
        """The behavioural guard for the picker's form separation: a real
        `edit` POST carries an uploaded file, a typed instruction, AND the
        picked model, and all three must reach the payload together.

        This is the exact regression a `formmethod="get"` picker button
        inside the multipart generate form would cause -- the file dropped,
        the instruction discarded -- and it is invisible to a template
        assertion, so it is pinned here instead.
        """
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )
        uploaded = SimpleUploadedFile("beach.png", PNG, content_type="image/png")

        class _EditOnly(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        with patch.dict(ENGINES, {"stubengine": _EditOnly()}, clear=True), patch(
            "tools.vision.views.enqueue", return_value=7
        ) as enqueued:
            response = client.post(
                reverse("vision-generate"),
                {
                    "operation": "edit",
                    "instruction": "put a red hat on the woman",
                    "init_image": uploaded,
                    "guidance": "4.0",
                    "steps": "20",
                    "seed": "42",
                    "connection": str(connection.pk),
                },
            )

        assert response.status_code in (202, 302)
        payload = enqueued.call_args.args[1]
        assert payload["operation"] == "edit"
        assert payload["params"]["instruction"] == "put a red hat on the woman"
        # The file became a staged REFERENCE (`services.stage_upload`), which
        # is how a browser upload survives into a JSON queue payload at all.
        assert payload["inputs"]["init_image"].startswith("input:")
        assert payload["connection"] == str(connection.pk)

    def test_a_pick_that_no_longer_resolves_is_refused_and_nothing_is_queued(self, client):
        with patch("tools.vision.views.enqueue") as enqueued:
            response = client.post(
                reverse("vision-generate"),
                {**RAW, "operation": "txt2img", "connection": "999999"},
                **XHR,
            )

        assert response.status_code == 503
        assert "no longer registered" in response.content.decode()
        enqueued.assert_not_called()

    def test_a_non_xhr_pick_that_no_longer_resolves_gets_a_whole_page_with_the_message(self, client):
        """The non-XHR twin of the test above: this is a real POST from a
        browser with no JS, so it must get the whole page back (chrome, nav,
        the message) -- the same "no bare fragment for a plain POST" rule
        `test_non_xhr_unbound_503_renders_the_whole_page_not_a_bare_fragment`
        already pins for the unbound-role case."""
        with patch("tools.vision.views.enqueue") as enqueued:
            response = client.post(
                reverse("vision-generate"),
                {**RAW, "operation": "txt2img", "connection": "999999"},
            )

        body = response.content.decode()
        assert response.status_code == 503
        assert "<html" in body
        assert "no longer registered" in body
        enqueued.assert_not_called()


class TestTheToolEntitlementGatesTheDirectSurface:
    """A labelled tool must not be reachable from its own page.

    Task 5 filters an AGENT's prompt; `vision-generate` is a POST a
    person makes directly, and until this gate it obeyed no label at all
    -- so an entitlement that took image generation away from somebody
    took it away only when an agent asked for it.

    403, NOT 503 and NOT 404: this is a refusal on a class of ACTION, the
    same shape `rag-document-delete` uses, and the caller can already see
    that the page exists.
    """

    def test_a_non_holder_is_refused_403_and_nothing_is_queued(self, client):
        from agents.models import ToolEntitlement
        from models.queue.models import InferenceJob
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="vision.generate", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("vision-generate"),
                                   {"operation": "txt2img", "prompt": "a picture"})
        assert response.status_code == 403
        assert b"Traceback" not in response.content
        assert InferenceJob.objects.count() == 0

    def test_the_xhr_refusal_carries_no_admin_only_console_link(self, client):
        """T13 review finding 2: the XHR 403 used to reuse `vision/
        _unavailable.html`, the 503 body -- whose `Models` link
        points at `inference-console` (class S, administrators only).
        Handing that link to the very non-admin this refusal names
        reframes a permission decision as a config problem for somebody
        who cannot act on it either way. `vision/_forbidden.html` is the
        403-only fragment this pins: message, no console link."""
        from agents.models import ToolEntitlement
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="vision.generate", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("vision-generate"),
                                   {"operation": "txt2img", "prompt": "a picture"}, **XHR)
        assert response.status_code == 403
        assert b"Models" not in response.content
        assert b"entitlement" in response.content.lower()

    def test_a_holder_is_not_refused_by_this_gate(self, client):
        """It may still fail later for an unbound role or an unreachable
        engine -- that is a 503 and a different fact. What this asserts is
        that the ENTITLEMENT gate is not what stopped them."""
        from agents.models import ToolEntitlement
        member = make_user()
        legal = make_entitlement(name="Legal")
        grant(legal, user=member)
        ToolEntitlement.objects.create(tool_key="vision.generate", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("vision-generate"),
                                   {"operation": "txt2img", "prompt": "a picture"})
        assert response.status_code != 403

    def test_an_unlabelled_tool_refuses_nobody(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("vision-generate"),
                                   {"operation": "txt2img", "prompt": "a picture"})
        assert response.status_code != 403

    def test_an_open_box_is_unchanged(self, client):
        """`tool_access_for(OPEN_PRINCIPAL)` is unrestricted because
        `sees_all_content` tests `accounts_on()` first, so a household box
        never reaches a `ToolEntitlement` query on this path."""
        from agents.models import ToolEntitlement
        ToolEntitlement.objects.create(tool_key="vision.generate",
                                       entitlement=make_entitlement(name="Legal"))
        response = client.post(reverse("vision-generate"),
                               {"operation": "txt2img", "prompt": "a picture"})
        assert response.status_code != 403

    def test_the_refusal_lands_before_anything_is_staged(self, client, tmp_path, settings):
        """The gate is the FIRST thing in the view, before
        `picked_connection`, before the form, before `stage_upload`. A
        refusal that had already written a file into the managed store
        would leave the box holding bytes for a job that never existed."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from agents.models import ToolEntitlement
        settings.GENERATED_DIR = str(tmp_path)
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="vision.generate", entitlement=legal)
        upload = SimpleUploadedFile("in.png", b"not-a-real-png", content_type="image/png")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("vision-generate"),
                                   {"operation": "img2img", "prompt": "a picture",
                                    "input_image": upload})
        assert response.status_code == 403
        assert list(tmp_path.rglob("*")) == []


class TestTheAndComposition:
    """`allows(T) AND (M in no set OR principal holds a reaching
    entitlement)`. Four cells, because three of them are the ones
    somebody would get wrong by implementing an OR."""

    @pytest.mark.parametrize(
        "holds_tool,holds_model,expected_403",
        [(False, False, True), (True, False, True), (False, True, True), (True, True, False)],
    )
    def test_both_are_required(self, client, holds_tool, holds_model, expected_403):
        from agents.models import ToolEntitlement
        from models.contracts.roles import IMAGE_GENERATION_CAPABILITY
        from models.registry.models import ModelSet, ModelSetEntitlement, ModelSetMember
        from models.registry.tests._helpers import make_chat_connection
        tool_ent = make_entitlement(name="ToolAccess")
        model_ent = make_entitlement(name="ModelAccess")
        connection = make_chat_connection(capabilities=[IMAGE_GENERATION_CAPABILITY])
        ToolEntitlement.objects.create(tool_key="vision.generate", entitlement=tool_ent)
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        ModelSetEntitlement.objects.create(model_set=model_set, entitlement=model_ent)
        member = make_user()
        if holds_tool:
            grant(tool_ent, user=member)
        if holds_model:
            grant(model_ent, user=member)
        # THE FULL FORM, not just `operation`/`prompt`: the (True, True)
        # cell has to actually reach a SUCCESS, and a submission missing
        # `width`/`height`/`steps`/etc. would 400 out of form validation
        # before the AND-composition's own answer ever mattered -- which
        # is exactly the gap `expected_403=False` used to paper over (see
        # below). The other three cells never reach form validation at
        # all (the tool or model gate refuses first), so the extra fields
        # cost them nothing.
        payload = dict(FORM, operation="txt2img", connection=str(connection.pk))
        with patch("tools.vision.views.enqueue", return_value=12):
            with posture(POSTURE_ENTERPRISE):
                sign_in(client, member)
                response = client.post(reverse("vision-generate"), payload)
        if expected_403:
            assert response.status_code == 403
        else:
            # Review finding: `!= 403` alone is satisfied by an unrelated
            # 400 or 503 too (a broken AND-composition that happened to
            # also fail form validation would still read as "not 403"),
            # so this is pinned to the actual success status a plain,
            # non-XHR POST answers with -- the redirect `generate` itself
            # returns once nothing refused it.
            assert response.status_code == 302


class TestANonDecimalConnectionNeverCrashes:
    """Whole-branch review, item 1 (the isdecimal ruling): `"²".isdigit()`
    is `True` but `int("²")` raises `ValueError`. `raw_connection.
    isdigit()` at this exact line (`generate`'s `if not usable:` branch)
    used to reach that `int()` call directly -- and NOTHING gates this
    POST first: `_may_generate` answers True with no `ToolEntitlement`
    row for `vision.generate` at all (the default, unconfigured state),
    so this was reachable by a completely ANONYMOUS, unauthenticated
    submission. `.isdecimal()` is the set `int()` can actually parse, so
    the AND at that line now short-circuits before `int()` ever runs,
    and the submission falls through to the ordinary "picked a model
    that is gone" 503 instead."""

    def test_a_non_decimal_digit_connection_answers_503_not_500(self, client):
        response = client.post(reverse("vision-generate"),
                               {"operation": "txt2img", "prompt": "a picture",
                                "connection": "²"})
        assert response.status_code == 503
        assert b"Traceback" not in response.content
