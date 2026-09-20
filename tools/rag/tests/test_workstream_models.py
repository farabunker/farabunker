"""Containment and pinning, at the table."""
from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from tools.rag.models import Document, WorkstreamPin
from tools.rag.tests._helpers import _workstream, make_document

pytestmark = pytest.mark.django_db


def test_every_existing_document_is_universal_and_an_upload():
    """No data migration anywhere, and that is the load-bearing property
    of every column added here: `workstream` null means "the universal
    library", which every existing document is; `origin` defaults to
    `upload`, which every existing document is."""
    doc = make_document()
    assert doc.workstream_id is None
    assert doc.origin == Document.Origin.UPLOAD


def test_a_contained_document_names_its_stream_by_string_fk():
    stream = _workstream()
    doc = make_document(workstream=stream)
    assert doc.workstream_id == stream.pk
    assert list(stream.documents.all()) == [doc]


def test_deleting_a_stream_that_still_contains_a_document_is_refused():
    """PROTECT on both containment FKs (author decision 15)."""
    stream = _workstream()
    make_document(workstream=stream)
    with pytest.raises(ProtectedError):
        stream.delete()


def test_a_pin_is_unique_per_stream_and_document():
    stream = _workstream()
    doc = make_document()
    WorkstreamPin.objects.create(workstream=stream, document=doc)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WorkstreamPin.objects.create(workstream=stream, document=doc)


def test_a_pin_dies_with_its_stream_and_with_its_document():
    """CASCADE on both halves, because a pin is pure association and its
    loss destroys nothing."""
    stream, other = _workstream(), _workstream()
    doc, doc2 = make_document(), make_document()
    WorkstreamPin.objects.create(workstream=stream, document=doc)
    WorkstreamPin.objects.create(workstream=other, document=doc2)
    stream.delete()
    assert WorkstreamPin.objects.count() == 1
    doc2.delete()
    assert WorkstreamPin.objects.count() == 0


def test_a_universal_document_may_be_pinned_into_two_streams():
    """One row, one home, and a pin is an ASSOCIATION, not a copy --
    section 21.2's own answer to "it belongs to both"."""
    one, two = _workstream(), _workstream()
    doc = make_document()
    WorkstreamPin.objects.create(workstream=one, document=doc)
    WorkstreamPin.objects.create(workstream=two, document=doc)
    assert doc.workstream_pins.count() == 2


def test_origin_is_its_own_column_not_a_method_value_inside_extraction():
    """AUTHOR DECISION 12. `extraction` snapshots WHAT PRODUCED THE TEXT;
    `origin` records WHAT PUT THE ROW IN THE LIBRARY. A note's
    `extraction` is still written, with `method="distillation"`, which
    `extraction_summary` already degrades to "Processed"."""
    doc = make_document(origin=Document.Origin.NOTES,
                        extraction={"method": "distillation", "produced_at": "2026-09-03"})
    assert doc.origin == "notes"
    assert doc.extraction["method"] == "distillation"
    assert "Processed" in doc.extraction_summary


def test_an_emptied_stream_deletes():
    """The other half of the `PROTECT` gate spec section 18 requires
    exercised: a stream with a conversation or a document refuses; an
    emptied one deletes."""
    stream = _workstream()
    doc = make_document(workstream=stream)
    doc.delete()
    stream.delete()          # no ProtectedError
