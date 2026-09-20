"""C-16: `vision/create.html` renders the shared poller.

A new module rather than an addition to `test_views_create.py`, per the
orchestrator FILE RULE for the hygiene sweep WP10 addendum, Task 43 --
that rule reserves this filename specifically so poller-behaviour tests
for this page do not have to compete with the rest of that (very large)
file's ownership.

This is the rendered-page half of the coverage; `foundation/ops/tests/
test_shared_poller.py` is the source-text half (the include line, the
call, and the absence of a hand-rolled loop in the template source), and
`test_views_create.py::test_the_preview_script_lives_inside_the_
generate_form` already pins the page's `<script>` count at 3 (the
generate-form preview script, the job-card poller, and this include).

There is no JS test runner in this repo, so a rendered-HTML check that
the include actually resolves -- and that this page's own scheduling
quirks (`startDelayMs`/`retryDelayMs`) are still passed through -- is
the strongest automated signal available that the swap did not silently
drop behaviour.
"""
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from models.contracts.engines import ENGINES
from models.contracts.roles import VISION_GENERATE_ROLE
from models.registry.models import ModelConnection, RoleBinding
from tools.vision.tests._helpers import StubEngine, clear_bindings


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


def _engine(healthy=True):
    engine = StubEngine(healthy=healthy)
    engine.list_choices = lambda endpoint, key: ()
    return patch.dict(ENGINES, {"stubengine": engine})


@pytest.mark.django_db
class TestCreatePagePoller:
    def test_the_shared_poller_is_included_and_the_page_calls_it(self, client):
        _bind()
        with _engine():
            response = client.get(reverse("vision-create"))

        assert response.status_code == 200
        body = response.content.decode()
        assert "window.pollUntilTerminal" in body
        assert "window.POLL_DEFAULTS" in body
        assert "pollUntilTerminal(" in body

    def test_the_pages_own_scheduling_quirks_are_still_passed_through(self, client):
        """This page's own first-poll delay and post-failure backoff
        (unlike the interval/ceilings, which now come from
        `POLL_DEFAULTS`) are per-call options -- ruled binding by the
        brief: `create.html`'s 2s first-poll delay survives as
        `startDelayMs`, and its 5s post-failure backoff as
        `retryDelayMs`."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        assert "startDelayMs: 2000" in body
        assert "retryDelayMs: 5000" in body

    def test_the_page_no_longer_declares_its_own_poll_or_watch_loop(self, client):
        """The teeth: `poll`/`tick` are gone, replaced by a `watch` that
        only calls the shared helper. `setTimeout`/`setInterval` absence
        is already pinned repo-wide by `foundation/ops/tests/
        test_shared_poller.py`; this checks the specific function names
        this page used to define."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        assert "function poll(" not in body
        assert "function watch(card) {" in body

    def test_give_up_leaves_an_honest_note_not_markLives_stale_claim(self, client):
        """Review finding: `markLive()` rewrites a card's `.no-js-note`
        to "Waiting for the engine…" as soon as polling starts. Before
        this fix, `onGiveUp` only dropped `data-job-poll` and left that
        sentence standing -- so a card that tripped the adopted ceiling
        (10 minutes, or 3 consecutive transport failures) froze on a
        claim that was now false: nothing is watching it any more.

        There is no JS runtime in this test suite, so this is a
        source-text pin on the rendered script, not a behavioural one:
        it can't prove `onGiveUp` actually WRITES the sentence at
        runtime, only that the honest sentence exists in the page's own
        script and reads differently from markLive's, so a reader (or a
        future edit) can't quietly let the two drift back together."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        give_up_sentence = "Stopped checking after a while — refresh to update."
        live_sentence = "Waiting for the engine…"
        assert give_up_sentence in body
        assert live_sentence in body
        assert give_up_sentence != live_sentence

    def test_the_page_tunes_its_own_give_up_ceiling_before_its_first_poll(self, client):
        """Review finding (whole-branch, Important #1): Images queues run
        much longer than Ask's, so this page keeps a give-up ceiling but a
        page-appropriate one, set on `window.POLL_DEFAULTS` before the
        page's own first `pollUntilTerminal(` call -- 60 minutes (vs.
        `_poller.html`'s 10-minute default) and 12 transport retries (vs.
        3), so a web-container restart mid-queue does not freeze the
        card. This is a source-text pin, matching the other tests in this
        module -- there is no JS runtime in this test suite."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        tuning_index = body.index("window.POLL_DEFAULTS.maxDurationMs = 60 * 60 * 1000;")
        retries_index = body.index("window.POLL_DEFAULTS.maxTransportRetries = 12;")
        first_poll_index = body.index("pollUntilTerminal(")

        assert tuning_index < first_poll_index
        assert retries_index < first_poll_index
