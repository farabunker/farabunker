"""The wire contract of the two Ask JSON endpoints, pinned independently
of the framework that serves them (C-55).

WRITTEN AGAINST DRF, KEPT ACROSS THE REWRITE. Every assertion here was
green before `rest_framework` was removed and is green after: status
codes, the exact JSON body keys, the `Content-Type` header, how a body
is parsed (JSON *and* form-encoded), and what a wrong method answers.
`tools/rag/tests/test_views_ask.py`'s Ask classes cover the *behaviour*;
this file covers the *transport*, which is the half a framework swap can
break silently.

THE TWO DELIBERATE CHANGES are marked `CHANGED AT C-55` inline, with the
reason: DRF's `{"detail": ...}` parse-error body becomes this platform's
`{"error": ...}`, and `/rag/ask/` stops being `csrf_exempt`.
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from identity.contracts.postures import POSTURE_PERSONAL
from models.queue.models import QUEUED, InferenceJob
from tools.rag.tests._helpers import model_available, post_ask, posture
from tools.rag.views import _JSON_PARSE_ERROR


@pytest.fixture(autouse=True)
def _model_available():
    yield from model_available()


@pytest.mark.django_db
class TestTheAskWireContract:
    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_a_json_post_is_accepted_and_answers_202_with_the_five_keys(
        self, mock_enqueue, mock_get_job, client
    ):
        """The success shape, verbatim: five keys, 202, application/json."""
        from types import SimpleNamespace

        mock_enqueue.return_value = 42
        mock_get_job.return_value = SimpleNamespace(position=3, priority=100)

        response = post_ask(client, {"question": "what does the library say?"})

        assert response.status_code == 202
        assert response["Content-Type"].startswith("application/json")
        body = json.loads(response.content)
        assert set(body) == {"job_id", "state", "position", "priority", "status_url"}
        assert body["state"] == "queued"
        assert body["status_url"] == reverse("rag-ask-status", args=[body["job_id"]])

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_a_FORM_ENCODED_post_is_accepted_too(self, mock_enqueue, mock_get_job, client):
        """D1. DRF's default parser list takes form-encoded bodies as
        well as JSON, and `identity/tests/test_route_matrix.py`'s
        `rag-ask` driver posts exactly that. A rewrite that only reads
        `request.body` as JSON would turn that matrix cell from 202 into
        400 -- which the matrix's `_ADMITTED` set would silently accept."""
        from types import SimpleNamespace

        mock_enqueue.return_value = 42
        mock_get_job.return_value = SimpleNamespace(position=3, priority=100)

        response = client.post(reverse("rag-ask"), {"question": "what does the library say?"})

        assert response.status_code == 202
        assert json.loads(response.content)["state"] == "queued"

    @pytest.mark.parametrize(("payload", "message"), [
        ({}, "'question' is required and must be a non-empty string."),
        ({"question": "   "}, "'question' is required and must be a non-empty string."),
        ({"question": 7}, "'question' is required and must be a non-empty string."),
        ({"question": "q", "priority": "soon"}, "Priority must be a whole number."),
        ({"question": "q", "priority": "0"}, "Priority must be a positive number."),
        ({"question": "q", "priority": "-3"}, "Priority must be a positive number."),
    ])
    def test_every_400_carries_one_error_key_and_that_exact_sentence(self, client, payload, message):
        """The refusal shape: 400, `{"error": <sentence>}`, nothing else.
        The sentences are operator copy and are asserted verbatim."""
        response = client.post(reverse("rag-ask"), data=json.dumps(payload),
                               content_type="application/json")
        assert response.status_code == 400
        assert json.loads(response.content) == {"error": message}

    def test_an_unparseable_json_body_is_a_400(self, client):
        """CHANGED AT C-55, deliberately. DRF answered
        `{"detail": "JSON parse error - ..."}`; the rewrite answers this
        platform's own `{"error": ...}`. `ask.html` reads
        `result.data.error || "Request failed."` and therefore rendered
        the generic fallback for DRF's body -- the new shape renders the
        real sentence. Status is 400 either way."""
        response = client.post(reverse("rag-ask"), data="{not json",
                               content_type="application/json")
        assert response.status_code == 400
        # OBSERVED AT STEP 3, against the unmodified DRF implementation:
        # `{"detail": "JSON parse error - Expecting property name enclosed
        # in double quotes: line 1 column 2 (char 1)"}`. Flipped here, at
        # Step 6, to this platform's own shape.
        assert json.loads(response.content) == {"error": _JSON_PARSE_ERROR}

    def test_a_GET_to_the_ask_endpoint_is_405(self, client):
        """D5. The status is the contract; the rewrite additionally makes
        the BODY json instead of DRF's own -- pinned here, not just the
        status, since a poller getting the verb wrong still has to be
        able to parse the answer."""
        response = client.get(reverse("rag-ask"))
        assert response.status_code == 405
        assert response["Content-Type"].startswith("application/json")
        assert json.loads(response.content) == {"error": "Method not allowed."}

    def test_a_POST_to_the_ask_status_endpoint_is_405(self, client):
        """D5's other half -- `AskJobStatusView` has the same in-body
        method guard as `AskView`, and it had no test at all before this
        one. Any `job_id` works: the method check runs before the job
        is ever looked up."""
        response = client.post(reverse("rag-ask-status", args=[999999]))
        assert response.status_code == 405
        assert response["Content-Type"].startswith("application/json")
        assert json.loads(response.content) == {"error": "Method not allowed."}

    def test_an_unknown_job_id_is_404_with_error_and_history_url(self, client):
        response = client.get(reverse("rag-ask-status", args=[999999]))
        assert response.status_code == 404
        assert response["Content-Type"].startswith("application/json")
        assert set(json.loads(response.content)) == {"error", "history_url"}

    def test_a_queued_job_reports_four_keys_and_an_iso_8601_Z_timestamp(self, client):
        """D4. `submitted_at` is a datetime, and the encoder is what
        renders it. Pinned as ISO-8601 ending in `Z` -- NOT to a
        microsecond count: DRF keeps six digits, `DjangoJSONEncoder`
        truncates to three, and no template on this box reads the field
        (no template reads this endpoint's JSON: the only `*.html` hits
        for these names are `jobs/queue.html`'s server-rendered
        `row.started_at` and a comment in `vision/_job_card.html`)."""
        job = InferenceJob.objects.create(
            kind="rag.ask", priority=100, state=QUEUED, payload={"question": "q"}
        )

        response = client.get(reverse("rag-ask-status", args=[job.pk]))

        body = json.loads(response.content)
        assert set(body) == {"state", "position", "priority", "submitted_at"}
        assert body["state"] == "queued"
        assert body["submitted_at"].endswith("Z")
        datetime.fromisoformat(body["submitted_at"].replace("Z", "+00:00"))  # parses


class TestTheAskEndpointAndCsrf:
    @pytest.mark.django_db
    def test_an_anonymous_post_with_no_csrf_token_is_refused(self, client):
        """CHANGED AT C-55, deliberately, and the reason the change is
        worth making. DRF enforced CSRF only for session-authenticated
        callers, so an anonymous cross-site POST carried no token
        requirement at all; Django's middleware now enforces it for
        every POST -- `CsrfViewMiddleware` covers this endpoint like
        every other POST on the box.

        `ask.html` is unaffected: it sends `X-CSRFToken` from the form's
        own `csrfmiddlewaretoken` (`getCsrfToken()`), which is why this
        was a gap being closed and not a client being broken.

        WAS WRITTEN RED ON PURPOSE (xfail, strict) against the DRF
        implementation, which answered 202 with no token at all; the
        marker is gone now that the rewrite makes it 403."""
        strict = Client(enforce_csrf_checks=True)
        with posture(POSTURE_PERSONAL):
            response = strict.post(reverse("rag-ask"),
                                   data=json.dumps({"question": "q"}),
                                   content_type="application/json")
        assert response.status_code == 403


def test_the_box_does_not_install_django_rest_framework():
    """C-55. A whole web framework was installed for a JSON encoder, a
    body parser and a method dispatcher, on a product whose whole point
    is an offline-first dependency footprint. Two pins, because either
    one alone can be satisfied while the other rots: the app is not
    installed, and the package is not required.

    NOT pinned: that `import rest_framework` fails. The package may
    legitimately still sit in a developer's long-lived virtualenv after
    this commit, and a pin that depends on somebody having rebuilt their
    venv is a pin that fails for the wrong reason."""
    from django.conf import settings

    assert "rest_framework" not in settings.INSTALLED_APPS
    assert not hasattr(settings, "REST_FRAMEWORK")
    requirements = (settings.BASE_DIR / "requirements.txt").read_text()
    assert "djangorestframework" not in requirements
    assert "rest_framework" not in requirements
