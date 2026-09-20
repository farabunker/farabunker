"""F10 (walk-fix batch): the in-stream upload placement chooser was fully
built at `document_upload` + `documents.html`, gated on a `workstream`
POST field no page inside a stream ever sent -- so it never rendered and
every UI upload landed universal. This is the panel form that closes
that gap: `rag/panels/documents.html`'s own Upload section, posting
straight to the existing `rag-document-upload` endpoint with `workstream`
carried as a hidden field.

Reuses `tools.rag.tests.test_upload_placement`'s own conventions
(`_upload`-shaped POST, `posture("enterprise")`, `owner_fields`) since
the placement RESOLUTION itself (`document_upload`'s own gate) is
already covered there in full -- these tests are about the PANEL: does
it render the form, in the right state, for the right viewer, and does
a POST from it actually reach the same view.
"""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from tools.rag.models import Document
from tools.rag.tests._helpers import _workstream
from identity.access import owner_fields
from identity.testing import grant, make_entitlement, make_user, posture, sign_in, user_principal

pytestmark = pytest.mark.django_db


def test_the_owner_sees_the_upload_form_with_unpreselected_radios(client):
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert f'action="{reverse("rag-document-upload")}"' in body
    assert f'name="workstream" value="{stream.pk}"' in body
    assert 'name="placement" value="universal" required' in body
    assert 'name="placement" value="contained" required' in body
    # NEITHER RADIO IS PRESELECTED (spec §9) -- no `checked` attribute
    # anywhere near either `<input type="radio" name="placement"...>`.
    assert 'value="universal" required checked' not in body
    assert 'value="contained" required checked' not in body


def test_the_radios_render_inside_placement_options(client):
    """round20-confirm.md's own R4, the `tools/rag`-side half:
    `foundation/ops/tests/test_css_ownership.py`'s own docstring admits
    `rag/_placement_choice.html` is "outside its reach either way" (it
    never walks `tools/rag` templates at all) -- this is the narrower,
    purpose-built check that gate cannot be for this one fragment,
    proving the SAME `.placement-options` wrap
    `agents/chat/tests/test_composer.py::TestRound20ConfirmResiduals`
    already pins for the composer's own attach door."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    options_start = body.index('<div class="placement-options">')
    options_end = body.index('</div>', options_start)
    options_block = body[options_start:options_end]
    assert 'value="universal"' in options_block
    assert 'value="contained"' in options_block
    assert "remember" not in options_block
    assert "hint" not in options_block
    assert body.index('class="remember"') > options_end


def test_a_default_active_shows_the_note_and_the_change_link(client):
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)),
                         default_upload_placement="contained")
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert "per this workstream" in body.lower()
    # SETTINGS-PAGE SPLIT: the "change" link points at the upload
    # default's new home, `chat-workstream-settings`, not the working
    # page this panel itself still renders on.
    assert (f'{reverse("chat-workstream-settings", args=[stream.pk])}#upload-default'
            in body)
    # The choose-every-time fieldset is gone once a default is set --
    # scoped to the UPLOAD SUBSECTION alone (round 18: the New-chat
    # composer's own attach door, `chat/_composer.html`, ALSO renders a
    # `name="placement" value="universal" required` radio elsewhere on
    # this same page now -- a real, unrelated control this test was
    # never about, so an unscoped `in body` would false-positive on it).
    upload_section = body[body.index('<h3 class="ws-subhead">Upload</h3>'):
                          body.index('<h3 class="ws-subhead">Contained</h3>')]
    assert 'name="placement" value="universal" required' not in upload_section


def test_a_non_manager_sees_no_upload_section_at_all(client):
    """Owner-gated the same way the pin control is (`may_upload`) -- a
    share recipient never sees a control the route would 404 for them."""
    from agents.models import Share

    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert f'action="{reverse("rag-document-upload")}"' not in body
    assert "<h3 class=\"ws-subhead\">Upload</h3>" not in body


def test_a_post_from_the_panel_form_lands_where_it_says(client):
    """The panel form is a plain POST to the SAME endpoint `documents.
    html`'s own form uses -- `test_upload_placement.py::
    test_the_chosen_placement_is_what_the_row_gets` already proves the
    view's own resolution; this proves the panel's markup actually wires
    up to it (the right field names, the right endpoint)."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        client.post(reverse("rag-document-upload"), {
            "files": SimpleUploadedFile("note.md", b"hello"),
            "workstream": str(stream.pk),
            "placement": "contained",
        })
    doc = Document.objects.get()
    assert doc.workstream_id == stream.pk


def test_the_pin_literal_equals_the_real_tool_key():
    """The pair `agents/chat/views/workstreams.py::_RAG_INGEST_TOOL_KEY`
    (a plain string, since `agents/` may not import `tools/`) and
    `tools.rag.tools.RAG_INGEST_TOOL_KEY` -- pinned equal here, on the
    side of the seam that MAY import both, the same shape `test_upload_
    placement.py::test_the_views_placement_literals_equal_the_models_
    own_values` already uses for its own cross-boundary literal pair."""
    from agents.chat.views.workstreams import _RAG_INGEST_TOOL_KEY
    from tools.rag.tools import RAG_INGEST_TOOL_KEY

    assert _RAG_INGEST_TOOL_KEY == RAG_INGEST_TOOL_KEY


def test_an_owner_missing_the_rag_ingest_label_sees_no_upload_form(client):
    """F10 review round 2 (Important finding 1): `is_owner` answers "may
    manage the stream", not "holds `rag.ingest`" -- an admin can label
    the tool to an entitlement this owner does not hold, and the panel
    must not render a form that looks live and then 403s bare on
    submit (the same render-vs-gate defect `tools/rag/views.py`'s own
    `pin_into_scope.may_upload` comment documents for the pin banner)."""
    from agents.models import ToolEntitlement

    user = make_user()
    ToolEntitlement.objects.create(tool_key="rag.ingest",
                                   entitlement=make_entitlement(name="Legal"))
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert "<h3 class=\"ws-subhead\">Upload</h3>" not in body
    assert f'action="{reverse("rag-document-upload")}"' not in body


def test_an_owner_holding_the_rag_ingest_label_still_sees_the_form(client):
    """The sibling case: labelled, but the owner DOES hold the labelling
    entitlement -- the form renders exactly as it does when the tool is
    unlabelled entirely (the other tests above)."""
    from agents.models import ToolEntitlement

    user = make_user()
    legal = make_entitlement(name="Legal")
    grant(legal, user=user)
    ToolEntitlement.objects.create(tool_key="rag.ingest", entitlement=legal)
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert "<h3 class=\"ws-subhead\">Upload</h3>" in body
    assert f'action="{reverse("rag-document-upload")}"' in body
