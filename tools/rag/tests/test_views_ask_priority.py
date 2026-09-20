"""C-4: `AskView`'s client-supplied `priority` never reaches `enqueue()`
-- for ANY caller. Review round 1: an earlier version of this module
(and of `models.contracts.queue.resolve_client_priority`) let a
signed-in administrator's own request-body value through; the
orchestrator reversed that -- queue priority is derived once by the
queue column's policy function, never chosen by a request, and an
administrator who wants `rag.ask` jobs to run at a different priority
uses the Queue page's own settings form instead.

NEW MODULE (constraint 26, round-2's #18): `tools/rag/tests/` is a whole
directory another session is editing, so this adds a module rather than
appending to `test_views_ask.py`'s own `TestAskEnqueue` -- which this
file reads for its posting helper and fixtures (`post_ask`,
`model_available`, `client`) and never edits. (Two of `TestAskEnqueue`'s
OWN tests pinned the pre-fix behaviour and were retargeted in this same
change, under a constraint-26 waiver -- see that class for the rename.)

`AskView`'s existing validation (parse, range-check, 400 on a bad value)
is `test_views_ask.py::TestAskEnqueue::test_invalid_priority_returns_
400_and_never_enqueues`'s job and is not re-pinned wholesale here; this
module's one new 400 case (below) only proves that validation still runs
BEFORE the drop, not that every bad-value shape 400s.

Every test mocks `tools.rag.views.enqueue`/`get_job` (the queue seam),
exactly as `TestAskEnqueue` does, and layers the class's `model_available`
autouse fixture so the pre-check (`resolve`/`get_engine`) always
succeeds -- never a real queue row, never a real worker, never Ollama.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from identity.contracts.postures import POSTURE_OPEN, POSTURE_PERSONAL
from tools.rag.tests._helpers import (  # noqa: F401 -- `client` is a fixture, discovered by name
    client, make_admin, make_user, model_available, post_ask, posture, sign_in,
)


@pytest.mark.django_db
class TestC4PriorityIsQueuePolicyNotRequestData:
    """`models.contracts.queue.resolve_client_priority` is the drop;
    these are its behaviour through the one endpoint that calls it, for
    every kind of caller."""

    @pytest.fixture(autouse=True)
    def _model_available(self):
        yield from model_available()

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_a_member_supplied_priority_is_ignored(self, mock_enqueue, mock_get_job, client):
        mock_enqueue.return_value = 1
        mock_get_job.return_value = SimpleNamespace(position=1, priority=100)

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_user())
            post_ask(client, {"question": "hi", "priority": 1})

        assert mock_enqueue.call_args.kwargs["priority"] is None

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_an_administrators_supplied_priority_is_ignored_too(
        self, mock_enqueue, mock_get_job, client
    ):
        """Review round 1: NO caller-chosen priority survives, including
        a signed-in superuser's own. An administrator who wants `rag.ask`
        jobs to run at a different priority uses the Queue page's own
        settings form -- audited, and applied to the job kind as a
        whole -- not this field."""
        mock_enqueue.return_value = 1
        mock_get_job.return_value = SimpleNamespace(position=1, priority=100)

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            post_ask(client, {"question": "hi", "priority": 1})

        assert mock_enqueue.call_args.kwargs["priority"] is None

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_an_anonymous_caller_on_an_open_box_is_ignored_too(
        self, mock_enqueue, mock_get_job, client
    ):
        """D-2 proved this route accepts anonymous cross-site POSTs; the
        open posture must not be the posture where the field works --
        which it never is now, for anyone, but this is the scenario the
        finding was actually about, so it keeps its own case."""
        mock_enqueue.return_value = 1
        mock_get_job.return_value = SimpleNamespace(position=1, priority=100)

        with posture(POSTURE_OPEN):
            post_ask(client, {"question": "hi", "priority": 1})

        assert mock_enqueue.call_args.kwargs["priority"] is None

    @patch("tools.rag.views.enqueue")
    def test_a_non_integer_priority_is_still_a_400(self, mock_enqueue, client):
        """The existing validation is not weakened -- a member's bad
        value is still a bad value, it is simply also dropped once it
        DOES parse. Validation runs before the drop, so this 400 fires
        regardless of what would have happened to a well-formed value."""
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_user())
            response = post_ask(client, {"question": "hi", "priority": "not-a-number"})

        assert response.status_code == 400
        assert response.json()["error"] == "Priority must be a whole number."
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_the_response_still_reports_the_resolved_priority(
        self, mock_enqueue, mock_get_job, client
    ):
        """A member's own `priority: 1` is dropped (the assertion
        above), but the 202 body's `"priority"` key is unchanged shape:
        it reports the job's RESOLVED priority (`JobStatus.priority`,
        off `get_job`) -- here deliberately a different number than the
        member sent, proving the response echoes what the queue actually
        picked, never the client's own dropped request."""
        mock_enqueue.return_value = 1
        mock_get_job.return_value = SimpleNamespace(position=1, priority=55)

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_user())
            response = post_ask(client, {"question": "hi", "priority": 1})

        assert response.status_code == 202
        assert response.json()["priority"] == 55
