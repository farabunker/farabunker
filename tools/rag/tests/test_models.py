"""Unit tests for tools/rag/models.py (ADR 0008).

Covers field/relationship wiring for Document and DocumentRow, plus __str__
where defined. No external services involved. AskRecord/RagSettings field
wiring is also covered here; the write-on-answer/prune/settings-form
behavior lives in test_services.py and the test_views_*.py modules instead.
"""
import pytest

from tools.rag.models import (
    AskRecord,
    Category,
    Document,
    DocumentRow,
    RagSettings,
)
from tools.rag.tests._helpers import make_document


@pytest.mark.django_db
class TestDocument:
    def test_create_prose_document(self):
        doc = Document.objects.create(
            title="notes.md",
            source_path="/data/notes.md",
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
        )
        assert doc.pk is not None
        assert doc.doc_type == "prose"
        assert doc.tabular_schema is None
        assert doc.created_at is not None
        assert doc.updated_at is not None

    def test_create_tabular_document_with_schema(self):
        doc = Document.objects.create(
            title="sales.csv",
            source_path="/data/sales.csv",
            file_hash="b" * 64,
            doc_type=Document.DocType.TABULAR,
            tabular_schema={"Amount": "int64", "Region": "object"},
        )
        assert doc.doc_type == "tabular"
        assert doc.tabular_schema == {"Amount": "int64", "Region": "object"}

    def test_str_returns_title(self):
        doc = Document.objects.create(
            title="my-title.txt",
            source_path="/data/my-title.txt",
            file_hash="c" * 64,
            doc_type=Document.DocType.PROSE,
        )
        assert str(doc) == "my-title.txt"

    def test_status_defaults_to_ready(self):
        """Every existing prose/tabular document (and every document
        ingest still handles synchronously) is created READY -- no data
        migration needed, since READY means "nothing pending"."""
        doc = Document.objects.create(
            title="notes.md",
            source_path="/data/notes.md",
            file_hash="d" * 64,
            doc_type=Document.DocType.PROSE,
        )
        assert doc.status == Document.Status.READY
        assert doc.status_detail == ""

    def test_media_type_and_duration_and_extraction_default_blank(self):
        doc = Document.objects.create(
            title="notes.md",
            source_path="/data/notes.md",
            file_hash="e" * 64,
            doc_type=Document.DocType.PROSE,
        )
        assert doc.media_type == ""
        assert doc.duration_seconds is None
        assert doc.extraction is None


@pytest.mark.django_db
class TestDocumentExtractionSummary:
    def _doc(self, **kwargs):
        defaults = dict(
            title="clip.mp4",
            source_path="/data/clip.mp4",
            file_hash="f" * 64,
            doc_type=Document.DocType.PROSE,
        )
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    def test_empty_when_no_extraction(self):
        doc = self._doc()
        assert doc.extraction_summary == ""

    def test_transcribed_rendering(self):
        doc = self._doc(
            extraction={
                "method": "transcription",
                "engine": "whisper",
                "model_id": "whisper-large-v3",
                "connection_name": "workstation whisper",
                "produced_at": "2026-08-20T12:34:56Z",
            }
        )
        assert doc.extraction_summary == "Transcribed by whisper-large-v3 · 2026-08-20"

    def test_extracted_rendering(self):
        doc = self._doc(
            extraction={
                "method": "extraction",
                "engine": "comfyui",
                "model_id": "moondream",
                "connection_name": "workstation vision",
                "produced_at": "2026-08-21T00:00:00Z",
            }
        )
        assert doc.extraction_summary == "Extracted by moondream · 2026-08-21"

    def test_model_id_none_renders_verb_only_never_the_literal_none(self):
        """T10 review MINOR 2: `extraction` has the `model_id` KEY but its
        value is `None` (not a missing key) -- `.get("model_id", "")`'s
        default only fires for a MISSING key, so the pre-fix code
        interpolated the bare `None` into the f-string, rendering the
        literal string "Transcribed by None · 2026-08-20". The fix omits
        the "by {model_id} · {date}" tail entirely when `model_id` comes
        back blank, rather than inventing a blank-model date line."""
        doc = self._doc(
            extraction={
                "method": "transcription",
                "engine": "whisper",
                "model_id": None,
                "connection_name": "",
                "produced_at": "2026-08-20T12:34:56Z",
            }
        )
        assert doc.extraction_summary == "Transcribed"
        assert "None" not in doc.extraction_summary

    def test_model_id_missing_key_renders_verb_only(self):
        """The other way to be blank -- the key absent entirely (an older
        snapshot shape, say) -- gets the same verb-only rendering."""
        doc = self._doc(
            extraction={
                "method": "extraction",
                "engine": "comfyui",
                "connection_name": "",
                "produced_at": "2026-08-21T00:00:00Z",
            }
        )
        assert doc.extraction_summary == "Extracted"

    @pytest.mark.parametrize("bad_produced_at", [123, ["x"], {"a": 1}, None])
    def test_non_string_produced_at_renders_no_date_never_raises(self, bad_produced_at):
        """T10 re-review MAJOR: `produced_at.split("T", 1)` used to raise
        `AttributeError` for any non-string truthy value (`Document
        Manager.extraction_summary` is a property, and Django templates
        RE-RAISE property exceptions rather than swallowing them -- this
        was an uncaught 500 for `/rag/documents/`). A non-string
        `produced_at` now renders with an empty date rather than raising."""
        doc = self._doc(
            extraction={
                "method": "transcription",
                "engine": "whisper",
                "model_id": "whisper-large-v3",
                "connection_name": "",
                "produced_at": bad_produced_at,
            }
        )
        assert doc.extraction_summary == "Transcribed by whisper-large-v3 · "

    def test_empty_string_produced_at_renders_no_date(self):
        doc = self._doc(
            extraction={
                "method": "transcription",
                "model_id": "whisper-large-v3",
                "produced_at": "",
            }
        )
        assert doc.extraction_summary == "Transcribed by whisper-large-v3 · "

    def test_garbage_string_produced_at_renders_the_whole_string_as_date(self):
        """A non-ISO string `produced_at` is still a `str`, so it's still
        split on "T" -- a string with no "T" renders as-is (no raise, no
        silent blanking of a value that IS a string, just an unexpected
        one)."""
        doc = self._doc(
            extraction={
                "method": "extraction",
                "model_id": "moondream",
                "produced_at": "not-a-real-timestamp",
            }
        )
        assert doc.extraction_summary == "Extracted by moondream · not-a-real-timestamp"

    @pytest.mark.parametrize("bad_model_id", [123, ["m"], {"m": 1}])
    def test_non_string_model_id_renders_verb_only_never_raises(self, bad_model_id):
        """`model_id` is only trusted when it's actually a `str` -- a
        non-str truthy value (int/list/dict) degrades to the plain verb
        rather than interpolating a nonsense value no operator ever
        typed."""
        doc = self._doc(
            extraction={
                "method": "transcription",
                "model_id": bad_model_id,
                "produced_at": "2026-08-20T12:34:56Z",
            }
        )
        assert doc.extraction_summary == "Transcribed"


@pytest.mark.django_db
class TestDocumentSourceKind:
    """`Document.source_kind` (T10): the library table's Source badge word."""

    def _doc(self, **kwargs):
        defaults = dict(
            title="doc",
            source_path="/data/doc",
            file_hash="0" * 64,
            doc_type=Document.DocType.PROSE,
        )
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    @pytest.mark.parametrize(
        "media_type,expected",
        [
            ("video/mp4", "Video"),
            ("audio/wav", "Audio"),
            ("audio/mpeg", "Audio"),
            ("image/png", "Image"),
            ("application/pdf", "PDF"),
        ],
    )
    def test_media_type_prefix_maps_to_its_badge(self, media_type, expected):
        doc = self._doc(media_type=media_type)
        assert doc.source_kind == expected

    def test_tabular_doc_type_is_table_regardless_of_media_type(self):
        doc = self._doc(doc_type=Document.DocType.TABULAR, media_type="text/csv")
        assert doc.source_kind == "Table"

    def test_plain_prose_with_no_specific_badge_is_document(self):
        doc = self._doc(media_type="text/markdown")
        assert doc.source_kind == "Document"

    def test_blank_media_type_is_document(self):
        """A Document row predating `media_type` (or a test fixture that
        never set it) has a blank `media_type` -- falls through to the
        generic "Document" badge rather than raising."""
        doc = self._doc(media_type="")
        assert doc.source_kind == "Document"


@pytest.mark.django_db
class TestDocumentDurationDisplay:
    """`Document.duration_display` (T10): `foundation.format.format_timecode`
    wired onto `duration_seconds` for the library table."""

    def _doc(self, **kwargs):
        defaults = dict(
            title="clip.mp4",
            source_path="/data/clip.mp4",
            file_hash="1" * 64,
            doc_type=Document.DocType.PROSE,
        )
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    def test_known_duration_renders_a_timecode(self):
        doc = self._doc(duration_seconds=760.0)
        assert doc.duration_display == "12:40"

    def test_unset_duration_is_blank(self):
        doc = self._doc()
        assert doc.duration_display == ""


@pytest.mark.django_db
class TestDocumentHasTranscript:
    """`Document.has_transcript` (T10): whether an `extract.json` sidecar
    exists in this document's managed-store directory."""

    def _doc(self, **kwargs):
        defaults = dict(
            title="clip.mp4",
            source_path="/data/clip.mp4",
            file_hash="2" * 64,
            doc_type=Document.DocType.PROSE,
        )
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    def test_false_when_no_sidecar_exists(self, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._doc()
        assert doc.has_transcript is False

    def test_true_when_a_sidecar_exists(self, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._doc()
        doc_dir = tmp_path / str(doc.id)
        doc_dir.mkdir(parents=True)
        (doc_dir / "extract.json").write_text("{}")
        assert doc.has_transcript is True


@pytest.mark.django_db
class TestDocumentTranscriptLabel:
    """`Document.transcript_label` (W1 review MINOR 5): the honest label for
    `has_transcript`'s link and the transcript page's own heading --
    `has_transcript` alone is a bare filesystem stat with no idea whether
    the sidecar it found is time-keyed (video/audio) or page-keyed
    (PDF/image); a post-W1 ordinary PDF with one textless page now gets a
    genuine one-page extraction sidecar, and calling that a "Transcript"
    would be a lie."""

    def _doc(self, **kwargs):
        defaults = dict(
            title="doc",
            source_path="/data/doc",
            file_hash="3" * 64,
            doc_type=Document.DocType.PROSE,
        )
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    @pytest.mark.parametrize("media_type", ["video/mp4", "audio/wav", "audio/mpeg"])
    def test_video_and_audio_say_transcript(self, media_type):
        doc = self._doc(media_type=media_type)
        assert doc.transcript_label == "Transcript"

    @pytest.mark.parametrize("media_type", ["application/pdf", "image/png"])
    def test_pdf_and_image_say_extracted_pages(self, media_type):
        doc = self._doc(media_type=media_type)
        assert doc.transcript_label == "Extracted pages"

    def test_blank_media_type_defaults_to_extracted_pages(self):
        """No real caller ever has `has_transcript` true with a blank
        `media_type` (only video/audio/image/PDF ever write a sidecar), but
        the property still needs a defined, non-crashing answer -- the
        page-based label is the safer generic default."""
        doc = self._doc(media_type="")
        assert doc.transcript_label == "Extracted pages"


@pytest.mark.django_db
class TestDocumentRow:
    def test_row_belongs_to_document(self):
        doc = Document.objects.create(
            title="sales.csv",
            source_path="/data/sales.csv",
            file_hash="d" * 64,
            doc_type=Document.DocType.TABULAR,
        )
        row = DocumentRow.objects.create(document=doc, row_index=0, data={"Amount": 10})

        assert row.document_id == doc.id
        assert row in doc.rows.all()
        assert row.data == {"Amount": 10}

    def test_rows_cascade_delete_with_document(self):
        doc = Document.objects.create(
            title="sales.csv",
            source_path="/data/sales.csv",
            file_hash="e" * 64,
            doc_type=Document.DocType.TABULAR,
        )
        DocumentRow.objects.create(document=doc, row_index=0, data={"a": 1})
        DocumentRow.objects.create(document=doc, row_index=1, data={"a": 2})
        assert DocumentRow.objects.filter(document=doc).count() == 2

        doc.delete()
        assert DocumentRow.objects.count() == 0

    def test_str_format(self):
        doc = Document.objects.create(
            title="sales.csv",
            source_path="/data/sales.csv",
            file_hash="f" * 64,
            doc_type=Document.DocType.TABULAR,
        )
        row = DocumentRow.objects.create(document=doc, row_index=3, data={})
        assert str(row) == f"{doc.id}#3"


def test_the_retired_chat_tables_are_gone():
    """Spec section 7.6. Conversation memory lives in `agents.Turn` now,
    which is a richer table (tool_call, data, artifacts, depth) and
    belongs to the agent column, not the RAG one. These names must not
    quietly come back."""
    from django.apps import apps

    names = {model.__name__ for model in apps.get_app_config("rag").get_models()}
    assert "ChatSession" not in names
    assert "ChatMessage" not in names


def test_category_carries_no_description_column():
    """C-42. `Category.description` was declared, migrated, and then
    never set or read -- no form, no admin, no template, and a seed
    migration that only ever passed `name`. A column nothing writes is a
    column nothing can be trusted to have."""
    assert not any(f.name == "description" for f in Category._meta.get_fields())


@pytest.mark.django_db
class TestAskRecord:
    def test_create_with_all_fields(self):
        record = AskRecord.objects.create(
            question="What is our refund policy?",
            category="Business",
            connection_name="workstation llama",
            model_id="llama3.1:8b",
            answer="Refunds are processed within 5 business days.",
            citations=[{"file": "policy.md", "score": 0.83}],
        )

        assert record.pk is not None
        assert record.category == "Business"
        assert record.connection_name == "workstation llama"
        assert record.model_id == "llama3.1:8b"
        assert record.citations == [{"file": "policy.md", "score": 0.83}]
        assert record.created_at is not None

    def test_category_defaults_to_blank_for_all_categories(self):
        record = AskRecord.objects.create(
            question="q", connection_name="c", model_id="m", answer="a",
        )
        assert record.category == ""

    def test_citations_default_to_empty_list(self):
        record = AskRecord.objects.create(
            question="q", connection_name="c", model_id="m", answer="a",
        )
        assert record.citations == []

    def test_ordering_is_newest_first(self):
        older = AskRecord.objects.create(
            question="first", connection_name="c", model_id="m", answer="a",
        )
        newer = AskRecord.objects.create(
            question="second", connection_name="c", model_id="m", answer="a",
        )
        assert list(AskRecord.objects.all()) == [newer, older]

    def test_str_includes_connection_name(self):
        record = AskRecord.objects.create(
            question="q", connection_name="workstation llama", model_id="m", answer="a",
        )
        assert "workstation llama" in str(record)


@pytest.mark.django_db
class TestRagSettings:
    def test_get_solo_creates_row_with_default_limit(self):
        assert RagSettings.objects.count() == 0

        settings_row = RagSettings.get_solo()

        assert settings_row.pk == 1
        assert settings_row.history_limit == RagSettings.HISTORY_LIMIT_DEFAULT == 100
        assert RagSettings.objects.count() == 1

    def test_get_solo_returns_the_same_row_on_subsequent_calls(self):
        first = RagSettings.get_solo()
        first.history_limit = 5
        first.save(update_fields=["history_limit"])

        second = RagSettings.get_solo()

        assert second.pk == first.pk
        assert second.history_limit == 5
        assert RagSettings.objects.count() == 1

    def test_max_upload_bytes_and_max_media_seconds_default(self):
        """Operational bounds (media-into-RAG plan, T1; ADR 0014),
        not yet enforced anywhere -- just defaulted and persisted."""
        settings_row = RagSettings.get_solo()

        assert settings_row.max_upload_bytes == RagSettings.MAX_UPLOAD_BYTES_DEFAULT == 2 * 1024**3
        assert settings_row.max_media_seconds == RagSettings.MAX_MEDIA_SECONDS_DEFAULT == 7200


@pytest.mark.django_db
class TestDocumentEntitlement:
    def test_one_label_per_document_and_entitlement(self):
        from django.db.utils import IntegrityError
        from tools.rag.models import DocumentEntitlement
        from tools.rag.tests._helpers import make_entitlement
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        with pytest.raises(IntegrityError):
            DocumentEntitlement.objects.create(document=document, entitlement=finance)

    def test_deleting_the_document_takes_its_labels(self):
        from tools.rag.models import DocumentEntitlement
        from tools.rag.tests._helpers import make_entitlement
        document = make_document()
        DocumentEntitlement.objects.create(document=document,
                                           entitlement=make_entitlement())
        document.delete()
        assert DocumentEntitlement.objects.count() == 0
