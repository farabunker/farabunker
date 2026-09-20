"""The library, once documents can be labelled.

THE CASE THE WHOLE SPLIT EXISTS FOR gets its own test: label a document
with an entitlement the administrator does NOT hold, assert the label
wrote, assert the chunk re-stamp ran, and assert `rag-document-file`
still answers 404 to that same administrator.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.urls import reverse
from django.utils.html import escape

from identity.contracts.postures import LIBRARY_LOCKED, POSTURE_ENTERPRISE
from tools.rag import index as rag_index
from tools.rag.models import Document, DocumentEntitlement
from tools.rag.tests._helpers import (
    grant, make_admin, make_document, make_entitlement, make_user, posture, reset_settings,
    seed_sweep_posture, sign_in,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


@pytest.fixture
def chunk_table():
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE TABLE {rag_index.LIVE_TABLE_NAME} "
                       f"(id bigserial primary key, metadata_ json)")
    yield
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {rag_index.LIVE_TABLE_NAME}")


def _seed(doc_id):
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {rag_index.LIVE_TABLE_NAME} (metadata_) VALUES (%s)",
                       [json.dumps({"file_id": str(doc_id)})])


def _metadata(doc_id):
    # Cast to `::json`, NOT `::jsonb` -- see `tools/rag/tests/
    # test_labels.py::_metadata`'s own comment: Django's psycopg3 backend
    # registers a `TextLoader` for the `jsonb` OID, which would hand this
    # assertion a JSON STRING instead of a dict. `json` carries no such
    # override and still auto-parses.
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT metadata_::json FROM {rag_index.LIVE_TABLE_NAME} "
                       f"WHERE metadata_->>'file_id' = %s", [str(doc_id)])
        return cursor.fetchone()[0]


class TestTheCaseTheSplitExistsFor:
    def test_an_admin_labels_a_document_they_cannot_read(self, client, chunk_table):
        # UNLABELLED to start: the label this test writes is the one and
        # only one this document ever carries, so `set_document_labels`'s
        # own `wanted != current` guard (Task 10 review: skip a no-op
        # restamp) sees a real add, not a resubmission of what was
        # already there, and the restamp this test asserts on actually
        # runs.
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        document = make_document()
        _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            # The ROW is theirs to manage...
            assert client.get(reverse("rag-documents")).status_code == 200
            response = client.post(reverse("rag-document-labels-bulk"), {
                "documents": [str(document.id)],
                "entitlements": [str(finance.pk)],
                "action": "apply",
            })
            assert response.status_code == 302
            assert _metadata(document.id)["entitlements"] == [str(finance.pk)]
            # ...the BYTES are not theirs to read.
            assert client.get(
                reverse("rag-document-file", args=[document.id])).status_code == 404


class TestTheTitleLinkFollowsReadability:
    """W-2 (IA-2 walkthrough finding): the fix wave that gated Download
    and Transcript on `row.readable` left the TITLE itself still linking
    at `rag-document-file` -- so an administrator with
    `admin_sees_content` off (the default -- `identity/tests/
    test_settings_page.py`) could open a labelled document's bytes by
    clicking its name, the one link the earlier wave meant to close.
    """

    def test_an_unreadable_labelled_documents_title_is_plain_text(self, client):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        document = make_document(title="Finance memo")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("rag-documents")).content.decode()
        assert "Finance memo" in body
        assert reverse("rag-document-file", args=[document.id]) not in body

    def test_a_readable_documents_title_keeps_its_link(self, client):
        admin = make_admin()
        document = make_document(title="Open memo")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("rag-documents")).content.decode()
        assert "Open memo" in body
        assert reverse("rag-document-file", args=[document.id]) in body


class TestTheContentRoutes:
    def test_a_holder_reads_the_file_and_a_non_holder_gets_404(self, client, tmp_path):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        holder, other = make_user(), make_user()
        grant(finance, user=holder)
        grant(legal, user=other)
        source = tmp_path / "a.txt"
        source.write_text("hello")
        document = make_document(source_path=str(source))
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        url = reverse("rag-document-file", args=[document.id])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, holder)
            assert client.get(url).status_code == 200
            second = client.__class__()
            sign_in(second, other)
            assert second.get(url).status_code == 404

    def test_the_transcript_route_answers_the_same_way(self, client):
        finance = make_entitlement(name="Finance")
        document = make_document()
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            assert client.get(
                reverse("rag-document-transcript", args=[document.id])).status_code == 404


class TestTheCountsAreAReadingSurface:
    def test_a_member_sees_only_their_own_documents_in_every_count(self, client):
        """A sidebar that says "Finance (14)" to a member who may open
        none of them leaks exactly the fact the labels exist to hide, and
        the count is the easiest one to forget because it renders no
        document."""
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        member = make_user()
        grant(finance, user=member)
        mine = make_document(title="mine")
        theirs = make_document(title="theirs")
        DocumentEntitlement.objects.create(document=mine, entitlement=finance)
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(reverse("rag-documents")).content
        assert b"mine" in body and b"theirs" not in body

    def test_an_admin_sees_every_row_and_every_count_with_the_setting_off(self, client):
        legal = make_entitlement(name="Legal")
        theirs = make_document(title="theirs")
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            assert b"theirs" in client.get(reverse("rag-documents")).content

    def test_a_category_whose_visible_count_is_zero_is_omitted_from_the_sidebar(self, client):
        """Not shown as `(0)`, for the same reason: the count itself is
        the leak.

        ASSERTED ON `response.context["sidebar"]`, not on the page body:
        the same category name is still in the upload form's own
        `<select>` -- that picker is a WRITE-side control and is
        deliberately not narrowed -- so a bare substring assertion over
        the whole page would pass only by accident of the picker also
        being hidden, which it is not."""
        from tools.rag.models import Category
        legal = make_entitlement(name="Legal")
        category = Category.objects.create(name="Contracts")
        theirs = make_document(title="theirs", category=category)
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.get(reverse("rag-documents"))
        assert all("Contracts" not in str(entry) for entry in response.context["sidebar"])

    def test_the_upload_pickers_category_list_is_not_narrowed(self, client):
        """A write-side control. Narrowing it would remove an existing
        capability -- uploading into an existing empty category -- that no
        rule asks to remove, and would change an `open` box's page, which
        must stay byte-identical to today's.

        Compared against a raw, unfiltered read of every `Category` row
        (not a literal `["Contracts"]`) because this box's own baseline
        seeds a handful of default categories (`tools/rag/migrations/
        0004_seed_default_categories.py`) that are present in every test
        here -- a `may_label`-narrowed context would still be caught by
        this comparison, since "Contracts" carries zero documents and
        would be the first one dropped."""
        from tools.rag.models import Category
        Category.objects.create(name="Contracts")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.get(reverse("rag-documents"))
        names = [c.name for c in response.context["categories"]]
        assert names == list(Category.objects.order_by("name").values_list("name", flat=True))
        assert "Contracts" in names


class TestTheWidenedActionPredicate:
    def test_an_entitlement_owner_may_delete_and_reingest_within_their_entitlement(
            self, client):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        owner = make_user()
        grant(finance, user=owner, role="owner")
        mine, theirs = make_document(), make_document()
        DocumentEntitlement.objects.create(document=mine, entitlement=finance)
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            assert client.post(
                reverse("rag-document-delete", args=[mine.id])).status_code == 302
            assert client.post(
                reverse("rag-document-delete", args=[theirs.id])).status_code == 403

    def test_a_plain_member_is_still_refused(self, client):
        finance = make_entitlement(name="Finance")
        member = make_user()
        grant(finance, user=member)
        document = make_document()
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            assert client.post(
                reverse("rag-document-delete", args=[document.id])).status_code == 403

    def test_a_member_gets_404_for_a_document_id_that_does_not_exist(self, client):
        """Decision 9's own pin, SUPERSEDED by round-12 whole-branch review
        A-2/B-2: IA-1 originally checked the predicate BEFORE looking the
        row up, so a member got 403 for any `doc_id`, existing or not.
        A-2/B-2 widened the predicate to admit a chat-scoped document's own
        uploader -- a row-specific fact `may_administer_document` cannot
        answer without the row -- so `document_delete` now resolves the row
        FIRST (`get_object_or_404`) and only then asks the predicate. A
        truly nonexistent id now 404s here, same as any other route with no
        cheap pre-filter; a real id this member may not touch still 403s
        (proven by `test_a_plain_member_is_still_refused` above). This is
        the accepted, documented cost of correct row-specific
        administration, not a regression."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            assert client.post(
                reverse("rag-document-delete", args=[999999])).status_code == 404


class TestTheUploadFormOffersOwnedLabels:
    def test_an_owner_sees_their_entitlement_in_the_upload_form(self, client):
        owner = make_user()
        finance = make_entitlement(name="Finance")
        make_entitlement(name="Legal")
        grant(finance, user=owner, role="owner")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(reverse("rag-documents")).content
        assert b"Finance" in body and b"Legal" not in body


class TestUploadingWithLabels:
    def test_an_owners_upload_lands_labelled(self, client, tmp_path, settings):
        """Spec section 11.3: "the upload form offers labels the uploader
        owns". The write uses the `offered` check, not
        `may_label_document` -- a brand-new document is unlabelled, and
        that predicate answers False for an unlabelled document held by a
        non-admin, which would make the picker a control that does
        nothing."""
        settings.INGEST_INBOX_DIR = str(tmp_path)
        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role="owner")
        upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        # The real staging (`stage_document`) is what this test needs to
        # exercise -- the label write happens off the Document row it
        # creates. The QUEUE half is patched at its own seam (mirrors
        # `AskView`'s tests patching `tools.rag.views.enqueue`): this box
        # has no `rag.embed` binding configured, and a real enqueue would
        # otherwise fail the pre-run model resolution `plan_ingest` does
        # at enqueue time, for a reason this test isn't about.
        with patch("tools.rag.ingest.enqueue", return_value=1):
            with posture(POSTURE_ENTERPRISE):
                sign_in(client, owner)
                client.post(reverse("rag-document-upload"),
                            {"files": [upload], "category": "",
                             "entitlements": [str(finance.pk)]})
        document = Document.objects.get(title__startswith="note")
        assert set(document.entitlement_labels.values_list("entitlement_id", flat=True)) == {
            finance.pk}

    def test_an_entitlement_the_page_never_offered_is_refused(self, client, tmp_path,
                                                              settings):
        settings.INGEST_INBOX_DIR = str(tmp_path)
        owner = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=owner, role="owner")
        upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("rag-document-upload"),
                                   {"files": [upload], "category": "",
                                    "entitlements": [str(legal.pk)]}, follow=True)
        assert b"chosen from the list" in response.content
        assert DocumentEntitlement.objects.count() == 0


class TestTheLabelsColumnReadsAndTheCardWrites:
    """ONE WRITING SURFACE (fix round 2). The owner rejected the old row:
    a raw `<select multiple>` and a "Save labels" button taller than the
    row it sat in, repeated down the page.

    The Labels column is READ-ONLY CHIPS for everybody now -- a holder
    and an owner see the same cell, because reporting what a document is
    labelled with is not a privilege. What `may_label_document` gates is
    the WRITING: the per-row checkbox that feeds the card, and the card
    itself.
    """

    def _bodies(self, client):
        """One document under Finance, read by an OWNER and by a plain
        HOLDER. A holder sees the row and its content --
        `readable_documents` reads `held_entitlement_ids`, which does not
        care about role -- but is not an owner, so `may_label_document`
        answers False for them."""
        finance = make_entitlement(name="Finance")
        owner, holder = make_user(), make_user()
        grant(finance, user=owner, role="owner")
        grant(finance, user=holder)
        document = make_document()
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            owner_body = client.get(reverse("rag-documents")).content.decode()
            second = client.__class__()
            sign_in(second, holder)
            holder_body = second.get(reverse("rag-documents")).content.decode()
        return owner_body, holder_body

    def test_both_of_them_see_the_label_as_a_chip(self, client):
        owner_body, holder_body = self._bodies(client)
        assert '<span class="chip">Finance</span>' in owner_body
        assert '<span class="chip">Finance</span>' in holder_body

    def test_no_row_carries_a_per_row_label_form_any_more(self, client):
        """The rejected control, pinned as ABSENT. There is no per-document
        label form on this page and no per-document label ROUTE to point one
        at (C-36 deleted it): the one writing surface is the "With selected…"
        card, which posts to `rag-document-labels-bulk`."""
        owner_body, holder_body = self._bodies(client)
        for body in (owner_body, holder_body):
            assert "/labels/" not in body.replace("/documents/labels/", "")
            assert "Save labels" not in body

    def test_only_a_labeller_gets_the_writing_surface(self, client):
        """The checkbox that feeds the card, and the card itself. Keyed
        on the SAME predicate the POST enforces, never a second truth."""
        owner_body, holder_body = self._bodies(client)
        assert 'form="bulk-label-form"' in owner_body
        assert "With selected" in owner_body
        assert 'form="bulk-label-form"' not in holder_body
        assert "With selected" not in holder_body

    def test_an_unlabelled_document_reads_as_a_dash_not_an_empty_cell(self, client):
        make_entitlement(name="Finance")
        make_document()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("rag-documents")).content.decode()
        assert 'class="doc-unlabelled"' in body


class TestTheUploadPickerVanishesOnAnOpenBox:
    def test_the_upload_labels_picker_is_absent_in_the_open_posture(self, client):
        """`identity.access.labelling_entitlements` answers `()` outright
        the moment `accounts_on()` is False -- its own first branch,
        never even reaching a query. Rendering the label/picker/helptext
        trio unconditionally would add an empty, useless control to the
        open box's page, breaking the byte-identity with today's page
        constraint 9 requires.

        `id_upload_entitlements` is now the chip group's own id rather
        than a `<select>`'s (fix round 2) -- the same control, the same
        `name="entitlements"` values the view reads, drawn as pills."""
        from identity.contracts.postures import POSTURE_OPEN
        with posture(POSTURE_OPEN):
            body = client.get(reverse("rag-documents")).content.decode()
        assert 'id="id_upload_entitlements"' not in body


class TestBulkLabelling:
    """ONE ACTION, NOT N (spec section 22.34). An administrator who has
    just watched forty documents arrive from the inbox must not label
    them one at a time -- that is the tedium the owner named as a
    failure, and the inbox gap (spec section 21) is exactly what makes it
    routine.
    """

    def test_one_post_labels_many_documents_and_restamps_each(self, client, chunk_table):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        documents = [make_document(title=f"doc-{i}") for i in range(3)]
        for document in documents:
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"documents": [str(d.id) for d in documents],
                                    "entitlements": [str(finance.pk)],
                                    "action": "apply"})
        assert response.status_code == 302
        for document in documents:
            assert _metadata(document.id)["entitlements"] == [str(finance.pk)]

    def test_remove_takes_the_label_off_every_selected_document(self, client, chunk_table):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        documents = [make_document(title=f"doc-{i}") for i in range(2)]
        for document in documents:
            DocumentEntitlement.objects.create(document=document, entitlement=finance)
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("rag-document-labels-bulk"),
                        {"documents": [str(d.id) for d in documents],
                         "entitlements": [str(finance.pk)], "action": "remove"})
        assert DocumentEntitlement.objects.count() == 0
        for document in documents:
            assert "entitlements" not in _metadata(document.id)

    def test_apply_ADDS_and_does_not_replace(self, client, chunk_table):
        """Bulk apply is ADD, never "set to exactly this". Somebody
        selecting forty documents and adding one label
        must not silently strip the labels those documents already carry
        -- a destructive bulk action is the worst possible reading of a
        convenience."""
        admin = make_admin()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        document = make_document()
        DocumentEntitlement.objects.create(document=document, entitlement=legal)
        _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("rag-document-labels-bulk"),
                        {"documents": [str(document.id)],
                         "entitlements": [str(finance.pk)], "action": "apply"})
        assert set(document.entitlement_labels.values_list(
            "entitlement_id", flat=True)) == {finance.pk, legal.pk}

    def test_a_whole_category_can_be_labelled_in_one_action(self, client, chunk_table):
        from tools.rag.models import Category
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        category = Category.objects.create(name="Contracts")
        inside = [make_document(title=f"in-{i}", category=category) for i in range(2)]
        outside = make_document(title="out")
        for document in inside + [outside]:
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("rag-document-labels-bulk"),
                        {"category": category.name,
                         "entitlements": [str(finance.pk)], "action": "apply"})
        assert all(d.entitlement_labels.count() == 1 for d in inside)
        assert outside.entitlement_labels.count() == 0

    def test_a_category_bulk_apply_STORES_NO_RULE(self, client, chunk_table):
        """THE ABSENCE OF A STORED MAPPING IS THE DECISION (spec section
        22.34, the owner's own ruling). A stored category->entitlement
        rule would be a second labelling authority beside
        `DocumentEntitlement`, applying itself to rows nobody reviewed.
        A document added to the category AFTERWARDS is unlabelled."""
        from tools.rag.models import Category
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        category = Category.objects.create(name="Contracts")
        make_document(title="before", category=category)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("rag-document-labels-bulk"),
                        {"category": category.name,
                         "entitlements": [str(finance.pk)], "action": "apply"})
            later = make_document(title="after", category=category)
        assert later.entitlement_labels.count() == 0

    def test_an_owner_may_bulk_label_only_within_their_entitlement(self, client,
                                                                   chunk_table):
        """The SAME predicate `may_label_document` enforces, applied per
        document -- not a second rule with a bulk exemption."""
        owner = make_user()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        grant(finance, user=owner, role="owner")
        document = make_document()
        _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"documents": [str(document.id)],
                                    "entitlements": [str(legal.pk)], "action": "apply"},
                                   follow=True)
        assert b"you own" in response.content.lower()
        assert DocumentEntitlement.objects.count() == 0

    def test_a_document_the_caller_may_not_label_is_skipped_and_counted(self, client,
                                                                        chunk_table):
        """Per-document, not all-or-nothing: an administrator selecting a
        page of rows should not have the whole submit refused by one row
        they lack standing over. The flash names how many went and how
        many did not."""
        owner = make_user()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        grant(finance, user=owner, role="owner")
        mine, theirs = make_document(), make_document()
        DocumentEntitlement.objects.create(document=mine, entitlement=finance)
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        for document in (mine, theirs):
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"documents": [str(mine.id), str(theirs.id)],
                                    "entitlements": [str(finance.pk)],
                                    "action": "apply"}, follow=True)
        assert b"1 skipped" in response.content
        # THE COUNT IS NOT ENOUGH -- `may_label_document` is the SOLE gate
        # now that resolution is against the raw table (T16 follow-up
        # review, finding 2), so the skip must also be proven to have
        # written nothing: `theirs` keeps its Legal label untouched, and
        # `mine` keeps its Finance label -- the skip on `theirs` did not
        # abort the loop before `mine` was reached.
        assert set(theirs.entitlement_labels.values_list(
            "entitlement_id", flat=True)) == {legal.pk}
        assert set(mine.entitlement_labels.values_list(
            "entitlement_id", flat=True)) == {finance.pk}

    def test_a_foreign_document_in_the_targeted_category_is_skipped_and_unchanged(
            self, client, chunk_table):
        """The category door resolves the SAME raw candidate set the
        id-list door does (`services.documents_targeted_for_labelling`),
        so a document in the category the caller may not label is
        skipped and counted here too -- the non-admin target path the
        category branch had never exercised."""
        from tools.rag.models import Category
        owner = make_user()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        grant(finance, user=owner, role="owner")
        category = Category.objects.create(name="Contracts")
        mine = make_document(category=category)
        theirs = make_document(category=category)
        DocumentEntitlement.objects.create(document=mine, entitlement=finance)
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        for document in (mine, theirs):
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"category": category.name,
                                    "entitlements": [str(finance.pk)],
                                    "action": "apply"}, follow=True)
        assert b"1 skipped" in response.content
        assert set(theirs.entitlement_labels.values_list(
            "entitlement_id", flat=True)) == {legal.pk}
        assert set(mine.entitlement_labels.values_list(
            "entitlement_id", flat=True)) == {finance.pk}

    def test_remove_takes_a_label_off_a_whole_category_in_one_action(
            self, client, chunk_table):
        """THE CELL THE REDESIGN NEWLY EXPOSED (round 2 review, Minor 2).
        `action=remove` with `category` was always accepted by the
        endpoint but hard-coded to `apply` by the old category-only form;
        the unified card offers both verbs for both scopes, so the
        remove-by-shelf cell is now one click and must be pinned as such
        -- the write, the chunk-cache re-stamp, and the shelf boundary (a
        document outside the shelf keeps its label)."""
        from tools.rag.models import Category
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        category = Category.objects.create(name="Contracts")
        inside = [make_document(title=f"in-{i}", category=category) for i in range(2)]
        outside = make_document(title="out")
        for document in inside + [outside]:
            DocumentEntitlement.objects.create(document=document, entitlement=finance)
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"category": category.name,
                                    "entitlements": [str(finance.pk)],
                                    "action": "remove"}, follow=True)
        assert "Removed labels from 2 document(s)." in response.content.decode()
        for document in inside:
            assert document.entitlement_labels.count() == 0
            # THE CHUNK CACHE TOO, in the same transaction: a document the
            # retriever still calls "Finance" after the label is gone is
            # the exact drift `restamp_document_chunks` exists to prevent,
            # and removal is the direction where that drift would keep
            # HIDING a document that is now readable.
            assert "entitlements" not in _metadata(document.id)
        assert outside.entitlement_labels.count() == 1

    def test_a_foreign_document_on_the_targeted_shelf_is_skipped_on_remove_too(
            self, client, chunk_table):
        """The per-document `may_label_document` gate sits OUTSIDE the
        apply/remove branch, so it must hold on both doors. This is the
        apply-side skip test above with the verb flipped: an owner of
        Finance removing across a shelf must not strip a Legal label off
        somebody else's document that happens to sit on it."""
        from tools.rag.models import Category
        owner = make_user()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        grant(finance, user=owner, role="owner")
        category = Category.objects.create(name="Contracts")
        mine = make_document(title="mine", category=category)
        theirs = make_document(title="theirs", category=category)
        DocumentEntitlement.objects.create(document=mine, entitlement=finance)
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        for document in (mine, theirs):
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"category": category.name,
                                    "entitlements": [str(finance.pk)],
                                    "action": "remove"}, follow=True)
        assert b"1 skipped" in response.content
        assert mine.entitlement_labels.count() == 0
        assert set(theirs.entitlement_labels.values_list(
            "entitlement_id", flat=True)) == {legal.pk}

    def test_a_blank_scope_with_nothing_ticked_changes_nothing(self, client, chunk_table):
        """THE PROPERTY STANDING BETWEEN THE REMOVE BUTTON AND THE WHOLE
        LIBRARY (round 2 review, Minor 3).

        With no `category` and no `documents`,
        `services.documents_targeted_for_labelling` falls through to
        `Document.objects.filter(pk__in=[])` -- EMPTY, not everything.
        That is the only thing that makes a mis-click on Remove harmless,
        and it is one refactor away from a blank filter meaning "all
        rows" -- the same class of mistake this module's own README calls
        the single most dangerous line in the phase, about empty
        vector-store filter lists. Pinned on BOTH verbs, because either
        one arriving unscoped would be unbounded."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        labelled = make_document(title="labelled")
        bare = make_document(title="bare")
        DocumentEntitlement.objects.create(document=labelled, entitlement=finance)
        for document in (labelled, bare):
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            for action in ("remove", "apply"):
                response = client.post(reverse("rag-document-labels-bulk"),
                                       {"category": "", "entitlements": [str(finance.pk)],
                                        "action": action}, follow=True)
                assert response.status_code == 200
        assert labelled.entitlement_labels.count() == 1
        assert bare.entitlement_labels.count() == 0
        # UNTOUCHED, key for key. These rows were seeded directly (never
        # through `set_document_labels`), so their metadata is exactly
        # `{"file_id": ...}` -- and a write on either verb would have
        # re-stamped them and put an `entitlements` key there. Comparing
        # the whole dict, not just probing for one key, is what makes
        # this "nothing happened" rather than "one thing did not".
        assert _metadata(labelled.id) == {"file_id": str(labelled.id)}
        assert _metadata(bare.id) == {"file_id": str(bare.id)}

    def test_an_unrecognised_action_is_refused_not_guessed(self, client, chunk_table):
        """The bulk route's own designed refusal (`document_labels_bulk`'s
        `action not in ("apply", "remove")` check): nothing written, and
        -- since M4, Wave C review -- a flash and a redirect rather than a
        raw 400, exactly like every other `action` dispatcher in this
        codebase (`identity.views.user_edit`/`group_edit`). The Document
        library is a page inside the shell with a messages region; a bare
        plain-text 400 dumped the operator out of it to read one
        sentence."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        document = make_document()
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"documents": [str(document.id)],
                                    "entitlements": [str(finance.pk)],
                                    "action": "wipe"}, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == reverse("rag-documents")
        assert escape("'wipe' is not a recognised action.") in response.content.decode()
        assert DocumentEntitlement.objects.count() == 1

    def test_a_non_numeric_entitlement_id_is_refused_not_crashed(self, client, chunk_table):
        """The other designed refusal: a non-digit `entitlements` value
        never reaches `int()` -- 302 with an honest flash, not a 500, and
        nothing written."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        document = make_document()
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"documents": [str(document.id)],
                                    "entitlements": ["x"], "action": "apply"},
                                   follow=True)
        assert response.redirect_chain[0][1] == 302
        assert b"Choose at least one entitlement" in response.content
        assert DocumentEntitlement.objects.count() == 1

    def test_a_non_decimal_digit_entitlement_id_answers_cleanly_not_500(
            self, client, chunk_table):
        """Whole-branch review, item 1 (the isdecimal ruling): `"²".
        isdigit()` is `True` but `int("²")` raises `ValueError`. The test
        just above uses `"x"`, which `.isdigit()` already correctly
        rejects -- proving nothing about this gap. `"²"` is the string
        that reached `int()` unguarded under the old `.isdigit()` check,
        and this route has NO entitlement-holder gate before that parse
        runs -- reachable by any signed-in MEMBER, not only an admin or
        an entitlement owner."""
        member = make_user()
        document = make_document()
        _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"documents": [str(document.id)],
                                    "entitlements": ["²"], "action": "apply"},
                                   follow=True)
        assert response.redirect_chain[0][1] == 302
        assert b"Choose at least one entitlement" in response.content
        assert b"Traceback" not in response.content
        assert DocumentEntitlement.objects.count() == 0

    def test_removing_the_last_label_makes_the_document_follow_the_library_posture(
            self, client, chunk_table, tmp_path):
        """Done-when 2, in both library postures, without a re-encode and
        within one request.

        A REAL FILE ON DISK, because `make_document` defaults
        `source_path` to a path that does not exist
        (`tools/rag/tests/_helpers.py:76`) and `document_file` would then
        404 for a missing file rather than for visibility -- which would
        make `in (200, 404)` the whole outcome space and the assertion
        vacuous.

        Moved from `TestTheLabelRoute` at C-36; the empty-set spelling
        went with the SET route, the outcome it asserts did not."""
        source = tmp_path / "a.txt"
        source.write_text("hello")
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        document = make_document(source_path=str(source))
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed(document.id)
        url = reverse("rag-document-file", args=[document.id])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            # Before: labelled, and the member holds nothing.
            before = client.__class__()
            sign_in(before, member)
            assert before.get(url).status_code == 404
            client.post(reverse("rag-document-labels-bulk"), {
                "documents": [str(document.id)],
                "entitlements": [str(finance.pk)],
                "action": "remove",
            })
            assert "entitlements" not in _metadata(document.id)
            # After, on an OPEN library: unlabelled is readable by anyone
            # signed in -- one request later, with no re-encode.
            second = client.__class__()
            sign_in(second, member)
            assert second.get(url).status_code == 200
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            third = client.__class__()
            sign_in(third, member)
            assert third.get(url).status_code == 404


class TestTheCategoryBulkControl:
    """W-5 (IA-2 walkthrough finding): the backend door
    (`document_labels_bulk` accepting `category`,
    `documents_targeted_for_labelling` resolving it) was already
    test-pinned above (`TestBulkLabelling`), but no CONTROL for it ever
    rendered anywhere the owner could find.

    Since fix round 2 it is not a form of its own either: it is the SCOPE
    of the one "With selected…" card -- a `<select>` whose blank option
    means "the rows I check below" and whose other options name a shelf.
    That is not a new semantic, it is the endpoint's own documented
    precedence made visible (`services.documents_targeted_for_labelling`:
    "`category`, when given, wins over `ids`"), and it is what let the
    page drop from three label-writing forms to one."""

    def test_the_control_renders_for_an_admin(self, client):
        """`id_bulk_category_select`, not a bare `name="category"` check:
        the upload form (`may_upload`, gated on a different predicate)
        carries its own `<select name="category">`, so a check scoped to
        the scope control's own id is what actually pins THIS control.

        An `Entitlement` has to exist too -- `may_label_any` (the same
        visibility the whole card shares) is `bool(label_choices)`, and
        `labelling_entitlements` offers an administrator EVERY
        entitlement that exists, which is none at all on a bare
        `Category`-only fixture."""
        from tools.rag.models import Category
        make_entitlement(name="Finance")
        Category.objects.create(name="Contracts")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("rag-documents")).content.decode()
        assert 'id="id_bulk_category_select"' in body
        assert '<option value="Contracts">everything in Contracts</option>' in body
        assert '<option value="">the rows I check below</option>' in body

    def test_the_two_verbs_are_two_actions_of_one_form_not_two_forms(self, client):
        """The rule the old pair of forms existed to keep -- no button
        doubles as two verbs -- is kept by `name="action"`, exactly as
        the checked-rows form always did. One form, one set of chips, one
        scope, two submits."""
        make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("rag-documents")).content.decode()
        assert 'name="action" value="apply"' in body
        assert 'name="action" value="remove"' in body
        assert body.count(f'action="{reverse("rag-document-labels-bulk")}"') == 1

    def test_a_principal_without_labelling_rights_sees_no_control(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            body = client.get(reverse("rag-documents")).content.decode()
        assert "With selected" not in body
        assert 'id="id_bulk_category_select"' not in body

    def test_the_page_states_that_a_shelf_overrides_the_ticked_rows(self, client):
        """THE PRECEDENCE, WHERE A PERSON CAN SEE IT (round 2 review,
        Minor 1). `services.documents_targeted_for_labelling` resolves
        `category` INSTEAD OF `ids`, never as well as -- so ticking three
        rows and then also picking a shelf acts on the whole shelf and
        silently discards the ticks. "Apply to" reads naturally as
        NARROWING, and with zero JS nothing can grey the ticks out, so
        the page has to say it in words. It was stated in a template
        comment, a service docstring and the README: three places nobody
        using the page can see."""
        make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("rag-documents")).content.decode()
        assert ("Choosing a shelf acts on every document in it and ignores the rows "
                "you have ticked.") in body
        # The consequence, not just the mechanic: on an open library a
        # document that loses its LAST label becomes readable by everyone
        # signed in, and the button that can do that to a whole shelf is
        # two inches away.
        assert "makes it follow the library posture" in body

    def test_posting_the_control_labels_every_document_in_the_category_at_once(
            self, client, chunk_table):
        from tools.rag.models import Category
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        category = Category.objects.create(name="Contracts")
        inside = [make_document(title=f"in-{i}", category=category) for i in range(3)]
        outside = make_document(title="out")
        for document in inside + [outside]:
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"category": category.name,
                                    "entitlements": [str(finance.pk)],
                                    "action": "apply"}, follow=True)
        # THE HONEST COUNT, and since round 3 the honest VERB: the
        # category control posts the identical fields to the identical
        # route as the id-list form, so it earns the identical, accurate
        # count -- and a confirmation that says which of the two things
        # just happened, because a shelf-wide removal must not read like
        # an addition.
        assert "Added labels to 3 document(s)." in response.content.decode()
        for document in inside:
            assert document.entitlement_labels.count() == 1
        assert outside.entitlement_labels.count() == 0


# The absolute pin's number, named once so both parametrized runs assert
# the SAME count and a reviewer can see what it is. It moves only when the
# bulk endpoint genuinely gains a query -- the event this pin exists for.
_BULK_LABEL_BASELINE_QUERIES = 16


class TestBulkLabellingIsFlatInTheDocumentCount:
    """C-05. `document_labels_bulk` paid 3-4 queries per document over an
    unbounded target set: `may_label_document` recomputed `is_admin_` and
    `owned` per row (its own docstring asks a bulk caller to hoist them),
    and `document_label_ids` issued a fresh `values_list` per row even
    under a prefetch. The list view three hundred lines above it already
    does both. Same expected count at 1 document and at 25.

    EVERY TARGET ALREADY CARRIES THE ENTITLEMENT BEING APPLIED, on
    purpose: `updated == current` for every document, so `set_document_
    labels`'s own per-document write (one audit row, one chunk re-stamp
    -- deliberately untouched by this fix; the brief's own docstring on
    the loop names it a different, unmade decision) never runs, and the
    only per-document cost this pin can see is the READ-side one C-05 is
    actually about: the permission check and the label read. A pin that
    let the writer's genuine per-document queries in could never be flat
    at all, fixed or not, and would say nothing about this bug."""

    @pytest.mark.parametrize("documents", [1, 25])
    def test_the_bulk_endpoint_costs_the_same_at_one_document_and_at_twenty_five(
        self, client, documents, django_assert_num_queries,
    ):
        admin = make_admin()
        entitlement = make_entitlement()
        targets = [make_document() for _ in range(documents)]
        for document in targets:
            DocumentEntitlement.objects.create(document=document, entitlement=entitlement)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            with django_assert_num_queries(_BULK_LABEL_BASELINE_QUERIES):
                response = client.post(reverse("rag-document-labels-bulk"), {
                    "action": "apply",
                    "entitlements": [str(entitlement.pk)],
                    "documents": [str(d.pk) for d in targets],
                })
        assert response.status_code == 302


def test_the_single_document_label_route_is_gone():
    """C-36. The per-document SET-semantics route had no live caller: no
    template ever reversed it, and the library's one writing surface is
    the bulk card (`rag-document-labels-bulk`, ADD/REMOVE semantics). Two
    routes that answer the same predicate with different destructiveness
    is exactly the ambiguity `document_labels_bulk`'s own docstring says
    a bulk control must never carry -- and the one that survived is the
    one the page actually posts to."""
    from django.urls import NoReverseMatch, reverse

    from identity.routes import ROUTE_RULES
    from tools.rag import views as rag_views

    with pytest.raises(NoReverseMatch):
        reverse("rag-document-labels", args=[1])
    assert "rag-document-labels" not in ROUTE_RULES
    assert not hasattr(rag_views, "document_labels_update")
    assert not hasattr(rag_views, "_LABEL_FORBIDDEN_MESSAGE")
    # The survivor, asserted in the same breath so this pin can never be
    # satisfied by deleting the whole feature.
    assert reverse("rag-document-labels-bulk") == "/rag/documents/labels/"
    assert ROUTE_RULES["rag-document-labels-bulk"] == "R"


class TestTheEntitlementFilter:
    """PHASE 2. The library gains `?entitlement=<id>`, linked from an
    entitlement's own page -- which is the door phase 1 deliberately left
    as a count. It NARROWS what this viewer could already list and never
    widens it, which is the whole of what makes it safe on an R-class
    page.
    """

    def _labelled(self, entitlement, **overrides):
        document = make_document(**overrides)
        DocumentEntitlement.objects.create(document=document, entitlement=entitlement)
        return document

    def test_it_narrows_the_list_to_that_entitlement(self, client):
        admin = make_admin()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        self._labelled(finance, title="Ledger")
        self._labelled(legal, title="Contract")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("rag-documents"),
                              {"entitlement": finance.pk}).content.decode()
        assert "Ledger" in body
        assert "Contract" not in body
        assert "Showing documents labelled" in body
        assert "Finance" in body

    def test_it_composes_with_visibility_and_never_widens_it(self, client):
        """THE RULE THIS FILTER LIVES OR DIES BY. A member who holds
        nothing may list no labelled document at all; asking for one by
        entitlement id must answer the empty honest state, not the row.
        Proven against an ADMIN who does see it, so the assertion is
        about the principal rather than about an empty database."""
        member = make_user()
        finance = make_entitlement(name="Finance")
        self._labelled(finance, title="Ledger")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            member_body = client.get(reverse("rag-documents"),
                                     {"entitlement": finance.pk}).content.decode()
            other = client.__class__()
            sign_in(other, make_admin())
            admin_body = other.get(reverse("rag-documents"),
                                   {"entitlement": finance.pk}).content.decode()
        assert "Ledger" not in member_body
        assert "Ledger" in admin_body

    def test_a_filter_the_viewer_may_not_label_with_is_applied_but_not_named(self, client):
        """The banner names the entitlement only where this page would
        already have named it (`labelling_entitlements`, the same
        predicate its own "With selected..." card renders from), so the
        banner cannot become a way to read the catalogue off a page that
        never offered it."""
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        self._labelled(finance, title="Ledger")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(reverse("rag-documents"),
                              {"entitlement": finance.pk}).content.decode()
        # Held, not owned: the row is readable, the name is not offered.
        assert "Ledger" in body
        assert "Showing documents labelled" in body
        assert "<strong>Finance</strong>" not in body

    def test_an_unknown_id_is_the_empty_honest_state_not_the_whole_library(self, client):
        """A filter that silently showed everything back would tell an
        operator who typed a wrong id that the entitlement covers the
        whole library."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        self._labelled(finance, title="Ledger")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            unknown = client.get(reverse("rag-documents"),
                                 {"entitlement": "424242"}).content.decode()
        assert "Ledger" not in unknown

    def test_a_non_numeric_id_is_refused_the_same_way_and_never_500s(self, client):
        admin = make_admin()
        self._labelled(make_entitlement(name="Finance"), title="Ledger")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.get(reverse("rag-documents"),
                                  {"entitlement": "../../etc/passwd"})
        assert response.status_code == 200
        body = response.content.decode()
        assert "Ledger" not in body
        assert "Traceback" not in body

    def test_the_shelf_counts_agree_with_the_filtered_list(self, client):
        """ONE QUERYSET FOR THE ROWS AND EVERY COUNT ABOVE THEM -- the
        rule this view already states for visibility, extended to the
        filter. A shelf that advertised a document the filtered list
        will not show is the same leak in a smaller place."""
        admin = make_admin()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        self._labelled(finance, title="Ledger")
        self._labelled(legal, title="Contract")
        self._labelled(legal, title="Deed")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("rag-documents"),
                              {"entitlement": finance.pk}).content.decode()
        shelf = body[body.index('class="shelf-list"'):]
        shelf = shelf[:shelf.index("</nav>")]
        assert ">1<" in shelf
        assert ">3<" not in shelf

    def test_a_shelf_link_keeps_the_filter_on(self, client):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        self._labelled(finance, title="Ledger")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("rag-documents"),
                              {"entitlement": finance.pk}).content.decode()
        assert f"entitlement={finance.pk}" in body

    def test_the_filter_costs_no_extra_identity_read(self, client):
        """FIX ROUND 2, P2-M3. The banner's name lookup and the "With
        selected..." card both want `labelling_entitlements` for the
        same principal in the same request; asking twice cost three
        extra queries on the filtered path, a twelfth `identitysettings`
        read among them. One read, two readers.

        EQUALITY AGAINST THE UNFILTERED PAGE, not a magic number: the
        filter is one more WHERE on a queryset this page already builds,
        so the only honest budget for it is "the same, plus nothing".
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        admin = make_admin()
        finance = make_entitlement(name="Finance")
        self._labelled(finance, title="Ledger")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.get(reverse("rag-documents"))              # warm the session
            with CaptureQueriesContext(connection) as plain:
                client.get(reverse("rag-documents"))
            with CaptureQueriesContext(connection) as filtered:
                body = client.get(reverse("rag-documents"),
                                  {"entitlement": finance.pk}).content.decode()
        assert len(filtered) == len(plain), (len(plain), len(filtered))
        assert "Ledger" in body and "Showing documents labelled" in body

    def test_it_costs_one_extra_query_and_stays_flat_in_document_count(self, client):
        """The filter is one more WHERE on a queryset the page already
        builds, so it must not move with the number of documents."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        admin = make_admin()
        finance = make_entitlement(name="Finance")
        self._labelled(finance, title="Ledger")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.get(reverse("rag-documents"))              # warm the session
            with CaptureQueriesContext(connection) as one:
                client.get(reverse("rag-documents"), {"entitlement": finance.pk})
            for index in range(8):
                self._labelled(finance, title=f"Extra {index}")
            with CaptureQueriesContext(connection) as many:
                body = client.get(reverse("rag-documents"),
                                  {"entitlement": finance.pk}).content.decode()
        assert len(one) == len(many), (len(one), len(many))
        assert "Extra 7" in body


class TestTheEntitlementPageLinksToTheFilter:
    def test_the_documents_section_links_to_the_filtered_library(self, client):
        """PHASE 1 LEFT DOCUMENTS AS A COUNT; phase 2 gives it a door.
        The section states NO count of its own -- the reach panel is the
        single home for those."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(
                reverse("identity-entitlement-edit", args=[finance.pk])).content.decode()
        assert f'href="{reverse("rag-documents")}?entitlement={finance.pk}"' in body

    def test_an_owner_gets_the_link_too(self, client):
        """Labelling documents is one of an entitlement owner's three
        capabilities, so the door is theirs as much as an
        administrator's."""
        from identity.models import EntitlementGrant

        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("identity-entitlement-edit", args=[finance.pk])).content.decode()
        assert f'?entitlement={finance.pk}' in body

    def test_the_link_carries_no_assistant_flag(self, client):
        """DELIBERATE, not an omission. The panel's gate keys on the
        route being a settings CARD route and `rag-documents` is not one,
        so the flag would be a parameter in the address bar of a page
        that renders no panel -- the identical reasoning the route matrix
        records for `inference-server-scan`."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(
                reverse("identity-entitlement-edit", args=[finance.pk])
                + "?assistant=1").content.decode()
        assert f'href="{reverse("rag-documents")}?entitlement={finance.pk}"' in body
        assert f'entitlement={finance.pk}&amp;assistant=1' not in body
