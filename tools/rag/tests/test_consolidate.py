"""Consolidation — the note document, the overwrite, and the window
between destroying the old note and writing the new one.

Every test here patches `tools.rag.jobs.distil_conversation`, NOT
`tools.rag.distil.distil_conversation`: `jobs.py` imports the name at
module scope beside `answer_question`, so the patch has to land on the
binding the handler actually reads -- the same target
`test_jobs.py:430` already patches for `answer_question`, and the same
trap.

`run_consolidate` also builds a REAL chat LLM via `models.contracts.
gateway.get_llm_for` (jobs.py's own module-scope import) even in a test
that patches `distil_conversation` away entirely: the LLM object is
constructed and handed to that mock as an argument, never skipped. The
module-wide `_consolidation_seams` fixture below (a) redirects
`settings.DOCUMENTS_DIR`/`settings.NOTES_DIR` to a throwaway directory --
the same `_managed_store` convention `tools/rag/tests/test_ingest.py`
already documents, so `stage_document`'s copy and `run_consolidate`'s own
note file never touch the real repo-local `data/` directory; (b)
registers this module's own `_FakeInferenceEngine` and binds
`CHAT_CONVERSE_ROLE`/`RAG_EMBED_ROLE` to it, so `resolve()` -- which
every `_ref_for` call makes for real, and which `plan_consolidate` also
makes at enqueue time for the `client.post`-driven tests -- never raises
"no inference binding resolved", and so a real worker's post-execution
footprint measurement never attempts a genuine, slow HTTP probe (this
engine implements neither `loaded_footprint` nor `unload`, so that
measurement skips it instantly); and (c) mocks `tools.rag.ingest.
gateway`/`tools.rag.ingest.rag_index`, the same two names every real
prose-ingest test in this app mocks (`test_ingest.py`'s own
`@patch("tools.rag.ingest.rag_index")`/`@patch("tools.rag.ingest.
gateway")` convention), so `run_consolidate` step 6's real
`stage_document` + prose-ingest round-trip never needs a reachable model
server.
"""
from __future__ import annotations

import itertools
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from agents.contracts.workstreams import WorkstreamScope
from agents.workstreams import record_consolidation, staleness_for
from identity import audit
from identity.access import held_entitlement_ids, owned_entitlement_ids, owner_fields
from identity.contracts import actions
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.testing import (
    make_entitlement, make_user, posture, sign_in, user_principal,
)
from models.contracts.testing import hermetic_engine_endpoints  # noqa: F401 -- autouse fence
from tools.rag import ingest, jobs, store
from tools.rag.access import readable_documents
from tools.rag.distil import CONSOLIDATION_MAX_TURNS
from tools.rag.models import Document
from tools.rag.tests._helpers import _workstream, make_agent, make_document, make_job_ctx
from tools.rag.workstreams import stream_documents

pytestmark = pytest.mark.django_db

_names = itertools.count()


class _FakeInferenceEngine:
    """Standalone copy, per `tools/rag/tests/test_jobs.py:379-397`'s own
    convention -- one per test module, never a cross-module import.

    UNLIKE that copy, `build_llm`/`build_embedder` return a harmless
    `MagicMock()` rather than raising `NotImplementedError`:
    `run_consolidate` calls `get_llm_for(...)` FOR REAL, directly,
    even when `distil_conversation` itself is patched out in every test
    below -- the LLM object is built and handed to that mock as an
    argument, never skipped -- so this engine's `build_llm` must
    succeed, not merely go unused the way `rag.ask`'s fully-mocked
    `answer_question` lets `test_jobs.py`'s own copy get away with
    raising. Neither `loaded_footprint` nor `unload` is implemented, so
    the worker's own post-execution footprint measurement
    (`models/queue/worker.py`) skips this engine instantly rather than
    attempting a real HTTP probe.
    """

    name = "test-inference"
    well_known_ports: tuple[int, ...] = ()

    def is_healthy(self, endpoint, timeout=None):
        return True

    def build_llm(self, model_id, endpoint, **cfg):
        return MagicMock()

    def build_embedder(self, model_id, endpoint, **cfg):
        return MagicMock()


@pytest.fixture(autouse=True)
def _consolidation_seams(tmp_path, settings):
    """See the module docstring for the three things this isolates, for
    every test in this module (including the worker-driven class below,
    which needs all three exactly as much as the direct-call tests do)."""
    from models.contracts.engines import ENGINES, register
    from models.contracts.roles import CHAT_CONVERSE_ROLE, RAG_EMBED_ROLE
    from models.registry.models import ModelConnection, RoleBinding

    settings.DOCUMENTS_DIR = tmp_path / "_managed_store"
    settings.DOCUMENTS_DIR.mkdir()
    settings.NOTES_DIR = tmp_path / "_notes"

    register(_FakeInferenceEngine())
    chat_conn = ModelConnection.objects.create(
        name=f"test-chat-{next(_names)}", engine="test-inference",
        endpoint="http://fake-inference:1", model_id="chat-model",
        capabilities=["chat"])
    embed_conn = ModelConnection.objects.create(
        name=f"test-embed-{next(_names)}", engine="test-inference",
        endpoint="http://fake-inference:1", model_id="embed-model",
        capabilities=["embeddings"], embed_dim=768)
    RoleBinding.objects.create(role_key=CHAT_CONVERSE_ROLE, connection=chat_conn)
    RoleBinding.objects.create(role_key=RAG_EMBED_ROLE, connection=embed_conn)

    with patch("tools.rag.ingest.rag_index") as mock_rag_index, \
         patch("tools.rag.ingest.gateway"):
        mock_rag_index.get_index.return_value = MagicMock()
        yield

    ENGINES.pop("test-inference", None)


def test_notes_dir_is_a_shared_root_never_force_chmodded_if_it_already_existed(settings):
    """H13 review round 1, finding 10: `NOTES_DIR` is a flat shared root
    holding every conversation's note, not this job's own leaf --
    `create_owner_only_dir(..., only_if_created=True)`, same rule as
    `tools.rag.views.document_upload`'s inbox root
    (`test_document_upload_permissions.py`)."""
    settings.NOTES_DIR.mkdir()
    os.chmod(settings.NOTES_DIR, 0o755)
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=3)

    with patch("tools.rag.jobs.distil_conversation", return_value="The note."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

    assert stat.S_IMODE(settings.NOTES_DIR.stat().st_mode) == 0o755


def test_the_note_is_one_contained_document_labelled_with_the_conversations_tags():
    """§19.2 done-when 6: one contained note document titled
    `Notes — <title>`, `origin=notes`, labelled with the conversation's
    tags, retrievable by that stream's next turn and by no other."""
    from django.apps import apps

    ConversationTaint = apps.get_model("agents", "ConversationTaint")
    ent = make_entitlement()
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=3)
    ConversationTaint.objects.create(conversation=conversation, entitlement=ent)

    with patch("tools.rag.jobs.distil_conversation", return_value="The note."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

    note = Document.objects.get(notes_conversation_id=conversation.pk)
    assert note.title == f"Notes — {conversation.title}"
    assert note.origin == Document.Origin.NOTES
    assert note.workstream_id == stream.pk
    assert note.extraction["method"] == "distillation"
    assert set(note.entitlement_labels.values_list("entitlement_id", flat=True)) == {ent.pk}
    assert Document.objects.filter(notes_conversation_id=conversation.pk).count() == 1
    # Contained, so it is in this stream's corpus and nowhere else.
    other = _workstream()
    other_scope = WorkstreamScope(workstream_id=other.pk, wall=frozenset(),
                                  default_upload_placement="", may_upload=True)
    assert not stream_documents(OPEN_PRINCIPAL, other_scope).filter(pk=note.pk).exists()


def test_re_consolidating_overwrites_the_same_row_and_re_ingests():
    """SAME PK, SAME STORE DIRECTORY — and an old turn's `document:<id>`
    reference still resolves. `stage_document` dedups on
    `original_path`: the second consolidation writes the same path, finds
    the existing row, sees a changed `file_hash`, calls
    `_delete_existing_data`, and re-ingests IN PLACE. Owner decision 6's
    "overwritten and re-ingested", delivered by the ingest path exactly
    as it already works, with no new code for the overwrite case."""
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=2)

    with patch("tools.rag.jobs.distil_conversation", return_value="First."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())
    note = Document.objects.get(notes_conversation_id=conversation.pk)
    first_pk, first_dir = note.pk, store.document_dir(note.id)

    with patch("tools.rag.jobs.distil_conversation", return_value="Second, longer."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

    note.refresh_from_db()
    # SAME ROW, SAME ID, SAME STORE DIRECTORY -- `stage_document` dedups
    # on `original_path`, sees a changed `file_hash`, calls
    # `_delete_existing_data` and re-ingests IN PLACE.
    assert Document.objects.filter(notes_conversation_id=conversation.pk).count() == 1
    assert note.pk == first_pk
    assert store.document_dir(note.id) == first_dir
    assert note.status == Document.Status.READY
    assert "Second" in Path(note.source_path).read_text()
    # And every old `document:<id>` reference still resolves.
    assert readable_documents(OPEN_PRINCIPAL,
                              workstream_id=stream.pk).filter(pk=first_pk).exists()


def test_consolidating_A_leaves_Bs_note_byte_identical():
    """IT NEVER CASCADES. One conversation, one note."""
    stream = _workstream()
    a = _conversation_with_turns(stream, count=2, title="A")
    b = _conversation_with_turns(stream, count=2, title="B")

    with patch("tools.rag.jobs.distil_conversation", return_value="B's note."):
        jobs.run_consolidate(_payload(b), _refs(), make_job_ctx())
    b_note = Document.objects.get(notes_conversation_id=b.pk)
    before = (b_note.file_hash, Path(b_note.source_path).read_bytes(),
              b_note.updated_at)

    with patch("tools.rag.jobs.distil_conversation", return_value="A's note."):
        jobs.run_consolidate(_payload(a), _refs(), make_job_ctx())

    b_note.refresh_from_db()
    # IT NEVER CASCADES. One conversation, one note: consolidating A does
    # not touch B's note, does not regenerate a stream digest, and does
    # not re-ingest anything but its own row.
    assert (b_note.file_hash, Path(b_note.source_path).read_bytes(),
            b_note.updated_at) == before


def test_the_database_refuses_a_second_note_for_one_conversation():
    """`uniq_notes_per_conversation`, asserted AT THE DATABASE — enforced
    by the constraint and not only by the job that writes it: a
    re-consolidation racing itself would otherwise produce two notes and
    the stream page would show both."""
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=1)
    make_document(workstream=stream, origin=Document.Origin.NOTES,
                  notes_conversation_id=conversation.pk)

    # ENFORCED BY THE DATABASE and not only by the job that writes it: a
    # re-consolidation racing itself would otherwise produce two notes
    # and the stream page would show both.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_document(workstream=stream, origin=Document.Origin.NOTES,
                          notes_conversation_id=conversation.pk)

    # And a NULL is not constrained: every universal document has one.
    make_document()
    make_document()


def test_a_conversation_longer_than_the_cap_distils_its_most_recent_N_and_says_so():
    """An honest partial beats a failure and beats a silent truncation.
    THE FIRST LINE IS THE JOB'S OWN PROSE (author decision 17), not the
    model's, so a model that ignored an instruction cannot make the note
    lie about its own provenance."""
    stream = _workstream()
    conversation = _conversation_with_turns(stream,
                                            count=CONSOLIDATION_MAX_TURNS + 30)
    seen = {}

    def _capture(turns, *, llm=None):
        seen["count"] = len(turns)
        seen["first"] = turns[0]["text"]
        return "The note."

    with patch("tools.rag.jobs.distil_conversation", side_effect=_capture):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

    # The MOST RECENT N, not the first N.
    assert seen["count"] == CONSOLIDATION_MAX_TURNS
    assert "turn-30" in seen["first"]

    note = Document.objects.get(notes_conversation_id=conversation.pk)
    text = Path(note.source_path).read_text()
    # AN HONEST PARTIAL beats a failure and beats a silent truncation --
    # and the sentence is the JOB's own prose, not the model's (author
    # decision 17), so a model that ignored an instruction cannot make
    # the note lie about its own provenance.
    assert str(CONSOLIDATION_MAX_TURNS) in text
    assert "most recent" in text
    assert str(conversation.pk) in text


def test_the_note_inherits_the_taint_and_step_5_is_a_widening_only():
    """Owner decision 6: no laundering. A re-consolidation recomputes the
    labels from the conversation's CURRENT tags, which are additive, so
    the note's labels only ever GROW — which is the property the whole
    step exists for: a note can never become MORE readable than the
    conversation it came from."""
    from django.apps import apps

    ConversationTaint = apps.get_model("agents", "ConversationTaint")
    one, two = make_entitlement(), make_entitlement()
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=2)
    ConversationTaint.objects.create(conversation=conversation, entitlement=one)

    with patch("tools.rag.jobs.distil_conversation", return_value="First."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())
    note = Document.objects.get(notes_conversation_id=conversation.pk)
    assert set(note.entitlement_labels.values_list("entitlement_id", flat=True)) == {one.pk}

    # The conversation acquires a second tag, then is re-consolidated.
    ConversationTaint.objects.create(conversation=conversation, entitlement=two)
    with patch("tools.rag.jobs.distil_conversation", return_value="Second."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

    # A WIDENING ONLY. The tags are additive, so the note's labels only
    # ever GROW -- which is the property the whole step exists for: a
    # note can never become MORE readable than the conversation it came
    # from.
    assert set(note.entitlement_labels.values_list("entitlement_id", flat=True)) == {
        one.pk, two.pk}


def test_run_consolidate_is_exempt_from_the_labelling_authority_rule():
    """AUTHOR DECISION 27, and THE EXEMPTION IS THE POINT.
    `set_document_labels`' docstring says "THE CALLER CHECKS THE
    PREDICATE"; `run_consolidate` checks neither `may_label_document` nor
    the owner-of-every-entitlement rule, because it applies
    `taint_ids_for_conversation(cid)`, which under ruling B can contain
    entitlements the actor neither owns nor holds. The authority rule
    governs a person CHOOSING labels; nobody is choosing here — the
    labels are copied from what the material already carried, and
    refusing to copy one because the actor does not own it is precisely
    the laundering owner decision 6 forbids."""
    from django.apps import apps

    ConversationTaint = apps.get_model("agents", "ConversationTaint")
    # The acting principal neither OWNS nor HOLDS the tag -- which under
    # ruling B is a real state: a share recipient's turn can put material
    # into the owner's stream that the owner holds no grant for.
    owner = make_user()
    foreign = make_entitlement(name="Foreign")
    stream = _workstream(**owner_fields(user_principal(owner)))
    conversation = _conversation_with_turns(stream, count=2,
                                            principal=user_principal(owner))
    ConversationTaint.objects.create(conversation=conversation, entitlement=foreign)

    with posture("enterprise"):
        assert foreign.pk not in held_entitlement_ids(user_principal(owner))
        assert foreign.pk not in owned_entitlement_ids(user_principal(owner))
        with patch("tools.rag.jobs.distil_conversation", return_value="The note."):
            jobs.run_consolidate(_payload(conversation, actor=user_principal(owner)),
                                 _refs(), make_job_ctx())

    note = Document.objects.get(notes_conversation_id=conversation.pk)
    # THE EXEMPTION IS THE POINT: refusing to copy the label because the
    # actor does not own it is precisely the laundering owner decision 6
    # forbids.
    assert set(note.entitlement_labels.values_list("entitlement_id", flat=True)) == {
        foreign.pk}
    # And the `DOCUMENT_LABELLED` row is written in the actor's name.
    labelled = [r for r in audit.for_target("document", note.pk)
                if r.action == actions.DOCUMENT_LABELLED]
    assert labelled and labelled[0].actor_key == str(owner.pk)
    # The exemption is NAMED in the handler's own docstring, so a reader
    # of `set_document_labels`' callers can see why one does not check.
    assert "THE CALLER CHECKS THE PREDICATE" in jobs.run_consolidate.__doc__ \
        or "exempt" in jobs.run_consolidate.__doc__.lower()


def test_a_recipient_gets_404_consolidating_anything_including_their_own_thread(client):
    """RULING F, THE NEGATIVE (spec §23.F), asserted beside ruling C's
    positive because the two are one sentence apart and an implementer
    reading only one of them would build the other wrongly.

    Consolidation writes a stream-contained `Document`, labelled from the
    stream's taint set and ingested on the owner's box — a STREAM
    MUTATION, and it belongs beside re-sharing, wall edits and pin
    changes."""
    from django.apps import apps

    Share = apps.get_model("agents", "Share")
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM,
                         target_key=str(stream.pk), user=reader,
                         level=Share.Level.USE)
    owners_thread = _conversation_with_turns(stream, count=1,
                                             principal=user_principal(owner))
    own_thread = _conversation_with_turns(stream, count=1,
                                          principal=user_principal(reader))

    with posture("enterprise"):
        sign_in(client, reader)
        for thread in (owners_thread, own_thread):
            response = client.post(
                reverse("chat-workstream-consolidate", args=[stream.pk]),
                {"conversation": str(thread.pk)})
            assert response.status_code == 404
            assert "Traceback" not in response.content.decode()

    # RULING F: consolidation writes a stream-contained `Document`,
    # labelled from the stream's taint set and ingested on the owner's
    # box -- a STREAM MUTATION, beside re-sharing, wall edits and pin
    # changes. Ruling C makes the OWNER able to consolidate a
    # recipient's thread; it does not make the recipient able to
    # consolidate anything.
    assert Document.objects.filter(origin=Document.Origin.NOTES).count() == 0


def test_no_completed_turns_flashes_and_redirects_not_a_raw_400(client):
    """Item 6 (Coherence Wave D): this refusal used to be a raw 400 (a
    bare plain-text page, no shell, no sidebar, no flash) even though
    "Consolidate" (`chat/workstream.html:138`) is a plain zero-JS
    `<form>` on a full page. `workstream_consolidate`'s own docstring
    argues for the STATUS CODE choice among its five refusals, not for
    the response shape -- flash-and-redirect keeps the 400's honesty
    (nothing is silently queued) while landing back on the working page
    inside the shell, exactly like the other refusals this platform
    already converted."""
    owner = make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    empty_thread = _conversation_with_turns(stream, count=0,
                                            principal=user_principal(owner))
    with posture("enterprise"):
        sign_in(client, owner)
        response = client.post(
            reverse("chat-workstream-consolidate", args=[stream.pk]),
            {"conversation": str(empty_thread.pk)}, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain
    assert response.redirect_chain[-1][0] == reverse("chat-workstream", args=[stream.pk])
    assert "no completed turns to distil yet" in response.content.decode()
    assert Document.objects.filter(origin=Document.Origin.NOTES).count() == 0


def test_the_consolidate_button_and_the_staleness_hints_do_not_render_for_a_recipient(client):
    """RULING F's render half. A staleness hint is a prompt to press
    Consolidate; rendering it to a recipient who would get a 404 would be
    the render-vs-gate pair broken on the most visible surface there
    is — and `staleness_for` is simply not CALLED for a non-owner, one
    predicate checked once for the page rather than once per row."""
    from django.apps import apps

    Share = apps.get_model("agents", "Share")
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM,
                         target_key=str(stream.pk), user=reader,
                         level=Share.Level.USE)
    _conversation_with_turns(stream, count=2, principal=user_principal(owner))

    url = reverse("chat-workstream", args=[stream.pk])
    with posture("enterprise"):
        sign_in(client, owner)
        owners_view = client.get(url).content.decode()
        client.logout()
        sign_in(client, reader)
        recipients_view = client.get(url).content.decode()

    assert "Consolidate" in owners_view
    assert "Not consolidated" in owners_view
    # THE RENDER-VS-GATE PAIR: a control that 404s is worse than one that
    # is absent, and a staleness hint is a prompt to press a button the
    # recipient may not press.
    assert "Consolidate" not in recipients_view
    assert "Not consolidated" not in recipients_view
    assert "since last consolidated" not in recipients_view


def test_the_staleness_hint_reads_three_ways_and_appends_the_tag_note():
    """§19.2 done-when 8, plus §10.5's last paragraph: the hint appends
    "; 1 tag added since" when `ConversationTaint.at > consolidated_at`
    for any row, so the one case where re-consolidating changes ACCESS
    rather than content is visible on the page that offers the button.

    INDEX DIFFERENCE, NOT TURN COUNT (author decision 20): `_finish`
    leaves deliberate gaps in `Turn.index`, so the difference can exceed
    the number of turns actually added. It is a HINT, and an
    over-estimate of "how much has happened here" is the right direction
    for a hint to err in."""
    from django.apps import apps

    ConversationTaint = apps.get_model("agents", "ConversationTaint")
    Turn = apps.get_model("agents", "Turn")
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=2)

    assert staleness_for([conversation])[conversation.pk] == "Not consolidated"

    latest = Turn.objects.filter(conversation=conversation).order_by("-index").first()
    record_consolidation(conversation.pk, through_index=latest.index,
                         at=timezone.now())
    conversation.refresh_from_db()
    assert staleness_for([conversation])[conversation.pk] == "Up to date"

    # INDEX DIFFERENCE, NOT TURN COUNT (author decision 20): `_finish`
    # leaves deliberate gaps in `Turn.index`, so this can exceed the
    # number of turns actually added. It is a HINT, and an over-estimate
    # of "how much has happened here" is the right direction to err in.
    Turn.objects.create(conversation=conversation, index=latest.index + 3,
                        role=Turn.Role.USER, text="more", state=Turn.State.DONE)
    assert staleness_for([conversation])[conversation.pk] == "3 turns since last consolidated"

    # And the one case where re-consolidating changes ACCESS rather than
    # content: a tag acquired since the last consolidation leaves the
    # note UNDER-LABELLED relative to its stream.
    ConversationTaint.objects.create(conversation=conversation,
                                     entitlement=make_entitlement())
    assert staleness_for([conversation])[conversation.pk].endswith(
        "; a tag has been added since")


def test_run_consolidate_stamps_the_bookkeeping_and_the_hint_reads_up_to_date():
    """§19.2 done-when 8's ROUND TRIP: `run_consolidate` itself calls
    `record_consolidation(conversation_id, through_index=payload[
    "through_index"], ...)` -- `through_index` is REQUIRED, not
    `.get(...)`, because a missing key would silently stamp
    `consolidated_through_index=None`, which `staleness_for` reads as
    "Not consolidated" FOREVER, even after a successful run. `_payload`
    carries the real view's own `latest_completed_turn_index` snapshot,
    so this proves the whole path a fresh consolidation actually takes,
    not just `record_consolidation`'s own unit shape (already covered
    above)."""
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=3)
    assert staleness_for([conversation])[conversation.pk] == "Not consolidated"

    with patch("tools.rag.jobs.distil_conversation", return_value="The note."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

    conversation.refresh_from_db()
    assert conversation.consolidated_at is not None
    assert conversation.consolidated_through_index is not None
    assert staleness_for([conversation])[conversation.pk] == "Up to date"


def test_on_consolidate_terminal_repairs_a_stranded_pending_note_and_leaves_a_ready_one_alone():
    """`on_consolidate_terminal`'s own repair `UPDATE`, exercised directly
    -- every other test in this module only ever reaches its NO-OP
    branch (a note that already resolved on its own). A job cancelled
    while still queued, or permanently orphaned, can leave the PREVIOUS
    note at PENDING on a re-consolidation: `stage_document` already
    destroyed its chunks and its stored file and set that status inside
    its own transaction before this job died.

    BOTH HALVES: the PENDING row is repaired to FAILED with a named
    `status_detail`, and a READY row is left untouched -- the
    `status__in` guard's whole point (module docstring: a still-alive
    "stale" worker can finish and write READY between this hook's own
    read and write, which a plain `save()` would silently clobber)."""
    stream = _workstream()
    stranded_conversation = _conversation_with_turns(stream, count=1)
    stranded = make_document(workstream=stream, origin=Document.Origin.NOTES,
                             notes_conversation_id=stranded_conversation.pk,
                             status=Document.Status.PENDING)

    jobs.on_consolidate_terminal({"conversation": str(stranded_conversation.pk)}, "cancelled")

    stranded.refresh_from_db()
    assert stranded.status == Document.Status.FAILED
    assert stranded.status_detail

    ready_conversation = _conversation_with_turns(stream, count=1)
    ready = make_document(workstream=stream, origin=Document.Origin.NOTES,
                          notes_conversation_id=ready_conversation.pk,
                          status=Document.Status.READY, status_detail="")

    jobs.on_consolidate_terminal({"conversation": str(ready_conversation.pk)}, "failed")

    ready.refresh_from_db()
    assert ready.status == Document.Status.READY
    assert ready.status_detail == ""


@pytest.mark.django_db(transaction=True)
class TestConsolidateViaWorker:
    """The two consolidation tests that go through the real queue.

    `transaction=True`, for the reason `tools/rag/tests/test_jobs.py::
    TestEndToEndViaWorker` records: a handler that runs on a pool thread
    has its own DB session, which cannot see this test's uncommitted
    rows. The module-wide `_consolidation_seams` fixture already covers
    the fake engine registration and role bindings these two need -- no
    second copy of that setup here.
    """

    def test_a_re_consolidation_whose_re_ingest_FAILS_is_visible_and_repairable(self, client):
        """M13 (spec §10.4, §24 concern 5), all four properties. Step 3
        destroys the previous note before step 6 recreates it, and that
        window is real: `stage_document`, on a changed hash, runs
        `_delete_existing_data(existing)` inside its own transaction,
        then sets PENDING. If step 6 then fails, the note row survives
        with NO chunks and NO stored file.

        Bounded by three things and fixed by a fourth: the model call
        precedes the destroy (so a distillation failure destroys
        nothing); the ROW is not lost, only its content; the stream page
        renders the FAILED chip rather than a silent absence; and the
        stream page offers RE-CONSOLIDATE on it — the whole repair path,
        in one click, from the page it happened on. Without that button
        the documented repair is `document_reingest` from the library
        page, which a non-admin stream owner cannot reach for a
        contained document at all."""
        stream = _workstream(**owner_fields(user_principal(owner := make_user())))
        conversation = _conversation_with_turns(stream, count=2,
                                                principal=user_principal(owner))

        with patch("tools.rag.jobs.distil_conversation", return_value="First."):
            jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())
        note_pk = Document.objects.get(notes_conversation_id=conversation.pk).pk

        # Force step 6 to raise, AFTER step 3 has already destroyed the
        # previous note's chunks and stored file. Patched DEEPER than
        # `run_ingest_or_fail` itself -- `run_ingest_or_fail`'s own
        # contract IS the FAILED write (its docstring: "catching ANY
        # exception to write doc.status = FAILED ... before re-raising"),
        # so replacing that whole function wholesale would prove nothing
        # about it and would make assertion (a) below pass only via
        # `run_consolidate`'s own defensive fallback rather than the real
        # wrapper. `ingest.rag_index` is the module-wide fixture's own
        # `MagicMock` (`_consolidation_seams`); overriding its
        # `get_index` for just this `with` block makes the REAL
        # `run_ingest_or_fail` -> `run_ingest_for` -> `_ingest_prose`
        # chain raise mid-re-ingest and do its own real FAILED write.
        with patch("tools.rag.jobs.distil_conversation", return_value="Second."), \
             patch.object(ingest.rag_index, "get_index",
                         side_effect=RuntimeError("the embed model is down")):
            with pytest.raises(RuntimeError):
                jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

        note = Document.objects.get(pk=note_pk)
        # (a) THE ROW IS NOT LOST, only its content -- written by the
        # REAL `run_ingest_or_fail`, not by `run_consolidate`'s own
        # fallback (which stays dead on this path; see that function's
        # own comment).
        assert note.status == Document.Status.FAILED
        assert note.status_detail
        assert note.notes_conversation_id == conversation.pk

        with posture("enterprise"):
            sign_in(client, owner)
            body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
            # (b) THE STREAM PAGE SAYS SO, rather than a silent absence.
            assert "FAILED" in body
            # (c) AND OFFERS RE-CONSOLIDATE ON IT -- the whole repair
            # path, from the page it happened on. Without it the
            # documented repair is `document_reingest` from the library
            # page, which a non-admin stream owner cannot reach for a
            # contained document at all.
            assert "Re-consolidate" in body
            # (d) AND PRESSING IT RESTORES A READABLE NOTE.
            with patch("tools.rag.jobs.distil_conversation", return_value="Third."):
                client.post(reverse("chat-workstream-consolidate", args=[stream.pk]),
                            {"conversation": str(conversation.pk)})
                _run_one_worker_tick()

        note.refresh_from_db()
        assert note.status == Document.Status.READY

    def test_a_cancelled_while_queued_job_clears_the_in_flight_marker(self, client):
        """Asserted the way `on_ingest_terminal`'s test does."""
        from models.queue.backend import cancel_job

        stream = _workstream(**owner_fields(user_principal(owner := make_user())))
        conversation = _conversation_with_turns(stream, count=1,
                                                principal=user_principal(owner))

        with posture("enterprise"):
            sign_in(client, owner)
            client.post(reverse("chat-workstream-consolidate", args=[stream.pk]),
                        {"conversation": str(conversation.pk)})
            job_id = _latest_job_id("rag.consolidate", conversation)
            # A second submission while one is in flight is refused with
            # 409, the shape `start_turn`'s own in-flight refusal already
            # uses.
            assert client.post(
                reverse("chat-workstream-consolidate", args=[stream.pk]),
                {"conversation": str(conversation.pk)}).status_code == 409

            cancel_job(job_id)
            # `on_terminal` runs AFTER the terminal write commits -- one
            # conditional `UPDATE` filtered on the marker still being
            # present, never a read-then-save, exactly as
            # `on_ingest_terminal` is.
            _run_on_commit_callbacks()

            # The button is offered again.
            assert client.post(
                reverse("chat-workstream-consolidate", args=[stream.pk]),
                {"conversation": str(conversation.pk)}).status_code in (302, 200)


# --- module helpers, so every body above resolves ------------------------


def _conversation_with_turns(stream, *, count, title="A thread", principal=None):
    """A stream conversation with `count` completed root-depth turns,
    numbered so a cap test can tell which N survived."""
    from django.apps import apps

    from identity.access import owner_fields
    from identity.contracts.principals import OPEN_PRINCIPAL

    Conversation = apps.get_model("agents", "Conversation")
    Turn = apps.get_model("agents", "Turn")
    conversation = Conversation.objects.create(
        agent=make_agent(), workstream=stream, title=title,
        **owner_fields(principal or OPEN_PRINCIPAL))
    Turn.objects.bulk_create([
        Turn(conversation=conversation, index=i, depth=0,
             role="user" if i % 2 == 0 else "assistant",
             text=f"turn-{i}", state="done")
        for i in range(count)
    ])
    return conversation


def _payload(conversation, *, actor=None):
    """The job payload `chat-workstream-consolidate` enqueues.

    The acting principal travels IN THE PAYLOAD, through
    `identity.contracts.principals.payload_fields`, because the worker is
    a separate process that shares no memory with the page that asked for
    the job -- the same way `agent.turn`'s payload already carries its
    actor. `title` is the real view's own enqueue-time snapshot
    (`conversation.title`), not re-read from the row at run time -- so
    this test payload carries it too, the same reason `plan_ingest`'s own
    payload carries its `title` snapshot (jobs.py module docstring).
    `through_index` is likewise the real view's own snapshot
    (`agents.visibility.latest_completed_turn_index`) -- `run_consolidate`
    reads it REQUIRED (`payload["through_index"]`, not `.get(...)`), so a
    payload missing it is a caller bug, not a job-time default.
    """
    from agents.visibility import latest_completed_turn_index
    from identity.contracts.principals import OPEN_PRINCIPAL, payload_fields

    return {"conversation": str(conversation.pk),
            "workstream": conversation.workstream_id,
            "title": conversation.title,
            "through_index": latest_completed_turn_index(conversation),
            **payload_fields(actor or OPEN_PRINCIPAL)}


def _refs():
    """The two `ModelRef`s `plan_consolidate` returns -- the chat role
    for the distillation call and the embed role for the re-ingest."""
    from models.contracts.roles import CHAT_CONVERSE_ROLE, RAG_EMBED_ROLE

    return [_ref_for(CHAT_CONVERSE_ROLE), _ref_for(RAG_EMBED_ROLE)]


def _latest_job_id(kind: str, conversation) -> int:
    """The live queue job of `kind` for `conversation`, through the one
    sanctioned seam.

    `models.queue.visibility` is the ONLY submodule of `models.queue`
    another column may import (import-law rule 2), and Step 3 puts
    `live_job_for` there for exactly this question. `models.contracts.
    queue` cannot answer it: that leaf deliberately does not import
    `models.queue.models`, and its `get_job` needs an id the caller does
    not have.
    """
    from models.queue.visibility import live_job_for

    job_id = live_job_for(kind, conversation=str(conversation.pk))
    assert job_id is not None, f"no live {kind} job for {conversation.pk}"
    return job_id


def _run_on_commit_callbacks():
    """Fire the `transaction.on_commit` callbacks pytest-django's
    `django_db` block otherwise defers -- `on_terminal` is scheduled with
    `on_commit` at all three of its call sites, deliberately, so it never
    runs inside `claim_and_admit`'s advisory-lock window."""
    from django.db import connection

    for _sids, func, _kw in connection.run_on_commit:
        func()
    connection.run_on_commit = []


def _run_one_worker_tick():
    """One worker tick, the shape `tools/rag/tests/test_jobs.py::
    TestEndToEndViaWorker` really drives one (lines 460-466).

    `Worker` has `__init__` and `tick`; THERE IS NO `run_once`. And the
    tick only CLAIMS and LAUNCHES -- the handler runs on the pool, so a
    test that does not wait on the futures asserts against a job that has
    not run yet.
    """
    import concurrent.futures

    from models.queue.worker import Worker

    worker = Worker(worker_id="test-worker")
    try:
        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)
    finally:
        worker._executor.shutdown(wait=True)


def _ref_for(role: str):
    """One `ModelRef` for `role`, resolved the way `plan_consolidate`
    resolves it -- the `ANSWER_RESOLVED`/`EMBED_RESOLVED` convention
    `tools/rag/tests/test_jobs.py:96-98` already uses, keyed by role
    rather than hardcoded per test."""
    from models.contracts.bindings import resolve

    return jobs._ref(role, resolve(role))
