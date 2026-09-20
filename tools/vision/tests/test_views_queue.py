"""Unit tests for the queued placeholder card and its poll endpoint.

The queue itself is stubbed at `tools.vision.views.get_job` -- the seam
this module actually calls. What the queue does with a job is
`models/queue/tests/`' business; what the PAGE does with the queue's
answer is this file's.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from foundation.format import format_timecode
from identity.contracts.postures import POSTURE_ENTERPRISE
from models.queue.models import QUEUED, InferenceJob
from tools.vision.jobs import JOB_KIND
from tools.vision.models import GenerationJob
from tools.vision.tests._helpers import (
    clear_bindings, make_admin, posture, seed_sweep_posture, sign_in, stored_output,
)

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    """Module-level, like every other vision test module's copy: both
    classes below need it, and a per-class duplicate would be a second
    place to keep the same line."""
    clear_bindings()


class _Status:
    """The shape `models.contracts.queue.get_job` returns (console-side
    `JobStatus`), reduced to what this page reads."""

    def __init__(
        self, state="queued", position=1, result=None, error="", summary="", created_at=None,
    ):
        self.state = state
        self.position = position
        self.result = result
        self.error = error
        self.summary = summary
        self.created_at = created_at


@pytest.mark.django_db
class TestQueueJobStatus:
    def test_a_queued_job_renders_the_placeholder_with_its_position(self, client):
        with patch("tools.vision.views.get_job", return_value=_Status(position=3)):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert response.status_code == 200
        body = response.content.decode()
        assert "position 3" in body
        assert reverse("vision-queue-status", args=[7]) in body

    def test_the_placeholder_shows_elapsed_since_submission(self, client):
        """Fix round 2026-08-25: the platform-side wait was invisible on
        this card before a `GenerationJob` row exists at all --
        `job_status.created_at` (the `InferenceJob`'s own enqueue stamp,
        the only clock there is here) now renders as `In queue <mm:ss>`."""
        submitted = timezone.now() - timedelta(seconds=45)
        with patch(
            "tools.vision.views.get_job",
            return_value=_Status(position=2, created_at=submitted),
        ):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        body = response.content.decode()
        assert "In queue" in body
        assert format_timecode(45) in body

    def test_a_running_job_still_polls(self, client):
        with patch("tools.vision.views.get_job", return_value=_Status(state="running", position=None)):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert 'data-job-poll' in response.content.decode()

    def test_the_placeholder_becomes_the_real_card_as_soon_as_the_row_exists(
        self, client, tmp_path
    ):
        """The whole point of `queue_job_id`: the swap happens while the
        generation is running, not when the queue job finishes."""
        job = stored_output(tmp_path).job
        GenerationJob.objects.filter(pk=job.pk).update(queue_job_id=7)

        with patch("tools.vision.views.get_job", return_value=_Status(state="running")):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        body = response.content.decode()
        assert f"job-{job.id}" in body
        assert "a lighthouse" in body

    def test_a_finished_queue_job_finds_the_generation_through_its_result(
        self, client, tmp_path
    ):
        """The fallback for a row written before correlation existed, or a
        result read after the row was found by id: the result names the
        job it created."""
        job = stored_output(tmp_path).job
        with patch(
            "tools.vision.views.get_job",
            return_value=_Status(state="succeeded", position=None, result={"job_id": str(job.id)}),
        ):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert f"job-{job.id}" in response.content.decode()

    def test_a_failed_queue_job_shows_its_error_and_stops_polling(self, client):
        with patch(
            "tools.vision.views.get_job",
            return_value=_Status(state="failed", position=None, error="No model assigned"),
        ):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        body = response.content.decode()
        assert "No model assigned" in body
        assert "data-job-poll" not in body

    def test_a_pruned_queue_row_never_hides_a_live_generation(self, client, tmp_path):
        """Retention prunes terminal queue rows on every enqueue, and a
        queue job can succeed (`timed_out`) while its generation is still
        running. The generation is the durable record, so it is looked up
        first and a vanished queue row is never reported as a removed
        generation."""
        job = stored_output(tmp_path).job
        GenerationJob.objects.filter(pk=job.pk).update(queue_job_id=7)

        with patch("tools.vision.views.get_job", return_value=None) as mock_get_job:
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert response.status_code == 200
        assert f"job-{job.id}" in response.content.decode()
        mock_get_job.assert_not_called()

    def test_an_unknown_queue_job_with_no_generation_is_a_404(self, client):
        with patch("tools.vision.views.get_job", return_value=None):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert response.status_code == 404  # nothing in the DB, nothing in the queue

    def test_an_unreachable_queue_reports_503_without_500ing(self, client):
        from models.contracts.queue import QueueUnavailable

        with patch("tools.vision.views.get_job", side_effect=QueueUnavailable()):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert response.status_code == 503
        assert "migrations" in response.content.decode()

    def test_a_malformed_job_id_in_a_result_does_not_500(self, client):
        with patch(
            "tools.vision.views.get_job",
            return_value=_Status(state="succeeded", position=None, result={"job_id": "not-a-uuid"}),
        ):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert response.status_code == 200


@pytest.mark.django_db
class TestCreatePageQueuedPlaceholder:
    """The no-JS path: a plain POST redirects back with `?queued=<id>`, and
    the page has to show something for it."""

    def test_the_placeholder_is_rendered_for_a_live_queue_job(self, client):
        with patch("tools.vision.views.get_job", return_value=_Status(position=2)):
            response = client.get(reverse("vision-create") + "?queued=7")

        assert "position 2" in response.content.decode()

    def test_no_placeholder_once_the_generation_row_exists(self, client, tmp_path):
        """The Recent list is already showing the real card; a second card
        for the same submission would be a lie about how much is queued.

        Asserted by ELEMENT ID, never by counting `data-job-poll`: the
        literal appears five more times inside create.html's own polling
        script, and a finished job's card carries none at all."""
        job = stored_output(tmp_path).job
        GenerationJob.objects.filter(pk=job.pk).update(queue_job_id=7)
        with patch("tools.vision.views.get_job", return_value=_Status(state="running")):
            response = client.get(reverse("vision-create") + "?queued=7")

        body = response.content.decode()
        assert 'id="queue-job-7"' not in body
        assert f'id="job-{job.id}"' in body

    def test_a_stale_or_malformed_queued_parameter_leaves_a_normal_page(self, client):
        with patch("tools.vision.views.get_job", return_value=None):
            assert client.get(reverse("vision-create") + "?queued=7").status_code == 200
        assert client.get(reverse("vision-create") + "?queued=nonsense").status_code == 200


@pytest.mark.django_db
class TestQueuedCardNamesTheWork:
    """The placeholder used to say "Image generation" -- the job KIND --
    while the operator waited. It now says what they typed, taken from the
    queue's own registered summarizer, so the XHR path, the no-JS redirect
    path and every poll all read from ONE source."""

    def test_the_placeholder_shows_the_prompt(self, client):
        with patch(
            "tools.vision.views.get_job",
            return_value=_Status(summary="a lighthouse at dusk"),
        ):
            body = client.get(f"{reverse('vision-create')}?queued=7").content.decode()

        assert "a lighthouse at dusk" in body

    def test_it_falls_back_to_the_job_kind_label_with_no_summary(self, client):
        with patch("tools.vision.views.get_job", return_value=_Status(summary="")):
            body = client.get(f"{reverse('vision-create')}?queued=7").content.decode()

        # `tools/vision/apps.py` registers the kind with this label.
        assert "Generate an image" in body

    def test_the_placeholder_wears_a_chip(self, client):
        with patch("tools.vision.views.get_job", return_value=_Status(summary="x")):
            body = client.get(f"{reverse('vision-create')}?queued=7").content.decode()

        assert '<span class="chip chip-queued">' in body


@pytest.mark.django_db
class TestTheQueuedCardHidesContentWithAnAffordance:
    """MINOR (fix round 1): a withheld queued card must not just say
    "Content hidden." -- it must say why and where to change it, the
    same affordance `models/queue/templates/jobs/queue.html` carries.

    Uses a REAL `InferenceJob` row (no `get_job` mock) so `_queue_card_
    context` has a real payload to test `may_read_job_content` against
    -- the whole point being that a job's OWNER, not just its existence,
    decides whether the content shows."""

    def test_an_admin_with_the_setting_off_sees_the_affordance(self, client):
        job = InferenceJob.objects.create(
            kind=JOB_KIND, priority=100, state=QUEUED,
            payload={"operation": "txt2img", "params": {"prompt": "a private prompt"},
                     "actor_kind": "user", "actor_key": "99999999"},
        )
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, make_admin())
            body = client.get(reverse("vision-queue-status", args=[job.pk]), **XHR) \
                .content.decode()
        assert "a private prompt" not in body
        assert "Content hidden" in body
        assert "admin_sees_content" in body
        assert reverse("identity-settings") in body
