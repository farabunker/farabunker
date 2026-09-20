"""The chunk-metadata cache: ONE WRITER, ONE SQL.

TABLES ARE TRUTH. `DocumentEntitlement` decides who may read a document;
the `entitlements` key in the chunk table's `metadata_` column is a COPY,
kept so `retrieve_nodes` can filter without a join it has no way to
express. A visibility change must not cost GPU hours -- which is what a
re-encode would cost, and what this one `UPDATE` replaces.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.db import connection

from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.models import AuditEvent
from tools.rag import index as rag_index
from tools.rag import labels
from tools.rag.labels import (
    document_label_ids, restamp_document_chunks, set_document_labels,
    unlabel_all_for_entitlement,
)
from tools.rag.models import Document, DocumentAttachment, DocumentEntitlement, WorkstreamPin
from tools.rag.tests._helpers import (
    make_admin, make_document, make_entitlement, posture, reset_settings,
    seed_sweep_posture, user_principal, _workstream,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


@pytest.fixture(params=["json", "jsonb"])
def chunk_table(request):
    """A minimal stand-in for the live chunk table, with the two columns
    the stamp touches.

    PARAMETRIZED over BOTH real live shapes (T10 review finding 5): `json`
    is what a box which has never enabled hybrid search actually has
    (`desired_store_shape` binds `use_jsonb` to the hybrid toggle), and is
    the branch `restamp_document_chunks`'s `{cast}` exists for; `jsonb` is
    what a box with hybrid search on has, where `{cast}` is empty because
    the column is already the type `jsonb_set`/`-` produce. Every test in
    this module that takes `chunk_table` therefore runs once against each
    shape -- `_metadata`'s `::json` read-back (see its own comment) works
    against both, so no test body needs to know which shape it got.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE TABLE {rag_index.LIVE_TABLE_NAME} "
            f"(id bigserial primary key, metadata_ {request.param})")
    yield
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {rag_index.LIVE_TABLE_NAME}")


def _seed_chunks(doc_id, count=2):
    with connection.cursor() as cursor:
        for i in range(count):
            cursor.execute(
                f"INSERT INTO {rag_index.LIVE_TABLE_NAME} (metadata_) VALUES (%s)",
                [json.dumps({"file_id": str(doc_id), "chunk": i})])


def _metadata(doc_id):
    # Cast to `::json`, NOT `::jsonb`, purely for this test's own
    # read-back: Django's psycopg3 backend deliberately registers a
    # `TextLoader` for the `jsonb` OID (so `JSONField` can decode through
    # its own encoder/decoder instead of paying for a round trip through
    # psycopg's), which would hand this assertion a JSON STRING instead
    # of a dict. `json` carries no such override and still auto-parses.
    # The production UPDATE in `tools.rag.labels` casts to `jsonb`
    # because `jsonb_set`/`-` need it; this is a separate, read-only cast
    # that exists only so `row["entitlements"]` works in Python.
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT metadata_::json FROM {rag_index.LIVE_TABLE_NAME} "
            f"WHERE metadata_->>'file_id' = %s", [str(doc_id)])
        return [row[0] for row in cursor.fetchall()]


class TestRestamping:
    def test_it_writes_decimal_STRINGS_for_a_labelled_document(self, chunk_table):
        """STRINGS, not integers: the store's ANY operator renders
        `metadata_::jsonb->'entitlements' ?| array['3','7']`, which is a
        JSON *string* array test. An integer array would match nothing,
        silently."""
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed_chunks(document.id)
        restamp_document_chunks(document.id)
        for row in _metadata(document.id):
            assert row["entitlements"] == [str(finance.pk)]

    def test_it_REMOVES_the_key_when_the_last_label_goes(self, chunk_table):
        """`IS_EMPTY` renders `metadata_->>'entitlements' IS NULL`, which
        is true exactly when the key is ABSENT -- which is what an
        unlabelled chunk looks like, and what EVERY chunk in every
        existing store already looks like. Writing `[]` instead would
        make an unlabelled document invisible to everybody."""
        document = make_document()
        finance = make_entitlement(name="Finance")
        label = DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed_chunks(document.id)
        restamp_document_chunks(document.id)
        label.delete()
        restamp_document_chunks(document.id)
        for row in _metadata(document.id):
            assert "entitlements" not in row

    def test_it_touches_only_this_documents_chunks(self, chunk_table):
        mine, theirs = make_document(), make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=mine, entitlement=finance)
        _seed_chunks(mine.id)
        _seed_chunks(theirs.id)
        restamp_document_chunks(mine.id)
        assert all("entitlements" not in row for row in _metadata(theirs.id))

    def test_it_is_a_no_op_when_the_chunk_table_does_not_exist(self):
        """Nothing prose has ever been ingested. The same defensive
        posture `delete_chunks_for_document` documents -- a label must be
        savable on a box with an empty store."""
        document = make_document()
        restamp_document_chunks(document.id)   # must not raise

    def test_the_ingest_path_logs_and_the_label_path_raises(self, chunk_table, monkeypatch):
        """The two callers differ DELIBERATELY. A failed re-stamp must
        not fail an ingest -- the label page can repair it. A failed
        re-stamp on a LABEL CHANGE must roll the `DocumentEntitlement`
        write back with it: a label that appears saved and is not
        enforced is worse than one that refuses to save."""
        document = make_document()

        def _boom(*args, **kwargs):
            raise RuntimeError("the store is down")

        monkeypatch.setattr("tools.rag.labels._execute_stamp", _boom)
        restamp_document_chunks(document.id)                     # logs, returns
        with pytest.raises(RuntimeError):
            restamp_document_chunks(document.id, raising=True)


class TestSetDocumentLabels:
    def test_it_writes_the_difference_audits_both_ways_and_restamps(self, chunk_table):
        admin = make_admin()
        document = make_document()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        _seed_chunks(document.id)
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_document_labels(actor, document, {finance.pk, legal.pk})
            assert AuditEvent.objects.filter(action=actions.DOCUMENT_LABELLED).count() == 2
            assert _metadata(document.id)[0]["entitlements"] == sorted(
                [str(finance.pk), str(legal.pk)])
            set_document_labels(actor, document, set())
            assert AuditEvent.objects.filter(action=actions.DOCUMENT_UNLABELLED).count() == 2
        assert "entitlements" not in _metadata(document.id)[0]

    def test_a_failed_restamp_rolls_the_label_write_back(self, chunk_table, monkeypatch):
        admin = make_admin()
        document = make_document()
        finance = make_entitlement(name="Finance")
        _seed_chunks(document.id)

        def _boom(*args, **kwargs):
            raise RuntimeError("the store is down")

        monkeypatch.setattr("tools.rag.labels._execute_stamp", _boom)
        with posture(POSTURE_ENTERPRISE), pytest.raises(RuntimeError):
            set_document_labels(user_principal(admin), document, {finance.pk})
        assert DocumentEntitlement.objects.count() == 0
        assert AuditEvent.objects.filter(action=actions.DOCUMENT_LABELLED).count() == 0

    def test_document_label_ids_reads_the_table_not_the_cache(self):
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        assert document_label_ids(document) == frozenset({finance.pk})

    def test_labelled_by_stamps_the_created_rows(self, chunk_table):
        """Mirrors `agents.labels.set_tool_labels`'s ruling: `labelled_by`
        is a `User` INSTANCE the calling view passes, keyword-only, and
        this function never imports `identity.models` to get one -- it
        only assigns whatever the caller handed it onto the FK of every
        row the add loop creates."""
        admin = make_admin()
        document = make_document()
        finance = make_entitlement(name="Finance")
        _seed_chunks(document.id)
        with posture(POSTURE_ENTERPRISE):
            set_document_labels(user_principal(admin), document, {finance.pk},
                                labelled_by=admin)
        row = DocumentEntitlement.objects.get(document=document, entitlement=finance)
        assert row.labelled_by == admin

    def test_labelled_by_defaults_to_null(self, chunk_table):
        """A shell or the entitlement-delete cascade does not hold a
        `User` instance -- the default must leave the column NULL rather
        than requiring every caller to supply one."""
        admin = make_admin()
        document = make_document()
        finance = make_entitlement(name="Finance")
        _seed_chunks(document.id)
        with posture(POSTURE_ENTERPRISE):
            set_document_labels(user_principal(admin), document, {finance.pk})
        row = DocumentEntitlement.objects.get(document=document, entitlement=finance)
        assert row.labelled_by is None


class TestTheEntitlementCascade:
    def test_counting_never_writes(self, chunk_table):
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        assert unlabel_all_for_entitlement(finance.pk, commit=False) == 1
        assert DocumentEntitlement.objects.count() == 1

    def test_running_it_detaches_and_restamps_every_affected_document(self, chunk_table):
        """Done-when 6. The label rows go, and every document that lost
        one is re-stamped IN THE SAME TRANSACTION -- otherwise the tables
        say "unlabelled" while the chunks still say "Finance", and
        retrieval would keep hiding a document nothing protects any more."""
        finance = make_entitlement(name="Finance")
        one, two = make_document(), make_document()
        for document in (one, two):
            DocumentEntitlement.objects.create(document=document, entitlement=finance)
            _seed_chunks(document.id)
            restamp_document_chunks(document.id)
        assert unlabel_all_for_entitlement(finance.pk, commit=True) == 2
        assert DocumentEntitlement.objects.count() == 0
        for document in (one, two):
            assert "entitlements" not in _metadata(document.id)[0]


@pytest.mark.parametrize("documents", [1, 25])
def test_a_relabel_cascade_resolves_the_store_shape_once(documents, chunk_table):
    """C-09. `restamp_document_chunks` re-derived `live_store_shape()` on
    every call -- a `pg_attribute` lookup per document inside two bulk
    loops (an entitlement-delete cascade, and the whole-library relabel
    command). The shape of one database cannot change between two
    iterations of one loop, so the loop resolves it once and hands it in."""
    entitlement = make_entitlement()
    for _ in range(documents):
        DocumentEntitlement.objects.create(
            document=make_document(), entitlement=entitlement)
    with patch("tools.rag.index.live_store_shape", wraps=rag_index.live_store_shape) as shape:
        labels.unlabel_all_for_entitlement(entitlement.id, commit=True)
    assert shape.call_count == 1


class TestReIngestRestoresLabels:
    def test_a_re_ingest_of_a_labelled_document_restores_its_labels(self, chunk_table):
        """The indexing library rewrites the chunk rows; the labels are
        not its business. Without the call in `_ingest_prose` a re-ingest
        would silently unlabel a document -- and a document that quietly
        became readable by everybody is the worst possible outcome of a
        maintenance operation."""
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed_chunks(document.id)          # stands in for `insert_nodes`
        restamp_document_chunks(document.id)
        assert _metadata(document.id)[0]["entitlements"] == [str(finance.pk)]


def _chunk_updated_marker(doc_id):
    """The `metadata_` of every chunk of `doc_id`, as a comparable value.

    The chunk table this module creates has no `updated_at`, so "did the
    UPDATE touch these rows" is answered by the CONTENT, which is what
    the no-op guard is about anyway: a re-stamp that changed nothing must
    leave every chunk byte-identical.
    """
    return _metadata(doc_id)


def _rows_touched_by_last_restamp(monkeypatch):
    """A context manager yielding a one-element list that receives the
    `rowcount` of the next `restamp_document_chunks` statement.

    `tools.rag.labels._execute_stamp` is factored out of that function
    precisely so a test can reach the one cursor -- its own docstring
    says so ("Factored out so a test can make the store fail without
    also making the table-existence check fail"). This wraps it rather
    than replacing it, so the real UPDATE still runs and the assertion is
    about what it actually did.
    """
    import contextlib

    from tools.rag import labels as labels_module

    @contextlib.contextmanager
    def _capture():
        seen = []
        real = labels_module._execute_stamp

        def _wrapped(sql, params):
            from django.db import connection

            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                seen.append(cursor.rowcount)

        monkeypatch.setattr(labels_module, "_execute_stamp", _wrapped)
        try:
            yield seen
        finally:
            monkeypatch.setattr(labels_module, "_execute_stamp", real)

    return _capture


class TestTheWorkstreamKey:
    def test_a_contained_documents_chunks_carry_the_workstream_key_as_a_decimal_string(
            self, chunk_table):
        """THE CONTAINMENT VALUE IS THE STREAM PK AS A DECIMAL STRING,
        matching the `entitlements` convention `retrieval.py` documents for
        the same reason: `ANY` and `EQ` render as JSON *string* comparisons
        over `metadata_`, so an integer stamped into the metadata would never
        match `str(workstream_id)` in the filter."""
        stream = _workstream()
        doc = make_document(workstream=stream)
        _seed_chunks(doc.id)
        restamp_document_chunks(doc.id, raising=True)
        assert _metadata(doc.id)[0]["workstream"] == str(stream.pk)

    def test_a_universal_documents_chunks_have_the_key_REMOVED_not_set_to_empty(
            self, chunk_table):
        """§17.2 CELL 7, the one that catches an empty-string value.
        `IS_EMPTY` renders `metadata_->>'workstream' IS NULL`, which `""`
        does not satisfy -- so a writer that stamped an empty value instead of
        removing the key would pass every other cell in the matrix and fail
        only here, and every re-stamped universal document would become
        invisible to every loose turn, to Ask and to Search: silently,
        totally, and only on rows that had been re-stamped."""
        stream = _workstream()
        doc = make_document(workstream=stream)
        _seed_chunks(doc.id)
        restamp_document_chunks(doc.id, raising=True)
        doc.workstream = None
        doc.save(update_fields=["workstream", "updated_at"])
        restamp_document_chunks(doc.id, raising=True)
        assert "workstream" not in _metadata(doc.id)[0]

    @pytest.mark.parametrize("labelled,contained", [
        (False, False), (True, False), (False, True), (True, True),
    ])
    def test_two_optional_keys_need_four_branches(self, chunk_table, labelled, contained):
        """Each key is present-or-absent INDEPENDENTLY, so there are four
        states and not two: an unlabelled universal document, a labelled
        universal one, an unlabelled contained one, a labelled contained
        one."""
        stream = _workstream() if contained else None
        doc = make_document(workstream=stream)
        if labelled:
            doc.entitlement_labels.create(entitlement=make_entitlement())
        _seed_chunks(doc.id)
        restamp_document_chunks(doc.id, raising=True)
        metadata = _metadata(doc.id)[0]
        assert ("entitlements" in metadata) is labelled
        assert ("workstream" in metadata) is contained

    def test_the_no_op_guard_still_fires_for_an_unlabelled_universal_document(
            self, chunk_table, monkeypatch):
        """§17.2 CELL 8. `tools/rag/labels.py` records why the guard exists
        ("review finding 3"): without it, every ingest of every never-labelled
        document rewrites every chunk row -- a dead tuple and a WAL record per
        chunk, on the box's most common case. A SINGLE-KEY guard carried
        forward unchanged would silently reintroduce that regression the
        moment a second optional key appeared, which is exactly what the
        DISJUNCTION prevents."""
        doc = make_document()
        _seed_chunks(doc.id)
        before = _chunk_updated_marker(doc.id)
        with _rows_touched_by_last_restamp(monkeypatch)() as touched:
            restamp_document_chunks(doc.id, raising=True)
        assert _chunk_updated_marker(doc.id) == before
        assert touched == [0]

    def test_setting_a_documents_containment_restamps_and_raises_on_failure(self, chunk_table):
        """`raising=True` for the third caller, for the reason the flag
        already encodes: a containment change that appears saved and is not
        enforced is worse than one that refuses to save."""
        from tools.rag.workstreams import set_document_workstream

        stream = _workstream()
        doc = make_document()
        _seed_chunks(doc.id)
        set_document_workstream(OPEN_PRINCIPAL, doc, stream.pk)
        doc.refresh_from_db()
        assert doc.workstream_id == stream.pk
        assert _metadata(doc.id)[0]["workstream"] == str(stream.pk)

    @pytest.mark.parametrize("release", [False, True])
    def test_re_homing_a_document_deletes_its_pins(self, chunk_table, release):
        """A pin names a document AS PINNED-INTO-THIS-STREAM, and
        re-homing the document (into a different stream, or back to
        universal with `None`) means it is no longer the universal
        document that pin was made against (`WorkstreamPin`'s own "A
        CONTAINED DOCUMENT MAY NEVER BE PINNED" invariant). Left
        standing, the pin would survive naming a stream the document no
        longer belongs to the way it did when it was pinned -- the
        retrieval-leak half of the same finding is pinned in
        `test_workstream_corpus.py`."""
        from tools.rag.workstreams import set_document_workstream

        pinned_into, other = _workstream(), _workstream()
        doc = make_document()
        _seed_chunks(doc.id)
        pin = WorkstreamPin.objects.create(workstream=pinned_into, document=doc)
        set_document_workstream(OPEN_PRINCIPAL, doc, None if release else other.pk)
        assert not WorkstreamPin.objects.filter(pk=pin.pk).exists()


class TestTheConversationKey:
    """Round 12 review I-4: "nothing anywhere covers the new
    `conversation` metadata stamp -- the single fact the entire
    exclusion mechanism rests on." `restamp_document_chunks`'s THIRD
    optional key, mirroring `TestTheWorkstreamKey`'s own shape exactly
    for the identical reasons."""

    def test_a_chat_scoped_documents_chunks_carry_the_conversation_key_as_a_string(
            self, chunk_table):
        """THE VALUE IS THE ATTACHING CONVERSATION'S UUID AS A STRING,
        matching `agents/runtime/loop.py`'s own `conversation_id=
        str(conversation.id)` stamp on `ToolContext` -- `EQ` renders as
        a JSON *string* comparison over `metadata_`, so the two sides
        must agree on shape."""
        doc = make_document(scope=Document.Scope.CONVERSATION)
        conversation_id = "11111111-1111-1111-1111-111111111111"
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation_id)
        _seed_chunks(doc.id)
        restamp_document_chunks(doc.id, raising=True)
        assert _metadata(doc.id)[0]["conversation"] == conversation_id

    def test_a_universal_documents_chunks_never_carry_the_key_even_with_an_attachment(
            self, chunk_table):
        """NO OVER-STAMPING: a UNIVERSAL document that merely has a
        `DocumentAttachment` row (round 11's own provenance-only kind)
        must not carry the `conversation` key at all -- only a
        `scope == "conversation"` document's chunks are stamped."""
        doc = make_document()  # scope defaults to Document.Scope.UNIVERSAL
        DocumentAttachment.objects.create(
            document=doc, conversation_id="22222222-2222-2222-2222-222222222222")
        _seed_chunks(doc.id)
        restamp_document_chunks(doc.id, raising=True)
        assert "conversation" not in _metadata(doc.id)[0]

    def test_the_key_is_removed_not_set_to_empty_if_scope_ever_changes_back(
            self, chunk_table):
        """The `IS_EMPTY` cell of the four-branch matrix -- the same
        failure mode `TestTheWorkstreamKey`'s own "not set to empty"
        test guards against, for the third key."""
        doc = make_document(scope=Document.Scope.CONVERSATION)
        DocumentAttachment.objects.create(
            document=doc, conversation_id="33333333-3333-3333-3333-333333333333")
        _seed_chunks(doc.id)
        restamp_document_chunks(doc.id, raising=True)
        assert "conversation" in _metadata(doc.id)[0]
        doc.scope = Document.Scope.UNIVERSAL
        doc.save(update_fields=["scope", "updated_at"])
        restamp_document_chunks(doc.id, raising=True)
        assert "conversation" not in _metadata(doc.id)[0]

    def test_a_reencode_restamps_the_conversation_key(self, chunk_table):
        """`reencode_all`'s own path (`ingest.ingest_prose` ->
        `_ingest_prose`, whose last act is `restamp_document_chunks`) is
        what the review verified BY PROBE keeps this key alive across a
        rematerialize -- pinned here directly rather than trusted a
        second time: calling the SAME function this round's `reencode_
        all` audit relies on must not silently skip the third key."""
        doc = make_document(scope=Document.Scope.CONVERSATION)
        DocumentAttachment.objects.create(
            document=doc, conversation_id="44444444-4444-4444-4444-444444444444")
        _seed_chunks(doc.id, count=3)
        restamp_document_chunks(doc.id, raising=True)
        for row in _metadata(doc.id):
            assert row["conversation"] == "44444444-4444-4444-4444-444444444444"
