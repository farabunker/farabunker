"""`readable_documents` versus `listable_documents` -- the split the
administer-versus-read decision exists for.

THE PAIR THAT MATTERS MOST: `listable_documents` returns every ROW to an
administrator with the content setting off, while `readable_documents`
returns none of the labelled ones to that same administrator. The library
page renders the row, the count and the label chips from the first; the
bytes come from the second.
"""
from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from agents.contracts.workstreams import WorkstreamScope
from identity.contracts.postures import (
    LIBRARY_LOCKED, POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL,
)
from identity.access import owner_fields
from identity.contracts.principals import OPEN_PRINCIPAL, SERVICE_PRINCIPAL
from tools.rag.access import (
    artifact_file_for, attached_documents, document_visibility, image_caption,
    image_caption_for, inline_text_for, listable_documents, may_label_document,
    readable_documents,
)
from tools.rag.models import Document, DocumentAttachment, DocumentEntitlement
from tools.rag.tests._helpers import (
    _workstream, grant, make_admin, make_document, make_entitlement, make_user, posture,
    reset_settings, seed_sweep_posture, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


def _labelled(entitlement, **overrides):
    document = make_document(**overrides)
    DocumentEntitlement.objects.create(document=document, entitlement=entitlement)
    return document


class TestReadableDocuments:
    def test_open_posture_returns_everything_with_no_permission_query(
            self, django_assert_num_queries):
        """The claim is in the NAME, so it is in the assertion too: one
        primary-key read of the settings singleton (inside
        `sees_all_content`), one for the document list, and nothing
        against a grant or label table.

        PINNED to `POSTURE_OPEN` explicitly: under a posture sweep
        (`FARABUNKER_TEST_POSTURE=enterprise`), an unpinned box would be
        enterprise, `OPEN_PRINCIPAL` would take the restricted branch,
        and this test would fail for a reason its own name denies."""
        labelled = _labelled(make_entitlement(name="Finance"))
        with posture(POSTURE_OPEN), django_assert_num_queries(2):
            assert list(readable_documents(OPEN_PRINCIPAL)) == [labelled]

    def test_a_holder_reads_a_labelled_document_and_a_non_holder_does_not(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        document = _labelled(finance)
        holder, other = make_user(), make_user()
        grant(finance, user=holder)
        grant(legal, user=other)
        with posture(POSTURE_ENTERPRISE):
            assert document in readable_documents(user_principal(holder))
            assert document not in readable_documents(user_principal(other))

    def test_any_one_entitlement_is_enough(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        document = _labelled(finance)
        DocumentEntitlement.objects.create(document=document, entitlement=legal)
        holder = make_user()
        grant(legal, user=holder)
        with posture(POSTURE_ENTERPRISE):
            assert document in readable_documents(user_principal(holder))

    def test_an_unlabelled_document_follows_the_library_posture(self):
        document = make_document()
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            assert document in readable_documents(user_principal(member))
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            assert document not in readable_documents(user_principal(member))

    def test_locked_never_hides_a_labelled_document_from_a_holder(self):
        """The library posture governs UNLABELLED documents and nothing
        else."""
        finance = make_entitlement(name="Finance")
        document = _labelled(finance)
        holder = make_user()
        grant(finance, user=holder)
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            assert document in readable_documents(user_principal(holder))

    def test_an_admin_with_the_setting_off_is_filtered_exactly_like_a_member(self):
        finance = make_entitlement(name="Finance")
        document = _labelled(finance)
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            assert document not in readable_documents(user_principal(admin))
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            assert document in readable_documents(user_principal(admin))

    def test_a_document_is_returned_once_even_when_two_labels_match(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        document = _labelled(finance)
        DocumentEntitlement.objects.create(document=document, entitlement=legal)
        holder = make_user()
        grant(finance, user=holder)
        grant(legal, user=holder)
        with posture(POSTURE_ENTERPRISE):
            assert list(readable_documents(user_principal(holder))) == [document]


class TestListableDocuments:
    def test_an_admin_sees_every_row_with_the_setting_off(self):
        """`is_admin`, NOT `sees_all_content`. Labelling, deleting and
        re-ingesting a document are administration, and an administrator
        MUST be able to label a document they are not cleared to read --
        otherwise the first label on a sensitive document can only be
        applied by somebody who does not need it, which is the wrong way
        round."""
        document = _labelled(make_entitlement(name="Finance"))
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            assert document in listable_documents(user_principal(admin))
            assert document not in readable_documents(user_principal(admin))

    def test_a_members_listing_is_exactly_their_readable_set(self):
        """Both sides must be genuinely non-empty: an equality of two
        empty lists would also pass a `listable_documents` that returned
        `Document.objects.none()`, proving nothing about the member half
        of the row-vs-content split. An unlabelled document and a
        document labelled with an entitlement the member actually holds
        both belong in their readable set; a document labelled with an
        entitlement they do NOT hold does not -- so `listable_documents`
        widening to every row (as it does for an admin) would make this
        inequality fail too."""
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        unlabelled = make_document()
        mine = _labelled(finance)
        _labelled(legal)
        member = make_user()
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            principal = user_principal(member)
            listable = list(listable_documents(principal))
            readable = list(readable_documents(principal))
        assert listable == readable != []
        assert set(listable) == {unlabelled, mine}


class TestSeesNothing:
    def test_a_member_with_no_entitlements_on_a_locked_library_sees_nothing(self):
        _labelled(make_entitlement(name="Finance"))
        member = make_user()
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            v = document_visibility(user_principal(member))
        assert v.sees_nothing is True

    def test_an_unrestricted_principal_never_sees_nothing(self):
        """PINNED to `POSTURE_OPEN`, and the branch is asserted directly
        (`.unrestricted`), not just its `sees_nothing` consequence: under
        a posture sweep, an unpinned box could be enterprise, and
        `sees_nothing` would still read `False` for `OPEN_PRINCIPAL` --
        but via `unlabelled_allowed`, not via `unrestricted`, which is
        not the branch this test's name claims to measure."""
        with posture(POSTURE_OPEN):
            v = document_visibility(OPEN_PRINCIPAL)
        assert v.unrestricted is True
        assert v.sees_nothing is False

    def test_a_service_principal_on_an_open_library_sees_the_unlabelled_part(self):
        with posture(POSTURE_ENTERPRISE):
            v = document_visibility(SERVICE_PRINCIPAL)
        assert v.unlabelled_allowed is True
        assert v.entitlement_ids == frozenset()
        assert v.sees_nothing is False


class TestMayLabelDocument:
    def test_an_admin_may_label_anything_and_an_owner_only_within_theirs(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        mine, theirs = _labelled(finance), _labelled(legal)
        owner, admin = make_user(), make_admin()
        grant(finance, user=owner, role="owner")
        with posture(POSTURE_ENTERPRISE):
            assert may_label_document(user_principal(admin), theirs) is True
            assert may_label_document(user_principal(owner), mine) is True
            assert may_label_document(user_principal(owner), theirs) is False

    def test_a_plain_member_may_label_nothing(self):
        finance = make_entitlement(name="Finance")
        document = _labelled(finance)
        member = make_user()
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            assert may_label_document(user_principal(member), document) is False


class TestThePosturesAgree:
    def test_personal_and_enterprise_answer_identically(self):
        finance = make_entitlement(name="Finance")
        document = _labelled(finance)
        member = make_user()
        answers = []
        for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
            with posture(name):
                answers.append(document in readable_documents(user_principal(member)))
        assert answers[0] == answers[1] is False


def test_readable_documents_excludes_contained_rows_by_default():
    """`None` — every existing caller, unedited — means the universal
    library exactly as today, which is the SAFE default: a caller that
    forgets the parameter sees no contained document rather than all of
    them."""
    stream = _workstream()
    universal = make_document()
    contained = make_document(workstream=stream)
    with posture("open"):
        keys = set(readable_documents(OPEN_PRINCIPAL).values_list("pk", flat=True))
    assert universal.pk in keys
    assert contained.pk not in keys


def test_readable_documents_admits_this_streams_contained_rows_when_asked():
    """CONTAINMENT IS STREAM-AWARE, NOT ABSOLUTE (spec §8.1, M1). An
    earlier draft filtered contained documents out unconditionally, which
    would have made every contained document — notes included — 404 at
    `rag-document-file` for its own stream's members."""
    stream, other = _workstream(), _workstream()
    here = make_document(workstream=stream)
    elsewhere = make_document(workstream=other)
    with posture("open"):
        keys = set(readable_documents(OPEN_PRINCIPAL, workstream_id=stream.pk)
                   .values_list("pk", flat=True))
    assert here.pk in keys
    assert elsewhere.pk not in keys


def test_containment_is_a_corpus_rule_not_an_entitlement_rule():
    """The clause applies in the OPEN posture and to an administrator
    with `admin_sees_content` on, exactly as it applies to everybody
    else — which is why it sits outside the `unrestricted` branch."""
    admin = make_admin()
    stream = _workstream()
    contained = make_document(workstream=stream)
    with posture("enterprise", admin_sees_content=True):
        keys = set(readable_documents(user_principal(admin)).values_list("pk", flat=True))
    assert contained.pk not in keys


def test_permits_and_readable_documents_agree_about_containment():
    """`permits` is the IN-MEMORY MIRROR of `readable_documents`' filter
    and its docstring says its whole purpose is that `DocumentsListView`
    must not "show a link `readable_documents`' own route would 404 on".
    Changing one and not the other reintroduces precisely the failure it
    was written to prevent (spec §8.1, §17.2 cell 6)."""
    stream, other = _workstream(), _workstream()
    here = make_document(workstream=stream)
    elsewhere = make_document(workstream=other)
    universal = make_document()
    with posture("open"):
        v = document_visibility(OPEN_PRINCIPAL)
        for doc in (here, elsewhere, universal):
            in_orm = readable_documents(OPEN_PRINCIPAL).filter(pk=doc.pk).exists()
            assert v.permits(doc) is in_orm, doc.pk
            in_stream_orm = readable_documents(
                OPEN_PRINCIPAL, workstream_id=stream.pk).filter(pk=doc.pk).exists()
            assert v.permits(doc, workstream_id=stream.pk) is in_stream_orm, doc.pk


def test_listable_documents_still_lists_every_row_for_an_administrator():
    """An administrator lists every document ROW including contained
    ones, marked with their stream — pruning and re-ingesting the library
    is administration, and a document an administrator cannot see the
    existence of is a document nobody can clean up. Listing a row is not
    opening it."""
    admin = make_admin()
    stream = _workstream()
    contained = make_document(workstream=stream)
    with posture("enterprise", admin_sees_content=False):
        keys = set(listable_documents(user_principal(admin)).values_list("pk", flat=True))
    assert contained.pk in keys



def _attach(document, conversation_id) -> None:
    """A `DocumentAttachment` row (round 11 review I-5's junction
    table), the shape `document_upload`'s own `_attach` writes -- a
    plain `.objects.create` here since this test module already reaches
    `DocumentEntitlement.objects.create` directly for the identical
    reason (a test file is exempt from the cross-column import law)."""
    DocumentAttachment.objects.create(document=document, conversation_id=conversation_id)


class TestAttachedDocuments:
    """`tools.rag.access.attached_documents` -- round 11's own provider,
    registered into `agents.contracts.attachments` (`tools/rag/apps.py::
    ready()`) so the conversation page's strip and the model's own
    prompt read the SAME query. Exercised here as a plain function call
    -- `agents/chat/tests/test_thread.py` and `agents/runtime/tests/
    test_prompt.py` cover the two callers that reach it through the
    registry."""

    def test_it_returns_only_documents_attached_to_this_conversation(self):
        conversation_id = uuid.uuid4()
        other_id = uuid.uuid4()
        mine = make_document(title="Mine")
        _attach(mine, conversation_id)
        not_mine = make_document(title="Not mine")
        _attach(not_mine, other_id)
        make_document(title="Never attached anywhere")
        with posture("open"):
            docs = attached_documents(OPEN_PRINCIPAL, conversation_id=conversation_id)
        assert [d["id"] for d in docs] == [mine.pk]
        assert docs[0]["title"] == "Mine"

    def test_it_is_readable_filtered_not_a_raw_query(self):
        """THE BRIEF'S OWN REQUIREMENT: a recipient who may not read a
        document's content must not see its title, even though it is
        genuinely attached to their own conversation."""
        conversation_id = uuid.uuid4()
        legal = make_entitlement(name="Legal")
        doc = make_document(title="Confidential")
        _attach(doc, conversation_id)
        DocumentEntitlement.objects.create(document=doc, entitlement=legal)
        stranger = make_user()
        with posture("enterprise"):
            docs = attached_documents(user_principal(stranger), conversation_id=conversation_id)
        assert docs == []

    def test_it_admits_a_contained_document_given_the_conversations_own_stream_scope(self):
        """CONTAINMENT (`readable_documents`' own rule, reached through
        `tools.rag.workstreams.stream_documents` when `stream` is given):
        a document contained in stream W is admitted ONLY when a
        `WorkstreamScope` naming W is threaded through -- the caller's
        own job, per `attached_documents`' docstring, since `tools/rag`
        cannot resolve the conversation's own stream itself."""
        stream = _workstream()
        conversation_id = uuid.uuid4()
        contained = make_document(workstream=stream, title="Contained")
        _attach(contained, conversation_id)
        scope = WorkstreamScope(workstream_id=stream.pk, wall=frozenset(),
                                default_upload_placement="", may_upload=False)
        with posture("open"):
            without_stream = attached_documents(
                OPEN_PRINCIPAL, conversation_id=conversation_id)
            with_stream = attached_documents(
                OPEN_PRINCIPAL, conversation_id=conversation_id, stream=scope)
        assert without_stream == []
        assert [d["id"] for d in with_stream] == [contained.pk]

    def test_a_universal_attachment_outside_the_streams_wall_is_flagged_not_in_corpus(self):
        """R-1 (round 11 RE-review, replacing this test's own first cut
        which asserted absence -- the re-review's own finding was that
        absence IS the bug, not the fix): a universal document under an
        entitlement OUTSIDE the wall is READABLE (the principal holds
        that entitlement) but not admitted by THIS stream's own corpus
        (`tools.rag.workstreams.stream_documents`, the same formula
        retrieval narrows a stream turn by) -- still RETURNED, flagged
        `in_corpus=False`, never silently dropped."""
        stream = _workstream()
        inside = make_entitlement(name="Inside the wall")
        outside = make_entitlement(name="Outside the wall")
        conversation_id = uuid.uuid4()
        doc = make_document(title="Universal, labelled outside the wall")
        DocumentEntitlement.objects.create(document=doc, entitlement=outside)
        _attach(doc, conversation_id)
        holder = make_user()
        grant(outside, user=holder)
        walled = WorkstreamScope(workstream_id=stream.pk, wall=frozenset({inside.pk}),
                                 default_upload_placement="", may_upload=False)
        with posture("enterprise"):
            docs = attached_documents(
                user_principal(holder), conversation_id=conversation_id, stream=walled)
        assert [d["id"] for d in docs] == [doc.pk]
        assert docs[0]["in_corpus"] is False
        # ROUND 17: the WALL is why, not the new toggle -- pinned here
        # against the PRE-round-17 cause this test already built, so a
        # future change that widened `"universal_off"` to apply here by
        # accident is caught.
        assert docs[0]["not_in_corpus_reason"] == "wall"

    def test_an_unreadable_attachment_stays_absent_even_though_it_is_out_of_corpus_too(self):
        """The DISTINCTION R-1's own fix depends on: "readable but out
        of corpus" (flagged, returned) is not the same as "not readable
        at all" (dropped, `test_it_is_readable_filtered_not_a_raw_query`'s
        own case) -- a stranger who holds NEITHER the document's own
        label NOR anything in the wall gets nothing back for it, exactly
        as before R-1."""
        stream = _workstream()
        inside = make_entitlement(name="Inside the wall")
        outside = make_entitlement(name="Outside the wall")
        conversation_id = uuid.uuid4()
        doc = make_document(title="Universal, labelled outside the wall")
        DocumentEntitlement.objects.create(document=doc, entitlement=outside)
        _attach(doc, conversation_id)
        stranger = make_user()
        walled = WorkstreamScope(workstream_id=stream.pk, wall=frozenset({inside.pk}),
                                 default_upload_placement="", may_upload=False)
        with posture("enterprise"):
            docs = attached_documents(
                user_principal(stranger), conversation_id=conversation_id, stream=walled)
        assert docs == []

    def test_the_identical_document_is_in_corpus_once_its_own_label_is_inside_the_wall(self):
        """The positive twin of the wall test above -- same document,
        same principal, only the wall itself differs."""
        stream = _workstream()
        inside = make_entitlement(name="Inside the wall")
        conversation_id = uuid.uuid4()
        doc = make_document(title="Universal, labelled inside the wall")
        DocumentEntitlement.objects.create(document=doc, entitlement=inside)
        _attach(doc, conversation_id)
        holder = make_user()
        grant(inside, user=holder)
        walled = WorkstreamScope(workstream_id=stream.pk, wall=frozenset({inside.pk}),
                                 default_upload_placement="", may_upload=False)
        with posture("enterprise"):
            docs = attached_documents(
                user_principal(holder), conversation_id=conversation_id, stream=walled)
        assert [d["id"] for d in docs] == [doc.pk]
        assert docs[0]["in_corpus"] is True

    def test_a_loose_conversations_attachment_is_always_in_corpus(self):
        """No wall to narrow by outside a stream -- the second query is
        skipped entirely (`attached_documents`'s own docstring) and
        every readable attachment is trivially in-corpus."""
        conversation_id = uuid.uuid4()
        doc = make_document()
        _attach(doc, conversation_id)
        with posture("open"):
            docs = attached_documents(OPEN_PRINCIPAL, conversation_id=conversation_id)
        assert docs[0]["in_corpus"] is True

    def test_a_contained_document_is_in_corpus_regardless_of_the_wall(self):
        """Containment is not narrowed by the wall (author decision 5's
        own scope: the wall narrows the UNIVERSAL leg only) -- a
        document CONTAINED in this stream is in-corpus even when the
        wall excludes every unlabelled universal one."""
        stream = _workstream()
        inside = make_entitlement(name="Inside the wall")
        conversation_id = uuid.uuid4()
        contained = make_document(workstream=stream, title="Contained, unlabelled")
        _attach(contained, conversation_id)
        walled = WorkstreamScope(workstream_id=stream.pk, wall=frozenset({inside.pk}),
                                 default_upload_placement="", may_upload=False)
        with posture("open"):
            docs = attached_documents(
                OPEN_PRINCIPAL, conversation_id=conversation_id, stream=walled)
        assert [d["id"] for d in docs] == [contained.pk]
        assert docs[0]["in_corpus"] is True

    def test_include_universal_off_flags_a_universal_attachment_with_the_new_cause(self):
        """ROUND 17 (owner, verbatim: "a bool in settings to use all rag
        documents ... default it on"). `include_universal=False` is a
        SECOND, DISTINCT cause for `in_corpus=False` -- named honestly
        (`not_in_corpus_reason == "universal_off"`), never conflated
        with the wall's own pre-existing cause above, EVEN THOUGH the
        wall here is EMPTY (so nothing about the wall itself would ever
        have excluded this document) -- the toggle alone is doing the
        excluding."""
        stream = _workstream()
        conversation_id = uuid.uuid4()
        doc = make_document(title="Universal, unlabelled")
        _attach(doc, conversation_id)
        toggled_off = WorkstreamScope(workstream_id=stream.pk, wall=frozenset(),
                                      default_upload_placement="", may_upload=False,
                                      include_universal=False)
        with posture("open"):
            docs = attached_documents(
                OPEN_PRINCIPAL, conversation_id=conversation_id, stream=toggled_off)
        assert [d["id"] for d in docs] == [doc.pk]
        assert docs[0]["in_corpus"] is False
        assert docs[0]["not_in_corpus_reason"] == "universal_off"

    def test_include_universal_off_leaves_pinned_contained_and_chat_attached_untouched(self):
        """THE BRIEF'S OWN WORDS: "pinned+contained+the round-12
        conversation leg all REMAIN (the chat-attachment guarantee
        outranks the toggle)" -- NON-VACUOUS POSITIVE TWINS, all three
        in ONE conversation alongside a universal attachment the SAME
        toggle DOES exclude, so "everything stayed" is not merely
        "nothing was tested"."""
        stream = _workstream()
        conversation_id = uuid.uuid4()
        contained = make_document(workstream=stream, title="Contained")
        pinned = make_document(title="Pinned")
        from tools.rag.models import WorkstreamPin

        WorkstreamPin.objects.create(workstream=stream, document=pinned)
        chat_scoped = make_document(title="Chat-scoped", scope=Document.Scope.CONVERSATION)
        excluded_universal = make_document(title="Excluded universal")
        for doc in (contained, pinned, chat_scoped, excluded_universal):
            _attach(doc, conversation_id)
        toggled_off = WorkstreamScope(workstream_id=stream.pk, wall=frozenset(),
                                      default_upload_placement="", may_upload=False,
                                      include_universal=False,
                                      pinned_file_ids=frozenset({pinned.pk}))
        with posture("open"):
            docs = attached_documents(
                OPEN_PRINCIPAL, conversation_id=conversation_id, stream=toggled_off)
        by_id = {d["id"]: d for d in docs}
        assert by_id[contained.pk]["in_corpus"] is True
        assert by_id[pinned.pk]["in_corpus"] is True
        assert by_id[chat_scoped.pk]["in_corpus"] is True
        assert by_id[chat_scoped.pk]["chat_scoped"] is True
        assert by_id[excluded_universal.pk]["in_corpus"] is False
        assert by_id[excluded_universal.pk]["not_in_corpus_reason"] == "universal_off"

    def test_it_carries_status_and_status_detail(self):
        from tools.rag.models import Document

        conversation_id = uuid.uuid4()
        doc = make_document(status=Document.Status.FAILED, status_detail="unsupported codec")
        _attach(doc, conversation_id)
        with posture("open"):
            docs = attached_documents(OPEN_PRINCIPAL, conversation_id=conversation_id)
        assert docs[0]["status"] == "failed"
        assert docs[0]["status_detail"] == "unsupported codec"

    def test_the_query_cost_does_not_scale_with_attachment_count(self):
        """GC-6, the same equality idiom `test_ten_held_entitlements_
        cost_exactly_the_same`-shaped pins already use elsewhere in this
        suite: one attachment and ten cost the identical number of
        queries -- proof there is no per-row N+1 hiding in the join."""
        one_id = uuid.uuid4()
        _attach(make_document(), one_id)
        many_id = uuid.uuid4()
        for _ in range(10):
            _attach(make_document(), many_id)
        with posture("open"):
            with CaptureQueriesContext(connection) as one:
                list(attached_documents(OPEN_PRINCIPAL, conversation_id=one_id))
            with CaptureQueriesContext(connection) as many:
                result = list(attached_documents(OPEN_PRINCIPAL, conversation_id=many_id))
        assert len(result) == 10
        assert len(many) == len(one)

    def test_the_chat_scoped_query_cost_does_not_scale_with_attachment_count(self):
        """ROUND 12 REVIEW NIT 1: `attached_documents`' own unconditional
        `Document.objects.filter(scope="conversation", ...)` query --
        run on EVERY conversation render/turn regardless of whether
        anything chat-scoped is attached at all -- was proven bounded
        only for the universal/contained path above (which counts
        entitlement-grant reads specifically, a different query
        entirely). This pins the CHAT-SCOPED path's own cost directly:
        one chat-scoped attachment and ten (ten DIFFERENT documents,
        each honouring the one-attachment-per-document invariant) cost
        the IDENTICAL number of queries."""
        from tools.rag.models import Document

        one_id = uuid.uuid4()
        doc = make_document(scope=Document.Scope.CONVERSATION)
        DocumentAttachment.objects.create(document=doc, conversation_id=one_id)
        many_id = uuid.uuid4()
        for _ in range(10):
            many_doc = make_document(scope=Document.Scope.CONVERSATION)
            DocumentAttachment.objects.create(document=many_doc, conversation_id=many_id)
        with posture("open"):
            with CaptureQueriesContext(connection) as one:
                result_one = list(attached_documents(OPEN_PRINCIPAL, conversation_id=one_id))
            with CaptureQueriesContext(connection) as many:
                result_many = list(attached_documents(OPEN_PRINCIPAL, conversation_id=many_id))
        assert len(result_one) == 1
        assert len(result_many) == 10
        assert len(many) == len(one)

    def test_every_row_carries_a_reference_a_future_tool_can_name(self):
        """NOT ONLY IMAGES: a prose attachment is equally referenceable
        by a future tool with a file input, so every row gets one. Minted
        HERE, in this column, because `agents/` may not import it to mint
        its own."""
        from agents.contracts.artifacts import artifact_title, parse_artifact

        conversation_id = uuid.uuid4()
        doc = make_document(title="Q3 report.pdf")
        _attach(doc, conversation_id)
        with posture("open"):
            row = attached_documents(OPEN_PRINCIPAL, conversation_id=conversation_id)[0]
        assert parse_artifact(row["reference"]) == ("document", doc.pk)
        assert artifact_title(row["reference"]) == "Q3 report.pdf"

    def test_an_image_row_is_flagged_and_captioned(self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        conversation_id = uuid.uuid4()
        doc = make_document(title="photo.png", media_type="image/png")
        _finished_sidecar(doc, [{"page": 1, "text": "A red bicycle against a wall."}])
        _attach(doc, conversation_id)
        with posture("open"):
            row = attached_documents(OPEN_PRINCIPAL, conversation_id=conversation_id)[0]
        assert row["is_image"] is True
        assert row["caption"] == "A red bicycle against a wall."

    def test_a_prose_row_is_not_an_image_and_carries_no_caption(self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        conversation_id = uuid.uuid4()
        _attach(make_document(title="notes.md", media_type="text/markdown"), conversation_id)
        with posture("open"):
            row = attached_documents(OPEN_PRINCIPAL, conversation_id=conversation_id)[0]
        assert row["is_image"] is False
        assert row["caption"] == ""

    def test_an_image_row_carries_its_caption_state(self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        conversation_id = uuid.uuid4()
        doc = make_document(title="photo.png", media_type="image/png")
        _finished_sidecar(doc, [{"page": 1, "kind": "description",
                                 "text": "A red bicycle."}], described=True)
        _attach(doc, conversation_id)
        with posture("open"):
            row = attached_documents(OPEN_PRINCIPAL, conversation_id=conversation_id)[0]
        assert row["caption_state"] == "ready"
        assert row["caption"] == "A red bicycle."

    def test_an_image_whose_description_found_nothing_is_flagged_empty(
            self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        conversation_id = uuid.uuid4()
        doc = make_document(title="blank.png", media_type="image/png")
        _finished_sidecar(doc, [], described=True)
        _attach(doc, conversation_id)
        with posture("open"):
            row = attached_documents(OPEN_PRINCIPAL, conversation_id=conversation_id)[0]
        assert row["caption_state"] == "empty"
        assert row["caption"] == ""

    def test_a_prose_row_is_not_applicable_and_uncaptioned(self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        conversation_id = uuid.uuid4()
        _attach(make_document(title="notes.md", media_type="text/markdown"), conversation_id)
        with posture("open"):
            row = attached_documents(OPEN_PRINCIPAL, conversation_id=conversation_id)[0]
        assert row["caption_state"] == "n/a"
        assert row["is_image"] is False


class TestTheCaptionNeverCrossesALineTheBytesDoNot:
    """PACKET FINDING A (moderate). The chat-scoped leg of
    `attached_documents` is deliberately NOT readable-filtered -- the
    round-12 ruling puts a chat-scoped document's TITLE and STATUS in
    front of anyone who can see the conversation while its BYTES stay
    uploader/admin-only (`readable_documents`' own conversation clause).

    A CAPTION IS CONTENT, NOT A TITLE. Before this gate, a non-uploader
    in a shared conversation -- and a `sees_nothing` principal most
    sharply, whose `retrieve_nodes` short-circuits to empty before the
    conversation leg is ever reached -- received 600 characters of
    somebody else's extracted image text in their SYSTEM message, on a
    row flagged `in_corpus=False`, for a document their own
    `rag__search` is structurally guaranteed to return nothing for.

    `readable` is the SAME predicate `/rag/documents/<id>/file/`
    enforces on the bytes, so the caption and the thumbnail can never
    show what the title link could not."""

    def _shared_chat_scoped_image(self, tmp_path, settings, uploader):
        settings.DOCUMENTS_DIR = tmp_path
        conversation_id = uuid.uuid4()
        doc = make_document(
            title="theirs.png", media_type="image/png",
            scope=Document.Scope.CONVERSATION, **owner_fields(user_principal(uploader)),
        )
        _finished_sidecar(doc, [{"page": 1, "kind": "description",
                                 "text": "A private whiteboard."}], described=True)
        _attach(doc, conversation_id)
        return conversation_id, doc

    def test_the_uploader_gets_the_caption_and_a_readable_row(self, tmp_path, settings):
        uploader = make_user()
        conversation_id, _doc = self._shared_chat_scoped_image(tmp_path, settings, uploader)
        with posture(POSTURE_ENTERPRISE):
            row = attached_documents(
                user_principal(uploader), conversation_id=conversation_id)[0]
        assert row["readable"] is True
        assert row["caption"] == "A private whiteboard."

    def test_a_non_uploader_sees_the_row_but_no_caption(self, tmp_path, settings):
        """TITLE AND STATUS YES, CONTENT NO -- the round-12 ruling,
        applied to the one key that is content."""
        uploader, other = make_user(), make_user()
        conversation_id, _doc = self._shared_chat_scoped_image(tmp_path, settings, uploader)
        with posture(POSTURE_ENTERPRISE):
            row = attached_documents(
                user_principal(other), conversation_id=conversation_id)[0]
        assert row["title"] == "theirs.png"          # still named
        assert row["readable"] is False
        assert row["caption"] == ""                   # never handed over
        assert "whiteboard" not in str(row)           # not in ANY key

    def test_a_sees_nothing_principal_gets_no_caption(self, tmp_path, settings):
        """THE SHARPEST CASE the packet names: a signed-in principal with
        ZERO entitlements on a locked library. `retrieve_nodes`
        short-circuits to empty for them BEFORE the conversation leg's
        own exemption is reached, so the row is already `in_corpus=
        False` -- handing them the content anyway was the contradiction."""
        uploader, nobody = make_user(), make_user()
        conversation_id, _doc = self._shared_chat_scoped_image(tmp_path, settings, uploader)
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            row = attached_documents(
                user_principal(nobody), conversation_id=conversation_id)[0]
        assert row["in_corpus"] is False
        assert row["readable"] is False
        assert row["caption"] == ""

    def test_an_ordinary_readable_attachment_is_readable_with_no_extra_query(
            self, tmp_path, settings, django_assert_num_queries):
        """THE NON-CHAT-SCOPED LEG IS READABLE BY CONSTRUCTION -- it came
        out of `readable_documents` -- so it needs no second check and
        pays for none. The extra query runs ONLY when there is something
        chat-scoped to answer for, the same "only when there is
        something to narrow" discipline this function already follows
        for `in_corpus`.

        PINNED (review fix round 1, minor 6) at the REAL count for this
        posture/scenario -- 3: `Document.objects.filter(scope=
        "conversation", ...)` (always paid, and empty here), settings/
        permission reads `readable_documents` itself does under
        `POSTURE_OPEN`, and the ONE combined `readable_documents(...)
        .exclude(...).filter(...).annotate(...)` query this ordinary
        attachment's own row comes from. What this pins is the ABSENCE
        of a FOURTH query -- the `chat_scoped_readable_ids` read this
        function pays only when `chat_scoped` is non-empty, which it
        never is here."""
        settings.DOCUMENTS_DIR = tmp_path
        conversation_id = uuid.uuid4()
        _attach(make_document(title="shared.png", media_type="image/png"), conversation_id)
        with posture(POSTURE_OPEN), django_assert_num_queries(3):
            rows = attached_documents(OPEN_PRINCIPAL, conversation_id=conversation_id)
        assert rows[0]["readable"] is True


class TestInlineTextForIsReadBounded:
    """ROUND-13 FIX-VERIFY IMPORTANT: "a perfectly legal 2 GiB `.txt`/
    `.pdf` attachment is fully parsed into memory, synchronously,
    inside the worker's prompt build, on every carrying turn, to keep
    4,000 characters of it." `inline_text_for`'s own `_INLINE_READ_
    SIZE_CAP_BYTES` (`tools.rag.access`, 20 MiB) is the fix -- a
    WHOLE-FILE size check before `read_prose_documents` is ever called.
    Both directions pinned: an over-cap file never triggers the parse
    at all (PROBE-SHAPED -- a monkeypatched `read_prose_documents` that
    would raise if reached), and an ordinary, well-under-cap file still
    extracts exactly as before.
    """

    def _document_at(self, tmp_path, *, size: int, suffix=".txt"):
        path = tmp_path / f"attachment{suffix}"
        path.write_bytes(b"x" * size)
        return make_document(source_path=str(path))

    def test_a_file_over_the_cap_never_reaches_the_parser(self, tmp_path, monkeypatch):
        from tools.rag import access as access_module

        def _must_not_be_called(source):
            raise AssertionError(
                "read_prose_documents was called for a file over the inline read cap -- "
                "the whole point of the bound is that this never happens"
            )

        monkeypatch.setattr("tools.rag.readers.read_prose_documents", _must_not_be_called)
        doc = self._document_at(
            tmp_path, size=access_module._INLINE_READ_SIZE_CAP_BYTES + 1)
        assert inline_text_for(doc.pk) == ""

    def test_a_file_exactly_at_the_cap_still_extracts(self, tmp_path, monkeypatch):
        """THE BOUNDARY, THE OTHER SIDE: `size > cap` is what refuses --
        a file AT the cap (not over it) must still reach the real
        reader, proven with a fake reader standing in for `tools.rag.
        readers.read_prose_documents` (a real 20 MiB text extraction
        would make this test slow for no extra coverage; the boundary
        arithmetic is what is under test, not the reader itself, which
        has its own suite in `test_readers.py`)."""
        from types import SimpleNamespace

        from tools.rag import access as access_module

        calls = []

        def _fake_reader(source):
            calls.append(source)
            return [SimpleNamespace(text="the extracted prose")]

        monkeypatch.setattr("tools.rag.readers.read_prose_documents", _fake_reader)
        doc = self._document_at(tmp_path, size=access_module._INLINE_READ_SIZE_CAP_BYTES)
        assert inline_text_for(doc.pk) == "the extracted prose"
        assert len(calls) == 1

    def test_an_ordinary_small_file_extracts_normally(self, tmp_path):
        """INLINE STILL WORKS UNDER THE CAP -- the addendum's own
        acceptance case, a real extraction through the REAL reader (no
        mock), proving the bound changed nothing about the ordinary
        path it was never meant to touch."""
        path = tmp_path / "attachment.txt"
        path.write_text("the quarterly figures are up 12%")
        doc = make_document(source_path=str(path))
        assert inline_text_for(doc.pk) == "the quarterly figures are up 12%"


class TestPermitsAnswersTheSameQuestionAsReadableDocuments:
    """C-03. `permits()` is `readable_documents()`'s one-row twin, and it
    was missing the conversation-scope axis: a chat-scoped document
    belonging to somebody else came back permitted, because it carries no
    labels and `unlabelled_allowed` was the last word. Masked today by
    `listable_documents`'s pre-filter, which is exactly the kind of luck a
    predicate should not depend on."""

    def test_a_chat_scoped_document_belonging_to_someone_else_is_not_permitted(self):
        mine, theirs = make_user(), make_user()
        document = make_document(scope=Document.Scope.CONVERSATION,
                                 **owner_fields(user_principal(theirs)))
        with posture("enterprise"):
            visibility = document_visibility(user_principal(mine))
            assert visibility.permits(document) is False

    def test_my_own_chat_scoped_document_is_permitted(self):
        """The other direction -- the axis must admit the uploader, or it
        is a refusal rather than a rule."""
        mine = make_user()
        document = make_document(scope=Document.Scope.CONVERSATION,
                                 **owner_fields(user_principal(mine)))
        with posture("enterprise"):
            assert document_visibility(user_principal(mine)).permits(document) is True

    @pytest.mark.parametrize("scope", [Document.Scope.UNIVERSAL])
    def test_an_ordinary_library_document_is_unaffected(self, scope):
        """And the axis must not change the answer for the rows that were
        already right."""
        mine, theirs = make_user(), make_user()
        document = make_document(scope=scope, **owner_fields(user_principal(theirs)))
        with posture("enterprise"):
            visibility = document_visibility(user_principal(mine))
            assert visibility.permits(document) == visibility.unlabelled_allowed

    def test_a_workstream_view_never_admits_a_chat_scoped_document(self):
        """The clause with NO pre-change analogue: `workstream_id is not
        None` refuses a chat-scoped document outright, for a restricted
        visibility AND for an unrestricted one -- containment is a
        corpus rule (spec §13), and a chat-scoped document has no
        corpus to be contained by, no matter how wide the visibility is
        otherwise."""
        stream = _workstream()
        mine, admin = make_user(), make_admin()
        document = make_document(scope=Document.Scope.CONVERSATION,
                                 **owner_fields(user_principal(mine)))
        with posture("enterprise"):
            restricted = document_visibility(user_principal(mine))
            assert restricted.permits(document, workstream_id=stream.pk) is False
        with posture("enterprise", admin_sees_content=True):
            unrestricted = document_visibility(user_principal(admin))
            assert unrestricted.unrestricted is True
            assert unrestricted.permits(document, workstream_id=stream.pk) is False

    def test_sees_all_content_reaches_anyones_chat_scoped_document(self):
        """The `self.unrestricted` leg: an administrator with the content
        setting on reaches a chat-scoped document belonging to somebody
        else, outside any workstream -- the same rule
        `readable_documents`'s own `if v.unrestricted:` branch documents
        ('`sees_all_content` sees EVERY chat-scoped document there ...
        not merely the uploader's own')."""
        admin, theirs = make_admin(), make_user()
        document = make_document(scope=Document.Scope.CONVERSATION,
                                 **owner_fields(user_principal(theirs)))
        with posture("enterprise", admin_sees_content=True):
            visibility = document_visibility(user_principal(admin))
            assert visibility.unrestricted is True
            assert visibility.permits(document) is True

    def test_a_locked_librarys_unlabelled_refusal_does_not_reach_my_own_chat_scoped_document(self):
        """The live behaviour change (the one caller,
        `DocumentsListView`, was masked from ever seeing it by
        `listable_documents`'s pre-filter): on a `LIBRARY_LOCKED` box
        `unlabelled_allowed` is False, which used to be `permits()`'s
        last word for an unlabelled, uncontained row -- a chat-scoped
        document is both. `readable_documents`'s own `:329-331`
        disjunct never applies that refusal to the uploader's own
        chat-scoped row, and now neither does `permits()`."""
        mine = make_user()
        document = make_document(scope=Document.Scope.CONVERSATION,
                                 **owner_fields(user_principal(mine)))
        with posture("enterprise", library_posture=LIBRARY_LOCKED):
            visibility = document_visibility(user_principal(mine))
            assert visibility.unlabelled_allowed is False
            assert visibility.permits(document) is True


def _finished_sidecar(doc, segments, *, produced_at="2026-09-16T10:00:00+00:00",
                      described=None):
    """Write a FINISHED extraction sidecar for `doc`, the exact shape
    `tools.rag.media._extraction_sidecar_payload` writes -- through
    `tools.rag.store.sidecar_path`, so this test is reading the same
    file the real pipeline writes rather than a shape invented here.

    `described` (preview UAT, 2026-09-17): `None` writes NO `described`
    key -- the shape every image ingested before this change has on
    disk, which is the exact case `"undescribed"` exists for.
    """
    import json

    from tools.rag import store

    payload = {
        "version": 1, "method": "vision", "source": "photo.png",
        "source_sha256": "a" * 64,
        "model": {"engine": "stub", "model_id": "stub"},
        "produced_at": produced_at,
        "segments": segments,
    }
    if described is not None:
        payload["described"] = described
    path = store.sidecar_path(doc.pk)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


class TestImageCaptionFor:
    """`tools.rag.access.image_caption_for` -- the attachments provider's
    `caption` key, read MODEL-FREE out of the extraction sidecar the
    ingest job already finished writing (the owner addendum, binding:
    turn building never calls a model and never waits on ingest)."""

    def test_a_finished_image_sidecar_becomes_the_caption(self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(title="photo.png", media_type="image/png")
        _finished_sidecar(doc, [{"page": 1, "text": "A red bicycle against a wall."}])
        assert image_caption_for(doc) == "A red bicycle against a wall."

    def test_several_segments_join_into_one_line(self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [{"page": 1, "text": "A red bicycle."},
                                {"page": 2, "text": "A wall behind it."}])
        assert image_caption_for(doc) == "A red bicycle. A wall behind it."

    def test_whitespace_runs_and_newlines_collapse_to_single_spaces(
            self, tmp_path, settings):
        """This text lands in a SYSTEM message on one line. A newline
        that survived would let extracted text forge a second, separate-
        looking instruction line."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [{"page": 1, "text": "A red\n\n  bicycle\tagainst\na wall."}])
        assert image_caption_for(doc) == "A red bicycle against a wall."

    def test_an_unfinished_sidecar_answers_blank(self, tmp_path, settings):
        """`produced_at` unset IS "still running" -- the same completeness
        gate `tools.rag.ingest._source_documents` applies before ever
        indexing one."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [{"page": 1, "text": "half of it"}], produced_at=None)
        assert image_caption_for(doc) == ""

    def test_no_sidecar_at_all_answers_blank(self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        assert image_caption_for(make_document(media_type="image/png")) == ""

    def test_a_corrupt_sidecar_answers_blank_rather_than_raising(self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        from tools.rag import store

        path = store.sidecar_path(doc.pk)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json at all")
        assert image_caption_for(doc) == ""

    def test_a_sidecar_with_no_extractable_text_answers_blank(self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [])
        assert image_caption_for(doc) == ""

    def test_a_non_image_document_answers_blank_without_reading_anything(
            self, tmp_path, settings):
        """A PDF gets a page-keyed extraction sidecar too -- that is a
        TRANSCRIPT, not a caption, and it belongs to `rag__search`, not
        to a one-line prompt bullet."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="application/pdf")
        _finished_sidecar(doc, [{"page": 1, "text": "Clause 4.1 of the agreement"}])
        assert image_caption_for(doc) == ""

    def test_a_caption_at_the_cap_is_untouched(self, tmp_path, settings):
        from tools.rag import access as access_module

        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        exact = "x" * access_module._CAPTION_CHAR_CAP
        _finished_sidecar(doc, [{"page": 1, "text": exact}])
        caption = image_caption_for(doc)
        assert caption == exact
        assert not caption.endswith("…")

    def test_a_caption_over_the_cap_is_cut_with_an_ellipsis(self, tmp_path, settings):
        from tools.rag import access as access_module

        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(
            doc, [{"page": 1, "text": "y" * (access_module._CAPTION_CHAR_CAP + 1)}])
        caption = image_caption_for(doc)
        assert len(caption) == access_module._CAPTION_CHAR_CAP
        assert caption.endswith("…")

    def test_the_cap_is_six_hundred_characters(self):
        from tools.rag import access as access_module

        assert access_module._CAPTION_CHAR_CAP == 600

    def test_a_rewritten_sidecar_is_read_again_rather_than_memoized_stale(
            self, tmp_path, settings):
        """THE MEMO'S CORRECTNESS PIN (review round 1, finding 8). The
        parse is cached on `(path, st_mtime_ns, st_size)`, so a
        re-ingest that rewrites the sidecar must produce a DIFFERENT key
        -- a cache that answered the first extraction forever would make
        a re-ingest invisible to every prompt."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [{"page": 1, "text": "A red bicycle."}])
        assert image_caption_for(doc) == "A red bicycle."
        _finished_sidecar(doc, [{"page": 1, "text": "A blue tandem, actually."}])
        assert image_caption_for(doc) == "A blue tandem, actually."

    def test_the_second_read_of_an_unchanged_sidecar_does_not_reparse(
            self, tmp_path, settings, monkeypatch):
        """THE MEMO ACTUALLY MEMOIZES -- the other half. A conversation
        render and every poller tick after it ask for the same caption;
        without this the same finished JSON is re-opened and re-parsed
        each time."""
        from tools.rag import access as access_module

        settings.DOCUMENTS_DIR = tmp_path
        access_module._caption_from_sidecar.cache_clear()
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [{"page": 1, "text": "A red bicycle."}])

        reads = []
        monkeypatch.setattr(
            "tools.rag.sidecar.read_sidecar",
            lambda path: (reads.append(path) or {
                "produced_at": "x", "segments": [{"page": 1, "text": "A red bicycle."}]}),
        )
        assert image_caption_for(doc) == "A red bicycle."
        assert image_caption_for(doc) == "A red bicycle."
        assert len(reads) == 1


class TestImageCaptionState:
    """Preview UAT (2026-09-17). `image_caption_for` answered `""` for
    several genuinely different situations, and the prompt rendered all
    of them as "description not ready yet" -- so a finished extraction
    that found nothing read as still-running, forever, and the chat
    model told the owner to keep waiting."""

    def test_a_described_image_is_ready_and_the_description_is_the_caption(
            self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [
            {"page": 1, "kind": "description", "text": "A red bicycle."},
            {"page": 1, "text": "CITY CYCLES"},
        ], described=True)
        assert image_caption(doc) == ("ready", "A red bicycle.")

    def test_the_description_wins_over_the_transcription_for_the_caption(
            self, tmp_path, settings):
        """A 600-character cap over "description + OCR dump" would cut
        the description in half for any image with a lot of writing on
        it. The description is what the chat model needs to know what
        the picture IS; `rag__search` still reaches the whole
        extraction."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [
            {"page": 1, "kind": "description", "text": "A poster."},
            {"page": 1, "text": "x" * 5000},
        ], described=True)
        assert image_caption(doc) == ("ready", "A poster.")

    def test_an_unfinished_sidecar_is_pending(self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [], produced_at=None, described=True)
        assert image_caption(doc) == ("pending", "")

    def test_no_sidecar_at_all_is_pending(self, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        assert image_caption(make_document(media_type="image/png")) == ("pending", "")

    def test_a_described_run_that_found_nothing_is_empty_not_pending(
            self, tmp_path, settings):
        """THE UAT BUG ITSELF. The description step RAN (`described`) and
        came back with nothing -- "still processing" is a lie, and it is
        the lie the chat model repeated to the owner."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [], described=True)
        assert image_caption(doc) == ("empty", "")

    def test_a_pre_description_sidecar_with_no_text_is_undescribed(
            self, tmp_path, settings):
        """STEWARD CONDITION S2. An image ingested BEFORE this change has
        no `described` key, and its empty sidecar means only "the OCR
        prompt found no text" -- never "there is nothing to describe".
        Calling that `"empty"` would claim we looked when we did not."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [])
        assert image_caption(doc) == ("undescribed", "")

    def test_a_pre_description_sidecar_with_ocr_text_is_still_ready(
            self, tmp_path, settings):
        """THE OTHER HALF OF S2: an existing image KEEPS the caption its
        OCR produced. Nothing is re-extracted and nothing regresses."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [{"page": 1, "text": "CITY CYCLES"}])
        assert image_caption(doc) == ("ready", "CITY CYCLES")

    def test_a_non_image_is_not_applicable_never_pending(self, tmp_path, settings):
        """`n/a`, NOT `pending`: a scanned PDF has a page-keyed sidecar
        of its own and would read as "still working" forever under
        `pending` -- about a document that will never be described.
        That is the exact shape of lie this state exists to stop telling
        about images, and it must not be reintroduced one media type
        over."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="application/pdf")
        _finished_sidecar(doc, [{"page": 1, "text": "Clause 4.1"}], described=True)
        assert image_caption(doc) == ("n/a", "")

    def test_image_caption_for_still_answers_the_text_alone(self, tmp_path, settings):
        """The existing one-value function stays as the thin wrapper
        every current caller and every current test already uses -- this
        task adds a state, it does not rename a function."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = make_document(media_type="image/png")
        _finished_sidecar(doc, [{"page": 1, "kind": "description",
                                 "text": "A red bicycle."}], described=True)
        assert image_caption_for(doc) == "A red bicycle."


class TestArtifactFileFor:
    """`tools.rag.access.artifact_file_for` -- the `document` kind's
    registered file resolver (`agents.contracts.artifacts.register_
    artifact_file_resolver`, `tools/rag/apps.py::ready()`). Exercised
    here as a plain function call; `tools/vision/tests/
    test_document_references.py` covers the caller that reaches it
    through the registry.

    ONE EXCEPTION FOR THREE CONDITIONS, and that is the point of the
    last two tests: missing, file-less and INVISIBLE all raise
    `LookupError`, so a member cannot derive from another principal's
    attachment by naming its id and reading which refusal comes back.
    """

    def test_it_returns_the_stored_file_for_a_readable_document(self, tmp_path):
        from agents.contracts.artifacts import ArtifactFile

        source = tmp_path / "photo.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n")
        doc = make_document(title="Photo", source_path=str(source),
                            media_type="image/png")
        with posture(POSTURE_OPEN):
            artifact = artifact_file_for(doc.pk, OPEN_PRINCIPAL)
        assert isinstance(artifact, ArtifactFile)
        assert artifact.path == str(source)
        assert artifact.name == "photo.png"
        assert artifact.media_type == "image/png"

    def test_the_name_is_the_basename_never_the_path(self, tmp_path):
        """A consuming column records this as the input's filename and
        joins it onto its OWN directory -- a path here would escape it."""
        source = tmp_path / "photo.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n")
        doc = make_document(source_path=str(source), media_type="image/png")
        with posture(POSTURE_OPEN):
            assert "/" not in artifact_file_for(doc.pk, OPEN_PRINCIPAL).name

    def test_a_blank_media_type_answers_blank_never_none(self, tmp_path):
        source = tmp_path / "notes.txt"
        source.write_text("x")
        doc = make_document(source_path=str(source), media_type="")
        with posture(POSTURE_OPEN):
            assert artifact_file_for(doc.pk, OPEN_PRINCIPAL).media_type == ""

    def test_a_missing_row_is_a_lookup_error(self):
        with posture(POSTURE_OPEN):
            with pytest.raises(LookupError):
                artifact_file_for(999999, OPEN_PRINCIPAL)

    def test_a_row_whose_file_is_gone_is_a_lookup_error(self, tmp_path):
        source = tmp_path / "photo.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n")
        doc = make_document(source_path=str(source), media_type="image/png")
        source.unlink()
        with posture(POSTURE_OPEN):
            with pytest.raises(LookupError):
                artifact_file_for(doc.pk, OPEN_PRINCIPAL)

    def test_a_row_with_no_source_path_at_all_is_a_lookup_error(self):
        doc = make_document(source_path="")
        with posture(POSTURE_OPEN):
            with pytest.raises(LookupError):
                artifact_file_for(doc.pk, OPEN_PRINCIPAL)

    def test_an_invisible_row_raises_the_same_lookup_error_as_a_missing_one(
            self, tmp_path):
        """THE SECURITY PIN: a labelled document a stranger may not read
        must answer EXACTLY as a nonexistent id does."""
        source = tmp_path / "confidential.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n")
        doc = _labelled(make_entitlement(name="Legal"),
                        source_path=str(source), media_type="image/png")
        stranger = make_user()
        with posture(POSTURE_ENTERPRISE):
            with pytest.raises(LookupError) as invisible:
                artifact_file_for(doc.pk, user_principal(stranger))
            with pytest.raises(LookupError) as missing:
                artifact_file_for(999999, user_principal(stranger))
        assert type(invisible.value) is type(missing.value)
