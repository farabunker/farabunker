"""`?pin_into=` on the library page, and the one pin route."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agents.contracts.workstreams import WorkstreamScope
from tools.rag.models import WorkstreamPin
from tools.rag.tests._helpers import _workstream, make_document
from identity.access import owner_fields
from identity.testing import make_admin, make_user, posture, sign_in, user_principal

pytestmark = pytest.mark.django_db


def test_the_library_page_names_each_contained_documents_stream(client):
    """A list that shows a row without saying where it lives is a list
    that invites the wrong delete."""
    admin = make_admin()
    stream = _workstream(name="Q3 planning")
    make_document(workstream=stream, title="Contained thing")
    make_document(title="Universal thing")
    with posture("enterprise", admin_sees_content=True):
        sign_in(client, admin)
        body = client.get(reverse("rag-documents")).content.decode()
    assert "Q3 planning" in body
    assert "Universal thing" in body


def test_pin_into_renders_a_banner_and_a_pin_control_per_universal_row(client):
    """AUTHOR DECISION 25: the pin picker is `?pin_into=` on the EXISTING
    library page, not a new page. The library page already lists,
    searches, paginates and permission-filters documents; a second
    listing would be a second set of counts and a second filter to keep
    in agreement with `readable_documents`."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)), name="Q3")
    doc = make_document(title="Pin me")
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(f"{reverse('rag-documents')}?pin_into={stream.pk}").content.decode()
    assert "Q3" in body
    assert "Pin me" in body
    assert reverse("rag-workstream-pin", args=[stream.pk]) in body


def test_pin_into_offers_no_pin_control_on_a_chat_scoped_row(client):
    """ROUND 12 WHOLE-BRANCH REVIEW B-1 (render half): a chat-scoped row
    always has `workstream_id` unset, the SAME condition an ordinary
    pinnable universal row satisfies -- without an explicit `scope`
    exclusion the OLD template condition would have offered "Pin" on it
    too, and the POST would have SUCCEEDED (`tools.rag.workstreams.
    pin_document`'s own new refusal is this test's gate-side twin)."""
    from tools.rag.models import Document

    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)), name="Q3")
    chat_doc = make_document(title="Chat only doc", **owner_fields(user_principal(user)))
    chat_doc.scope = Document.Scope.CONVERSATION
    chat_doc.save(update_fields=["scope", "updated_at"])
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(f"{reverse('rag-documents')}?pin_into={stream.pk}").content.decode()
    assert "Chat only doc" in body
    # The row IS listed (uploader's own library view); it simply carries
    # no Pin form -- an empty `<td></td>` instead (the `{% elif pin_into
    # %}` branch), the SAME shape a contained row already gets. This is
    # the ONLY document in the library for this test, so the button's
    # total absence from the page is unambiguous proof for this row.
    assert '<button type="submit">Pin</button>' not in body


def test_pin_into_a_stream_the_caller_may_not_reach_renders_no_banner(client):
    owner, stranger = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)), name="Private")
    with posture("enterprise"):
        sign_in(client, stranger)
        body = client.get(f"{reverse('rag-documents')}?pin_into={stream.pk}").content.decode()
    assert "Private" not in body


def test_pin_into_a_stream_the_caller_may_visit_but_not_manage_renders_no_banner(client):
    """FIX ROUND 1 (controller review finding 1): the pin-mode context
    must follow the ROUTE's own gate (`scope.may_upload`), not mere
    visibility, or the two drift apart the moment a principal can see a
    stream without being able to manage it -- WS-2's read-only share
    recipient. WS-1 has no such principal yet (no sharing), so this
    proves the gate directly by patching the pin-mode context builder's
    own seam call to answer `may_upload=False` rather than waiting for
    WS-2 to exist. Without the fix, this caller would see a banner and a
    Pin button that always 404 at `rag-workstream-pin` (which checks the
    same field).

    PATCHES `agents.workstreams.pin_target`, NOT `workstream_scope`
    (final-polish round: the pin-mode banner's own context builder in
    `tools/rag/views.py::DocumentsView` was rehomed onto `pin_target` --
    one row read answering both the scope and the display name -- so
    this is the seam that call site actually asks now)."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)), name="Q3 planning window")
    make_document(title="Pin me")
    visible_but_not_owner = WorkstreamScope(
        workstream_id=stream.pk, wall=frozenset(), default_upload_placement="", may_upload=False)
    with posture("enterprise"):
        sign_in(client, user)
        with patch("agents.workstreams.pin_target",
                  return_value=(visible_but_not_owner, "Q3 planning window")):
            body = client.get(f"{reverse('rag-documents')}?pin_into={stream.pk}").content.decode()
    # THE NAME IS ABSENT, which is the claim -- and the FIXTURE is what
    # was wrong, not the assertion (found flaking in a full-suite run,
    # 2026-09-16, on a branch that never touched this file). "Q3" is two
    # characters, every rendered page carries a random alphanumeric CSRF
    # token, and one came back containing them. Widening the name to a
    # phrase, the convention the sibling above already follows with
    # "Private", restores this assertion at full strength instead of
    # trading it for a weaker one about the banner's own words.
    assert "Q3 planning window" not in body
    assert reverse("rag-workstream-pin", args=[stream.pk]) not in body


def test_the_pin_route_resolves_both_halves_404_shaped(client):
    """TWO RESOLUTIONS, BOTH 404-SHAPED: the stream through
    `agents.workstreams.workstream_scope` (which is `None` for a stream
    the caller may not mutate) and the document through
    `readable_documents`."""
    owner, stranger = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    doc = make_document()
    with posture("enterprise"):
        sign_in(client, stranger)
        response = client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                               {"action": "pin", "document": str(doc.pk)})
    assert response.status_code == 404
    assert WorkstreamPin.objects.count() == 0


def test_a_named_refusal_is_a_message_not_a_404(client):
    """The named refusals of §8.2 -- contained, capped -- are messages on a
    row the caller can already SEE, not 404s."""
    user = make_user()
    stream, other = (_workstream(**owner_fields(user_principal(user))),
                     _workstream(**owner_fields(user_principal(user))))
    contained = make_document(workstream=other)
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                               {"action": "pin", "document": str(contained.pk)},
                               follow=True)
    assert response.status_code == 200
    assert "already lives in a workstream" in response.content.decode()


def test_an_unrecognised_action_flashes_and_redirects(client):
    """Item 6 (Coherence Wave D): flash-and-redirect, not a raw 400 --
    the same conversion four other shelled-form sites already took in
    Waves B/C."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                               {"action": "bogus"}, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain
    assert b"Unknown action" in response.content
    assert WorkstreamPin.objects.count() == 0


def test_one_url_two_actions(client):
    """Unpinning is the same route with an `action` field, keyed on the
    pin id, exactly as `chat-conversation-share` revokes: one URL, two
    actions, so the two predicates cannot drift."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    doc = make_document()
    with posture("enterprise"):
        sign_in(client, user)
        client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                    {"action": "pin", "document": str(doc.pk)})
        pin = WorkstreamPin.objects.get()
        client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                    {"action": "unpin", "pin": str(pin.pk)})
    assert WorkstreamPin.objects.count() == 0


def test_an_off_site_next_is_ignored_not_an_open_redirect(client):
    """CONSOLIDATION WAVE (audit A, B5's own "highlight" -- an
    orchestrator-verified open-redirect class): `next` used to be
    honoured UNGUARDED (`redirect(request.POST.get("next") or ...)`),
    the one caller-supplied redirect target on this box with no
    same-origin guard on it at all. HOSTILE: an attacker-controlled
    `next` naming an off-site URL must never be followed -- the route
    falls back to its own default (`chat-workstream`), the same
    fallback a JS-off caller who never sent `next` at all already gets."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    doc = make_document()
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(
            reverse("rag-workstream-pin", args=[stream.pk]),
            {"action": "pin", "document": str(doc.pk), "next": "https://evil.example/steal"},
        )
    assert response.status_code == 302
    assert response.url == reverse("chat-workstream", args=[stream.pk])


def test_a_same_origin_next_is_honoured(client):
    """THE POSITIVE TWIN: a same-origin, same-scheme `next` -- the
    legitimate use this field exists for (returning to `/chat/all/`'s
    own preview pane, say) -- still works exactly as before this fix."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    doc = make_document()
    next_url = reverse("chat-all") + f"?selected={stream.pk}"
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(
            reverse("rag-workstream-pin", args=[stream.pk]),
            {"action": "pin", "document": str(doc.pk), "next": next_url},
        )
    assert response.status_code == 302
    assert response.url == next_url


class TestTheWorkstreamColumnAddsNoPerRowQuery:
    """THE QUERY-COUNT PIN (Global Constraint 6) on the one thing this
    task adds to the library page's own row-building:
    `document.workstream`. `select_related("category", "workstream")`
    (`tools/rag/views.py::DocumentsView`) turns that FK into a JOIN on
    the SAME query the listing already runs -- without it,
    `{{ document.workstream.name }}` (`documents.html`'s own Workstream
    column) would run one query PER ROW that happens to be contained.

    AN ABSOLUTE NUMBER, ASSERTED TWICE: "these two are equal" would also
    pass if both grew, the same shape `agents/chat/tests/
    test_workstream_page.py::TestTheListBuilderCostsTheSameAtOneRowAndAtTwentyFive`
    uses for its own builder-level pin.

    THIS PINS A PROXY QUERYSET, NOT THE FULL `DocumentsView` SURFACE
    (fix round 1, controller review finding 2). `rag-documents` already
    carries an unrelated, PRE-EXISTING per-row cost -- measured directly
    with `CaptureQueriesContext` (admin, enterprise posture): 27 queries
    at one document, 75 at twenty-five -- reproduced IDENTICALLY by
    `git stash`-ing every file this task touches and re-running the same
    1-row/25-row measurement against the untouched, pre-Task-15 tree.
    That gap predates this task, is tracked separately at the
    whole-branch gate, and is OUT OF SCOPE HERE BY RULING -- this pin
    does not attempt to fix it. Pinning the real view's total here would
    make this test fail non-vacuously for a reason that has nothing to
    do with the one thing this task changed, so the pin is scoped
    instead to the ONE queryset this task's own code builds --
    `Document.objects.select_related("category", "workstream")` -- which
    IS flat, proven below.

    `posture("enterprise")` WRAPS BOTH TESTS, matching this class's own
    cited precedent (`TestTheListBuilderCostsTheSameAtOneRowAndAtTwenty
    Five`) even though the proxy queryset above never touches
    `readable_documents`/`sees_all_content` and so is posture-inert on
    its own: the reason every sibling pin in this codebase gives is
    "an open box short-circuits before the per-row question is even
    asked", and pinning under the SAME posture as the class it mirrors
    keeps this test from becoming the one place a later refactor (this
    proxy growing a real permission check, say) could quietly start
    passing against the wrong posture.
    """

    EXPECTED_QUERIES = 1

    def _make_documents(self, count):
        stream = _workstream()
        for i in range(count):
            # ALTERNATING contained/universal, so `.workstream.name` is
            # actually dereferenced on some rows -- a pin that never
            # touches the joined column would pass even without
            # `select_related` at all.
            make_document(workstream=stream if i % 2 == 0 else None, title=f"row-{i}")

    def _rows(self):
        from tools.rag.models import Document

        return [d.workstream.name if d.workstream_id else None
                for d in Document.objects.select_related("category", "workstream")
                                          .order_by("title")]

    def test_one_document(self, django_assert_num_queries):
        self._make_documents(1)
        with posture("enterprise"):
            with django_assert_num_queries(self.EXPECTED_QUERIES):
                rows = self._rows()
        assert len(rows) == 1

    def test_twenty_five_documents_cost_exactly_the_same(self, django_assert_num_queries):
        """The assertion that makes the one above mean something: the
        SAME number, with twenty-four more rows in the list."""
        self._make_documents(25)
        with posture("enterprise"):
            with django_assert_num_queries(self.EXPECTED_QUERIES):
                rows = self._rows()
        assert len(rows) == 25


class TestTheFullPageIsNowFlatAtOneRowAndAtTwentyFive:
    """THE FULL-VIEW PIN THE CLASS ABOVE COULD NOT MAKE, un-blocked by
    the whole-branch review's own fix (finding 6). The class above
    measured the real `rag-documents` view directly and found a
    PRE-EXISTING, UNRELATED per-row cost of 27 -> 75 queries (admin,
    enterprise posture) and ruled it out of scope for Task 15: `may_label
    _document` ran a fresh `.filter(...).exists()` plus a fresh
    `is_admin`/`owned_entitlement_ids` pair PER ROW, bypassing the
    `prefetch_related("entitlement_labels__entitlement")` this view
    already carries. With that cost hoisted out of the row loop and
    answered from the prefetched `entitlement_labels.all()` in memory
    instead (`tools.rag.access.may_label_document`'s own `is_admin_`/
    `owned` parameters), the real view is flat, and this is the pin that
    proves it -- the one `TestTheWorkstreamColumnAddsNoPerRowQuery`
    itself says was blocked until this gap closed.

    EQUALITY BETWEEN TWO RENDERS, NOT AN ABSOLUTE NUMBER, the same shape
    `agents/chat/tests/test_workstream_page.py::TestTheListPageCostsThe
    SameAtOneRowAndAtTwentyFive` uses for the identical reason: a view
    count carries session and authentication reads that are nobody's
    business here and would make an absolute pin brittle for reasons
    unrelated to this page.

    `posture("enterprise", admin_sees_content=True)` and a signed-in
    admin, matching the pre-existing gap's own measurement exactly (the
    class above's docstring) -- the posture and the principal under
    which the 27 -> 75 regression was found.
    """

    def test_the_page_renders_the_same_number_of_queries_either_way(self, client):
        admin = make_admin()
        make_document(title="row-0")
        with posture("enterprise", admin_sees_content=True):
            sign_in(client, admin)
            # A WARM-UP REQUEST FIRST, discarded: the same reason
            # `test_workstream_page.py`'s own equality pin discards its
            # first request -- Django caches a few things (content
            # types, the resolved URL conf) on the first request of a
            # test, and counting that once would make the FIRST
            # measurement the larger one for a reason that has nothing
            # to do with how many documents exist.
            client.get(reverse("rag-documents"))
            with CaptureQueriesContext(connection) as one_row:
                first = client.get(reverse("rag-documents"))
            assert first.status_code == 200
            for i in range(1, 25):
                make_document(title=f"row-{i}")
            with CaptureQueriesContext(connection) as many_rows:
                second = client.get(reverse("rag-documents"))
            assert second.status_code == 200
        # Non-vacuous: the second render really did list all 25 rows, so
        # the equality is about a longer list rather than about a cap or
        # a filter quietly dropping the extra ones.
        assert second.content.decode().count('class="doc-title"') == 25
        assert len(many_rows) == len(one_row)
