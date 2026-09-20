"""`tools.vision.visibility` -- generated images are content, not rows.

`_generation(...)`/`_output(...)` build the minimum `GenerationJob`/
`GeneratedOutput` rows these tests need -- no engine, no queue, no disk
file (`tools.vision.tests._helpers.stored_output` is for tests that
actually serve bytes; these never do).
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from identity.access import owner_fields
from identity.contracts.postures import POSTURE_OPEN, POSTURE_PERSONAL
from identity.contracts.principals import OPEN_PRINCIPAL
from models.contracts.operations import get_operation
from tools.vision.models import GeneratedOutput, GenerationJob, JobInput
from tools.vision.tests._helpers import (
    make_admin, make_user, posture, seed_sweep_posture, sign_in, stored_output, user_principal,
)
from tools.vision.views import _stored_input_context
from tools.vision.visibility import may_read_job, visible_jobs

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


def _generation(**overrides) -> GenerationJob:
    fields = dict(
        operation="txt2img",
        params={"prompt": "a lighthouse"},
        engine="stubengine",
        model_id="stub.safetensors",
        model_fingerprint="stubengine:stub.safetensors:None",
        status=GenerationJob.Status.DONE,
    )
    fields.update(overrides)
    return GenerationJob.objects.create(**fields)


def _output(**overrides) -> GeneratedOutput:
    job = overrides.pop("job", None) or _generation()
    fields = dict(job=job, index=0, path="/tmp/does-not-matter.png", media_type="image/png")
    fields.update(overrides)
    return GeneratedOutput.objects.create(**fields)


def _job_input(**overrides) -> JobInput:
    job = overrides.pop("job", None) or _generation()
    fields = dict(job=job, param_key="init_image", path="/tmp/does-not-matter.png",
                  media_type="image/png")
    fields.update(overrides)
    return JobInput.objects.create(**fields)


class TestGeneratedImagesAreContent:
    def test_a_member_sees_only_their_own_generations(self):
        ann, bob = make_user(), make_user()
        mine = _generation(**owner_fields(user_principal(ann)))
        theirs = _generation(**owner_fields(user_principal(bob)))
        with posture(POSTURE_PERSONAL):
            ids = {j.pk for j in visible_jobs(user_principal(ann))}
        assert mine.pk in ids and theirs.pk not in ids

    def test_an_admin_with_the_setting_off_sees_none_of_them(self):
        """A generated image is CONTENT. An administrator has no job
        that requires looking at somebody's pictures."""
        admin, bob = make_admin(), make_user()
        theirs = _generation(**owner_fields(user_principal(bob)))
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            assert may_read_job(user_principal(admin), theirs) is False
        with posture(POSTURE_PERSONAL, admin_sees_content=True):
            assert may_read_job(user_principal(admin), theirs) is True

    def test_the_four_row_addressed_routes_answer_404_not_403(self, client):
        """`vision-job-status`, `vision-job-delete`, `vision-output-file`
        and `vision-input-file`. 403 on a row-addressed URL confirms the
        row exists."""
        ann, bob = make_user(), make_user()
        theirs = _generation(**owner_fields(user_principal(bob)))
        output = _output(job=theirs)
        job_input = _job_input(job=theirs)
        with posture(POSTURE_PERSONAL):
            sign_in(client, ann)
            assert client.get(
                reverse("vision-job-status", args=[theirs.pk])).status_code == 404
            assert client.post(
                reverse("vision-job-delete", args=[theirs.pk])).status_code == 404
            assert client.get(
                reverse("vision-output-file", args=[output.pk])).status_code == 404
            assert client.get(
                reverse("vision-input-file", args=[job_input.pk])).status_code == 404

    def test_the_gallery_and_the_recent_list_both_narrow(self, client):
        """Two listings, one rule. The create page's Recent list reads
        the same manager the gallery does, and a filter applied to one
        and not the other is exactly the drift a single visibility
        module exists to prevent."""
        ann, bob = make_user(), make_user()
        theirs = _generation(**owner_fields(user_principal(bob)))
        with posture(POSTURE_PERSONAL):
            sign_in(client, ann)
            gallery = client.get(reverse("vision-gallery")).content.decode()
            create = client.get(reverse("vision-create")).content.decode()
        # `vision/gallery.html`'s own empty-state copy ("Nothing generated
        # yet.") -- not a literal "no images" (the brief's paraphrase),
        # so this narrows the assertion to the real production copy
        # rather than a string the template never renders.
        assert "nothing generated yet" in gallery.lower()
        # `theirs.pk` (a UUID), not `bob.pk`: a small integer user pk can
        # coincidentally appear anywhere in the page's own markup (a
        # charset declaration, a CSS value) with no leak involved --
        # the UUID is the value that would ONLY be there if bob's job
        # leaked onto ann's Recent list.
        assert str(theirs.pk) not in create


class TestTheCarriedReferencePreviewIsGatedByVisibility:
    """Fix round 1, CRITICAL 1: `_stored_input_context` (the create
    page's "here is the image you carried in" preview) used to render a
    row for ANY reference that still resolved to a file on disk, with
    no principal check at all -- an existence oracle over another
    principal's images. `stored_input_exists` now gates it through
    `services._visible_referenced_row`."""

    def test_a_members_reference_to_anothers_output_previews_like_a_bogus_one(self, tmp_path):
        ann, bob = make_user(), make_user()
        theirs = stored_output(tmp_path)
        theirs.job.owner_kind = "user"
        theirs.job.owner_key = str(bob.pk)
        theirs.job.save(update_fields=["owner_kind", "owner_key"])
        operation = get_operation("img2img")
        with posture(POSTURE_PERSONAL):
            foreign = _stored_input_context(
                {"init_image": f"output:{theirs.id}"}, operation, user_principal(ann),
            )
            bogus = _stored_input_context(
                {"init_image": "output:999999"}, operation, user_principal(ann),
            )
        assert foreign == [] == bogus

    def test_the_owner_still_sees_their_own_preview(self, tmp_path):
        bob = make_user()
        mine = stored_output(tmp_path)
        mine.job.owner_kind = "user"
        mine.job.owner_key = str(bob.pk)
        mine.job.save(update_fields=["owner_kind", "owner_key"])
        operation = get_operation("img2img")
        with posture(POSTURE_PERSONAL):
            shown = _stored_input_context(
                {"init_image": f"output:{mine.id}"}, operation, user_principal(bob),
            )
        assert len(shown) == 1
        assert shown[0]["reference"] == f"output:{mine.id}"

    def test_an_open_box_is_unchanged(self, tmp_path):
        bob = make_user()
        theirs = stored_output(tmp_path)
        theirs.job.owner_kind = "user"
        theirs.job.owner_key = str(bob.pk)
        theirs.job.save(update_fields=["owner_kind", "owner_key"])
        operation = get_operation("img2img")
        # PINNED: this module's `_sweep` autouse fixture applies
        # `FARABUNKER_TEST_POSTURE` when the sweep sets it, and "an open
        # box" is specifically the OPEN posture's own claim --
        # `posture(...)` always wins over the sweep.
        with posture(POSTURE_OPEN):
            shown = _stored_input_context(
                {"init_image": f"output:{theirs.id}"}, operation, OPEN_PRINCIPAL,
            )
        assert len(shown) == 1
        assert shown[0]["reference"] == f"output:{theirs.id}"


class TestStagedInputPreviewIsAdminOnly:
    """IMPORTANT 2 (fix round 1): a STAGED `JobInput` (`job_id is None`)
    carries no owner column -- adding one is a fifth migration, out of
    IA-1's scope (`tools/vision/README.md` names this as a limitation).
    In every posture with accounts on, it is served to `is_admin` only;
    an open box is unaffected (`is_admin(OPEN_PRINCIPAL)` is always
    True)."""

    @staticmethod
    def _staged(tmp_path) -> JobInput:
        source = tmp_path / "staged.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n")
        return JobInput.objects.create(
            job=None, param_key="init_image", path=str(source), media_type="image/png",
        )

    def test_a_member_is_refused(self, client, tmp_path):
        staged = self._staged(tmp_path)
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_user())
            assert client.get(
                reverse("vision-input-file", args=[staged.pk])).status_code == 404

    def test_an_admin_may_still_preview_it(self, client, tmp_path):
        staged = self._staged(tmp_path)
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            assert client.get(
                reverse("vision-input-file", args=[staged.pk])).status_code == 200

    def test_an_open_box_is_unchanged(self, client, tmp_path):
        staged = self._staged(tmp_path)
        with posture(POSTURE_OPEN):
            assert client.get(
                reverse("vision-input-file", args=[staged.pk])).status_code == 200
