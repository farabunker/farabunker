"""Ruling G: containment fences the CORPUS, not the bytes."""
from __future__ import annotations

import pytest
from django.urls import reverse

from tools.rag.tests._helpers import _workstream, make_document
from identity.access import owner_fields
from identity.testing import make_admin, make_user, posture, sign_in, user_principal

pytestmark = pytest.mark.django_db


def test_a_contained_document_opens_from_inside_its_stream(client, tmp_path):
    # A real file on disk: `document_file`'s existence check (unrelated to
    # containment) 404s for the default `source_path` every other
    # `make_document()` call in this module deliberately leaves pointing
    # nowhere -- see `TestDocumentFileView` in test_views_documents.py and
    # `TestTheContentRoutes` in test_document_label_page.py, which use the
    # same `tmp_path` shape whenever a real 200 is expected from this route.
    source = tmp_path / "a.txt"
    source.write_text("hello")
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    doc = make_document(workstream=stream, source_path=str(source))
    with posture("enterprise"):
        sign_in(client, user)
        response = client.get(reverse("rag-document-file", args=[doc.pk]))
    assert response.status_code == 200


def test_the_same_url_404s_for_a_signed_in_account_outside_the_stream(client):
    owner, stranger = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    doc = make_document(workstream=stream)
    with posture("enterprise"):
        sign_in(client, stranger)
        response = client.get(reverse("rag-document-file", args=[doc.pk]))
    assert response.status_code == 404
    assert "Traceback" not in response.content.decode()


def test_an_administrator_with_the_content_setting_off_gets_404(client):
    """A LISTED ROW IS NOT A KEY TO THE BYTES — the rule
    `rag-document-file` already carries for every other document."""
    owner, admin = make_user(), make_admin()
    stream = _workstream(**owner_fields(user_principal(owner)))
    doc = make_document(workstream=stream)
    with posture("enterprise", admin_sees_content=False):
        sign_in(client, admin)
        response = client.get(reverse("rag-document-file", args=[doc.pk]))
    assert response.status_code == 404


def test_an_administrator_with_the_content_setting_on_gets_200(client, tmp_path):
    """RULING G (spec §23.G). `visible_workstreams`' first branch admits
    `sees_all_content` to every stream, so `workstream_scope` returns a
    scope rather than `None` and `readable_documents` takes its
    `unrestricted` branch. Containment fences the CORPUS, not the bytes —
    and the corpus half still holds for this same administrator, which
    `test_workstream_corpus.py` cell 4 asserts."""
    # Real file on disk -- see the sibling 200 test's comment above.
    source = tmp_path / "a.txt"
    source.write_text("hello")
    owner, admin = make_user(), make_admin()
    stream = _workstream(**owner_fields(user_principal(owner)))
    doc = make_document(workstream=stream, source_path=str(source))
    with posture("enterprise", admin_sees_content=True):
        sign_in(client, admin)
        response = client.get(reverse("rag-document-file", args=[doc.pk]))
    assert response.status_code == 200


def test_the_transcript_route_answers_the_same_pair(client):
    owner, stranger = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    doc = make_document(workstream=stream)
    with posture("enterprise"):
        sign_in(client, stranger)
        response = client.get(reverse("rag-document-transcript", args=[doc.pk]))
    assert response.status_code == 404
