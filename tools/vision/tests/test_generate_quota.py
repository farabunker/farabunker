"""The per-principal queue quota, answered by the create page (C-7/H39).

`models.queue.backend.enqueue` refuses with `models.contracts.queue.
QueueQuotaExceeded` once an account already holds as many queued-or-
running jobs as `JobSettings.max_queued_per_principal` allows. Before
this module, `tools.vision.views.generate` had no clause for that name at
all, so the refusal fell into the route's broad `except Exception` and
came back as the 503 that means "the queue is broken" -- a lie about a
queue that is working exactly as the operator configured it, and one that
also threw away the operator's just-uploaded files on the way out.

THREE THINGS ARE PINNED HERE, and the first is the reason the other two
can be trusted:

1. **The clause ordering.** A 429 coming back at all is what proves the
   `except QueueQuotaExceeded` sits BEFORE the broad `except Exception`.
   Move it after, and every assertion in this module reads 503.
2. **The refusal SHAPE**, which is the route's own -- the create page
   re-rendered with the message as its banner for a plain POST, the small
   `_unavailable.html` fragment for an XHR -- and the banner text, which
   is the exception's own sentence verbatim rather than a paraphrase
   written here.
3. **The staged inputs survive.** Every other refusal on this path
   discards them, because nothing will ever claim those bytes. This one
   is the try-again-in-a-minute case.

A NEW MODULE, not an addition to `test_views_generate.py`: the vision
column's steward granted this change against a named file list, and no
existing vision test is edited by it. The `_bind`/`_engine`/`FORM`
fixtures below are a fresh copy of that module's own, the same way
`test_views_queue.py` and the queue column's `test_quota.py` each carry
their own copy rather than importing another module's privates.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from models.contracts.engines import ENGINES
from models.contracts.queue import QueueQuotaExceeded
from models.contracts.roles import VISION_GENERATE_ROLE
from models.registry.models import ModelConnection, RoleBinding
from tools.vision.models import JobInput
from tools.vision.tests._helpers import PNG, StubEngine, StubGenerator, clear_bindings

# The refusal `models.queue.backend._enforce_principal_quota` raises,
# copied verbatim rather than imported: this module's whole point is that
# the banner says what the queue said, so the string it compares against
# must be an independent statement of it, not the same expression.
QUOTA_MESSAGE = (
    "3 jobs are already queued or running for this account — "
    "the limit is 3. Wait for one to finish before starting another."
)

FORM = {
    "prompt": "a lighthouse", "negative_prompt": "", "width": "512", "height": "512",
    "steps": "20", "cfg_scale": "7", "seed": "42", "sampler": "euler",
    "scheduler": "normal", "batch_size": "1",
}

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


def _engine(choices=("euler",)):
    engine = StubEngine(StubGenerator(), healthy=True)
    engine.list_choices = lambda endpoint, key: choices if key == "sampler" else ()
    return patch.dict(ENGINES, {"stubengine": engine})


class TestC7TheQuotaRefusalIsAnswered:

    @patch("tools.vision.views.enqueue", side_effect=QueueQuotaExceeded(QUOTA_MESSAGE))
    def test_an_xhr_submission_gets_a_429_not_a_503(self, mock_enqueue, client):
        """The clause-ordering pin. `QueueQuotaExceeded` is an ordinary
        exception, so a route whose only handler for it is the broad
        `except Exception` answers 503 here instead."""
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        assert response.status_code == 429

    @patch("tools.vision.views.enqueue", side_effect=QueueQuotaExceeded(QUOTA_MESSAGE))
    def test_the_xhr_banner_is_the_queues_own_sentence(self, mock_enqueue, client):
        """Verbatim, not paraphrased: one place on this box knows what the
        cap is, and it is not this route."""
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        assert QUOTA_MESSAGE in response.content.decode()

    @patch("tools.vision.views.enqueue", side_effect=QueueQuotaExceeded(QUOTA_MESSAGE))
    def test_a_plain_post_re_renders_the_create_page_with_the_banner(
        self, mock_enqueue, client
    ):
        """The route's OWN refusal shape -- the same whole-page re-render
        `_queue_failure_response` gives a 503, at a different status. No
        new template, and no bare response: the operator lands back on the
        form they submitted, with their typing still in it."""
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM)

        assert response.status_code == 429
        body = response.content.decode()
        assert QUOTA_MESSAGE in body
        # The create form came back, not a bare error document.
        assert 'name="prompt"' in body
        assert "a lighthouse" in body

    @patch("tools.vision.views.enqueue", side_effect=QueueQuotaExceeded(QUOTA_MESSAGE))
    def test_the_staged_inputs_survive_for_the_retry(self, mock_enqueue, client, tmp_path):
        """The difference from every other refusal on this path. A quota
        refusal is the one case where trying again in a minute is exactly
        the right move, so the files the operator just attached are still
        staged when they do."""
        _bind()
        form = {**IMG2IMG_FORM, "init_image": SimpleUploadedFile("a.png", PNG)}
        with _engine(), override_settings(GENERATED_DIR=tmp_path):
            response = client.post(reverse("vision-generate"), form, **XHR)

        assert response.status_code == 429
        assert JobInput.objects.filter(job__isnull=True).count() == 1
