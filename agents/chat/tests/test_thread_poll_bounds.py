"""The POLL LOOP'S BOUNDED RETRY -- every answer the page cannot act on
is counted against one ceiling, and the loop stops rather than ticking
for ever.

SPLIT OUT OF `test_thread.py` (merge of the queue memory-governance track
into chat-cluster). Neither side crossed
`foundation/ops/tests/test_column_boundaries.py`'s 2,100-line split
threshold alone -- chat-cluster's `test_thread.py` was 2,031 lines and the
queue track's 2,100 -- but the merge concatenated one side's single
re-wrapped import line with the other's seventy-line class and landed on
2,101. The exemption set was NOT grown: AGENTS.md non-negotiable 2 ends
"A dated exemption list gets its exempted code removed, not grown", and
the same branch already answered this exact question once, splitting
`test_thread_meter.py` out rather than keeping its entry (whole-branch
review I-5). This is that ruling applied a second time.

THE SEAM IS THE POLLER'S OWN EDGE, not a line count. Everything here is
about ONE fact -- the transport ceiling and what does and does not reset
it -- and it reads the RENDERED script for the same reason
`TestThePoller` in `test_thread.py` does: `POLL_INTERVAL_MS`,
`MAX_TRANSPORT_RETRIES` and `MAX_POLL_DURATION_MS` are interpolated by
the view, so the template source is not what ships. `TestThePoller`
stays where it is: it pins the constants ARRIVING on the page, which is
the thread page's own contract, while this module pins what the loop
DOES with them.

Every test below is a MOVE. No name changed, no assertion changed.

NO FARABUNKER_FEATURES OVERRIDE (test_mount.py), as in `test_thread.py`.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import make_conversation

pytestmark = pytest.mark.django_db


def _rendered_conversation_script(client, conversation) -> str:
    """The conversation page's rendered inline script, as text. The poll
    loop is asserted at the RENDERED level (not by reading the template
    file) because the numbers it depends on -- POLL_INTERVAL_MS,
    MAX_TRANSPORT_RETRIES, MAX_POLL_DURATION_MS -- are interpolated by
    the view, the same reason `test_thread.py`'s own `TestThePoller`
    reads a real GET rather than the template source."""
    body = client.get(
        reverse("chat-conversation", args=[conversation.id])
    ).content.decode()
    return body[body.index("<script"):body.rindex("</script>")]


class TestTheLoopCountsWhatItCannotActOn:
    """The uncounted branch: a PARSEABLE body carrying no recognized state
    fell into the forward-compatible final `else` and re-ticked silently
    until the duration ceiling, hours later. A 5xx with an unparseable
    body was already counted -- it rejects in response.json() and lands in
    .catch()."""

    def _final_else(self, script: str) -> str:
        """The forward-compatible `else` alone -- from the branch that
        follows the last recognized state to the `.catch()` that closes
        the chain -- so an assertion about it cannot be satisfied by
        `.catch()`'s own counting further down."""
        tail = script[script.index('data.state === "cancelled"'):]
        return tail[tail.index("} else {"):tail.index(".catch(function ()")]

    def test_an_unrecognized_state_increments_the_bounded_counter(self, client):
        branch = self._final_else(_rendered_conversation_script(client, make_conversation()))

        assert "transportFailures += 1;" in branch
        assert "MAX_TRANSPORT_RETRIES" in branch
        assert "setTimeout(tick, POLL_INTERVAL_MS)" in branch

    def test_a_parseable_5xx_is_counted_too(self, client):
        """Its unparseable twin already rejects in `response.json()` and
        is counted in `.catch()`; this one used to fall through to the
        state branches with no state to match."""
        script = _rendered_conversation_script(client, make_conversation())

        assert "result.status >= 500" in script

    def test_exhausting_the_counter_shows_the_same_honest_note(self, client):
        """One sentence for every exhausted counter, not a new one per
        branch -- the page composes no prose of its own."""
        script = _rendered_conversation_script(client, make_conversation())

        assert script.count("Lost contact with the server") >= 1

    def test_the_retryable_503_is_retried_rather_than_given_up_on(self, client):
        script = _rendered_conversation_script(client, make_conversation())

        assert "data.retryable" in script

    def test_the_transport_counter_is_still_only_reset_by_an_actionable_answer(
        self, client
    ):
        """`transportFailures = 0` must not sit before the branches that
        count -- resetting on every parseable response would make the
        ceiling unreachable. It lives inside the recognized-state
        branches instead, which is the only place a reset is honest."""
        script = _rendered_conversation_script(client, make_conversation())
        chain = script[script.index(".then(function (result) {"):]
        before_the_counting = chain[:chain.index("result.status >= 500")]

        assert "transportFailures = 0" not in before_the_counting
        assert "transportFailures = 0" in chain[chain.index('data.state === "queued"'):]
