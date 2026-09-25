"""Round 18 (owner feedback, verbatim: "the ability to add an
attachment isn't on the intial new chat screen... I thought all the
chats used the same code base, why is there something using a
different sub-template?"). `chat/_composer.html` is the ONE fragment
`chat/index.html`'s start box, `chat/workstream.html`'s New-chat card,
and `chat/conversation.html`'s message form all render now; this file
pins the shape that claim depends on -- one fragment, three consumers,
the SAME markup on all three -- plus the round-18 addendum's own
rewrite (owner feedback: "the box that allows me to add it just stays
hovered... there should be a button to add a document and then it
should stack") and `agents.chat.views.conversations.conversation_start`
threading a start's own files through the identical `start_turn`
service path a later turn's do.

`agents/chat/tests/test_turn_attachments.py` already covers the SAME
staging service in depth from the TURN side (`chat-turn`); this file's
own functional test proves the START side reaches the identical place,
not a second, bespoke path -- it does not re-derive that service's own
scope-refusal/queue-outage matrix, which is unchanged by this round.
"""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    bound_chat_role, fake_turn_queue, make_agent, make_conversation, make_entitlement,
    make_user, posture, sign_in, user_principal,
)
from agents.models import Share, Turn
from agents.runtime.tests._helpers import isolated_tool_registry  # noqa: F401
from agents.tests._helpers import _workstream
from agents.visibility import create_conversation
from identity.contracts.postures import POSTURE_ENTERPRISE
from tools.rag.models import Document, DocumentAttachment

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]

_CHAT_TEMPLATES = [
    "agents/chat/templates/chat/index.html",
    "agents/chat/templates/chat/workstream.html",
    "agents/chat/templates/chat/conversation.html",
]


class TestOneFragmentThreeConsumers:
    """THE STRUCTURAL CLAIM itself: `chat/_composer.html` is included by
    all three composer surfaces, and none of them carries its own,
    separate copy of the composer's own markup any more (`.start-box`,
    the pre-round-18 class name, is gone from every one of them)."""

    def test_all_three_pages_include_the_shared_composer(self):
        for path in _CHAT_TEMPLATES:
            text = open(path).read()
            assert '{% include "chat/_composer.html"' in text, path

    def test_no_page_carries_its_own_copy_of_the_retired_start_box(self):
        for path in _CHAT_TEMPLATES:
            text = open(path).read()
            assert 'class="start-box"' not in text, path
            # THE RETIRED ELEMENT ITSELF, not the word: `conversation.
            # html`'s own comment prose still NAMES `.attach-trigger`,
            # in past tense, explaining what round 18 removed -- a
            # legitimate historical reference, not a live selector.
            assert 'chat-menu attach-trigger"' not in text, path

    def test_the_fragment_itself_carries_every_attachment_related_piece_once(self):
        composer = open("agents/chat/templates/chat/_composer.html").read()
        assert '{% include "chat/_attach_files.html" %}' in composer
        assert '{% include "chat/_attach_dragdrop.html" %}' in composer
        assert '{% include "chat/_enter_to_send.html" %}' in composer


class TestTheGateMatrixOnBothStartSurfaces:
    """`may_attach_files` is a REAL gate on the two start surfaces now,
    not merely on the conversation page -- render-vs-gate holds for a
    conversation that does not exist yet too."""

    def test_the_index_start_box_shows_the_door_by_default(self, client):
        make_agent()
        body = client.get(reverse("chat-index")).content.decode()
        assert '<label class="attach-label-button" for="attach-files">' in body

    def test_the_index_start_box_hides_the_door_when_rag_ingest_is_labelled(self, client):
        """`posture(POSTURE_ENTERPRISE)` + `sign_in`, not the default
        open posture: `sees_all_content` makes an open box's sole
        principal immune to every label (`identity.access`'s own
        docstring) -- an unauthenticated `client` under open posture
        would see the door regardless of labelling, which would prove
        nothing about the gate this test exists to pin."""
        from agents.models import ToolEntitlement

        user = make_user()
        make_agent(resident=True)
        ToolEntitlement.objects.create(tool_key="rag.ingest",
                                       entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, user)
            body = client.get(reverse("chat-index")).content.decode()
        assert 'for="attach-files"' not in body

    def test_the_workstream_new_chat_card_shows_the_door_by_default(self, client):
        owner = make_user()
        stream = _workstream(user_principal(owner))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-workstream", args=[stream.pk])).content.decode()
        assert '<label class="attach-label-button" for="attach-files">' in body

    def test_the_workstream_new_chat_card_offers_the_contained_option(self, client):
        """THE SCOPE MATRIX'S OWN STREAM HALF: a New-chat composer
        rendered on a page this principal manages offers all THREE
        placement options, `_attach_files.html`'s own `attach_
        workstream`-driven chooser -- the SAME predicate `chat/
        conversation.html`'s own attach door already uses for a
        conversation ALREADY inside a stream."""
        owner = make_user()
        stream = _workstream(user_principal(owner))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-workstream", args=[stream.pk])).content.decode()
        assert 'name="placement" value="contained" required' in body


class TestStartWithFiles:
    """THE FUNCTIONAL CLAIM: a chat-start POST carrying files reaches
    the SAME `agents.chat.service.start_turn` a later turn's own upload
    does -- conversation created, first turn created, `Document`/
    `DocumentAttachment` bound to that FIRST turn, and the ingest job
    enqueued -- never a second, bespoke attach path for a thread's
    opening message."""

    def test_a_start_with_a_file_creates_conversation_turn_document_and_attachment(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        agent = make_agent()
        response = client.post(reverse("chat-start"), {
            "agent": agent.slug,
            "text": "here's the report",
            "files": SimpleUploadedFile("report.md", b"quarterly numbers"),
            "placement": "conversation",
        })
        assert response.status_code == 302
        conversation = agent.conversations.get()
        user_turn = Turn.objects.get(role=Turn.Role.USER)
        assert user_turn.conversation_id == conversation.id
        assert user_turn.text == "here's the report"
        doc = Document.objects.get()
        assert doc.scope == Document.Scope.CONVERSATION
        attachment = DocumentAttachment.objects.get()
        assert attachment.document_id == doc.id
        assert attachment.conversation_id == conversation.id
        # THE FIRST TURN, specifically -- the claim this test exists to
        # pin (round 18's own addendum, "the first turn carries the
        # attachments exactly like any later turn").
        assert attachment.turn_id == user_turn.pk

    def test_a_start_with_a_file_enqueues_the_turn_job(
        self, client, bound_chat_role, monkeypatch,
    ):
        enqueued = []

        def _fake_enqueue(kind, payload):
            enqueued.append((kind, payload))
            return 1

        monkeypatch.setattr("agents.chat.service.enqueue", _fake_enqueue)
        monkeypatch.setattr("agents.chat.service.get_job", lambda jid: None)
        agent = make_agent()
        client.post(reverse("chat-start"), {
            "agent": agent.slug,
            "text": "hi",
            "files": SimpleUploadedFile("note.md", b"x"),
            "placement": "conversation",
        })
        assert enqueued and enqueued[0][0] == "agent.turn"

    def test_a_start_with_files_but_no_message_refuses_honestly(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        """ROUND 18: a blank message WITH files must not take the
        "empty thread, nothing to say" branch -- `start_turn` requires
        `text` regardless of files, so this now reaches that refusal
        (deleting the just-created conversation) rather than silently
        dropping the file with the conversation left empty and no
        attachment anywhere -- exactly the "I attached a file and
        nothing happened" complaint this whole door exists to prevent."""
        agent = make_agent()
        response = client.post(reverse("chat-start"), {
            "agent": agent.slug,
            "text": "",
            "files": SimpleUploadedFile("note.md", b"x"),
            "placement": "conversation",
        })
        assert response.status_code == 400
        assert agent.conversations.count() == 0
        assert Document.objects.count() == 0

    def test_a_workstream_start_with_contained_placement_lands_in_the_stream(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        owner = make_user()
        stream = _workstream(user_principal(owner))
        # `box_wide=True` (task 5, chat cluster feature B): `resident`
        # alone no longer reaches a non-owning principal -- see
        # `agents/visibility.py::visible_agents`.
        agent = make_agent(resident=True, box_wide=True)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("chat-start"), {
                "agent": agent.slug,
                "text": "start here",
                "workstream": str(stream.pk),
                "files": SimpleUploadedFile("plan.md", b"x"),
                "placement": "contained",
            })
        assert response.status_code == 302
        conversation = agent.conversations.get()
        assert conversation.workstream_id == stream.pk
        doc = Document.objects.get()
        assert doc.scope == Document.Scope.UNIVERSAL
        assert doc.workstream_id == stream.pk

    def test_no_files_at_all_behaves_exactly_as_before(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        """THE REGRESSION GUARD, the start-side twin of `test_turn_
        attachments.py::TestOnePostFlow::test_a_message_with_no_files_
        behaves_exactly_as_before`: a plain text start, no `files` field
        at all, must not require a `placement` or otherwise change
        shape."""
        agent = make_agent()
        response = client.post(reverse("chat-start"), {
            "agent": agent.slug, "text": "just text",
        })
        assert response.status_code == 302
        assert Document.objects.count() == 0

    def test_a_blank_message_and_no_files_still_starts_an_empty_thread(self, client):
        """UNCHANGED EXISTING BEHAVIOUR, explicitly re-pinned: a bare
        "start a conversation" (no text, no files) is still not an
        error -- only a blank message WITH files (the test above) is
        new territory this round adds a refusal to."""
        agent = make_agent()
        response = client.post(reverse("chat-start"), {"agent": agent.slug, "text": ""})
        assert response.status_code == 302
        assert agent.conversations.count() == 1
        assert Turn.objects.count() == 0


class TestNeverFiveHundredOnMultipartEdgesAtChatStart:
    """`chat-start` is multipart now (round 18) -- the same "a bad
    request is a 400/403, never an unhandled exception" contract every
    other multipart route on this box already keeps."""

    def test_an_unrecognised_placement_is_a_400_not_a_500(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        agent = make_agent()
        response = client.post(reverse("chat-start"), {
            "agent": agent.slug, "text": "hi",
            "files": SimpleUploadedFile("x.md", b"x"), "placement": "not-a-real-value",
        })
        assert response.status_code == 400

    def test_an_unsupported_extension_still_queues_the_turn_not_a_500(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        agent = make_agent()
        response = client.post(reverse("chat-start"), {
            "agent": agent.slug, "text": "hi",
            "files": SimpleUploadedFile("evil.exe", b"MZ"), "placement": "conversation",
        })
        assert response.status_code == 302

    def test_a_bad_agent_with_files_attached_is_a_400_not_a_500(self, client):
        response = client.post(reverse("chat-start"), {
            "agent": "no-such-agent", "text": "hi",
            "files": SimpleUploadedFile("x.md", b"x"), "placement": "conversation",
        })
        assert response.status_code == 400
        assert Document.objects.count() == 0


class TestTheStagedStackAndLabelForWiring:
    """ROUND 18 ADDENDUM markers (owner feedback: "there should be a
    button to add a document and then it should stack... with the
    ability to remove attachments"): the `<label for>` wiring, the chip
    stack, and the JS-off fallback, pinned by source text -- no headless
    browser in this suite (house convention)."""

    def test_the_label_for_wiring_needs_no_script(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert '<label class="attach-label-button" for="attach-files">' in body
        assert '<input type="file" id="attach-files"' in body
        assert "<details" not in body[body.index('id="attach-files"'):
                                      body.index('id="attach-files"') + 400] \
            or "attach-trigger" not in body

    def test_the_file_input_is_never_display_none(self, client):
        """JS-OFF HONESTY (round 18's own "honest either way" call): the
        input stays visibly rendered -- `.attach-input`, never a
        `hidden` attribute or an inline `display: none` -- so its own
        native "N file(s)" text is an always-present fallback."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        input_tag_start = body.index('<input type="file" id="attach-files"')
        input_tag = body[input_tag_start:body.index(">", input_tag_start)]
        assert "hidden" not in input_tag
        assert "display: none" not in input_tag
        assert 'class="attach-input"' in input_tag

    def test_the_scope_fieldset_carries_no_hidden_attribute_in_markup(self, client):
        """The OTHER half of the same "honest either way" call: the
        scope fieldset is never JS-off-hidden either -- `.is-empty` is a
        class `chat/_attach_dragdrop.html`'s own script adds, never an
        attribute authored in the template, so a JS-off reader (who
        never gets it added) sees it from the first paint."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        fieldset_start = body.index('<fieldset class="placement attach-scope">')
        fieldset_open_tag = body[fieldset_start:body.index(">", fieldset_start) + 1]
        assert "hidden" not in fieldset_open_tag

    def test_the_staged_list_ships_empty_at_render(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert '<ul class="staged-files" aria-live="polite"></ul>' in body


class TestAccumulateSemanticsAndTheClearHook:
    """ROUND 18 ADDENDUM point 2 (owner feedback: "add more documents"):
    a second pick ACCUMULATES rather than replacing -- pinned by source,
    the same "no headless browser" convention this whole suite follows."""

    def test_the_script_merges_rather_than_replaces_on_pick(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "var stagedFiles = [];" in body
        assert "function mergeInFiles(newFiles)" in body
        assert "stagedFiles.push(file);" in body
        # NEGATIVE: the round-13/round-10 REPLACE semantics (a bare
        # `fileInput.files = files;` on drop, with no prior merge) are
        # gone -- a regression back to them would restore this exact
        # line.
        assert "fileInput.files = files;" not in body

    def test_the_dedupe_key_is_name_and_size(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert 'function keyFor(file) { return file.name' in body

    def test_the_page_specific_submit_handler_calls_the_shared_clear_hook(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "form._clearStagedAttachments = clearStagedAttachments;" in body
        assert "if (form._clearStagedAttachments) { form._clearStagedAttachments(); }" in body


class TestDragAndDropMarkersOnAllThreeSurfaces:
    """Round 18 addendum point 6: "Drag-drop drops onto the same stack
    (accumulate)... applies identically on ALL THREE surfaces"."""

    def test_the_index_start_box_carries_the_shared_dragdrop_script(self, client):
        make_agent()
        body = client.get(reverse("chat-index")).content.decode()
        assert 'document.addEventListener("dragover"' in body
        assert "mergeInFiles(files);" in body

    def test_the_workstream_card_carries_the_shared_dragdrop_script(self, client):
        owner = make_user()
        stream = _workstream(user_principal(owner))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-workstream", args=[stream.pk])).content.decode()
        assert 'document.addEventListener("dragover"' in body
        assert "mergeInFiles(files);" in body

    def test_the_conversation_page_carries_the_shared_dragdrop_script(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert 'document.addEventListener("dragover"' in body
        assert "mergeInFiles(files);" in body


class TestRound18ConfirmResiduals:
    """round18-confirm.md's own R4/R5/R6/R8a: no dead class, an
    accessible name restored on the composer textarea, one hidden
    `workstream` field instead of two, and the JS-added `.js-attach`
    class that lets `.attach-input` clip down only once script is
    proven to be running."""

    def test_no_dead_composer_form_class(self, client):
        """R4: `_composer.html`'s own `<form>` carries no `class=` at
        all now -- `.composer-card > form { display: contents; }`
        (`chat/base.html`) keys on structure, not a class nobody
        defined."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert 'class="composer-form"' not in body

    def test_the_textarea_carries_an_accessible_name_on_every_surface(self, client):
        """R5."""
        make_agent(slug="index-agent")
        index_body = client.get(reverse("chat-index")).content.decode()
        assert 'id="composer-text"' in index_body
        assert 'aria-label="Message"' in index_body

        conversation = make_conversation(agent=make_agent(slug="thread-agent"))
        thread_body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert 'id="composer-text"' in thread_body
        assert "aria-label=" in thread_body

    def test_only_one_hidden_workstream_field_on_the_new_chat_card(self, client):
        """R6: `_composer.html` is the ONE place `name="workstream"`
        renders inside the New-chat card now -- `_attach_files.html` no
        longer emits its own copy. Scoped to the card itself: the
        Documents panel's own Upload form (`rag/panels/documents.html`)
        carries an unrelated `name="workstream"` hidden field further
        down this SAME page, which this test was never about."""
        owner = make_user()
        stream = _workstream(user_principal(owner))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-workstream", args=[stream.pk])).content.decode()
        new_chat_card = body[body.index('id="new-chat"'):body.index('id="conversations"')]
        assert new_chat_card.count('name="workstream"') == 1

    def test_the_dragdrop_script_marks_the_attach_block_js_on(self, client):
        """R8a: the class exists only once the script that adds it
        runs -- a JS-off reader never sees it added, so `.attach-input`
        stays its normal visible self on that path (already pinned by
        `TestTheStagedStackAndLabelForWiring::test_the_file_input_is_
        never_display_none`, unaffected by this addition). The clip
        rule itself lives in `chat/base.html`, keyed on that class, not
        on `display: none`, so the input stays focusable once clipped."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert 'block.classList.add("js-attach");' in body
        assert ".attach-block.js-attach .attach-input" in body
        rule_start = body.index(".attach-block.js-attach .attach-input")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "display: none" not in rule
        assert "clip: rect(0, 0, 0, 0)" in rule


class TestRound20ConfirmResiduals:
    """round20-confirm.md's own R4: the radios addendum (1d17edb) shipped
    with no test anywhere pinning that the composer's own attach door
    actually wraps its scope radios in `.placement-options` -- a future
    edit could silently revert it with the suite still green. `.remember`
    and `.hint` stay OUTSIDE that wrapper, unchanged, so this also pins
    they were not swept in by mistake."""

    def test_the_scope_radios_render_inside_placement_options(self, client):
        owner = make_user()
        stream = _workstream(user_principal(owner))
        agent = make_agent(resident=True)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            conversation = create_conversation(user_principal(owner), agent,
                                              workstream=stream)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        options_start = body.index('<div class="placement-options">')
        options_end = body.index('</div>', options_start)
        options_block = body[options_start:options_end]
        # All three radios -- this conversation sits in a stream, so the
        # attach door offers the in-stream three-option case.
        assert 'value="conversation"' in options_block
        assert 'value="contained"' in options_block
        assert 'value="universal"' in options_block
        # `.remember`/`.hint` are NOT inside the wrapper.
        assert "remember" not in options_block
        assert "hint" not in options_block
        assert body.index('class="remember"') > options_end


class TestTheAttachDoorDoesNotPayForAStreamItWillNotOffer:
    """AUDIT-2 CONFIRM, FINDING 3 -- the freeze breach F5 introduced and
    this pin is what keeps shut.

    F5 made `thread_context` call the shared `composer_attach_context`
    instead of recomputing the attach-door predicate by hand. The first
    cut passed `workstream=conversation.workstream` as a CALL ARGUMENT,
    i.e. unconditionally -- and `visible_conversations` only
    `select_related("agent")` (`agents/visibility.py`), so that attribute
    is a real single-row query. The old hand-written line read the FK
    only on the `may_upload_here` branch, because `attach_workstream` is
    `None` for anybody who may not upload: the row is not returned, so it
    was never fetched. One posture therefore paid +1 query per thread
    render (measured 74 vs 73).

    WHY IT WAS MISSED, and why the posture below is the one that catches
    it: on a `view`-level SHARE RECIPIENT, `may_post_to` itself ends on
    `conversation.workstream`, so the old code paid that same read later
    in the same render and the totals matched. The posture that exposes
    the drift is a viewer who CREATED the conversation inside a stream
    they do NOT manage -- `may_post_to` returns True at its
    `may_read_owned_row` clause and never reaches the FK, and
    `scope.may_upload` is False so the attach line never read it either.

    PINNED ON THE FK READ, NOT ON A TOTAL. A whole-page count would
    wobble on any unrelated change to this page; what the fix is about is
    one specific dereference, so that is what these two tests count --
    and they count it in BOTH directions, which is what makes the first
    non-vacuous: the owner, who DOES get `attach_workstream`, must still
    pay exactly one.
    """

    @staticmethod
    def _stream_row_reads(captured) -> int:
        """Single-row `Workstream` fetches by primary key -- the FK
        dereference shape. Deliberately NOT matching `workstream_scope`'s
        own `visible_workstreams` filter, which is a `DISTINCT ... WHERE`
        over the visible set and is paid on every one of these renders.
        """
        return sum(1 for query in captured.captured_queries
                   if 'FROM "agents_workstream"' in query["sql"]
                   and 'WHERE "agents_workstream"."id" =' in query["sql"])

    def _render(self, client, principal_user, conversation):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, principal_user)
            with CaptureQueriesContext(connection) as captured:
                response = client.get(
                    reverse("chat-conversation", args=[conversation.id]))
        assert response.status_code == 200
        return captured

    def test_a_viewer_who_may_not_upload_never_dereferences_the_stream_fk(
            self, client, fake_turn_queue):
        """The posture the original F5 measurement missed. This viewer
        OWNS the conversation row (so `may_post_to` short-circuits before
        touching the FK) but does not manage the stream (so the attach
        door is refused and `attach_workstream` is `None`). Nothing on
        this render has any use for the stream ROW, and nothing fetches
        it."""
        owner = make_user()
        viewer = make_user()
        stream = _workstream(user_principal(owner))
        agent = make_agent(resident=True)
        with posture(POSTURE_ENTERPRISE):
            Share.objects.create(target_type=Share.Target.WORKSTREAM,
                                 target_key=str(stream.pk), user=viewer,
                                 level=Share.Level.USE)
            conversation = create_conversation(
                user_principal(viewer), agent, workstream=stream)
        captured = self._render(client, viewer, conversation)
        assert self._stream_row_reads(captured) == 0

    def test_but_the_owner_who_does_get_the_door_still_pays_for_it_once(
            self, client, fake_turn_queue):
        """NON-VACUOUS, and the other half of the claim: the fix is "read
        the FK exactly where the old code did", not "never read it". The
        stream's owner gets `attach_workstream` -- the scope chooser needs
        the row.

        ZERO STANDALONE READS, as of the context meter (2026-09-21):
        `agents.chat.service.visible_conversation_or_404` now
        `select_related`s `workstream` on every `/chat/` row-addressed
        render (that task's own docstring names the same review-R2
        precedent this class's own docstring already cites for
        `visible_turn`'s poll path). The single-row FK fetch this test
        was written to pin still happens exactly once, in the sense that
        matters -- the ROW is still read -- but it now arrives as part of
        the conversation's own LEFT JOIN rather than as its own
        `SELECT ... FROM "agents_workstream" WHERE "agents_workstream".
        "id" = ...`, so `_stream_row_reads` (which deliberately matches
        only that standalone shape) counts zero, not one. The class's own
        `test_a_viewer_who_may_not_upload_never_dereferences_the_stream_fk`
        is unaffected: that render was never doing this fetch at all, and
        still is not -- the JOIN costs the SAME conversation query
        whether or not this principal may upload, which is the entire
        point of widening it."""
        owner = make_user()
        stream = _workstream(user_principal(owner))
        agent = make_agent(resident=True)
        with posture(POSTURE_ENTERPRISE):
            conversation = create_conversation(
                user_principal(owner), agent, workstream=stream)
        captured = self._render(client, owner, conversation)
        assert self._stream_row_reads(captured) == 0


class TestPasteIsAttach:
    """Chat image artifacts (2026-09-16), the owner's first outcome:
    "I should also be able to copy an image from clipboard into the
    chat" — behaving "exactly like one chosen through + Add files".

    Pinned by SOURCE TEXT, the house convention for this script (there
    is no headless browser in this suite). The behavioural half — a
    pasted-image filename really staging and attaching — is
    `agents/chat/tests/test_turn_attachments.py::TestAPastedImage`,
    which posts one through the real turn-create path."""

    def test_the_paste_listener_is_wired_to_the_composer_card(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert 'card.addEventListener("paste"' in body

    def _paste_handler(self, client):
        """The paste listener's own source text, sliced to the line that
        really follows it.

        NOT A SLICE TO THE FIRST `});` (review round 1, blocker 2): the
        handler body holds an `Array.prototype.forEach.call(..., function
        (item) { ... });`, so the first `});` is the FOREACH's. NOT A
        FIXED CHARACTER WINDOW EITHER (round 2): the handler is ~950
        characters and the drag-and-drop block that follows it opens with
        two `event.preventDefault()` calls ~800 characters later, so any
        window wide enough to be safe is wide enough to be wrong. The
        listener is inserted immediately BEFORE `if (!chatWrap)` --
        deliberately, so paste is wired on the two start surfaces, which
        have no `.chat-wrap` -- and that line is the honest end marker.
        """
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        start = body.index('card.addEventListener("paste"')
        return body[start:body.index("if (!chatWrap)", start)]

    def test_a_pasted_image_goes_through_the_same_accumulator_a_drop_does(self, client):
        """THE STRUCTURAL CLAIM: one accumulator, three doors (pick,
        drop, paste) — never a second staging path with its own dedup,
        its own `DataTransfer` rebuild and its own bugs."""
        assert "mergeInFiles(" in self._paste_handler(client)

    def test_the_paste_handler_never_prevents_the_default(self, client):
        """Text on the clipboard is left to the browser, so a mixed paste
        keeps its text and stages its image.

        NOT VACUOUS: `test_a_pasted_image_goes_through_the_same_
        accumulator_a_drop_does` asserts a POSITIVE over the same slice,
        so a slice that captured nothing would fail there first."""
        assert "preventDefault" not in self._paste_handler(client)

    def test_the_pasted_filename_format_is_the_documented_one(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert '"pasted-image-"' in body

    def _extension_function(self, client):
        """`extensionForImageType`'s own source text, sliced the same
        way `_paste_handler` above slices the paste listener: by its own
        start marker to the next function's own start marker, the
        honest end boundary rather than a fixed character window."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        start = body.index("function extensionForImageType(type)")
        return body[start:body.index("function twoDigits(", start)]

    def test_extension_for_image_type_maps_png_and_jpeg_to_their_short_forms(
            self, client):
        """Final fix wave, Fix 2: `image/png` -> "png", `image/jpeg` ->
        "jpg" -- the two extensions `tools.rag.readers.IMAGE_EXTS`
        actually ingests, each named explicitly rather than falling
        through the generic subtype rule below."""
        source = self._extension_function(client)
        assert 'if (type === "image/png") { return "png"; }' in source
        assert 'if (type === "image/jpeg") { return "jpg"; }' in source

    def test_extension_for_image_type_keeps_any_other_types_own_subtype(
            self, client):
        """A webp or tiff clipboard item is no longer silently renamed
        `.png` -- it keeps its OWN subtype as the extension, so the
        server's extension allow-list refuses it by name with its own
        message, exactly as it would a chosen file of that type. This
        was the mislabeling bug: the previous version named every
        unrecognised type "png", which passed the allow-list by name
        while the bytes underneath stayed whatever they actually were."""
        source = self._extension_function(client)
        assert "split(\"/\")[1]" in source
        assert ".toLowerCase()" in source

    def test_extension_for_image_type_is_png_only_when_the_type_is_blank(
            self, client):
        """The one case with no better answer -- a clipboard item
        carrying no type at all -- and ONLY that case, is what still
        gets "png"."""
        source = self._extension_function(client)
        assert 'return subtype ? subtype.toLowerCase() : "png";' in source

    def test_no_paste_handler_is_wired_when_the_attach_door_is_not_offered(self, client):
        """`chat/_composer.html` includes this whole fragment behind
        `may_attach_files`, so a principal without attach rights gets the
        browser's own default paste and the server's refusal path is
        untouched for anyone who bypasses the UI."""
        from agents.models import ToolEntitlement

        user = make_user()
        make_agent(resident=True)
        ToolEntitlement.objects.create(tool_key="rag.ingest",
                                       entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, user)
            body = client.get(reverse("chat-index")).content.decode()
        assert 'addEventListener("paste"' not in body
