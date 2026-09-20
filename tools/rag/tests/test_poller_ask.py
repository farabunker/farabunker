"""C-16: `rag/ask.html` renders the shared poller.

A new module rather than an addition to `test_views_ask.py` -- that file
is held for another in-flight task in this sweep and must not be edited
(orchestrator FILE RULE, hygiene sweep WP10 addendum, Task 43). This is
the rendered-page half of the coverage; `foundation/ops/tests/
test_shared_poller.py` is the source-text half (the include line, the
call, and the absence of a hand-rolled loop in the template source).
There is no JS test runner in this repo, so a rendered-HTML check that
the include actually resolves is the strongest automated signal
available that the swap did not silently no-op (e.g. a typo'd template
name that Django would 500 on, but a check that never looked would not
catch).
"""
import pytest
from django.urls import reverse

from tools.rag.tests._helpers import (  # noqa: F401 -- `client` is a fixture, discovered by name
    client,
)


@pytest.mark.django_db
class TestAskPagePoller:
    def test_the_shared_poller_is_included_and_the_page_calls_it(self, client):
        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        body = response.content.decode()
        # The include resolved (Django would 404/500 on a bad template
        # name rather than silently drop it) and the page calls the
        # function it defines.
        assert "window.pollUntilTerminal" in body
        assert "window.POLL_DEFAULTS" in body
        assert "pollUntilTerminal(" in body

    def test_the_page_no_longer_declares_its_own_timing_constants(self, client):
        """The three hand-synchronised constants this page used to
        declare (`POLL_INTERVAL_MS`/`MAX_TRANSPORT_RETRIES`/
        `MAX_POLL_DURATION_MS`) live in `_poller.html`'s
        `POLL_DEFAULTS` now, not on this page."""
        response = client.get(reverse("rag-ask-page"))

        body = response.content.decode()
        assert "POLL_INTERVAL_MS" not in body
        assert "MAX_TRANSPORT_RETRIES" not in body
        assert "MAX_POLL_DURATION_MS" not in body
