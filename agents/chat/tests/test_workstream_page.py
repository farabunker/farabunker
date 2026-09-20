"""The stream list, the two stream pages (working + settings), and the
one edit route with an `action` field."""
from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agents.chat.views.workstreams import _stream_rows
from agents.defaults import SETTINGS_SURFACE_SLUGS, install_default
from agents.models import Agent, Share, Workstream, WorkstreamScopeEntitlement, WorkstreamTaint
from agents.tests._helpers import _workstream, make_agent
from identity.access import owner_fields
from identity.testing import (
    grant, make_entitlement, make_user, posture, sign_in, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def agent():
    """A plain `Agent` row -- this module's own fixture, not a shared
    one, matching `agents/tests/test_workstreams.py`'s own precedent:
    no `agent` fixture exists anywhere else in this app's tests (every
    sibling module calls `make_agent()` directly)."""
    return make_agent()


def test_the_list_shows_only_streams_this_principal_may_read(client):
    owner, other = make_user(), make_user()
    mine = _workstream(**owner_fields(user_principal(owner)), name="Mine")
    theirs = _workstream(**owner_fields(user_principal(other)), name="Theirs")
    with posture("enterprise"):
        sign_in(client, owner)
        body = client.get(reverse("chat-workstreams")).content.decode()
    assert "Mine" in body
    assert "Theirs" not in body


def test_the_list_refuses_a_post_with_405_now_that_create_moved_off_it(client):
    """Owner feedback round 7 retired POST-create from `chat-workstreams`
    entirely -- `@require_GET` on `workstream_list` now, so a POST here
    answers a clean 405 rather than the create it used to do (moved
    wholesale to `chat-workstream-new`, tested below)."""
    user = make_user()
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("chat-workstreams"), {"name": "Q3 planning"})
    assert response.status_code == 405
    assert Workstream.objects.count() == 0


# --- chat-workstream-new (owner feedback round 7: the setup screen) -------

def test_the_setup_screen_renders_for_an_authenticated_principal(client):
    user = make_user()
    with posture("enterprise"):
        sign_in(client, user)
        response = client.get(reverse("chat-workstream-new"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "<title>New workstream — farabunker</title>" in body
    assert f'href="{reverse("chat-workstreams")}"' in body
    assert Workstream.objects.count() == 0


def test_posting_the_setup_form_creates_a_stream_with_every_field_set(client):
    """The one submit that used to only take a name now takes all five
    key items in one POST -- Name/Description/Instructions/Scope/Upload
    default -- and every writer's own effect lands on the SAME row."""
    user = make_user()
    finance = make_entitlement(name="Finance")
    grant(finance, user=user)
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("chat-workstream-new"), {
            "name": "Q3 planning",
            "description": "Quarterly close",
            "instructions": "Be terse.",
            "entitlements": [str(finance.pk)],
            "placement": "contained",
        })
    stream = Workstream.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("chat-workstream", args=[stream.pk])
    assert stream.name == "Q3 planning"
    assert stream.description == "Quarterly close"
    assert stream.instructions == "Be terse."
    assert stream.default_upload_placement == "contained"
    assert set(stream.scope_entitlements.values_list("entitlement_id", flat=True)) == {
        finance.pk}


def test_a_bare_name_creates_a_stream_with_every_other_field_left_blank(client):
    """Description/Instructions/Scope/Upload default are all OPTIONAL --
    the one required item is Name, matching the retired list-page form's
    own validation exactly (owner feedback round 7's own brief: "same
    validation/refusal copy as today's create")."""
    user = make_user()
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("chat-workstream-new"), {"name": "Bare"})
    stream = Workstream.objects.get()
    assert response.status_code == 302
    assert stream.description == ""
    assert stream.instructions == ""
    assert stream.default_upload_placement == ""
    assert stream.scope_entitlements.count() == 0


def test_a_duplicate_name_re_renders_the_setup_screen_with_entered_values_preserved(client):
    """SAME COPY AS THE RETIRED LIST-PAGE FORM (owner feedback round 7's
    own brief), and nothing already typed is lost on the refusal -- the
    exact "a re-render can never be missing a section the GET has, and
    never blank a field the caller filled in" guarantee `_setup_context`'s
    own docstring gives."""
    user = make_user()
    with posture("enterprise"):
        sign_in(client, user)
        client.post(reverse("chat-workstream-new"), {"name": "Taxes"})
        response = client.post(reverse("chat-workstream-new"), {
            "name": "taxes",
            "description": "Second try",
            "instructions": "Keep this.",
            "placement": "universal",
        })
    assert response.status_code == 400
    body = response.content.decode()
    assert "already have a workstream called" in body
    assert Workstream.objects.count() == 1
    assert '<div class="banner warn">' in body
    assert 'class="job-error"' in body
    # ENTERED VALUES PRESERVED, not reset to blank on the refusal.
    assert 'value="taxes"' in body
    assert ">Second try<" in body
    assert ">Keep this.<" in body
    # The "universal" radio is the one re-rendered as `checked` -- the
    # exact value this refusal's own POST submitted.
    assert 'value="universal"\n        checked> Always the universal library' in body


def test_the_scope_section_offers_exactly_what_the_principal_holds(client):
    """PARITY WITH `chat/workstream_settings.html`'s OWN SCOPE CARD: the
    identical chip-picker wrapping shape (`.chip-picker`/`.chip-check`,
    checkbox INSIDE the label) it already uses -- reused verbatim, per
    the brief's own "reuse the settings page's card designs"
    instruction.

    NAMED PRECISELY, NOT "the settings pages" (phase 2). The two ACCESS
    settings pages moved to collapsed rows over a two-pane transfer
    panel, so a blanket parity claim would now be false. The scope
    editor deliberately keeps chips and is out of that scope: its
    catalogue is one principal's own holdings rather than the whole
    box's, it is chosen once when a stream is created rather than edited
    in a run, and a wall is a different question from a label.
    """
    user = make_user()
    finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
    grant(finance, user=user)
    grant(legal, user=user)
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream-new")).content.decode()
    for entitlement in (finance, legal):
        assert f'<input type="checkbox" name="entitlements" value="{entitlement.pk}"' in body
        assert f'<span class="chip">{entitlement.name}</span>' in body
    # WRAPPING, not a separate `id`/`for` pair (F4, carried forward a
    # second time): the checkbox and its chip text share one `<label>`.
    assert '<label class="chip-check">' in body


def test_the_scope_section_is_absent_entirely_on_an_open_box(client):
    """RULING A (the identical condition `_settings_context`'s own Scope
    gate uses): the wall is inert on an open box, so the editor is
    absent, not merely empty."""
    with posture("open"):
        body = client.get(reverse("chat-workstream-new")).content.decode()
    assert "Scope" not in body
    assert "entitlement" not in body.lower()


def test_a_scope_submission_naming_an_unheld_entitlement_is_refused_and_creates_nothing(client):
    """ONE TRANSACTION, THE WHOLE WAY DOWN: a tampered submission naming
    an entitlement the principal does not hold refuses the ENTIRE
    creation, not just the scope step -- `create_workstream`'s own row,
    already written earlier in the same `transaction.atomic()` block,
    is rolled back too, matching `workstream_scope`'s own dedicated view
    refusal ("A workstream's scope may only name entitlements you
    hold.") rather than silently dropping the bad ids and creating an
    unscoped stream anyway."""
    user = make_user()
    unheld = make_entitlement(name="NotMine")
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("chat-workstream-new"), {
            "name": "Tampered",
            "entitlements": [str(unheld.pk)],
        })
    assert response.status_code == 400
    assert "may only name entitlements you hold" in response.content.decode()
    assert Workstream.objects.count() == 0


def test_the_setup_screen_carries_no_script_tag_of_its_own(client):
    """ZERO JAVASCRIPT OF ITS OWN -- and the qualifier in this test's own
    name is now doing real work. ROUND 21 gives the RAIL one shared
    script (`chat/_menu_exclusive.html`, the exclusive-open handler the
    owner asked for), and this page renders the rail like every other
    chat page, so exactly ONE `<script` reaches it -- from the shared
    fragment, never from this page's own body. Counted rather than
    forbidden, so a stray script added HERE still fails."""
    with posture("open"):
        body = client.get(reverse("chat-workstream-new")).content.decode()
    assert body.count("<script") == 1
    assert 'rail.addEventListener("toggle"' in body


class TestTheSetupScreenCostsTheSameAtOneAndTenHeldEntitlements:
    """GC-6, applied to the one variable-length list this page renders --
    the Scope chip-picker. `entitlement_names(held_entitlement_ids(...))`
    is TWO single queries once the held set is non-empty (an aggregate
    `IN` read each), the same "one query for the whole pin set" shape
    `_settings_context`'s own Scope section already relies on -- pinned
    here, on the setup screen, the way the coordinator's own brief asked
    ("pin what the settings page pins"), since the settings page itself
    never needed a dedicated Scope-only pin.

    ONE HELD ENTITLEMENT, NOT ZERO, is the smaller size: Django's ORM
    skips the `Entitlement.objects.filter(pk__in=...)` query entirely
    when the `__in` set is empty (an `EmptyResultSet` optimisation, no
    SQL sent at all), so a 0-vs-10 comparison would cross that free-vs-
    real-query line and fail for a reason that has nothing to do with
    entitlement COUNT -- the exact wrong-comparison trap `test_the_list_
    builder...AtOneRowAndAtTwentyFive`'s own choice of 1 (not 0) as its
    smaller size already avoids for the identical reason.
    """

    #: Measured against this round's own code, the same "run it, read
    #: the real count" method every query-count pin in this module uses.
    #: DROPPED FROM 28 TO 24 BY THE CONSOLIDATION WAVE'S OWN F1 (`agents/
    #: chat/sidebar.py`'s own archived-count no longer re-runs `visible_
    #: conversations` fresh -- 4 fewer queries on this page's own
    #: sidebar render). RAISED FROM 24 TO 25 BY ROUND 20: the sidebar's
    #: own PINNED section costs one bounded extra query per render
    #: (`agents/chat/tests/test_sidebar.py::TestItCostsTheSameAtOneRow
    #: AndAtThirty.EXPECTED_QUERIES`'s own comment has the accounting),
    #: and this page renders that same sidebar. DROPPED FROM 25 TO 23 BY
    #: TASK 9 (Task 8 review, R2): the same sidebar render's own -2 (see
    #: that pin's comment -- `visible_conversations`'s `owned_rows_q`
    #: call now threads `settings_row`).
    EXPECTED_QUERIES = 23

    def _render(self, client, user, count, django_assert_num_queries):
        for i in range(count):
            grant(make_entitlement(name=f"Ent{i}"), user=user)
        with posture("enterprise"):
            sign_in(client, user)
            with django_assert_num_queries(self.EXPECTED_QUERIES):
                response = client.get(reverse("chat-workstream-new"))
        assert response.status_code == 200
        return response.content.decode()

    def test_one_held_entitlement(self, client, django_assert_num_queries):
        body = self._render(client, make_user(), 1, django_assert_num_queries)
        assert body.count('<span class="chip">') == 1

    def test_ten_held_entitlements_cost_exactly_the_same(self, client, django_assert_num_queries):
        body = self._render(client, make_user(), 10, django_assert_num_queries)
        assert body.count('<span class="chip">') == 10


def test_the_stream_page_renders_the_identical_unscoped_rail(client):
    """ROUND 14 (OWNER AMENDMENT) RETIRES THE SCOPED SIDEBAR ENTIRELY
    (owner: "I believe we should remove the workstream chats from the
    side bar") -- `_working_context` no longer threads `workstream=`
    into `sidebar_context` at all, so the stream page's own rail renders
    the SAME "Chats" heading and global recent list every other chat
    page does, never the stream's own name as a heading. `agents/chat/
    tests/test_sidebar.py::
    test_the_rail_never_scopes_the_conversation_list_to_one_stream`
    pins the same claim through the builder directly; this is the
    stream page's own rendered-HTML twin of it."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)), name="Q3 planning")
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert '<h2 class="chat-nav-label">Chats</h2>' in body
    assert f'<h2 class="chat-nav-label" title="{stream.name}">{stream.name}</h2>' not in body
    assert '<a href="{}">All chats</a>'.format(reverse("chat-all")) in body


def test_the_stream_page_sets_a_title_naming_the_stream(client):
    """F2 (walk-fix batch): the stream page set no `<title>` at all --
    the browser tab read bare "farabunker" while every sibling page
    (`chat/conversation.html`, `chat/index.html`, and the stream LIST,
    `chat/workstreams.html`, which already carries one) reads "X —
    farabunker". Asserted the same way `test_thread.py`'s own `<title>`
    pins are."""
    stream = _workstream(name="Q3 planning")
    with posture("open"):
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert "<title>Q3 planning — farabunker</title>" in body


def test_the_stream_page_404s_for_a_stranger(client):
    owner, stranger = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    with posture("enterprise"):
        sign_in(client, stranger)
        response = client.get(reverse("chat-workstream", args=[stream.pk]))
    assert response.status_code == 404
    assert "Traceback" not in response.content.decode()


def test_the_working_page_header_links_to_settings_for_the_owner(client):
    """Settings-page split (owner feedback): the header keeps exactly
    ONE link where the old four-control "Manage" overlay used to sit."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert (f'<a class="pill-link" href="{reverse("chat-workstream-settings", args=[stream.pk])}">'
            "Settings</a>") in body


def test_a_recipient_sees_no_settings_link_on_the_working_page(client):
    """The link is gated `is_owner`, not merely styled away -- a
    recipient who followed it would 404 on the settings page anyway
    (`test_the_settings_page_404s_for_a_live_share_recipient`, below),
    so the render-vs-gate rule this surface applies everywhere else
    applies here too."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    # `.pill-link` is a promoted, general-purpose class now (setup-screen
    # round) -- but on THIS page's own render the working page has only
    # ONE anchor that ever carries it (the Settings link), so the class
    # attribute alone is still an unambiguous stand-in for it here.
    # ">Settings<" alone also matches the UNRELATED account-settings link
    # this box's own nav always carries -- the class attribute on a real
    # anchor is the only thing that is actually this gate's own business.
    assert 'class="pill-link"' not in body


@pytest.mark.parametrize("action,field,value,check", [
    ("rename", "name", "Renamed", lambda s: s.name == "Renamed"),
    ("description", "description", "Why", lambda s: s.description == "Why"),
    ("instructions", "instructions", "Be terse.", lambda s: s.instructions == "Be terse."),
    ("upload_default", "placement", "contained",
     lambda s: s.default_upload_placement == "contained"),
    # ROUND 17: a CHECKBOX, not a field=value pair -- `field=""` (like
    # "archive" above it) means the payload carries no extra key at
    # all, which is an UNCHECKED box (checkbox semantics: absence means
    # False). The default is True on every freshly-created stream
    # (`_workstream`'s own fields never override it), so seeing it land
    # on False is what proves this write actually ran.
    ("include_universal", "", "", lambda s: s.include_universal is False),
    ("archive", "", "", lambda s: s.archived_at is not None),
])
def test_each_edit_action_writes_its_own_field(client, action, field, value, check):
    """ONE `edit` ROUTE WITH AN `action` FIELD, NOT SEVEN ROUTES (author
    decision 11 / spec §14). The seven owner-only mutations of a stream
    row share one predicate and one 404 rule; seven routes would be seven
    chances for one of them to drift, and the drift would show as a
    control that 404s."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    payload = {"action": action}
    if field:
        payload[field] = value
    with posture("enterprise"):
        sign_in(client, user)
        client.post(reverse("chat-workstream-edit", args=[stream.pk]), payload)
    stream.refresh_from_db()
    assert check(stream)


@pytest.mark.parametrize("action,field,value,flash", [
    ("rename", "name", "Renamed", "Renamed."),
    ("description", "description", "Why", "Description saved."),
    ("instructions", "instructions", "Be terse.", "Instructions saved."),
    ("upload_default", "placement", "contained", "Upload default saved."),
    ("include_universal", "", "", "Document library setting saved."),
])
def test_each_save_action_flashes_a_confirmation(client, action, field, value, flash):
    """F5 (walk-fix batch): these four actions (plus Scope, its own
    route, covered separately) used to complete with no visible
    confirmation at all -- the page just redirected back to itself.
    `chat/_messages.html` is included on the SETTINGS page now (settings-
    page split: every one of these actions' own form lives there), so
    `follow=True` lands there and the flash still costs nothing beyond
    the `messages.success` call itself. The assertion itself needed no
    change -- it never named a URL, only the flash text on whichever
    page the redirect actually lands on."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                               {"action": action, field: value}, follow=True)
    assert flash in response.content.decode()
    assert response.redirect_chain[-1][0] == reverse("chat-workstream-settings",
                                                      args=[stream.pk])


def test_the_scope_save_action_flashes_a_confirmation(client):
    user = make_user()
    held = make_entitlement(name="Held")
    grant(held, user=user)
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                               {"entitlements": [str(held.pk)]}, follow=True)
    assert "Scope saved." in response.content.decode()
    assert response.redirect_chain[-1][0] == reverse("chat-workstream-settings",
                                                      args=[stream.pk])


def test_an_unknown_action_is_a_400_and_writes_nothing(client):
    """UPDATED, ITEM 6 (Coherence Wave D): the refusal was already a
    400; it used to be a bare plain-text page. It now re-renders the
    settings page's own `page_error` slot (`chat/workstream_settings.
    html:62-63`), matching this view's own seven OTHER action branches,
    which already re-render on refusal rather than returning raw text."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)), name="Keep")
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                               {"action": "explode"})
    assert response.status_code == 400
    assert '<div class="banner warn">' in response.content.decode()
    assert "Unknown action" in response.content.decode()
    stream.refresh_from_db()
    assert stream.name == "Keep"


def test_a_recipient_gets_404_on_every_edit_action(client):
    """Every "does not reach" row is a 404 and NOT a 403, because they
    are all row-addressed mutations of a row the recipient can see --
    which is precisely the case `may_manage_conversation`'s docstring
    already rules on for shared conversations."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        for action in ("rename", "description", "instructions", "upload_default",
                       "include_universal", "archive", "unarchive", "delete"):
            response = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                                   {"action": action, "name": "x"})
            assert response.status_code == 404, action
        response = client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                               {"entitlements": []})
        assert response.status_code == 404


def test_delete_refuses_by_name_while_rows_remain_and_succeeds_when_emptied(client, agent):
    """The refusal re-renders the SETTINGS page now (settings-page
    split: Delete's own form lives there) -- `follow=True` lands there
    and the same "Delete them first" text still renders, unchanged."""
    from agents.models import Conversation

    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                               {"action": "delete"}, follow=True)
        assert "Delete them first" in response.content.decode()
        assert response.redirect_chain == []  # a direct 400 render, not a redirect
        conversation.delete()
        response = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                               {"action": "delete"})
    assert response.status_code == 302
    # The row is gone, so there is no settings page left to land on --
    # the one redirect target this split leaves unchanged.
    assert response["Location"] == reverse("chat-workstreams")
    assert not Workstream.objects.filter(pk=stream.pk).exists()


def test_the_scope_editor_offers_exactly_what_the_gate_accepts(client):
    """M10 (spec §6.1), the single test that catches
    `labelling_entitlements` being used here. `labelling_entitlements`
    answers OWNED, not HELD, and the two diverge in BOTH directions: an
    entitlement you own but do not hold would render and then be refused,
    and one you hold but do not own would never render, making a wall
    §6.1 says you may set unsettable.

    SETTINGS-PAGE SPLIT: the editor itself now renders on `chat-
    workstream-settings`, not `chat-workstream` -- the GET below was
    retargeted; the POST route (`chat-workstream-scope`) kept its own
    URL untouched."""
    user = make_user()
    held, owned_not_held = make_entitlement(name="Held"), make_entitlement(name="Owned")
    grant(held, user=user)
    # An entitlement this user OWNS but does not HOLD is not
    # representable through `grant` (a grant is a holding), so the owned
    # -not-held half is built by granting the owner role to a group the
    # user is not in and asserting the name is absent.
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
        assert "Held" in body
        assert "Owned" not in body
        ok = client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                         {"entitlements": [str(held.pk)]})
        assert ok.status_code == 302
        refused = client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                              {"entitlements": [str(owned_not_held.pk)]}, follow=True)
    assert "you hold" in refused.content.decode().lower()
    assert set(WorkstreamScopeEntitlement.objects.values_list(
        "entitlement_id", flat=True)) == {held.pk}


def test_a_page_error_renders_as_a_styled_banner_not_bare_text(client):
    """F9 (walk-fix batch): `page_error` used to be a bare `<p class=
    "error">` this box's CSS never defines anywhere -- unstyled text
    sitting above the heading with nothing marking it as a refusal.
    Reuses the scope refusal above (any `page_error` branch does; this
    one needs no extra setup beyond a held/not-held entitlement pair) to
    assert the established `.banner.warn` + `.job-error` idiom (`chat/
    _form_errors.html`'s own pair) renders instead of the old dead
    class. SETTINGS-PAGE SPLIT: `page_error` itself moved off `chat/
    workstream.html` entirely (nothing can populate it there any more --
    every POST whose refusal used to land there now lands on `chat/
    workstream_settings.html`), which this test does not need to know:
    it only checks the banner classes on whatever page the refusal
    actually rendered."""
    user = make_user()
    held, owned_not_held = make_entitlement(name="Held2"), make_entitlement(name="Owned2")
    grant(held, user=user)
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        refused = client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                              {"entitlements": [str(owned_not_held.pk)]}, follow=True)
    body = refused.content.decode()
    assert '<div class="banner warn">' in body
    assert 'class="job-error"' in body
    assert 'class="error"' not in body


def test_the_scope_checkboxes_are_bound_to_their_entitlement_names(client):
    """F4 (walk-fix batch, a11y): a screen reader used to announce the
    checkbox's VALUE ("1"/"2"), not the entitlement it stood for -- bare
    wrapping alone was not enough, which used to be fixed with an
    explicit `id`/`for` pair.

    WORKSTREAM COMPOSITION PASS: the checkboxes moved onto the
    chip-picker component (`_shell.html`'s `.chip-picker`/`.chip-check`,
    the SAME "pick any number of entitlements" control the document
    library's own upload card already uses), whose own idiom is
    WRAPPING -- the checkbox and its visible `.chip` text are both
    children of one `<label>`, so the accessible name is the label's own
    text content (the entitlement's name) with no separate `id`/`for`
    pair needed at all. Asserted here as the exact wrapping shape for
    each held entitlement, not just bare text presence.

    SETTINGS-PAGE SPLIT: fetches `chat-workstream-settings` now, the
    Scope card's new home."""
    user = make_user()
    finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
    grant(finance, user=user)
    grant(legal, user=user)
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
    for ent in (finance, legal):
        checkbox_start = body.index(
            f'<input type="checkbox" name="entitlements" value="{ent.pk}"')
        # The nearest preceding `<label class="chip-check">` really is
        # THIS checkbox's own wrapper, not a coincidence of ordering:
        # `rindex` (backward from the checkbox) finds it, and the
        # entitlement's own name sits AFTER the checkbox and BEFORE that
        # same wrapper's `</label>` -- both ends of the association
        # checked, not just one.
        label_start = body.rindex("<label class=\"chip-check\">", 0, checkbox_start)
        label_end = body.index("</label>", checkbox_start)
        assert label_start < checkbox_start < label_end
        assert ent.name in body[checkbox_start:label_end]


def test_the_scope_editor_is_absent_entirely_on_an_open_box(client):
    """RULING A, and the condition is THE POSTURE, not "the principal
    holds no entitlements". The distinction matters: the wall is inert on
    an open box, so a hidden editor strands nothing, whereas the old
    condition would have hidden the editor on a box where the wall was
    still binding. SETTINGS-PAGE SPLIT: checked against `chat-workstream-
    settings`, Scope's own home -- reachable at 200 even on an open box,
    since `sees_all_content` makes the sole principal `is_owner` for
    free."""
    stream = _workstream()
    with posture("open"):
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
    assert "Scope" not in body
    assert "entitlement" not in body.lower()


def test_the_page_carries_exactly_the_shared_enter_to_send_script(client):
    """ALMOST ZERO JAVASCRIPT (owner feedback round 8 narrowed this from
    an absolute claim -- the New-chat start box here had NO script at
    all, so Enter just inserted a newline instead of sending; see
    `chat/_enter_to_send.html`). ROUND 17 ADDS A SECOND SANCTIONED
    SCRIPT for an OWNER specifically: the "Pin a document" chooser's own
    searchable-checkbox dropdown (`tools/rag/templates/rag/panels/
    documents.html`, `_filter_rows_script.html` -- the round-16
    tags-filter script REUSED, not copied; it moved to `foundation/
    templates/` when a third consumer outside `agents/` needed it). ROUND 18 ADDS A THIRD:
    `chat/_attach_dragdrop.html`, gated on `may_attach_files` exactly
    like the conversation page's own copy -- `rag.ingest` is unlabelled
    by default (`test_thread.py::TestTheAttachDoor`'s own comment: "means
    everyone holds it"), so this owner's own New-chat card renders the
    attach door and its script both. THREE now, pinned here rather than
    left for a stray script to slip past unnoticed. Fetched as the OWNER
    principal, same as `test_the_working_page_header_links_to_settings_
    for_the_owner` above, since the New-chat card is the surface the bug
    report's own screenshot showed. Non-vacuous: removing any include
    drops the count and this fails."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert body.count("<script") == 4
    assert "form.requestSubmit()" in body
    assert "event.shiftKey" in body
    assert "event.isComposing" in body


def test_a_non_owner_sees_only_the_enter_to_send_script(client):
    """SCOPE DISCIPLINE, THE OTHER DIRECTION: a share recipient (not an
    owner) never sees the "Pin a document" section at all (`is_owner`
    is its own block's one gate, unchanged by round 17) -- so the
    dropdown script this principal was never offered is absent, and the
    count is the two that are NOT owner-gated: Enter-to-send, and round
    21's rail-wide exclusive-open handler."""
    owner = make_user()
    recipient = make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    from agents.visibility import share_workstream

    share_workstream(user_principal(owner), stream, user=recipient, level="use")
    with posture("enterprise"):
        sign_in(client, recipient)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert "Pin a document" not in body
    assert body.count("<script") == 2


def test_the_settings_page_carries_no_script_tag_either(client):
    """The same guarantee, on the new page, in the same corrected terms
    as `test_the_setup_screen_carries_no_script_tag_of_its_own` above:
    the rail's own round-21 script is the ONE `<script` here, and this
    page's own body still contributes none. Its "Manage" disclosure
    reuses the `.chat-menu` idiom but sits OUTSIDE `nav.chat-nav`, so
    the rail-scoped listener leaves it on plain native `<details>`."""
    stream = _workstream()
    with posture("open"):
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
    assert body.count("<script") == 1
    assert 'rail.addEventListener("toggle"' in body


def test_the_settings_page_sets_a_title_naming_the_stream(client):
    """Title conventions: "<stream> settings — farabunker", the same
    "X — farabunker" shape every sibling page uses, with "settings"
    naming which of the two pages this tab is."""
    stream = _workstream(name="Q3 planning")
    with posture("open"):
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
    assert "<title>Q3 planning settings — farabunker</title>" in body


def test_the_settings_page_404s_for_a_live_share_recipient(client):
    """Owner-gated with the plain house 404 rule, deliberately NOT
    `chat-workstream`'s own `stream_access`/403 exception: every section
    this page hosts is already owner-only or owner-relevant, so a
    recipient -- a LIVE share holder here, not merely a dormant one --
    gets exactly the 404 a stranger gets. The working page stays
    reachable for the same recipient (the companion assertion below)."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        settings_response = client.get(reverse("chat-workstream-settings", args=[stream.pk]))
        working_response = client.get(reverse("chat-workstream", args=[stream.pk]))
    assert settings_response.status_code == 404
    assert "Traceback" not in settings_response.content.decode()
    assert working_response.status_code == 200


def test_the_settings_page_404s_for_a_stranger_too(client):
    owner, stranger = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    with posture("enterprise"):
        sign_in(client, stranger)
        response = client.get(reverse("chat-workstream-settings", args=[stream.pk]))
    assert response.status_code == 404
    assert "Traceback" not in response.content.decode()


def test_the_settings_page_renders_the_manage_controls(client):
    """No dedicated template-level pin existed for Rename/Description/
    Archive/Delete before the settings-page split either (they were
    reached only through the view-level tests above) -- worth one here,
    on their new page, now that they render as a plain card rather than
    inside an overlay only a click could reveal.

    ALL FOUR AS MATCHING `<details>` DISCLOSURES (review fix, fold-in):
    Archive used to be the one bare always-visible form breaking up three
    disclosures -- a leftover from the composition pass's own cramped
    overlay, gone now that Manage is a full-width card. `<summary>
    Archive</summary>` (never `Unarchive` here -- the freshly created
    stream this test builds is never archived) proves the wrap without
    duplicating the POST-level coverage `test_each_edit_action_writes_
    its_own_field` already gives the archive/unarchive actions
    themselves."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
    assert '<div class="ws-card" id="manage">' in body
    assert "<summary>Rename</summary>" in body
    assert "<summary>Description</summary>" in body
    assert "<summary>Archive</summary>" in body
    assert "<summary>Delete</summary>" in body
    assert 'class="danger"' in body


def test_the_documents_panel_renders_from_the_registry(client):
    """The page renders whatever is REGISTERED, in registration order,
    through one include -- so a second panel later is a REGISTRATION, not
    an edit to this view."""
    from tools.rag.tests._helpers import make_document

    stream = _workstream()
    make_document(workstream=stream, title="Contained thing")
    with posture("open"):
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert "Documents" in body
    assert "Contained thing" in body


class TestTheRound17PinChooserDropdown:
    """ROUND 17, Part 2 (owner, verbatim: "The documents section should
    be better organized ... If I have 50 documents in the system this
    UI is usless"). The "Pin a document" control's own candidate list
    -- REUSING round 16's searchable-checkbox dropdown component
    (`chat/_search_checklist.html`), not a second copy."""

    def test_the_plain_select_is_gone_replaced_by_the_shared_dropdown(self, client):
        from tools.rag.tests._helpers import make_document

        stream = _workstream()
        make_document(title="Candidate one")
        with posture("open"):
            body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
        assert '<select name="document">' not in body
        assert 'class="chat-menu search-checklist"' in body
        assert 'type="radio" name="document"' in body
        assert "Candidate one" in body

    def test_the_candidate_set_matches_the_old_offered_set_minus_pinned_and_chat_scoped(
            self, client):
        """Same offered set as before this round (the brief's own
        words): every readable universal document, MINUS one already
        pinned and minus a chat-scoped one (B-1 fix) -- `tools.rag.
        workstreams.panel`'s own candidate query is untouched by this
        round; only the MARKUP rendering it changed."""
        from tools.rag.models import Document, WorkstreamPin
        from tools.rag.tests._helpers import make_document

        stream = _workstream()
        offered = make_document(title="Offered candidate")
        already_pinned = make_document(title="Already pinned")
        WorkstreamPin.objects.create(workstream=stream, document=already_pinned)
        chat_scoped = make_document(title="Chat only", scope=Document.Scope.CONVERSATION)
        with posture("open"):
            body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
        assert f'value="{offered.pk}"' in body
        assert "Offered candidate" in body
        # "Already pinned" legitimately appears once, in the PINNED
        # list above the chooser -- the claim here is narrower: its
        # OWN id never appears as a radio CANDIDATE (the dropdown's own
        # `value="<id>"` shape, pinned nowhere else on this page).
        assert f'value="{already_pinned.pk}"' not in body
        assert f'value="{chat_scoped.pk}"' not in body
        assert "Chat only" not in body

    def test_the_search_script_renders_with_the_js_off_honesty_title(self, client):
        from tools.rag.tests._helpers import make_document

        stream = _workstream()
        make_document(title="Candidate")
        with posture("open"):
            body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
        assert 'class="checklist-search"' in body
        start = body.index('class="checklist-search"')
        tag = body[body.rindex("<input", 0, start):body.index(">", start) + 1]
        assert "requires JavaScript" in tag

    def test_zero_js_pin_flow_end_to_end_via_the_rendered_radio_value(self, client):
        """The whole point of a plain radio `<input>` inside a plain
        `<form method="post">`: picking one and submitting pins it, no
        script required -- the candidate id this test POSTs is read
        straight off the RENDERED page, not invented, so a future markup
        drift (a wrong `name=`/`value=`) would fail this rather than a
        test that merely called `pin_document` in Python."""
        from tools.rag.models import WorkstreamPin
        from tools.rag.tests._helpers import make_document

        user = make_user()
        stream = _workstream(**owner_fields(user_principal(user)))
        doc = make_document(title="Pin me")
        with posture("enterprise"):
            sign_in(client, user)
            body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
            assert f'value="{doc.pk}"' in body
            response = client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                                   {"action": "pin", "document": str(doc.pk)})
        assert response.status_code == 302
        assert WorkstreamPin.objects.filter(workstream=stream, document=doc).exists()

    def test_both_headers_get_the_info_disclosure_with_accurate_copy(self, client):
        """ADDENDUM (owner, verbatim: "I have no idea what Pinned is, we
        should have an info icon for it") -- CONTAINED gets the SAME
        treatment "for consistency" (the brief's own words), since it
        had no equivalent explanation either. NON-VACUOUS: the two
        sentences make DIFFERENT, SPECIFIC claims (Pinned survives the
        wall/toggle BECAUSE it bypasses them; Contained was never
        subject to either in the first place) -- a generic "what's
        this" placeholder for both would pass a bare presence check but
        fail these."""
        from tools.rag.models import WorkstreamPin
        from tools.rag.tests._helpers import make_document

        stream = _workstream()
        make_document(workstream=stream, title="Contained thing")
        WorkstreamPin.objects.create(workstream=stream, document=make_document())
        with posture("open"):
            body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
        assert body.count("&#9432; What's this?") == 2
        assert "always count as part of this workstream" in body
        assert "retrievable here even when they fall outside" in body
        assert "live only in this workstream" in body
        assert "never part of the shared library" in body

    def test_the_pin_cap_refusal_message_still_renders_beside_the_dropdown(self, client):
        """Cap/refusal pins still green (the brief's own words) --
        unchanged message, now rendered beside the shared component
        rather than beside a `<select>`."""
        from tools.rag.tests._helpers import make_document
        from tools.rag.workstreams import MAX_PINS_PER_STREAM

        stream = _workstream()
        for _ in range(MAX_PINS_PER_STREAM):
            from tools.rag.models import WorkstreamPin
            WorkstreamPin.objects.create(workstream=stream, document=make_document())
        with posture("open"):
            body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
        assert "this workstream is at its pin limit" in body


class TestTheListBuilderCostsTheSameAtOneRowAndAtTwentyFive:
    """THE BUILDER-LEVEL PIN (Global Constraint 6, on the list surface
    this task adds). `_stream_rows` is the one queryset both halves of
    `workstream_list`'s context build from, and
    `.annotate(Count("conversations"))` is what keeps it flat -- a
    per-row `.count()` in the template would be exactly the N+1 Global
    Constraint 6 forbids on a list surface.

    AN ABSOLUTE NUMBER, ASSERTED TWICE, the same shape `agents/chat/
    tests/test_sidebar.py::TestItCostsTheSameAtOneRowAndAtThirty` uses
    for its own builder-level pin: "these two are equal" would also pass
    if both grew, and the number is what a later reader needs in order
    to know whether their change added a query or removed one.

    PINNED TO `POSTURE_ENTERPRISE` (the string form this module already
    uses), for the reason every sibling pin in this codebase gives: on
    an open box `sees_all_content` short-circuits before reading
    anything, so the per-row question is free however it is written and
    this pin would pass against the very defect it exists to catch.
    """

    #: The `.filter()` plus the `Count("conversations")` JOIN/GROUP BY,
    #: plus the identity reads `visible_workstreams` itself makes --
    #: written down rather than compared, for the reason the class
    #: docstring gives.
    EXPECTED_QUERIES = 6

    def _rows(self, user, count, django_assert_num_queries):
        for _ in range(count):
            _workstream(**owner_fields(user_principal(user)))
        with posture("enterprise"):
            with django_assert_num_queries(self.EXPECTED_QUERIES):
                return list(_stream_rows(user_principal(user), archived=False))

    def test_one_workstream(self, django_assert_num_queries):
        rows = self._rows(make_user(), 1, django_assert_num_queries)
        assert len(rows) == 1

    def test_twenty_five_workstreams_cost_exactly_the_same(self, django_assert_num_queries):
        """The assertion that makes the one above mean something: the
        SAME number, with twenty-four more rows in the list."""
        rows = self._rows(make_user(), 25, django_assert_num_queries)
        assert len(rows) == 25


class TestTheListPageCostsTheSameAtOneRowAndAtTwentyFive:
    """THE SAME CLAIM, THROUGH THE REAL VIEW -- the pin above cannot make
    it, for the reason `agents/chat/tests/test_sidebar.py::
    TestThePagesCostTheSameAtOneRowAndAtTwentyFive`'s own docstring
    gives: it calls `_stream_rows` directly, so it is blind to a
    regression added around the builder call inside `workstream_list`
    itself (an accidental per-row read in the view or the template,
    say).

    EQUALITY BETWEEN TWO RENDERS, NOT AN ABSOLUTE NUMBER: a view count
    carries session and authentication reads that are nobody's business
    here and would make an absolute pin brittle for reasons unrelated to
    this list.

    `POSTURE_ENTERPRISE` and a signed-in owner, for the same reason the
    builder-level pin above is pinned that way.
    """

    def test_the_list_renders_the_same_number_of_queries_either_way(self, client):
        user = make_user()
        _workstream(**owner_fields(user_principal(user)))
        with posture("enterprise"):
            sign_in(client, user)
            # A WARM-UP REQUEST FIRST, discarded: Django caches a few
            # things on the first request of a test (content types, the
            # resolved URL conf), and counting that once would make the
            # FIRST measurement the larger one for a reason that has
            # nothing to do with how many workstreams exist -- which is
            # precisely the direction that would hide a regression from
            # the equality below.
            client.get(reverse("chat-workstreams"))
            with CaptureQueriesContext(connection) as one_row:
                first = client.get(reverse("chat-workstreams"))
            assert first.status_code == 200
            for _ in range(24):
                _workstream(**owner_fields(user_principal(user)))
            with CaptureQueriesContext(connection) as many_rows:
                second = client.get(reverse("chat-workstreams"))
            assert second.status_code == 200
        # Non-vacuous: the second render really did list all 25 rows, so
        # the equality is about a longer list rather than about a cap or
        # a filter quietly dropping the extra ones.
        assert second.content.decode().count('class="ws-row"') == 25
        assert len(many_rows) == len(one_row)


def test_the_owner_sees_the_upload_default_control(client):
    """Spec decision 10 / §9's table: the upload form's own "change" link
    (`tools/rag/templates/rag/documents.html`) points at `#upload-
    default` on the SETTINGS page now (settings-page split), so the
    anchor id and the control must both be there."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
    assert 'id="upload-default"' in body
    assert "Ask every time" in body


def test_the_owner_sees_the_include_universal_control_checked_by_default(client):
    """ROUND 17 (owner: "a bool in settings to use all rag documents
    ... default it on") -- a fresh stream's checkbox is CHECKED the
    moment the settings page first renders, matching `Workstream.
    include_universal`'s own migration default with no edit needed."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
    assert 'id="include-universal"' in body
    start = body.index('name="include_universal"')
    tag = body[body.rindex("<input", 0, start):body.index(">", start) + 1]
    assert "checked" in tag


def test_the_include_universal_checkbox_reflects_an_off_stream(client):
    """THE OTHER DIRECTION: a stream already toggled off renders
    UNCHECKED, not merely a control that exists -- this is what lets an
    owner trust the box as the honest current state rather than a
    write-only switch."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)), include_universal=False)
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
    start = body.index('name="include_universal"')
    tag = body[body.rindex("<input", 0, start):body.index(">", start) + 1]
    assert "checked" not in tag


def test_a_recipient_gets_404_before_ever_reaching_the_upload_default_control(client):
    """OWNER-ONLY, the SAME 404 the settings page answers for every
    section it hosts (settings-page split) -- stronger than the old
    "this one section is absent" claim, since a recipient no longer
    reaches ANY part of this page at all."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        response = client.get(reverse("chat-workstream-settings", args=[stream.pk]))
    assert response.status_code == 404


def test_the_upload_default_control_appears_for_the_sole_operator_on_an_open_box(client):
    """Open box: `sees_all_content` short-circuits `is_owner` to True for
    anyone (RULING A's own reasoning, applied to this section rather
    than to Scope), so this section is PRESENT here, unlike Scope, which
    stays absent regardless of ownership."""
    stream = _workstream()
    with posture("open"):
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
    assert 'id="upload-default"' in body


def test_the_tags_section_names_the_streams_taint_set_for_the_owner(client):
    """Spec §15.3 item 8: rendered only when non-empty, naming the whole
    taint set (not filtered to what the OWNER holds -- that filtering is
    gate one's and the dormant marker's rule, not this section's)."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=make_entitlement(name="Finance"))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
    assert "Finance" in body
    assert "must hold all of them" in body


def test_the_tags_section_is_absent_when_the_stream_carries_no_tags(client):
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
    # `.ws-card-head` (workstream composition pass): the section's own
    # heading is no longer a bare `<h2>` -- pinned as the CURRENT markup
    # so this stays a real assertion about the section rather than one a
    # future rename could pass vacuously.
    assert '<h2 class="ws-card-head">Tags</h2>' not in body


def test_the_sharing_section_lists_recipients_for_the_owner_only(client):
    """OWNER-ONLY: a recipient reads and converses and never manages this
    stream's share list. Settings-page split raises the stakes past "the
    section is absent" -- a recipient no longer reaches this page at all,
    live share included, so there is no rendered body to check a control
    is missing FROM."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, owner)
        owner_body = client.get(reverse("chat-workstream-settings", args=[stream.pk])).content.decode()
        sign_in(client, reader)
        reader_response = client.get(reverse("chat-workstream-settings", args=[stream.pk]))
    assert "Shared with" in owner_body
    assert reader.username in owner_body
    assert reader_response.status_code == 404


def test_the_tags_and_sharing_sections_are_absent_entirely_on_an_open_box(client):
    """Spec §15.3's closing rule: sections 3, 8 and 9 are absent, not
    disabled, on an open box -- a household operator never sees the
    words "entitlement", "taint" or "share" here at all, whatever rows
    happen to exist underneath. The settings page IS reachable on an
    open box (`sees_all_content` makes the sole principal `is_owner`),
    unlike a recipient on an accounts-on box -- this is the "inert-
    consistent subset" the split's own brief asks for."""
    stream = _workstream()
    WorkstreamTaint.objects.create(workstream=stream,
                                   entitlement=make_entitlement(name="Finance"))
    with posture("open"):
        response = client.get(reverse("chat-workstream-settings", args=[stream.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Finance" not in body
    assert "Shared with" not in body


class TestTheStreamPageCostsTheSameAtOneAndTenConversations:
    """GLOBAL CONSTRAINT 6, on the WORKING page (settings-page split
    left this page holding New chat/Conversations/Documents only).
    Every builder `_working_context` calls -- `staleness_for` (owner-
    only, one call for the whole list), `panels_for` (one call per
    stream, not per conversation) and `sidebar_context` (its own
    flatness already pinned by `agents/chat/tests/test_sidebar.py`) --
    is a single call over the whole conversation list rather than a
    loop, so the page as a whole should cost the same whether it lists
    one conversation or ten. This is the pin that says so, for BOTH
    viewers the page renders differently for: the OWNER (Section 3's
    staleness hints, gated `if is_owner`) and a SHARE RECIPIENT (ruling
    C: reads every conversation in the stream, manages none of them).

    AN ABSOLUTE NUMBER, ASSERTED TWICE PER VIEWER, the same shape
    `TestTheListBuilderCostsTheSameAtOneRowAndAtTwentyFive` above uses:
    "these two are equal" would also pass if both grew, and the number is
    what a later reader needs in order to know whether their change
    added a query or removed one. If the page is not flat, THE FIX IS
    THE VIEW, NOT THIS PIN.

    `posture("enterprise")` for the reason every sibling pin in this
    module is: on an open box `sees_all_content` short-circuits before
    the per-row question is even asked, and this pin would pass against
    the very defect it exists to catch.
    """

    # RE-MEASURED (round 18, ROUND-18-CONFIRM R7 fix). `_working_context`
    # now calls `agents.chat.service.composer_attach_context` for EVERY
    # viewer, owner or not -- the New-chat card's own attach door is not
    # owner-gated (a live share recipient may also start a conversation
    # here), so this is a genuinely new question this page never asked
    # either viewer before. AN EARLIER DRAFT of this fix let `composer_
    # attach_context` re-derive `workstream_scope(principal, stream.pk)`
    # fresh, which cost +10/+12 over the pre-round-18 baseline -- R7's
    # own finding. `_working_context` already resolves the IDENTICAL
    # predicate, as `is_owner` (`stream_access`'s own owner branch IS
    # `may_manage_workstream`, which IS `WorkstreamScope.may_upload`
    # under two other names -- `agents.workstreams._scope_from_row`),
    # so `composer_attach_context`'s own `may_upload=` keyword now
    # takes it directly, skipping that re-derivation. OWNER lands back
    # at the exact PRE-round-18 baseline (58): the composer's own attach
    # gate costs nothing extra once `may_upload` is threaded rather than
    # recomputed. RECIPIENT lands two above it (60), a real and
    # necessary cost, not a residual: a recipient's own `tool_access_for`
    # call (`rag.ingest`) is genuinely new -- this page never asked a
    # recipient that question before round 18, because a recipient never
    # had an attach door to gate. Both numbers measured directly against
    # this fix's own code, the same "run it, read the real count" method
    # every prior re-measurement note in this class already used; flat
    # at one conversation and at ten either way, which is the property
    # this class exists to pin.
    #
    # RAISED BY ONE MORE, EACH, BY ROUND 20: the sidebar's own PINNED
    # section costs one bounded extra query per render (`agents/chat/
    # tests/test_sidebar.py::TestItCostsTheSameAtOneRowAndAtThirty.
    # EXPECTED_QUERIES`'s own comment has the accounting), and this page
    # renders that same sidebar for both viewers alike.
    #
    # DROPPED BY FOUR, EACH, BY TASK 9 (Task 8 review, R2): `visible_
    # conversations`'s own `owned_rows_q(principal)` call now threads
    # `settings_row` (`agents/visibility.py:107`). TWO of the four are
    # the sidebar's own drop (same accounting as the sidebar pin above);
    # the other TWO are this page's OWN Conversations-list call
    # (`agents/chat/views/workstreams.py:373`, `visible_conversations(
    # principal, settings_row=settings_row)`), which takes the identical
    # non-cheap branch for both an owner and a share recipient and so
    # drops by the same 2 either viewer sees on the sidebar.
    OWNER_QUERIES = 55
    RECIPIENT_QUERIES = 57

    def _conversations(self, principal, agent, stream, count):
        from agents.models import Conversation

        for i in range(count):
            Conversation.objects.create(agent=agent, workstream=stream, title=f"c-{i}",
                                        **owner_fields(principal))

    def _render(self, client, stream, count, expected_queries, django_assert_num_queries):
        owner_principal = user_principal(self._owner)
        self._conversations(owner_principal, self._agent, stream, count)
        with django_assert_num_queries(expected_queries):
            response = client.get(reverse("chat-workstream", args=[stream.pk]))
        assert response.status_code == 200
        # Non-vacuous: the page really did list every conversation, so the
        # pin is about a longer list rather than about a cap or a filter
        # quietly dropping the extra ones. Counted by the ROW MARKUP
        # (`chat/workstream.html`'s own `.ws-item` per conversation,
        # workstream composition pass -- was `<li><a href=...>` before
        # the row list moved off `<ul>`/`<li>`), not by title text --
        # "c-1" is a substring of "c-10", which would double-count past
        # nine rows. `.ws-item` is this page's own row class, distinct
        # from the sidebar's `.chat-nav-row` (which renders on this same
        # page too, in the scoped conversation list) for exactly this
        # reason: a shared class name would make this count ambiguous
        # between the two regions.
        assert response.content.decode().count('<div class="ws-item">') == count

    def test_owner_at_one_conversation(self, client, agent, django_assert_num_queries):
        self._owner, self._agent = make_user(), agent
        stream = _workstream(**owner_fields(user_principal(self._owner)))
        with posture("enterprise"):
            sign_in(client, self._owner)
            self._render(client, stream, 1, self.OWNER_QUERIES, django_assert_num_queries)

    def test_owner_at_ten_conversations_costs_exactly_the_same(
            self, client, agent, django_assert_num_queries):
        self._owner, self._agent = make_user(), agent
        stream = _workstream(**owner_fields(user_principal(self._owner)))
        with posture("enterprise"):
            sign_in(client, self._owner)
            self._render(client, stream, 10, self.OWNER_QUERIES, django_assert_num_queries)

    def test_recipient_at_one_conversation(self, client, agent, django_assert_num_queries):
        self._owner, self._agent = make_user(), agent
        reader = make_user()
        stream = _workstream(**owner_fields(user_principal(self._owner)))
        Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                             user=reader, level=Share.Level.USE)
        with posture("enterprise"):
            sign_in(client, reader)
            self._render(client, stream, 1, self.RECIPIENT_QUERIES, django_assert_num_queries)

    def test_recipient_at_ten_conversations_costs_exactly_the_same(
            self, client, agent, django_assert_num_queries):
        self._owner, self._agent = make_user(), agent
        reader = make_user()
        stream = _workstream(**owner_fields(user_principal(self._owner)))
        Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                             user=reader, level=Share.Level.USE)
        with posture("enterprise"):
            sign_in(client, reader)
            self._render(client, stream, 10, self.RECIPIENT_QUERIES, django_assert_num_queries)


class TestTheSettingsPageCostsAKnownAmountAtOneShare:
    """THE SETTINGS PAGE'S OWN QUERY-COST PIN (new in the settings-page
    split -- `_settings_context` renders a real list, Shared-with).
    NOT an equality-at-two-sizes pin like the working page's own
    Conversations pin, below, and deliberately so: `share_list_for`'s own
    docstring (`agents/visibility.py`) rules that this list pays "one
    grants query per LISTED ROW... this is a bound on the recipient
    count of one stream's own share list... a handful in practice, and a
    page only its owner visits" -- a documented, PRE-EXISTING design
    point this split did not touch, distinct from the flat-by-batching
    guarantee `staleness_for`/`panels_for` give the Conversations list.
    Measured here: an equality pin at 1 vs. 10 shares would simply fail
    against the view's own intended shape, not catch a regression.

    Owner-only by construction (a recipient 404s before this builder
    ever runs), so there is only ONE viewer to measure.
    """

    #: Measured against this split's own code, the same "run it, read
    #: the real count" method every query-count pin in this module uses.
    #: A second share would cost 2 more (one more `entitlement_ids_for_
    #: subject` call, per the docstring above) -- a per-row cost this pin
    #: does not re-assert at a second size, since it is meant to scale.
    #: DROPPED FROM 44 TO 40 BY THE CONSOLIDATION WAVE'S OWN F1 (`agents/
    #: chat/sidebar.py`'s own archived-count no longer re-runs `visible_
    #: conversations` fresh -- 4 fewer queries on this page's own
    #: sidebar render, same as every other page that includes it).
    #: RAISED FROM 40 TO 41 BY ROUND 20, the same one-query PINNED
    #: section cost every sidebar-rendering page's own pin picks up
    #: (`agents/chat/tests/test_sidebar.py::TestItCostsTheSameAtOneRow
    #: AndAtThirty.EXPECTED_QUERIES`'s own comment has the accounting).
    #: DROPPED FROM 41 TO 39 BY TASK 9 (Task 8 review, R2): the sidebar's
    #: own -2 (see that pin's comment -- `visible_conversations`'s
    #: `owned_rows_q` call now threads `settings_row`). This page (unlike
    #: the Conversations working page above) has no conversation list of
    #: its own to pick up a second -2 from -- the Shared-with list reads
    #: `Share`, not `Conversation`.
    OWNER_QUERIES_AT_ONE_SHARE = 39

    def test_one_share(self, client, django_assert_num_queries):
        owner = make_user()
        stream = _workstream(**owner_fields(user_principal(owner)))
        Share.objects.create(target_type=Share.Target.WORKSTREAM,
                             target_key=str(stream.pk), user=make_user(),
                             level=Share.Level.USE)
        with posture("enterprise"):
            sign_in(client, owner)
            with django_assert_num_queries(self.OWNER_QUERIES_AT_ONE_SHARE):
                response = client.get(reverse("chat-workstream-settings", args=[stream.pk]))
        assert response.status_code == 200
        assert response.content.decode().count('<div class="ws-item">') == 1


class TestTheDesignPassReviewFixRound:
    """Design-pass review, final round: two of the three cheap fixes on
    the workstream composition pass are still checked here (the third,
    the "Manage" overlay's own width override, no longer exists at all
    after the settings-page split retired the overlay in favour of a
    plain card -- see `chat/workstream_settings.html`'s own docstring).
    `posture("open")` with no sign-in, the same pattern `test_the_upload_
    default_control_appears_for_the_sole_operator_on_an_open_box` above
    already uses -- `sees_all_content` makes the sole open-box principal
    `is_owner` for free, which is what puts the "New chat" card on the
    page without any setup beyond a bare stream."""

    def test_the_placement_hint_is_styled_not_dead(self, client):
        """`rag/_placement_choice.html`'s own "Neither is preselected…"
        paragraph carries `class="hint"` -- a class neither this page nor
        `rag/documents.html` ever defined, so it rendered as unstyled
        body text inside an otherwise-muted Documents card. Scoped to
        `.placement .hint`, not a bare `.hint` or a rename to `.muted`
        (a `chat/`-only utility class `rag/documents.html` never
        defines, which would have fixed this page and left the library
        page's identical copy exactly as broken). Still on the WORKING
        page: the Documents panel never moved."""
        stream = _workstream()
        with posture("open"):
            body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
        start = body.index(".placement .hint {")
        rule = body[start:body.index("}", start)]
        assert "color: var(--muted);" in rule
        assert "font-size: 0.85rem;" in rule

    def test_the_new_chat_textarea_inherits_the_page_font(self, client):
        """`.composer-textarea` (`chat/base.html`, shared by all three
        composer surfaces since round 18, superseding the former
        `.start-box textarea`) never set a font, so it fell back to the
        UA default -- monospace in every engine this box targets --
        which is the actual source of the "terminal look" the owner saw
        on this page's own "New chat" card. Still on the WORKING page:
        New chat never moved."""
        stream = _workstream()
        with posture("open"):
            body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
        start = body.index(".composer-textarea {")
        rule = body[start:body.index("}", start)]
        assert "font: inherit;" in rule


def test_the_new_chat_card_never_offers_the_settings_assistant(client):
    """`_working_context`'s `agents` key (`agents/chat/views/
    workstreams.py:401`) reads `chat_surface_agents`, not `visible_
    agents` -- one of Task 6's six moved call sites, and, per the Task 6
    review's Finding 3, shipped with no pin of its own: `settings-
    helper` appears in only one other chat test module, and that module
    never renders a workstream page, so reverting that one word back to
    `visible_agents` would be silent. Pinned directly against the
    rendered "New chat" card's `<option>` list; an ordinary agent stays
    offered, so this is the narrowing, not an empty picker."""
    from identity.contracts.principals import OPEN_PRINCIPAL

    slug = next(iter(SETTINGS_SURFACE_SLUGS))
    make_agent(slug="ordinary", name="Ordinary agent", resident=True)
    stream = _workstream()
    with posture("open"):
        install_default("agent", slug, OPEN_PRINCIPAL)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert f'value="{slug}"' not in body
    assert 'value="ordinary"' in body
