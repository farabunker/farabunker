"""Unit tests for tools/rag/services.py::delete_document and
::reencode_all (ADR 0008, ADR 0009), plus (`TestStageTurnAttachmentsPer
FileOutcomes`) the `stage_turn_attachments` per-file outcomes the browser
door's own `test_views_upload_and_settings.py::TestDocumentUpload` has no
equivalent for.

All external services (the pgvector store) and filesystem effects (the
managed document store) are mocked/patched -- these tests never touch a
real vector index or the real filesystem. DB-backed assertions run against
the real Postgres+pgvector test database pytest-django creates.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from models.contracts.bindings import ResolvedModel
from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE
from identity.access import owner_fields
from identity.contracts.principals import OPEN_PRINCIPAL, Principal
from tools.rag import jobs, services
from tools.rag.models import AskRecord, Document, DocumentAttachment, DocumentRow, RagSettings
from tools.rag.tests._helpers import (
    make_conversation, make_job_ctx, make_pdf_bytes, make_user, user_principal,
)

RESOLVED_768 = ResolvedModel("ollama", "nomic-embed-text", "http://localhost:11434", embed_dim=768)
RESOLVED_1024 = ResolvedModel(
    "ollama", "mxbai-embed-large", "http://localhost:11434", embed_dim=1024
)


def _make_document(**kwargs):
    defaults = dict(
        title="doc.txt",
        source_path="/data/documents/1/doc.txt",
        file_hash="a" * 64,
        doc_type=Document.DocType.PROSE,
    )
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


@pytest.mark.django_db
class TestDeleteDocument:
    @patch("tools.rag.services.store")
    @patch("tools.rag.services.rag_index")
    def test_deletes_the_document_row(self, mock_rag_index, mock_store):
        doc = _make_document()
        doc_id = doc.id

        services.delete_document(doc)

        assert not Document.objects.filter(pk=doc_id).exists()

    @patch("tools.rag.services.store")
    @patch("tools.rag.services.rag_index")
    def test_cascades_document_rows(self, mock_rag_index, mock_store):
        doc = _make_document(doc_type=Document.DocType.TABULAR)
        DocumentRow.objects.create(document=doc, row_index=0, data={"a": 1})
        DocumentRow.objects.create(document=doc, row_index=1, data={"a": 2})

        services.delete_document(doc)

        assert DocumentRow.objects.count() == 0

    @patch("tools.rag.services.store")
    @patch("tools.rag.services.rag_index")
    def test_removes_stored_files_via_store(self, mock_rag_index, mock_store):
        doc = _make_document()
        doc_id = doc.id

        services.delete_document(doc)

        mock_store.remove_document_files.assert_called_once_with(doc_id)

    @patch("tools.rag.services.store")
    @patch("tools.rag.services.rag_index")
    def test_deletes_vector_chunks_via_shared_helper(self, mock_rag_index, mock_store):
        doc = _make_document()
        doc_id = doc.id

        services.delete_document(doc)

        # Vector-chunk teardown is delegated to the one shared helper
        # (index.delete_chunks_for_document), which owns the file_id filter
        # and the defensive error handling (covered in test_index.py).
        mock_rag_index.delete_chunks_for_document.assert_called_once_with(doc_id)

    @patch("tools.rag.services.store")
    @patch("tools.rag.services.rag_index")
    def test_order_of_operations_files_then_row_deleted(self, mock_rag_index, mock_store):
        """remove_document_files must run while the Document still exists
        (doc.id is used to key the store path) -- verify it's called with
        the id before the row is gone."""
        doc = _make_document()
        doc_id = doc.id

        def _assert_row_still_exists(passed_id):
            assert passed_id == doc_id
            assert Document.objects.filter(pk=doc_id).exists()

        mock_store.remove_document_files.side_effect = _assert_row_still_exists

        services.delete_document(doc)

        mock_store.remove_document_files.assert_called_once_with(doc_id)
        assert not Document.objects.filter(pk=doc_id).exists()


@pytest.mark.django_db
class TestReencodeAll:
    """`reencode_all` is the `rag.embed` role's rematerialize callback (ADR
    0009/0010 embedding-drift rebuild path): it rebuilds vector chunks for
    every prose Document from its retained managed-store copy. `ingest` is
    imported locally inside `reencode_all` (to avoid an import cycle), so
    it's patched at its source (`tools.rag.ingest.ingest_prose`, the
    public wrapper `services.py` now calls instead of reaching into the
    private `_ingest_prose`) rather than as an attribute of
    `tools.rag.services`. `resolve` and `rag_index` (which carries the
    live-dim check, the table drop, the single per-run `get_vector_store()`
    build, and the per-doc chunk delete) are patched at their bound names in
    `tools.rag.services`; each test pins the live/target dims explicitly
    so the same-dim vs. rebuild branch under test is unambiguous.

    `gateway` (T5) is patched too, everywhere `ingest_prose` is called or
    asserted on: `reencode_all` builds ONE embedder for the whole run via
    `gateway.get_embed_model_for(target)` and threads it through every
    `ingest.ingest_prose(..., embed_model=...)` call (the same
    one-resolve-per-run shape `vector_store` already has, audit E1) --
    `mock_gateway.get_embed_model_for.return_value` is the sentinel every
    `embed_model=` assertion below compares against. Every `ingest_prose`
    side effect below accepts `embed_model=None` (unused) alongside
    `vector_store=None`, since the real call now always passes both.
    """

    def _same_dim(self, mock_rag_index, mock_resolve):
        """Configure the mocks for the same-dim (per-doc delete) path."""
        mock_rag_index.live_embed_dim.return_value = 768
        mock_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_reencodes_only_prose_documents(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        self._same_dim(mock_rag_index, mock_resolve)
        prose = _make_document(source_path="/data/documents/1/doc.txt")
        _make_document(
            title="data.csv",
            source_path="/data/documents/2/data.csv",
            doc_type=Document.DocType.TABULAR,
        )

        result = services.reencode_all()

        assert result == {"documents": 1, "reencoded": 1}
        mock_gateway.get_embed_model_for.assert_called_once_with(RESOLVED_768)
        mock_ingest_prose.assert_called_once_with(
            prose,
            Path(prose.source_path),
            vector_store=mock_rag_index.get_vector_store.return_value,
            embed_model=mock_gateway.get_embed_model_for.return_value,
        )

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_deletes_existing_chunks_before_reingesting(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        """Same-dim path: each document's stale chunks are deleted BEFORE its
        re-ingest (a shared recorder pins the relative order), so a re-ingest
        never stacks new chunks on top of old ones."""
        self._same_dim(mock_rag_index, mock_resolve)
        doc = _make_document()

        calls = []
        mock_rag_index.delete_chunks_for_document.side_effect = (
            lambda doc_id, vector_store=None: calls.append(("delete", doc_id))
        )
        mock_ingest_prose.side_effect = (
            lambda d, path, vector_store=None, embed_model=None: calls.append(("ingest", d.id))
        )

        services.reencode_all()

        assert calls == [("delete", doc.id), ("ingest", doc.id)]
        mock_rag_index.drop_chunk_table.assert_not_called()

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_no_prose_documents_returns_zero_counts(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        self._same_dim(mock_rag_index, mock_resolve)

        result = services.reencode_all()

        assert result == {"documents": 0, "reencoded": 0}
        mock_ingest_prose.assert_not_called()

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_dim_mismatch_drops_the_table_instead_of_per_doc_deletes(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        """Live width 768, target 1024: per-doc deletes can't work (every
        insert would hit a pgvector dimension mismatch), so the whole table
        is dropped up front and every document re-ingested into the
        recreated store."""
        mock_rag_index.live_embed_dim.return_value = 768
        mock_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_1024
        _make_document(title="a.txt", source_path="/data/documents/1/a.txt")
        _make_document(title="b.txt", source_path="/data/documents/2/b.txt")

        result = services.reencode_all()

        assert result == {"documents": 2, "reencoded": 2}
        mock_rag_index.drop_chunk_table.assert_called_once_with()
        mock_rag_index.delete_chunks_for_document.assert_not_called()
        assert mock_ingest_prose.call_count == 2

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_dim_mismatch_drops_the_table_before_any_reingest(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        mock_rag_index.live_embed_dim.return_value = 768
        mock_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_1024
        _make_document()

        calls = []
        mock_rag_index.drop_chunk_table.side_effect = lambda: calls.append("drop")
        mock_ingest_prose.side_effect = (
            lambda d, path, vector_store=None, embed_model=None: calls.append("ingest")
        )

        services.reencode_all()

        assert calls == ["drop", "ingest"]

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_a_not_ready_media_doc_does_not_poison_a_rebuild(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        """T7 review M4b: probed live -- WITHOUT the skip, a not-ready
        media Document raised inside the per-document try/except like any
        other failure, and on the REBUILD path that happens AFTER
        `rag_index.drop_chunk_table()` already ran, so the eventual
        "reencoded < documents" raise at the end left the table dropped
        and only partially repopulated. One bad video must never poison an
        otherwise-clean rebuild of every OTHER document."""
        mock_rag_index.live_embed_dim.return_value = 768
        mock_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_1024
        _make_document(title="a.txt", source_path="/data/documents/1/a.txt")
        _make_document(
            title="clip.mp4",
            source_path="/data/documents/2/clip.mp4",
            media_type="video/mp4",
            status=Document.Status.PENDING,
        )

        result = services.reencode_all()

        assert result == {"documents": 1, "reencoded": 1}
        mock_rag_index.drop_chunk_table.assert_called_once_with()
        assert mock_ingest_prose.call_count == 1

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_unknown_live_dim_keeps_the_per_doc_path(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        """No chunk table yet (live width None): nothing structural to
        rebuild, so the per-doc delete+re-ingest path runs and creates the
        table at the resolved dim on first ingest."""
        mock_rag_index.live_embed_dim.return_value = None
        mock_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_1024
        doc = _make_document()

        result = services.reencode_all()

        assert result == {"documents": 1, "reencoded": 1}
        mock_rag_index.drop_chunk_table.assert_not_called()
        mock_rag_index.delete_chunks_for_document.assert_called_once_with(
            doc.id, vector_store=mock_rag_index.get_vector_store.return_value
        )

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_unknown_target_dim_fails_fast_before_any_document_is_touched(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        """Rewritten from the old "keeps the per-doc path" test: a binding
        that doesn't know its own dimension used to fall back to
        settings.EMBED_DIM and quietly proceed; now (D1/E2 fix, ADR 0010) it
        raises ValueError before the live-dim check, before the vector store
        is built, and before a single document is touched -- no more
        burning N embedding calls only to fail at the DB layer."""
        mock_resolve.return_value = ResolvedModel(
            "ollama", "some-model", "http://localhost:11434", embed_dim=None
        )
        _make_document()

        with pytest.raises(ValueError, match="some-model"):
            services.reencode_all()

        mock_rag_index.live_embed_dim.assert_not_called()
        mock_rag_index.get_vector_store.assert_not_called()
        mock_rag_index.drop_chunk_table.assert_not_called()
        mock_rag_index.delete_chunks_for_document.assert_not_called()
        mock_gateway.get_embed_model_for.assert_not_called()
        mock_ingest_prose.assert_not_called()

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_builds_the_vector_store_and_embedder_once_for_the_whole_run(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        """Audit E1 fix: one store build (and, T5, one embedder build) for
        the whole run, not one per document -- every document reuses the
        same pre-built store/embedder instead of each re-resolving the
        binding and reconstructing `PGVectorStore`/the embedding model."""
        self._same_dim(mock_rag_index, mock_resolve)
        _make_document(title="a.txt", source_path="/data/documents/1/a.txt")
        _make_document(title="b.txt", source_path="/data/documents/2/b.txt")
        _make_document(title="c.txt", source_path="/data/documents/3/c.txt")

        services.reencode_all()

        mock_rag_index.get_vector_store.assert_called_once_with()
        mock_gateway.get_embed_model_for.assert_called_once_with(RESOLVED_768)
        store = mock_rag_index.get_vector_store.return_value
        embed_model = mock_gateway.get_embed_model_for.return_value
        assert mock_ingest_prose.call_count == 3
        for call in mock_ingest_prose.call_args_list:
            assert call.kwargs["vector_store"] is store
            assert call.kwargs["embed_model"] is embed_model
        assert mock_rag_index.delete_chunks_for_document.call_count == 3
        for call in mock_rag_index.delete_chunks_for_document.call_args_list:
            assert call.kwargs["vector_store"] is store

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_a_failing_document_does_not_block_the_rest_but_the_run_raises(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        """A single document's failure is logged and the loop continues --
        but a partial run must NOT return a success dict: it raises
        RuntimeError (naming the counts) so `run_rematerialize` never
        stamps a partial re-encode as a completed materialization."""
        self._same_dim(mock_rag_index, mock_resolve)
        _make_document(title="a.txt", source_path="/data/documents/1/a.txt")
        _make_document(title="b.txt", source_path="/data/documents/2/b.txt")

        def _side_effect(doc, path, vector_store=None, embed_model=None):
            if doc.title == "b.txt":
                raise Exception("boom")

        mock_ingest_prose.side_effect = _side_effect

        with pytest.raises(RuntimeError, match=r"1 of 2"):
            services.reencode_all()

        # The failure did not short-circuit the loop: both documents were
        # attempted before the run reported failure.
        assert mock_ingest_prose.call_count == 2

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_every_document_failing_raises(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        self._same_dim(mock_rag_index, mock_resolve)
        _make_document()
        mock_ingest_prose.side_effect = Exception("store gone")

        with pytest.raises(RuntimeError, match=r"0 of 1"):
            services.reencode_all()


@pytest.mark.django_db
class TestRecordAsk:
    """Unit tests for services.record_ask/_prune_ask_records."""

    def test_creates_an_ask_record_with_snapshot_fields(self):
        services.record_ask(
            question="what is our refund policy?",
            category="Business",
            connection_name="workstation llama",
            model_id="llama3.1:8b",
            answer="Refunds within 5 business days.",
            citations=[
                {
                    "source": "vector",
                    "document_id": 3,
                    "title": "policy.md",
                    "chunk_id": "abc",
                    "row_index": None,
                    "source_path": "/data/documents/3/policy.md",
                    "score": 0.83,
                    "snippet": "Refunds...",
                }
            ], actor=OPEN_PRINCIPAL,
        )

        record = AskRecord.objects.get()
        assert record.question == "what is our refund policy?"
        assert record.category == "Business"
        assert record.connection_name == "workstation llama"
        assert record.model_id == "llama3.1:8b"
        assert record.answer == "Refunds within 5 business days."
        # Only the fields the Ask/History UI shows per citation are kept
        # (T10 adds locator/page/start_seconds/end_seconds, T10 review
        # MINOR 4 adds locator_text, alongside the original file/score
        # pair -- "" / None for a citation with no locator, exactly like
        # this plain-prose one).
        assert record.citations == [
            {
                "file": "policy.md",
                "score": 0.83,
                "locator": "",
                "locator_text": "",
                "page": None,
                "start_seconds": None,
                "end_seconds": None,
            }
        ]

    def test_av_citation_snapshot_keeps_its_timecode_locator(self):
        """T10: a media citation's `locator`/`start_seconds`/`end_seconds`
        survive the snapshot -- History's own render needs them to show
        "clip.mp4 at 12:40", not just the bare filename. T10 review MINOR
        4: `locator_text` (the connector-prefixed " at 12:40") survives
        alongside it."""
        services.record_ask(
            question="q", category=None, connection_name="c", model_id="m", answer="a",
            citations=[
                {
                    "title": "clip.mp4",
                    "score": 0.5,
                    "page": None,
                    "start_seconds": 760.0,
                    "end_seconds": 764.0,
                    "locator": "12:40",
                    "locator_text": " at 12:40",
                }
            ], actor=OPEN_PRINCIPAL,
        )

        record = AskRecord.objects.get()
        assert record.citations == [
            {
                "file": "clip.mp4",
                "score": 0.5,
                "locator": "12:40",
                "locator_text": " at 12:40",
                "page": None,
                "start_seconds": 760.0,
                "end_seconds": 764.0,
            }
        ]

    def test_pre_locator_text_citation_snapshot_defaults_to_blank(self):
        """T10 review MINOR 4: a citation dict without a `locator_text`
        key at all (the shape every citation had before this review
        finding) still snapshots cleanly -- `locator_text` defaults to
        `""`, never a `KeyError`/`None` leaking into the stored
        snapshot."""
        services.record_ask(
            question="q", category=None, connection_name="c", model_id="m", answer="a",
            citations=[{"title": "old.md", "score": 0.5, "locator": "12:40", "page": None}], actor=OPEN_PRINCIPAL,
        )

        record = AskRecord.objects.get()
        assert record.citations[0]["locator_text"] == ""

    def test_none_category_is_stored_as_blank(self):
        services.record_ask(
            question="q", category=None, connection_name="c", model_id="m",
            answer="a", citations=[], actor=OPEN_PRINCIPAL,
        )

        assert AskRecord.objects.get().category == ""

    def test_empty_citations_list_round_trips(self):
        services.record_ask(
            question="q", category=None, connection_name="c", model_id="m",
            answer="a", citations=[], actor=OPEN_PRINCIPAL,
        )

        assert AskRecord.objects.get().citations == []

    def test_prunes_down_to_the_configured_limit(self):
        RagSettings.objects.create(pk=1, history_limit=2)
        for i in range(5):
            services.record_ask(
                question=f"q{i}", category=None, connection_name="c", model_id="m",
                answer="a", citations=[], actor=OPEN_PRINCIPAL,
            )

        assert AskRecord.objects.count() == 2
        remaining = list(AskRecord.objects.order_by("question").values_list("question", flat=True))
        assert remaining == ["q3", "q4"]

    def test_lowering_the_limit_prunes_existing_records_on_next_insert(self):
        for i in range(5):
            AskRecord.objects.create(
                question=f"old{i}", connection_name="c", model_id="m", answer="a",
            )
        assert AskRecord.objects.count() == 5

        RagSettings.objects.create(pk=1, history_limit=1)
        services.record_ask(
            question="new", category=None, connection_name="c", model_id="m",
            answer="a", citations=[], actor=OPEN_PRINCIPAL,
        )

        assert AskRecord.objects.count() == 1
        assert AskRecord.objects.get().question == "new"

    def test_default_limit_of_100_is_not_pruned_below_that(self):
        for i in range(100):
            AskRecord.objects.create(
                question=f"q{i}", connection_name="c", model_id="m", answer="a",
            )

        services.record_ask(
            question="q100", category=None, connection_name="c", model_id="m",
            answer="a", citations=[], actor=OPEN_PRINCIPAL,
        )

        assert AskRecord.objects.count() == 100
        assert not AskRecord.objects.filter(question="q0").exists()
        assert AskRecord.objects.filter(question="q100").exists()


_ANSWER_RESOLVED = ResolvedModel("ollama", "llama3.1:8b", "http://localhost:11434")
_EMBED_RESOLVED = ResolvedModel(
    "ollama", "nomic-embed-text", "http://localhost:11435", embed_dim=768
)


def _resolve_side_effect(role):
    return _ANSWER_RESOLVED if role == RAG_ANSWER_ROLE else _EMBED_RESOLVED


def _run_ask_with_doubles(payload):
    """Run `tools.rag.jobs.run_ask` end to end with every external
    dependency doubled -- `resolve`, `get_engine`, `answer_role_primary`,
    `answer_question` -- EXCEPT `services.record_ask` itself, which runs
    for real so this module's assertions read an actual `AskRecord` row.
    Mirrors `tools.rag.tests.test_jobs.TestRunAsk`'s own happy path."""
    with patch("tools.rag.jobs.resolve", side_effect=_resolve_side_effect), patch(
        "tools.rag.messages.get_engine"
    ) as mock_get_engine, patch(
        "tools.rag.jobs.answer_role_primary", return_value=("workstation llama", 1)
    ), patch(
        "tools.rag.jobs.answer_question",
        return_value={"answer": "an answer", "citations": []},
    ):
        mock_get_engine.return_value.is_healthy.return_value = True
        return jobs.run_ask(payload, [], make_job_ctx())


@pytest.mark.django_db
class TestAskRecordOwnership:
    def test_an_ask_record_is_stamped_with_the_actor(self):
        services.record_ask(
            question="q", category=None, connection_name="a name",
            model_id="an id", answer="a", citations=[],
            actor=Principal("user", "7"),
        )
        row = AskRecord.objects.get()
        assert (row.owner_kind, row.owner_key) == ("user", "7")

    def test_it_is_stamped_from_the_jobs_payload_actor_not_from_a_request(self):
        """`record_ask` runs on the WORKER, inside
        `tools.rag.jobs.run_ask` -- there is no request there, so
        the actor comes from the payload the page wrote at enqueue
        time. This drives the handler, not the service, so the wiring
        between the two is what is asserted."""
        payload = {"question": "q", "category": None,
                   "actor_kind": "user", "actor_key": "7"}
        _run_ask_with_doubles(payload)          # module's existing gateway doubles
        assert AskRecord.objects.get().owner_key == "7"

    def test_a_pre_phase_payload_stamps_the_open_principal(self):
        """A `rag.ask` job enqueued before IA-1 and still in the queue
        when the worker picks it up. Never a crash, never a blank."""
        _run_ask_with_doubles({"question": "q", "category": None})
        row = AskRecord.objects.get()
        assert (row.owner_kind, row.owner_key) == ("open", "box")


@pytest.mark.django_db
class TestStageTurnAttachmentsPerFileOutcomes:
    """C-14 (task 23) characterization: `stage_turn_attachments`'s own
    per-file outcomes that had no direct-call pin anywhere before the
    per-file body moved into `ingest.stage_and_enqueue_one`, shared with
    `views.document_upload`'s identical loop. `tools/rag/tests/
    test_chat_scoped_documents.py` and `agents/chat/tests/
    test_turn_attachments.py` already cover the caps (oversize/duration/
    page) and the ordinary watcher-race-is-queued-and-attached case
    end to end; this class fills the gaps the brief's own characterization
    matrix names: the tabular flag, `staged_document_ids` on both branches
    that populate it, and the ONE branch that must NOT -- the raced one,
    which is exactly why `StageOutcome.raced` exists (see `ingest.
    StageOutcome`'s own docstring)."""

    def _conversation_and_actor(self):
        user = make_user()
        principal = user_principal(user)
        conversation = make_conversation(**owner_fields(principal))
        return conversation, principal

    def test_a_tabular_upload_is_reported_via_result_tabular(self):
        conversation, principal = self._conversation_and_actor()

        result = services.stage_turn_attachments(
            principal, conversation_id=conversation.id, turn_id=1,
            files=[SimpleUploadedFile("numbers.csv", b"a,b\n1,2")],
            placement="conversation", actor=principal,
        )

        assert result.ok
        assert result.queued == 1
        assert result.tabular == ("numbers.csv",)

    def test_staged_document_ids_names_the_row_a_successful_upload_moved(self):
        conversation, principal = self._conversation_and_actor()

        result = services.stage_turn_attachments(
            principal, conversation_id=conversation.id, turn_id=1,
            files=[SimpleUploadedFile("report.md", b"quarterly numbers")],
            placement="conversation", actor=principal,
        )

        doc = Document.objects.get()
        assert result.staged_document_ids == (doc.id,)

    def test_an_unchanged_reupload_is_reported_unchanged_and_attaches_the_existing_document(self):
        conversation, principal = self._conversation_and_actor()
        services.stage_turn_attachments(
            principal, conversation_id=conversation.id, turn_id=1,
            files=[SimpleUploadedFile("dup.md", b"same bytes")],
            placement="conversation", actor=principal,
        )
        doc = Document.objects.get()

        result = services.stage_turn_attachments(
            principal, conversation_id=conversation.id, turn_id=2,
            files=[SimpleUploadedFile("dup.md", b"same bytes")],
            placement="conversation", actor=principal,
        )

        assert result.unchanged == ("dup.md",)
        assert result.queued == 0
        assert DocumentAttachment.objects.filter(
            document=doc, conversation_id=conversation.id).exists()

    def test_staged_document_ids_names_the_row_when_staging_succeeded_but_the_queue_declined_it(
        self, monkeypatch,
    ):
        """The `else` branch (`job_id is None` and `dest_unchanged` is
        False): `stage_document` DID create/move the row, only the
        SEPARATE enqueue call afterwards failed
        (`ingest.enqueue_ingest`'s own docstring, outcome (b)) --
        `_enqueue_ingest_job`'s own `except Exception` degrades that to
        an honest FAILED row rather than raising, so `stage_and_enqueue_
        one` sees `job_id is None` with a real `Document` to hand back."""
        monkeypatch.setattr(
            "tools.rag.ingest.enqueue",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("queue is on fire")),
        )
        conversation, principal = self._conversation_and_actor()

        result = services.stage_turn_attachments(
            principal, conversation_id=conversation.id, turn_id=1,
            files=[SimpleUploadedFile("broken.md", b"hello")],
            placement="conversation", actor=principal,
        )

        doc = Document.objects.get()
        assert result.failed == ("broken.md",)
        assert result.staged_document_ids == (doc.id,)
        assert DocumentAttachment.objects.filter(
            document=doc, conversation_id=conversation.id).exists()

    def test_a_watcher_race_is_queued_and_attached_but_never_counted_in_staged_document_ids(self):
        """Resolves this task's own brief placeholder: read against the
        real code (`services.py:695-710` before this task's extraction),
        the raced branch calls `_attach` but does NOT append to
        `staged_document_ids` -- only the request that actually moved the
        bytes owns that id (`AttachmentUploadResult.staged_document_ids`'s
        own docstring: "every id whose bytes THIS call physically
        moved"). `StageOutcome.raced` exists so `stage_and_enqueue_one`
        can hand that distinction back rather than collapsing it."""
        conversation, principal = self._conversation_and_actor()
        services.stage_turn_attachments(
            principal, conversation_id=conversation.id, turn_id=1,
            files=[SimpleUploadedFile("raced.md", b"raced bytes")],
            placement="conversation", actor=principal,
        )
        doc = Document.objects.get()

        with patch("tools.rag.ingest.enqueue_ingest", side_effect=FileNotFoundError):
            result = services.stage_turn_attachments(
                principal, conversation_id=conversation.id, turn_id=2,
                files=[SimpleUploadedFile("raced.md", b"raced bytes")],
                placement="conversation", actor=principal,
            )

        assert result.queued == 1
        assert result.staged_document_ids == ()
        assert DocumentAttachment.objects.filter(
            document=doc, conversation_id=conversation.id).exists()


@pytest.mark.django_db
class TestReencodeAllMedia:
    """T7: `reencode_all` over an already-transcribed video/audio Document
    reuses its sidecar (`extract.json`) -- no transcriber call -- and
    re-chunks with timestamps intact. Unlike `TestReencodeAll` above, this
    class does NOT mock `tools.rag.ingest.ingest_prose`: the whole point
    is to prove the REAL dispatch runs (`_ingest_prose`'s `llama_docs=None`
    default -> `_source_documents` -> `tools.rag.media.documents_from_extract`),
    so it touches a real (throwaway, `tmp_path`) sidecar file on disk --
    only the pgvector store (`tools.rag.ingest.rag_index`) and the
    embedding role resolution (`tools.rag.services.rag_index`/`resolve`/
    `gateway`) are mocked, plus `tools.rag.media.get_transcriber_for` (to
    assert it's never called at all)."""

    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_reencodes_from_the_sidecar_without_calling_the_transcriber(
        self,
        mock_services_rag_index,
        mock_resolve,
        mock_gateway,
        mock_ingest_rag_index,
        mock_get_transcriber_for,
        tmp_path,
    ):
        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768
        mock_index = MagicMock()
        mock_ingest_rag_index.get_index.return_value = mock_index

        media_dir = tmp_path / "documents" / "9"
        media_dir.mkdir(parents=True)
        source = media_dir / "clip.mp4"
        source.write_bytes(b"fake video bytes")
        sidecar = {
            "version": 1,
            "method": "whisper",
            "source": "clip.mp4",
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [{"start": 0.0, "end": 2.0, "text": "hello from the transcript"}],
        }
        (media_dir / "extract.json").write_text(json.dumps(sidecar))

        # status=READY + extraction set -- M4b's own pre-filter (below) only
        # lets an already-FINISHED media Document through; a Document that
        # never finished transcribing (status != READY, or extraction
        # unset) is skipped before ever reaching this far.
        _make_document(
            title="clip.mp4",
            source_path=str(source),
            doc_type=Document.DocType.PROSE,
            media_type="video/mp4",
            status=Document.Status.READY,
            extraction={
                "method": "transcription",
                "engine": "whisper",
                "model_id": "ggml-base.en",
                "connection_name": "",
                "produced_at": "2026-08-24T00:00:00+00:00",
            },
        )

        result = services.reencode_all()

        assert result == {"documents": 1, "reencoded": 1}
        mock_get_transcriber_for.assert_not_called()
        mock_index.insert_nodes.assert_called_once()

        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) == 1
        assert nodes[0].metadata["start_seconds"] == 0.0
        assert nodes[0].metadata["end_seconds"] == 2.0
        assert "hello from the transcript" in nodes[0].get_content()

    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_pollution_fix_covers_the_reencode_and_documents_from_extract_paths(
        self,
        mock_services_rag_index,
        mock_resolve,
        mock_gateway,
        mock_ingest_rag_index,
        mock_get_transcriber_for,
        tmp_path,
    ):
        """T10 metadata-exclusion follow-through, paths 2 and 3 of 3
        together: this class's own real dispatch (`_ingest_prose`'s
        `llama_docs=None` default -> `_source_documents` ->
        `tools.rag.media.documents_from_extract`) is reached here by the
        REENCODE loop (`services.reencode_all`) -- one real run through
        `SentenceSplitter` proves BOTH "documents_from_extract's own chunks
        stay excluded once `_ingest_prose` re-applies the fix" and "the
        reencode path itself never re-introduces the pollution it doesn't
        create" in the one call that actually exercises both."""
        from llama_index.core.schema import MetadataMode

        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768
        mock_index = MagicMock()
        mock_ingest_rag_index.get_index.return_value = mock_index

        media_dir = tmp_path / "documents" / "11"
        media_dir.mkdir(parents=True)
        source = media_dir / "clip.mp4"
        source.write_bytes(b"fake video bytes")
        sidecar = {
            "version": 1,
            "method": "whisper",
            "source": "clip.mp4",
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "content long enough to survive the real splitter pass"}
            ],
        }
        (media_dir / "extract.json").write_text(json.dumps(sidecar))

        _make_document(
            title="clip.mp4",
            source_path=str(source),
            doc_type=Document.DocType.PROSE,
            media_type="video/mp4",
            status=Document.Status.READY,
            extraction={
                "method": "transcription",
                "engine": "whisper",
                "model_id": "ggml-base.en",
                "connection_name": "",
                "produced_at": "2026-08-24T00:00:00+00:00",
            },
        )

        result = services.reencode_all()

        assert result == {"documents": 1, "reencoded": 1}
        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) == 1
        node = nodes[0]
        for key in ("file_id", "file_name", "source_path", "category", "start_seconds", "end_seconds"):
            assert key in node.excluded_embed_metadata_keys
            assert key in node.excluded_llm_metadata_keys
        embed_text = node.get_content(metadata_mode=MetadataMode.EMBED)
        llm_text = node.get_content(metadata_mode=MetadataMode.LLM)
        assert "start_seconds:" not in embed_text
        assert "start_seconds:" not in llm_text

    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_not_ready_media_doc_is_skipped_not_counted_as_a_failure(
        self,
        mock_services_rag_index,
        mock_resolve,
        mock_gateway,
        mock_ingest_rag_index,
        mock_get_transcriber_for,
        caplog,
    ):
        """T7 review M4b: a media Document still PENDING (transcription
        never finished) is skipped BEFORE `ingest_prose` is ever called --
        logged once, and excluded from `documents`/`reencoded` entirely
        (not a failure of this run)."""
        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768

        _make_document(
            title="clip.mp4",
            source_path="/data/documents/9/clip.mp4",
            doc_type=Document.DocType.PROSE,
            media_type="video/mp4",
            status=Document.Status.PENDING,
        )

        with caplog.at_level("INFO"):
            result = services.reencode_all()

        assert result == {"documents": 0, "reencoded": 0}
        mock_get_transcriber_for.assert_not_called()
        assert any("skipping Document" in r.message for r in caplog.records)

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_not_ready_image_doc_is_skipped_not_counted_as_a_failure(
        self, mock_services_rag_index, mock_resolve, mock_gateway, mock_ingest_rag_index, caplog
    ):
        """T8: the image sibling of M4b's video/audio skip -- `media_type`
        alone (`image/*`) is enough to recognize this, unambiguously, the
        same way `video/`/`audio/` already are."""
        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768

        _make_document(
            title="photo.jpg",
            source_path="/data/documents/9/photo.jpg",
            doc_type=Document.DocType.PROSE,
            media_type="image/jpeg",
            status=Document.Status.PENDING,
        )

        with caplog.at_level("INFO"):
            result = services.reencode_all()

        assert result == {"documents": 0, "reencoded": 0}
        assert any("skipping Document" in r.message for r in caplog.records)

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_not_ready_scanned_pdf_is_skipped_via_the_content_probe(
        self, mock_services_rag_index, mock_resolve, mock_gateway, mock_ingest_rag_index, tmp_path, caplog
    ):
        """T8: a scanned PDF's `media_type` is `"application/pdf"` --
        identical to an ordinary text-layer PDF -- so THIS is the one case
        `reencode_all`'s skip has to probe the stored file itself
        (`tools.rag.readers.pdf_textless_pages`, W1) rather than
        trusting `media_type` alone."""
        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768

        media_dir = tmp_path / "documents" / "9"
        media_dir.mkdir(parents=True)
        source = media_dir / "scan.pdf"
        source.write_bytes(make_pdf_bytes([None]))  # no text layer

        _make_document(
            title="scan.pdf",
            source_path=str(source),
            doc_type=Document.DocType.PROSE,
            media_type="application/pdf",
            status=Document.Status.PENDING,
        )

        with caplog.at_level("INFO"):
            result = services.reencode_all()

        assert result == {"documents": 0, "reencoded": 0}
        assert any("skipping Document" in r.message for r in caplog.records)

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_not_ready_text_layer_pdf_is_processed_normally_not_skipped(
        self, mock_services_rag_index, mock_resolve, mock_gateway, mock_ingest_rag_index, tmp_path
    ):
        """The probe's other branch: an ORDINARY text-layer PDF (also
        `media_type == "application/pdf"`, also PENDING/no `extraction`)
        must NOT be skipped -- its text is already fully present on disk,
        nothing about it is "still processing"."""
        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768
        mock_index = MagicMock()
        mock_ingest_rag_index.get_index.return_value = mock_index

        media_dir = tmp_path / "documents" / "9"
        media_dir.mkdir(parents=True)
        source = media_dir / "doc.pdf"
        source.write_bytes(make_pdf_bytes(["real extractable text"]))

        _make_document(
            title="doc.pdf",
            source_path=str(source),
            doc_type=Document.DocType.PROSE,
            media_type="application/pdf",
            status=Document.Status.PENDING,
        )

        result = services.reencode_all()

        assert result == {"documents": 1, "reencoded": 1}
        mock_index.insert_nodes.assert_called_once()


    # --- T8 review MAJOR 3: READY documents are NEVER skipped ------------

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_ready_text_layer_pdf_with_textless_front_pages_is_never_skipped(
        self, mock_services_rag_index, mock_resolve, mock_gateway, mock_ingest_rag_index, tmp_path
    ):
        """T8 review MAJOR 3, the silent-data-loss reproduction: a READY
        text-layer PDF whose first 5 pages have NO text layer -- a scanned
        cover page, figures-only pages -- but page 6 has real text. The OLD
        guard probed this document (READY + no `extraction` still counted
        as "incomplete"), the probe (`pdf_has_extractable_text`'s own
        `probe_pages=5` default, since retired -- W1) returned `False` (no
        text in the first 5 pages), and the document was silently skipped
        -- on the REBUILD path, its chunks were dropped table-wide and
        NEVER reinserted, with `reencode_all()` reporting a clean success
        anyway. The FIX gates purely on `status != READY`, so this READY
        document is never even probed -- it goes straight through the
        ordinary readers path, exactly as a READY document always safely
        can. Still a meaningful regression fixture post-W1: `pdf_textless_
        pages` has no `probe_pages` window at all (it always scans the
        WHOLE document, W1 review N4) -- so even if this guard's status
        check were ever weakened back to probing a READY document, THIS
        fixture would still be found correctly, unlike the retired
        5-page-probe function it replaces."""
        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768
        mock_index = MagicMock()
        mock_ingest_rag_index.get_index.return_value = mock_index

        media_dir = tmp_path / "documents" / "9"
        media_dir.mkdir(parents=True)
        source = media_dir / "doc.pdf"
        # 5 textless pages, then a 6th with real text -- the retired
        # pdf_has_extractable_text's own default probe_pages=5 would have
        # returned False (never seeing page six) if this document were
        # ever probed at all; this READY document must never be probed.
        source.write_bytes(make_pdf_bytes([None, None, None, None, None, "real text on page six"]))

        _make_document(
            title="doc.pdf",
            source_path=str(source),
            doc_type=Document.DocType.PROSE,
            media_type="application/pdf",
            status=Document.Status.READY,
            extraction=None,  # never extraction-stamped -- an ordinary text PDF
        )

        result = services.reencode_all()

        assert result == {"documents": 1, "reencoded": 1}
        mock_index.insert_nodes.assert_called_once()
        (nodes,), _ = mock_index.insert_nodes.call_args
        assert "real text on page six" in "".join(n.get_content() for n in nodes)

    @patch("tools.rag.services.pdf_textless_pages")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_ready_pdf_never_even_calls_the_probe(
        self,
        mock_services_rag_index,
        mock_resolve,
        mock_gateway,
        mock_ingest_rag_index,
        mock_pdf_textless_pages,
        tmp_path,
    ):
        """The direct assertion behind the previous test's reproduction:
        the probe function itself is never invoked for a READY PDF at
        all, proving the fix is "never probe a READY doc", not merely
        "the probe happened to return the right answer this time". Patched
        at `tools.rag.services.pdf_textless_pages` -- the name `services.
        py`'s own `from tools.rag.readers import pdf_textless_pages`
        binds locally, which is the reference the guard actually calls."""
        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768
        mock_index = MagicMock()
        mock_ingest_rag_index.get_index.return_value = mock_index

        media_dir = tmp_path / "documents" / "9"
        media_dir.mkdir(parents=True)
        source = media_dir / "doc.pdf"
        source.write_bytes(make_pdf_bytes(["real text"]))

        _make_document(
            title="doc.pdf",
            source_path=str(source),
            doc_type=Document.DocType.PROSE,
            media_type="application/pdf",
            status=Document.Status.READY,
        )

        services.reencode_all()

        mock_pdf_textless_pages.assert_not_called()

    @patch("tools.rag.media.get_llm_for")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_ready_scanned_pdf_is_reencoded_via_the_sidecar(
        self, mock_services_rag_index, mock_resolve, mock_gateway, mock_ingest_rag_index, mock_get_llm_for, tmp_path
    ):
        """The vision-extraction sibling of `test_reencodes_from_the_
        sidecar_without_calling_the_transcriber` (T7, above): a READY
        scanned PDF with a FINISHED extraction sidecar re-chunks from
        that sidecar -- page-keyed metadata intact, no vision model call
        at all."""
        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768
        mock_index = MagicMock()
        mock_ingest_rag_index.get_index.return_value = mock_index

        media_dir = tmp_path / "documents" / "9"
        media_dir.mkdir(parents=True)
        source = media_dir / "scan.pdf"
        source.write_bytes(make_pdf_bytes([None]))
        sidecar = {
            "version": 1,
            "method": "vision",
            "source": "scan.pdf",
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [{"page": 1, "text": "hello from the scanned page"}],
        }
        (media_dir / "extract.json").write_text(json.dumps(sidecar))

        _make_document(
            title="scan.pdf",
            source_path=str(source),
            doc_type=Document.DocType.PROSE,
            media_type="application/pdf",
            status=Document.Status.READY,
            extraction={
                "method": "extraction",
                "engine": "ollama",
                "model_id": "llava",
                "connection_name": "",
                "produced_at": "2026-08-24T00:00:00+00:00",
            },
        )

        result = services.reencode_all()

        assert result == {"documents": 1, "reencoded": 1}
        mock_get_llm_for.assert_not_called()
        mock_index.insert_nodes.assert_called_once()
        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) == 1
        assert nodes[0].metadata["page"] == 1
        assert "hello from the scanned page" in nodes[0].get_content()

    # --- T8 review MAJOR 2: the probe itself failing must never abort ----
    # --- the whole rebuild -------------------------------------------------

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_corrupt_pdf_probe_failure_fails_open_and_does_not_abort_the_rebuild(
        self, mock_services_rag_index, mock_resolve, mock_gateway, mock_ingest_rag_index, tmp_path, caplog
    ):
        """T8 review MAJOR 2, probe-reproduced: a not-READY PDF whose
        bytes are genuinely corrupt makes `pdf_textless_pages` (W1) raise
        (`pypdf.errors.PdfStreamError`) rather than return a clean list.
        On the REBUILD path (forced here via a dim mismatch), an uncaught
        raise from the skip-guard's own probe would escape this loop
        entirely AFTER `drop_chunk_table()` already ran, aborting the
        whole rematerialize with the table dropped and only partially
        repopulated. The fix fails OPEN (does not skip), so the corrupt
        document falls through to the ordinary per-document try/except,
        where it fails again for real (`ingest.ingest_prose`'s own parse)
        -- but crucially the loop CONTINUES to the next (good) document,
        which IS successfully rebuilt, proving no post-drop abort."""
        mock_services_rag_index.live_embed_dim.return_value = 1024  # forces the rebuild path
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768  # target_dim=768 != live_dim=1024
        mock_index = MagicMock()
        mock_ingest_rag_index.get_index.return_value = mock_index

        media_dir = tmp_path / "documents"

        corrupt_dir = media_dir / "9"
        corrupt_dir.mkdir(parents=True)
        corrupt_source = corrupt_dir / "corrupt.pdf"
        corrupt_source.write_bytes(b"%PDF-1.4 not a real pdf, corrupt garbage bytes")

        good_dir = media_dir / "10"
        good_dir.mkdir(parents=True)
        good_source = good_dir / "good.txt"
        good_source.write_text("perfectly good prose content")

        _make_document(
            title="corrupt.pdf",
            source_path=str(corrupt_source),
            doc_type=Document.DocType.PROSE,
            media_type="application/pdf",
            status=Document.Status.PENDING,
        )
        _make_document(
            title="good.txt",
            source_path=str(good_source),
            doc_type=Document.DocType.PROSE,
            media_type="",
            status=Document.Status.READY,
        )

        with caplog.at_level("ERROR"):
            with pytest.raises(RuntimeError, match=r"only 1 of 2"):
                services.reencode_all()

        # The rebuild path really ran (drop happened BEFORE the loop) --
        # this is the exact scenario a post-drop abort would have left
        # the table dropped and empty.
        mock_services_rag_index.drop_chunk_table.assert_called_once()
        # The good document still got its chunks rebuilt -- the loop
        # reached and completed it despite the corrupt one's probe
        # exception earlier in iteration order.
        mock_index.insert_nodes.assert_called_once()
        (nodes,), _ = mock_index.insert_nodes.call_args
        assert "perfectly good prose content" in nodes[0].get_content()

    # --- W1: the probe is per-page, not whole-file ------------------------

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_reencode_all_skips_a_processing_mixed_pdf(
        self, mock_services_rag_index, mock_resolve, mock_gateway, mock_ingest_rag_index, tmp_path, caplog
    ):
        """A MIXED not-READY PDF (some text pages, some textless) must be
        skipped exactly like a fully-scanned one -- the retired whole-file
        `pdf_has_extractable_text` probe would have returned `True` (SOME
        text exists) and NOT skipped it, silently leaving its still-
        incomplete extraction indexed as if it were the whole document."""
        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768

        media_dir = tmp_path / "documents" / "9"
        media_dir.mkdir(parents=True)
        source = media_dir / "mixed.pdf"
        source.write_bytes(make_pdf_bytes(["real text", None]))  # mixed: one text page, one textless

        _make_document(
            title="mixed.pdf",
            source_path=str(source),
            doc_type=Document.DocType.PROSE,
            media_type="application/pdf",
            status=Document.Status.PENDING,
        )

        with caplog.at_level("INFO"):
            result = services.reencode_all()

        assert result == {"documents": 0, "reencoded": 0}
        assert any("skipping Document" in r.message for r in caplog.records)

    @patch("tools.rag.services.pdf_textless_pages")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_reencode_all_probe_is_bounded_to_one_page(
        self,
        mock_services_rag_index,
        mock_resolve,
        mock_gateway,
        mock_ingest_rag_index,
        mock_pdf_textless_pages,
        tmp_path,
    ):
        """Review S5: the not-READY-PDF guard's probe is bounded to
        `limit=1` -- it only needs to know whether ANY page lacks a text
        layer, not the full textless list, so it exits at the first one
        (cheaper than the retired 5-page probe for a genuinely scan-
        bearing not-READY PDF, the case this guard exists for)."""
        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768
        mock_pdf_textless_pages.return_value = ([1], False)

        media_dir = tmp_path / "documents" / "9"
        media_dir.mkdir(parents=True)
        source = media_dir / "scan.pdf"
        source.write_bytes(b"irrelevant -- the probe is mocked")

        _make_document(
            title="scan.pdf",
            source_path=str(source),
            doc_type=Document.DocType.PROSE,
            media_type="application/pdf",
            status=Document.Status.PENDING,
        )

        services.reencode_all()

        mock_pdf_textless_pages.assert_called_once_with(Path(str(source)), limit=1)

    @patch("tools.rag.services.pdf_textless_pages")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_reencode_all_still_skips_a_missing_stored_file(
        self,
        mock_services_rag_index,
        mock_resolve,
        mock_gateway,
        mock_ingest_rag_index,
        mock_pdf_textless_pages,
        tmp_path,
        caplog,
    ):
        """Review D4: the guard keeps its `stored_path.is_file() and ...`
        conjunct verbatim -- a not-READY PDF whose managed-store file is
        simply missing must NEVER be probed (there's nothing to probe;
        `is_file()` short-circuits the `and` before `pdf_textless_pages`
        is ever reached). This does NOT mean this guard's own "still
        processing" skip fires for it -- `is_unfinished_media` is `False`
        (there's nothing to call "unfinished media" about a vanished
        file), so it falls through to the ORDINARY per-document re-encode
        attempt, which fails honestly and visibly (a real, missing-file
        error) instead of being silently waved through as "not this run's
        business yet"."""
        mock_services_rag_index.live_embed_dim.return_value = 768
        mock_services_rag_index.live_store_shape.return_value = None  # W5: no shape rebuild trigger in these tests
        mock_resolve.return_value = RESOLVED_768

        missing_source = tmp_path / "documents" / "9" / "gone.pdf"  # never created

        _make_document(
            title="gone.pdf",
            source_path=str(missing_source),
            doc_type=Document.DocType.PROSE,
            media_type="application/pdf",
            status=Document.Status.PENDING,
        )

        with caplog.at_level("ERROR"):
            with pytest.raises(RuntimeError, match=r"only 0 of 1"):
                services.reencode_all()

        mock_pdf_textless_pages.assert_not_called()

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_reencode_all_rebuilds_when_the_live_store_shape_does_not_match(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        """W5 (ADR 0014 §18): the SAME dimension-match dim, but a live
        store shape (hybrid/jsonb) that disagrees with today's toggle --
        `desired_store_shape()` -- forces the same drop-and-rebuild path
        `dim_changed` does, even though the embed_dim itself hasn't
        changed at all.

        W5 review m5: this is also the one path (`shape_changed` True)
        that used to call `desired_store_shape()` a SECOND time -- once
        for the comparison, again inside the `logger.info` shape-changed
        line -- pinned here as `call_count == 1` now that both reads share
        one local."""
        mock_rag_index.live_embed_dim.return_value = 768
        mock_resolve.return_value = RESOLVED_768
        mock_rag_index.live_store_shape.return_value = {"hybrid": False, "jsonb": False}
        mock_rag_index.desired_store_shape.return_value = {"hybrid": True, "jsonb": True}
        _make_document(title="a.txt", source_path="/data/documents/1/a.txt")

        result = services.reencode_all()

        assert result == {"documents": 1, "reencoded": 1}
        mock_rag_index.drop_chunk_table.assert_called_once_with()
        mock_rag_index.delete_chunks_for_document.assert_not_called()
        mock_rag_index.desired_store_shape.assert_called_once_with()

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_reencode_all_does_not_rebuild_when_the_table_does_not_exist_yet(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        """S8: `live_store_shape() is None` (nothing ingested yet) must
        NOT be treated as a shape mismatch -- there's nothing to drop, and
        the very next `get_vector_store()` call creates the table already
        in the desired shape."""
        mock_rag_index.live_embed_dim.return_value = None
        mock_resolve.return_value = RESOLVED_768
        mock_rag_index.live_store_shape.return_value = None
        mock_rag_index.desired_store_shape.return_value = {"hybrid": True, "jsonb": True}
        _make_document(title="a.txt", source_path="/data/documents/1/a.txt")

        result = services.reencode_all()

        assert result == {"documents": 1, "reencoded": 1}
        mock_rag_index.drop_chunk_table.assert_not_called()

    @patch("tools.rag.ingest.ingest_prose")
    @patch("tools.rag.services.gateway")
    @patch("tools.rag.services.resolve")
    @patch("tools.rag.services.rag_index")
    def test_reencode_all_does_not_rebuild_for_an_install_that_never_enabled_hybrid(
        self, mock_rag_index, mock_resolve, mock_gateway, mock_ingest_prose
    ):
        """W5 review M2b's guarantee, asserted end-to-end rather than
        inferred: an install that never touched `hybrid_search` has a live
        shape equal to the desired shape (both all-False), so a drift
        rematerialize (this same function, called by
        `models.registry.drift.run_rematerialize`) never surprise-
        rebuilds it on shape grounds."""
        mock_rag_index.live_embed_dim.return_value = 768
        mock_resolve.return_value = RESOLVED_768
        mock_rag_index.live_store_shape.return_value = {"hybrid": False, "jsonb": False}
        mock_rag_index.desired_store_shape.return_value = {"hybrid": False, "jsonb": False}
        _make_document(title="a.txt", source_path="/data/documents/1/a.txt")

        result = services.reencode_all()

        assert result == {"documents": 1, "reencoded": 1}
        mock_rag_index.drop_chunk_table.assert_not_called()
        mock_rag_index.delete_chunks_for_document.assert_called_once()
