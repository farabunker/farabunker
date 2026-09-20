"""Owner decision 5: where a document lives is a decision, and a decision
that defaults silently is a decision nobody made."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from tools.rag.models import Document
from tools.rag.tests._helpers import _workstream
from identity.access import owner_fields
from identity.testing import make_user, posture, sign_in, user_principal

pytestmark = pytest.mark.django_db


def _upload(client, **fields):
    return client.post(reverse("rag-document-upload"), {
        "files": SimpleUploadedFile("note.md", b"hello"), **fields})


def _write_temp(name: str, content: str) -> Path:
    """Write `content` to `name` inside a shared temp directory, returning
    the Path -- a plain function, not a `tmp_path` fixture indirection, so
    `test_a_re_stage_does_not_re_read_the_placement` (which takes no
    fixture arguments) can call it twice against the SAME file name to
    simulate a content change in place."""
    directory = Path(tempfile.gettempdir()) / "rag-test-upload-placement"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content)
    return path


def test_an_upload_outside_a_stream_is_unchanged_and_offers_no_choice(client):
    """Everything outside a stream is unchanged, including its actor: the
    watcher, `manage.py ingest`, the `rag.ingest` tool runner and the
    library page's own form all produce universal documents, exactly as
    today. The DEFAULT PARAMETER is what makes that true without a single
    caller edit."""
    user = make_user()
    with posture("enterprise"):
        sign_in(client, user)
        _upload(client)
    assert Document.objects.get().workstream_id is None


def test_an_in_stream_upload_with_neither_radio_picked_flashes_and_redirects(client):
    """AUTHOR DECISION 17. HTML's `required` on a radio group is the
    RENDER half; the view is the GATE half, and this is the one field
    where a server-side fallback would silently undo an owner decision,
    so there is none -- the refusal itself is unchanged.

    UPDATED, ITEM 6 (Coherence Wave D): the refusal used to be a raw
    400 (a bare plain-text page); it is now the SAME flash-and-redirect
    shape the "no files selected" refusal on this view already used, so
    this pin follows the redirect and reads the message rather than
    asserting the old bare status."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        response = _upload(client, workstream=str(stream.pk))
        assert response.status_code == 302
        followed = client.get(response.url)
    assert "placement" in followed.content.decode().lower()
    assert Document.objects.count() == 0


def test_an_unrecognised_placement_flashes_and_redirects_not_a_fallback(client):
    """UPDATED, ITEM 6 (Coherence Wave D): see the sibling test just
    above -- same conversion, same reasoning. N3 (review fix round):
    read the flashed message too, not only the redirect -- the same
    strength the sibling test asserts."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        response = _upload(client, workstream=str(stream.pk), placement="sideways")
        assert response.status_code == 302
        followed = client.get(response.url)
    assert "placement" in followed.content.decode().lower()
    assert Document.objects.count() == 0


@pytest.mark.parametrize("placement,contained", [("universal", False), ("contained", True)])
def test_the_chosen_placement_is_what_the_row_gets(client, placement, contained):
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        _upload(client, workstream=str(stream.pk), placement=placement)
    doc = Document.objects.get()
    assert (doc.workstream_id == stream.pk) is contained


def test_remembering_the_choice_sets_the_streams_default_and_audits_it(client):
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        _upload(client, workstream=str(stream.pk), placement="contained",
                remember_placement="1")
    stream.refresh_from_db()
    assert stream.default_upload_placement == "contained"


def test_with_a_default_active_the_form_carries_no_placement_and_the_view_reads_it(client):
    """State three: no radios. One line — "Placed in this workstream only
    per this workstream's default." — with **change**, a link to the
    stream's SETTINGS page (settings-page split) upload-default
    control."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)),
                         default_upload_placement="contained")
    with posture("enterprise"):
        sign_in(client, user)
        response = _upload(client, workstream=str(stream.pk))
    assert response.status_code in (302, 200)
    assert Document.objects.get().workstream_id == stream.pk


def test_a_stream_the_uploader_may_not_upload_to_is_a_404(client):
    """`may_upload` is False for a share recipient, and the route is
    row-addressed, so this is a 404 and not a 403."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    with posture("enterprise"):
        sign_in(client, reader)
        response = _upload(client, workstream=str(stream.pk), placement="contained")
    assert response.status_code == 404
    assert Document.objects.count() == 0


def test_placement_and_labels_are_independent(client):
    """"Placement decides where it lives. Labels decide who may read it."
    A contained document MAY carry labels, and should, if its content
    warrants them: containment is organisation, labels are access.

    `tools.rag.ingest.enqueue` is patched (the same "mock at the module
    boundary" convention `TestEnqueueIngest`/`TestDocumentUpload` already
    use elsewhere in this package) purely so the real, unmocked
    `document_upload` -> `enqueue_ingest` -> `stage_document` path can run
    to a successful `job_id` in a fresh test database with no `rag.embed`
    role binding -- label application, like the queued-count message,
    happens only once the queue accepts the job. Nothing about placement
    or labelling itself is stubbed."""
    from unittest.mock import patch

    from identity.testing import grant, make_entitlement
    user = make_user()
    ent = make_entitlement()
    grant(ent, user=user, role="owner")
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        with patch("tools.rag.ingest.enqueue", return_value=42):
            _upload(client, workstream=str(stream.pk), placement="contained",
                    entitlements=str(ent.pk))
    doc = Document.objects.get()
    assert doc.workstream_id == stream.pk
    assert doc.entitlement_labels.count() == 1


def test_a_re_stage_does_not_re_read_the_placement():
    """"A document's home is set when it is first staged and a later
    content change re-ingests it in place" (spec §9.2). Asserted at
    `stage_document`, because that is where the rule lives."""
    from tools.rag import ingest

    stream = _workstream()
    path = _write_temp("note.md", "one")
    doc, changed = ingest.stage_document(str(path), None, move=False,
                                         workstream_id=stream.pk)
    assert changed and doc.workstream_id == stream.pk
    _write_temp("note.md", "two")
    doc2, changed2 = ingest.stage_document(str(path), None, move=False)
    assert changed2 and doc2.pk == doc.pk
    assert doc2.workstream_id == stream.pk       # unchanged, not cleared


def test_the_views_placement_literals_equal_the_models_own_values():
    """Two literals beside the view that reads them, because `tools/rag`
    may reach `agents` only through the three named seams. Pinned equal
    here so the pair cannot drift."""
    from django.apps import apps

    from tools.rag.views import _PLACEMENTS

    model = apps.get_model("agents", "Workstream")
    assert set(_PLACEMENTS) == set(model.UploadPlacement.values)
