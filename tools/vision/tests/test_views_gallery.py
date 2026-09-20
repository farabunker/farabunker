"""Unit tests for the /vision/gallery/ page and the reuse-settings prefill."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from identity.access import owner_fields
from identity.contracts.postures import POSTURE_PERSONAL
from models.registry.models import ModelConnection, RoleBinding
from models.contracts import operations
from models.contracts.engines import ENGINES
from models.contracts.operations import IMG2IMG, TXT2IMG
from models.contracts.roles import VISION_GENERATE_ROLE
from tools.vision import views
from tools.vision.models import GeneratedOutput, GenerationJob, JobInput
from tools.vision.tests._helpers import (
    PNG, StubEngine, clear_bindings, make_user, posture, stored_output, user_principal,
)


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


def _job_with_output(prompt="a lighthouse", seed=42, index=0):
    job = GenerationJob.objects.create(
        operation="txt2img",
        params={
            "prompt": prompt, "negative_prompt": "", "width": 512, "height": 512,
            "steps": 20, "cfg_scale": 7.0, "seed": seed, "sampler": "euler",
            "scheduler": "normal", "batch_size": 1,
        },
        seed=seed, engine="stubengine", model_id="stub.safetensors",
        model_fingerprint="stubengine:stub.safetensors:None",
        status=GenerationJob.Status.DONE,
    )
    output = GeneratedOutput.objects.create(
        job=job, index=index, path="/tmp/a.png", media_type="image/png", width=512, height=512
    )
    return job, output


@pytest.mark.django_db
class TestGallery:
    def test_lists_outputs_with_their_facts(self, client):
        job, output = _job_with_output()

        response = client.get(reverse("vision-gallery"))
        body = response.content.decode()

        assert response.status_code == 200
        assert "a lighthouse" in body
        assert "42" in body
        assert "stub.safetensors" in body
        assert reverse("vision-output-file", args=[output.id]) in body

    def test_empty_gallery_says_so(self, client):
        response = client.get(reverse("vision-gallery"))
        assert "Nothing generated yet" in response.content.decode()

    def test_is_paginated_newest_first(self, client):
        from tools.vision import views

        for index in range(views.GALLERY_PAGE_SIZE + 1):
            _job_with_output(prompt=f"prompt {index}")

        page_one = client.get(reverse("vision-gallery"))
        page_two = client.get(reverse("vision-gallery"), {"page": 2})

        assert len(page_one.context["page_obj"].object_list) == views.GALLERY_PAGE_SIZE
        assert len(page_two.context["page_obj"].object_list) == 1
        assert page_one.context["page_obj"].object_list[0].job.params["prompt"] == (
            f"prompt {views.GALLERY_PAGE_SIZE}"
        )

    def test_reuse_link_points_at_the_create_page_with_the_job_id(self, client):
        job, _ = _job_with_output()

        body = client.get(reverse("vision-gallery")).content.decode()

        assert f"{reverse('vision-create')}?reuse={job.id}" in body

    def test_row_carries_a_no_confirm_delete_control(self, client):
        """Fix-wave item 6: gallery rows had no delete control at all. Same
        dialog-free two-step confirm as the job card's."""
        job, _ = _job_with_output()

        body = client.get(reverse("vision-gallery")).content.decode()

        assert "confirm(" not in body
        assert 'class="delete-disclosure"' in body
        assert reverse("vision-job-delete", args=[job.id]) in body
        assert f'name="next" value="{reverse("vision-gallery")}"' in body

    def test_the_caption_facts_come_from_the_schema(self, client):
        """The gallery caption renders `_job_facts.html`, so a second
        operation's parameters appear here with no template edit. The
        OUTPUT's own pixel size leads, because that measures the file
        rather than the request."""
        _job_with_output()

        body = client.get(reverse("vision-gallery")).content.decode()

        assert "Size 512×512" in body
        assert "Seed 42" in body
        assert "Sampler euler" in body
        assert "Model stub.safetensors" in body

    def test_the_caption_shows_how_long_the_generation_ran(self, client):
        """`· ran {{ processing }}` beside the engine it already names --
        the gallery lists finished work only, so the run time is the one
        duration worth showing there (owner requirement 2026-08-25)."""
        job, _output = _job_with_output()
        GenerationJob.objects.filter(pk=job.pk).update(
            started_at=timezone.now() - timedelta(seconds=280),
            finished_at=timezone.now() - timedelta(seconds=10),
        )
        job.refresh_from_db()

        body = client.get(reverse("vision-gallery")).content.decode()

        assert f"ran {job.durations_display['processing']}" in body

    def test_use_in_links_follow_the_role_bound_model_not_registry_order(self, client):
        """B2: `gallery()` used to call `input_targets()` bare -- the FULL
        registry, in registration order (`img2img` before `edit`) -- so a
        role bound to a flux2 (edit-only) connection still offered an
        img2img "Use in ..." link that would fail honestly at the engine
        the moment it was followed. The gallery has no picker of its own,
        so it narrows by the ROLE BINDING (`services.preflight(None).
        resolved`), the same fact the create page's default view uses."""
        connection = ModelConnection.objects.create(
            name="flux2 bound", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )
        RoleBinding.objects.update_or_create(
            role_key=VISION_GENERATE_ROLE, defaults={"connection": connection}
        )

        class _EditOnly(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        _job_with_output()

        with patch.dict(ENGINES, {"stubengine": _EditOnly()}, clear=True):
            body = client.get(reverse("vision-gallery")).content.decode()

        assert f"{reverse('vision-create')}?operation=edit" in body
        assert f"{reverse('vision-create')}?operation=img2img" not in body


@pytest.mark.django_db
class TestGalleryJobFilter:
    """`?job=<uuid>` (2026-09-03 fix batch, item B) -- the href contract
    chat cards use to jump here:
    `{% url 'vision-gallery' %}?job=<uuid>#job-<uuid>`."""

    def test_it_narrows_to_the_named_jobs_outputs(self, client):
        job_one, output_one = _job_with_output(prompt="keep")
        _job_two, output_two = _job_with_output(prompt="drop")

        body = client.get(reverse("vision-gallery"), {"job": str(job_one.id)}).content.decode()

        assert reverse("vision-output-file", args=[output_one.id]) in body
        assert reverse("vision-output-file", args=[output_two.id]) not in body
        assert "{#" not in body, "a multi-line Django comment leaked into the rendered page"

    def test_an_unknown_uuid_is_the_normal_empty_gallery_not_a_500(self, client):
        _job_with_output()
        import uuid as uuid_module

        response = client.get(reverse("vision-gallery"), {"job": str(uuid_module.uuid4())})

        assert response.status_code == 200
        assert "Nothing generated yet" in response.content.decode()

    def test_a_malformed_uuid_is_the_normal_empty_gallery_not_a_500(self, client):
        _job_with_output()

        response = client.get(reverse("vision-gallery"), {"job": "not-a-uuid"})

        assert response.status_code == 200
        assert "Nothing generated yet" in response.content.decode()

    def test_a_crafted_job_value_cannot_smuggle_a_second_query_param(self, client):
        """Review finding 5: `job_filter` used to be echoed into several
        links (the header Select/Done link, the bulk-delete form's
        hidden `next` field, "Select all", both pager links) with no
        escaping -- a value containing `&` could smuggle an extra query
        param onto them. Every echo site now runs through `|urlencode`;
        the crafted value never resolves to a real job either way, so
        this only proves the LINK itself stays one safely-encoded `job=`
        param rather than growing a second, attacker-chosen one."""
        body = client.get(reverse("vision-gallery"), {"job": "x&evil=1"}).content.decode()

        assert "job=x%26evil%3D1" in body
        assert "evil=1" not in body

    def test_a_foreign_jobs_id_is_the_normal_empty_gallery_no_oracle(self, client):
        """A job that exists but belongs to someone else must read exactly
        like an unknown uuid -- nothing in the response may let a caller
        tell "not yours" apart from "does not exist"."""
        ann, bob = make_user(), make_user()
        theirs = GenerationJob.objects.create(
            operation="txt2img",
            params={"prompt": "theirs"},
            engine="stubengine", model_id="stub.safetensors",
            model_fingerprint="stubengine:stub.safetensors:None",
            status=GenerationJob.Status.DONE,
            **owner_fields(user_principal(bob)),
        )
        GeneratedOutput.objects.create(
            job=theirs, index=0, path="/tmp/theirs.png", media_type="image/png",
            width=512, height=512,
        )

        with posture(POSTURE_PERSONAL):
            from tools.vision.tests._helpers import sign_in

            sign_in(client, ann)
            response = client.get(reverse("vision-gallery"), {"job": str(theirs.id)})

        assert response.status_code == 200
        assert "Nothing generated yet" in response.content.decode()

    def test_each_figure_carries_a_stable_job_anchor_id(self, client):
        job, _output = _job_with_output()

        body = client.get(reverse("vision-gallery")).content.decode()

        assert f'id="job-{job.id}"' in body

    def test_select_mode_and_the_job_filter_coexist(self, client):
        job_one, output_one = _job_with_output(prompt="keep")
        _job_two, output_two = _job_with_output(prompt="drop")

        body = client.get(
            reverse("vision-gallery"), {"job": str(job_one.id), "select": "1"}
        ).content.decode()

        assert f'name="jobs" value="{job_one.id}"' in body
        assert reverse("vision-output-file", args=[output_one.id]) in body
        assert reverse("vision-output-file", args=[output_two.id]) not in body

    def test_pager_links_preserve_the_job_filter(self, client):
        from tools.vision import views

        job, _first_output = _job_with_output(index=0)
        for index in range(1, views.GALLERY_PAGE_SIZE + 1):
            GeneratedOutput.objects.create(
                job=job, index=index, path=f"/tmp/{index}.png",
                media_type="image/png", width=512, height=512,
            )

        body = client.get(
            reverse("vision-gallery"), {"job": str(job.id), "page": 1}
        ).content.decode()

        assert f"page=2&job={job.id}" in body


@pytest.mark.django_db
class TestGalleryKeepsTheCompactFactsLine:
    def test_the_caption_still_reads_as_one_line(self, client):
        _job_with_output(seed=42)

        body = client.get(reverse("vision-gallery")).content.decode()

        assert "Seed 42 ·" in body
        assert '<dl class="job-facts">' not in body


@pytest.mark.django_db
class TestGalleryDelete:
    def test_deleting_from_a_gallery_row_redirects_back_to_the_gallery(self, client, tmp_path, settings):
        from tools.vision import store

        settings.GENERATED_DIR = tmp_path
        job, output = _job_with_output()
        job_dir = store.job_dir(job.id)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "a.png").write_bytes(b"x")

        response = client.post(
            reverse("vision-job-delete", args=[job.id]),
            {"next": reverse("vision-gallery")},
        )

        assert response.status_code == 302
        assert response["Location"] == reverse("vision-gallery")
        assert not GenerationJob.objects.filter(pk=job.id).exists()
        assert not GeneratedOutput.objects.filter(pk=output.id).exists()
        assert not job_dir.exists()

    def test_an_off_site_next_falls_back_to_the_create_page(self, client):
        job, _ = _job_with_output()

        response = client.post(
            reverse("vision-job-delete", args=[job.id]),
            {"next": "https://evil.example/steal"},
        )

        assert response.status_code == 302
        assert response["Location"] == reverse("vision-create")

    def test_no_next_field_still_defaults_to_the_create_page(self, client):
        job, _ = _job_with_output()

        response = client.post(reverse("vision-job-delete", args=[job.id]))

        assert response["Location"] == reverse("vision-create")

    def test_the_gallery_delete_control_comes_from_the_shared_fragment(self, client):
        _job_with_output()
        response = client.get(reverse("vision-gallery"))

        assert "vision/_delete_control.html" in [t.name for t in response.templates]


@pytest.mark.django_db
class TestGallerySelectMode:
    """`?select=1`/`?select=all` -- the iPhone-style select mode (owner
    ask, 2026-09-02). Zero JS: the whole thing is a GET state that
    composes with `page=`, plus one bulk POST form."""

    def test_normal_mode_has_no_checkboxes_or_bulk_form(self, client):
        _job_with_output()

        body = client.get(reverse("vision-gallery")).content.decode()

        assert 'name="jobs"' not in body
        assert reverse("vision-jobs-delete-selected") not in body
        assert f'{reverse("vision-gallery")}?page=1&select=1' in body
        assert "Select</a>" in body

    def test_select_mode_renders_one_form_with_a_checkbox_per_figure(self, client):
        job, output = _job_with_output()

        body = client.get(reverse("vision-gallery"), {"select": "1"}).content.decode()

        assert body.count(f'<form method="post" action="{reverse("vision-jobs-delete-selected")}"') == 1
        # The test client does not enforce CSRF, so a missing token would
        # otherwise pass silently here -- assert it is actually rendered.
        assert "csrfmiddlewaretoken" in body
        assert f'name="jobs" value="{job.id}"' in body
        assert "Select all" in body
        assert "Delete selected" in body
        assert "Done</a>" in body
        # Per-figure actions are gone in select mode: no per-figure delete
        # control (the bulk bar's own <details class="delete-disclosure">
        # is the only one on the page) and no "Use in .../Reuse/Download" row.
        assert body.count('class="delete-disclosure"') == 1
        assert reverse("vision-job-delete", args=[job.id]) not in body
        assert "Reuse settings" not in body
        assert reverse("vision-output-file", args=[output.id]) + "?download=1" not in body

    def test_bulk_bar_is_a_row_above_the_grid_not_a_bottom_bar(self, client):
        """Owner ask (2026-09-02): the bar with "Select all" / "Delete
        selected" must render as a normal row directly under the Gallery
        header, before the image grid -- not stuck to the bottom of the
        viewport. Assert the bar's markup precedes the first figure in
        the document, and that it no longer carries sticky/bottom
        positioning."""
        _job_with_output()

        body = client.get(reverse("vision-gallery"), {"select": "1"}).content.decode()

        assert body.index('class="bulk-bar"') < body.index('<figure class="card"')
        assert "position: sticky; bottom: 0" not in body

    def test_select_all_checks_every_box_select_1_does_not(self, client):
        _job_with_output(seed=1)
        _job_with_output(seed=2)

        unchecked = client.get(reverse("vision-gallery"), {"select": "1"}).content.decode()
        checked = client.get(reverse("vision-gallery"), {"select": "all"}).content.decode()

        assert '" checked>' not in unchecked
        assert unchecked.count('name="jobs"') == checked.count('" checked>') == 2

    def test_select_links_carry_the_current_page(self, client):
        from tools.vision import views

        for index in range(views.GALLERY_PAGE_SIZE + 1):
            _job_with_output(prompt=f"prompt {index}")

        body = client.get(reverse("vision-gallery"), {"page": 2}).content.decode()

        assert f'{reverse("vision-gallery")}?page=2&select=1' in body

    def test_the_pager_also_preserves_select_mode(self, client):
        """The header's Select/Done link is not the only navigation that
        must survive select mode: paging away and back should not
        silently drop the caller out of it (gallery.html's own `{% if
        select_mode %}&select=1{% endif %}` on both pager links)."""
        from tools.vision import views

        for index in range(2 * views.GALLERY_PAGE_SIZE + 1):
            _job_with_output(prompt=f"prompt {index}")

        body = client.get(
            reverse("vision-gallery"), {"page": 2, "select": "1"}
        ).content.decode()

        assert '?page=1&select=1">← Newer</a>' in body
        assert '?page=3&select=1">Older →</a>' in body


@pytest.mark.django_db
class TestJobsDeleteSelected:
    def test_deletes_only_the_checked_jobs(self, client, tmp_path, settings):
        from tools.vision import store

        settings.GENERATED_DIR = tmp_path
        keep_job, keep_output = _job_with_output(prompt="keep me")
        dead_job_one, dead_output_one = _job_with_output(prompt="delete me one")
        dead_job_two, dead_output_two = _job_with_output(prompt="delete me two")
        for job in (keep_job, dead_job_one, dead_job_two):
            job_dir = store.job_dir(job.id)
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "a.png").write_bytes(b"x")

        response = client.post(
            reverse("vision-jobs-delete-selected"),
            {"jobs": [str(dead_job_one.id), str(dead_job_two.id)],
             "next": reverse("vision-gallery") + "?page=1"},
        )

        assert response.status_code == 302
        assert response["Location"] == reverse("vision-gallery") + "?page=1"
        assert GenerationJob.objects.filter(pk=keep_job.id).exists()
        assert not GenerationJob.objects.filter(pk=dead_job_one.id).exists()
        assert not GenerationJob.objects.filter(pk=dead_job_two.id).exists()
        assert not GeneratedOutput.objects.filter(pk=dead_output_one.id).exists()
        assert not GeneratedOutput.objects.filter(pk=dead_output_two.id).exists()
        assert GeneratedOutput.objects.filter(pk=keep_output.id).exists()
        assert not store.job_dir(dead_job_one.id).exists()
        assert not store.job_dir(dead_job_two.id).exists()
        assert store.job_dir(keep_job.id).exists()

        follow = client.get(response["Location"]).content.decode()
        assert "Deleted 2 generations." in follow

    def test_duplicate_checkboxes_for_the_same_job_count_once(self, client):
        job, output = _job_with_output()

        response = client.post(
            reverse("vision-jobs-delete-selected"),
            {"jobs": [str(job.id), str(job.id)]},
        )

        assert response.status_code == 302
        assert not GenerationJob.objects.filter(pk=job.id).exists()
        follow = client.get(response["Location"]).content.decode()
        assert "Deleted 1 generation." in follow

    def test_invalid_and_foreign_ids_are_skipped_silently(self, client):
        ann, bob = make_user(), make_user()
        mine = GenerationJob.objects.create(
            operation="txt2img",
            params={"prompt": "mine"},
            engine="stubengine", model_id="stub.safetensors",
            model_fingerprint="stubengine:stub.safetensors:None",
            status=GenerationJob.Status.DONE,
            **owner_fields(user_principal(ann)),
        )
        theirs = GenerationJob.objects.create(
            operation="txt2img",
            params={"prompt": "theirs"},
            engine="stubengine", model_id="stub.safetensors",
            model_fingerprint="stubengine:stub.safetensors:None",
            status=GenerationJob.Status.DONE,
            **owner_fields(user_principal(bob)),
        )

        with posture(POSTURE_PERSONAL):
            from tools.vision.tests._helpers import sign_in

            sign_in(client, ann)
            response = client.post(
                reverse("vision-jobs-delete-selected"),
                {"jobs": [str(mine.id), str(theirs.id), "not-a-uuid", ""]},
            )

        assert response.status_code == 302
        assert not GenerationJob.objects.filter(pk=mine.id).exists()
        assert GenerationJob.objects.filter(pk=theirs.id).exists()

    def test_empty_selection_says_nothing_selected_and_does_not_error(self, client):
        response = client.post(reverse("vision-jobs-delete-selected"))

        assert response.status_code == 302
        assert response["Location"] == reverse("vision-gallery")
        follow = client.get(response["Location"]).content.decode()
        assert "Nothing selected." in follow

    def test_an_off_site_next_falls_back_to_the_gallery(self, client):
        job, _ = _job_with_output()

        response = client.post(
            reverse("vision-jobs-delete-selected"),
            {"jobs": [str(job.id)], "next": "https://evil.example/steal"},
        )

        assert response["Location"] == reverse("vision-gallery")


@pytest.mark.django_db
class TestReusePrefill:
    def test_prefills_the_form_from_a_previous_job(self, client):
        job, _ = _job_with_output(prompt="reuse me", seed=7)

        response = client.get(reverse("vision-create"), {"reuse": str(job.id)})
        form = response.context["form"]

        assert form.fields["prompt"].initial == "reuse me"
        assert form.fields["steps"].initial == 20
        assert form.fields["seed"].initial == 7

    def test_an_unknown_job_id_is_ignored_not_an_error(self, client):
        response = client.get(
            reverse("vision-create"), {"reuse": "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"}
        )
        assert response.status_code == 200

    def test_a_malformed_job_id_is_ignored(self, client):
        assert client.get(reverse("vision-create"), {"reuse": "not-a-uuid"}).status_code == 200


@pytest.mark.django_db
class TestInputFile:
    """A stored input is served exactly the way a stored output is --
    strictly by primary key, so the request never supplies a path."""

    def _job_with_input(self, tmp_path):
        output = stored_output(tmp_path)
        source = tmp_path / "init_image-beach.png"
        source.write_bytes(PNG)
        return JobInput.objects.create(
            job=output.job, param_key="init_image", path=str(source), media_type="image/png"
        )

    def test_it_streams_the_stored_file(self, client, tmp_path):
        job_input = self._job_with_input(tmp_path)
        response = client.get(reverse("vision-input-file", args=[job_input.id]))
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert b"".join(response.streaming_content) == PNG

    def test_download_forces_an_attachment(self, client, tmp_path):
        job_input = self._job_with_input(tmp_path)
        response = client.get(reverse("vision-input-file", args=[job_input.id]) + "?download=1")
        assert "attachment" in response["Content-Disposition"]

    def test_a_file_no_longer_on_disk_is_a_404_that_says_why(self, client, tmp_path):
        job_input = self._job_with_input(tmp_path)
        Path(job_input.path).unlink()
        response = client.get(reverse("vision-input-file", args=[job_input.id]))
        assert response.status_code == 404

    def test_an_unknown_id_is_a_404(self, client):
        assert client.get(reverse("vision-input-file", args=[9999])).status_code == 404

    def test_a_non_image_media_type_is_clamped_to_octet_stream(self, client, tmp_path):
        """A stored file's `media_type` is whatever was recorded at upload
        time (T4's clamp lives at store time); this view must not trust it
        blindly when serving it back -- anything outside `image/*` is
        served as `application/octet-stream` so a browser never renders
        untrusted bytes as HTML."""
        output = stored_output(tmp_path)
        source = tmp_path / "init_image-payload.html"
        source.write_bytes(b"<script>alert(1)</script>")
        job_input = JobInput.objects.create(
            job=output.job, param_key="init_image", path=str(source), media_type="text/html"
        )
        response = client.get(reverse("vision-input-file", args=[job_input.id]))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/octet-stream"


@pytest.mark.parametrize("stored_type", ["image/svg+xml", "image/svg+xml; charset=utf-8"])
def test_an_svg_media_type_is_never_named_in_a_content_type_header(stored_type, tmp_path):
    """C-01. `image/*` is not an allowlist: `image/svg+xml` is an image
    type a browser renders as a DOCUMENT -- scripts and all -- in the
    origin that fetched it. A row carrying it (however it got there:
    every row written before this fix stored the uploading client's own
    `Content-Type` verbatim) must be served as a download, not as markup."""
    stored = tmp_path / "payload.svg"
    stored.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>')
    response = views._serve_stored_file(
        RequestFactory().get("/"), str(stored), stored_type, "gone")
    assert response.headers["Content-Type"] == "application/octet-stream"


def test_an_ordinary_png_is_still_served_as_a_png(tmp_path):
    """The other direction: the clamp must not turn every image into a
    download. A gate that refuses everything is not a gate."""
    stored = tmp_path / "ok.png"
    stored.write_bytes(b"\x89PNG\r\n\x1a\n")
    response = views._serve_stored_file(
        RequestFactory().get("/"), str(stored), "image/png", "gone")
    assert response.headers["Content-Type"] == "image/png"


@pytest.mark.django_db
class TestUseInLinks:
    def test_the_gallery_offers_every_file_taking_mode_by_registration_alone(self, client, tmp_path):
        output = stored_output(tmp_path)
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": IMG2IMG}):
            body = client.get(reverse("vision-gallery")).content.decode()

        expected = (
            f"{reverse('vision-create')}?operation=img2img"
            f"&input_init_image=output:{output.id}"
        )
        assert expected in body
        assert "Use in Image to image" in body

    def test_a_mode_with_no_file_params_gets_no_link(self, client, tmp_path):
        stored_output(tmp_path)
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG}):
            body = client.get(reverse("vision-gallery")).content.decode()
        assert "Use in Text to image" not in body

    def test_the_gallery_links_come_from_the_shared_partial(self, client, tmp_path):
        """The gallery's own links must be the SAME markup the card renders,
        not a surviving second copy."""
        stored_output(tmp_path)

        response = client.get(reverse("vision-gallery"))

        assert "vision/_output_actions.html" in [t.name for t in response.templates]
