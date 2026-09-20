"""Unit tests for `GET /vision/jobs/strip/` -- the create page's Recent
region, discoverable on its own (T1, 2026-09-16).

The create page's own Recent list is `test_views_create.py`'s business;
this module is only the standalone fragment endpoint the page's own
inline script polls for a job it did not itself insert into the DOM, and
the page-source pins that prove create.html actually wires the poll up.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from identity.access import owner_fields
from identity.contracts.postures import POSTURE_PERSONAL
from models.contracts.engines import ENGINES
from models.registry.models import ModelConnection
from tools.vision.models import GenerationJob
from tools.vision.tests._helpers import (
    StubEngine, clear_bindings, make_user, posture, sign_in, user_principal,
)


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


def _engine():
    """`stubengine` registered as a real adapter, for the one test whose
    picked connection must actually resolve (`resolve_connection_named`
    calls `get_engine(connection.engine)`) -- the same helper
    `test_views_create.py` defines locally for its own identical need."""
    return patch.dict(ENGINES, {"stubengine": StubEngine()})


def _job(status=GenerationJob.Status.DONE, prompt="a lighthouse", seed=42, **extra):
    """A minimal job, exactly the shape `_job_card.html` needs -- no
    files on disk, since none of this module's assertions touch an
    output's bytes."""
    fields = dict(
        operation="txt2img",
        params={
            "prompt": prompt, "negative_prompt": "", "width": 512, "height": 512,
            "steps": 20, "cfg_scale": 7.0, "seed": seed, "sampler": "euler",
            "scheduler": "normal", "batch_size": 1,
        },
        seed=seed, engine="stubengine", model_id="stub.safetensors",
        model_fingerprint="stubengine:stub.safetensors:None",
        status=status,
    )
    fields.update(extra)
    return GenerationJob.objects.create(**fields)


@pytest.mark.django_db
class TestJobsStripFragment:
    def test_a_visible_job_renders(self, client):
        job = _job()

        response = client.get(reverse("vision-jobs-strip"))
        body = response.content.decode()

        assert response.status_code == 200
        assert f'id="job-{job.id}"' in body
        assert "a lighthouse" in body

    def test_a_job_invisible_to_the_principal_does_not_render(self, client):
        """Mirrors `test_views_gallery.py`'s own foreign-job pin: the
        Recent strip is the same `visible_jobs(principal)` narrowing the
        gallery and the create page's own render already use (IA-1), so a
        generation belonging to someone else must not appear here either,
        with no oracle distinguishing "not mine" from "does not exist"."""
        ann, bob = make_user(), make_user()
        theirs = _job(prompt="theirs", **owner_fields(user_principal(bob)))

        with posture(POSTURE_PERSONAL):
            sign_in(client, ann)
            response = client.get(reverse("vision-jobs-strip"))

        body = response.content.decode()
        assert response.status_code == 200
        assert f'id="job-{theirs.id}"' not in body
        assert "Nothing generated yet" in body

    def test_a_non_terminal_jobs_card_carries_the_poll_attribute(self, client):
        job = _job(status=GenerationJob.Status.RUNNING)

        body = client.get(reverse("vision-jobs-strip")).content.decode()

        assert f'data-job-poll="{reverse("vision-job-status", args=[job.id])}"' in body

    def test_a_terminal_jobs_card_carries_no_poll_attribute(self, client):
        job = _job(status=GenerationJob.Status.DONE)

        body = client.get(reverse("vision-jobs-strip")).content.decode()

        assert "data-job-poll" not in body
        assert f'id="job-{job.id}"' in body

    def test_an_empty_list_renders_the_empty_state(self, client):
        response = client.get(reverse("vision-jobs-strip"))

        assert response.status_code == 200
        assert 'id="jobs-empty"' in response.content.decode()
        assert "Nothing generated yet" in response.content.decode()

    def test_selected_connection_is_echoed_onto_the_cards_poll_url(self, client):
        """Mirrors `test_views_create.py`'s own
        `test_a_picked_connections_running_card_polls_with_that_
        connection`: the same `?connection=` a card polls under on the
        create page must survive on this endpoint's answer too, or a
        card discovered here would silently revert to the role binding
        the moment it started polling."""
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.safetensors", capabilities=["image-generation"],
        )
        job = _job(status=GenerationJob.Status.RUNNING)

        with _engine():
            body = client.get(
                reverse("vision-jobs-strip"), {"connection": str(connection.pk)}
            ).content.decode()

        expected = (
            f'data-job-poll="{reverse("vision-job-status", args=[job.id])}'
            f'?connection={connection.pk}"'
        )
        assert expected in body

    def test_no_connection_param_means_no_connection_query_on_the_poll_url(self, client):
        job = _job(status=GenerationJob.Status.RUNNING)

        body = client.get(reverse("vision-jobs-strip")).content.decode()

        assert f'data-job-poll="{reverse("vision-job-status", args=[job.id])}"' in body
        assert "connection=" not in body


@pytest.mark.django_db
class TestJobsStripFingerprint:
    """Fix round 2026-09-16 (review finding 1): the discovery poll compares
    a FINGERPRINT, never the fragment's rendered text -- `_jobs_strip.html`
    renders with different incidental whitespace depending on whether it
    is included inline (create.html) or standalone (this endpoint), so a
    text compare "changed" on every tick regardless of whether a job
    actually had, forcing an unconditional swap that detached the cards
    `watch()` had just attached at load."""

    def test_the_response_carries_a_fingerprint_header(self, client):
        _job()

        response = client.get(reverse("vision-jobs-strip"))

        assert response["X-Strip-Fingerprint"]

    def test_the_header_matches_the_create_pages_own_wrapper_attribute(self, client):
        """Same DB state, same principal, same `views._strip_fingerprint`
        call underneath both renders -- the two MUST agree, or the
        discovery poll's very first comparison (attribute-from-load vs.
        header-from-poll) would be comparing two different things."""
        _job()

        strip_response = client.get(reverse("vision-jobs-strip"))
        create_body = client.get(reverse("vision-create")).content.decode()

        fp_header = strip_response["X-Strip-Fingerprint"]
        assert fp_header
        assert f'data-strip-fp="{fp_header}"' in create_body

    def test_the_fingerprint_changes_when_a_jobs_status_changes(self, client):
        job = _job(status=GenerationJob.Status.RUNNING)

        before = client.get(reverse("vision-jobs-strip"))["X-Strip-Fingerprint"]
        job.status = GenerationJob.Status.DONE
        job.save(update_fields=["status"])
        after = client.get(reverse("vision-jobs-strip"))["X-Strip-Fingerprint"]

        assert before != after

    def test_the_fingerprint_changes_when_a_jobs_output_count_changes(self, client, tmp_path):
        from tools.vision.models import GeneratedOutput

        job = _job(status=GenerationJob.Status.DONE)

        before = client.get(reverse("vision-jobs-strip"))["X-Strip-Fingerprint"]
        GeneratedOutput.objects.create(
            job=job, index=0, path=str(tmp_path / "a.png"), media_type="image/png",
            width=512, height=512,
        )
        after = client.get(reverse("vision-jobs-strip"))["X-Strip-Fingerprint"]

        assert before != after


class _Status:
    """The shape `models.contracts.queue.get_job` returns (console-side
    `JobStatus`), reduced to what the placeholder card reads -- the same
    minimal stand-in `test_views_queue.py` defines for its own module."""

    def __init__(self, state="queued", position=1, result=None, error="", summary="",
                 created_at=None):
        self.state = state
        self.position = position
        self.result = result
        self.error = error
        self.summary = summary
        self.created_at = created_at


@pytest.mark.django_db
class TestCreatePageDiscoveryWiring:
    """Page-source pins on `create.html` itself -- these prove the page
    actually wires the discovery poll up, not just that the endpoint it
    polls answers correctly."""

    def test_the_jobs_strip_wrapper_exists(self, client):
        body = client.get(reverse("vision-create")).content.decode()

        assert 'id="jobs-strip"' in body

    def test_the_wrapper_carries_the_fingerprint_attribute(self, client):
        """The discovery poll's starting point (fix round 2026-09-16,
        review finding 1) -- without it the poll would have no fingerprint
        to compare its first response's header against."""
        body = client.get(reverse("vision-create")).content.decode()

        assert 'data-strip-fp="' in body

    def test_the_strip_url_literal_appears_in_the_script(self, client):
        body = client.get(reverse("vision-create")).content.decode()

        assert reverse("vision-jobs-strip") in body

    def test_the_orphan_guard_is_present_in_the_script(self, client):
        """Fix round 2026-09-16, review finding 1: any `watch()` loop whose
        card has left the DOM (this swap, or any future one) must
        self-terminate on its own next tick rather than polling a detached
        node all the way to the ceiling."""
        body = client.get(reverse("vision-create")).content.decode()

        assert "card.isConnected" in body

    def test_the_queued_placeholder_sits_outside_the_wrapper(self, client):
        """The `?queued=` placeholder must never be inside the region the
        discovery poll replaces wholesale -- it names a submission with
        no `GenerationJob` row yet, so a swap that ate it would blank the
        one card a no-JS redirect just landed the operator on."""
        with patch("tools.vision.views.get_job", return_value=_Status(position=2)):
            body = client.get(reverse("vision-create") + "?queued=7").content.decode()

        assert 'id="queue-job-7"' in body
        placeholder_open = body.index('id="queue-job-7"')
        strip_open = body.index('id="jobs-strip"')
        assert placeholder_open < strip_open

    def test_poll_until_terminal_call_count_grew_by_exactly_the_discovery_call(self):
        """Before T1 (2026-09-16), `create.html` called `pollUntilTerminal(`
        exactly once, from `watch()`. This pins that the discovery poll
        added exactly one more call site, not a second hand-rolled loop
        (`foundation/ops/tests/test_shared_poller.py` already gates the
        `setTimeout`/`setInterval` half of that rule)."""
        from pathlib import Path

        source = Path("tools/vision/templates/vision/create.html").read_text()

        assert source.count("pollUntilTerminal(") == 2

    def test_no_raw_settimeout_or_setinterval_was_added(self):
        from pathlib import Path

        source = Path("tools/vision/templates/vision/create.html").read_text()

        assert "setTimeout(" not in source
        assert "setInterval(" not in source
