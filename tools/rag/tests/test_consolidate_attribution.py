"""H32/C-3, round 2 — the consolidation note is attributed to the
CONVERSATION's owner (not the job's acting principal), stays
workstream-contained (never `Document.Scope.CONVERSATION` — a note is
workstream-only by construction), and stays reachable only inside that
stream's own wall, exactly as any other contained document is.

A NEW module (Global Constraint 26): `tools/rag/tests/` is a whole
directory another session is editing, and `test_consolidate.py` already
holds the wider consolidation test surface. This module duplicates the
minimal scaffolding it needs — the same "standalone copy, never a
cross-module import" convention `test_consolidate.py`'s own module
docstring documents for its `_FakeInferenceEngine` — trimmed to only
what the three cases below need.
"""
from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

import pytest

from identity.access import owner_fields
from identity.testing import grant, make_entitlement, make_user, posture, user_principal
from tools.rag import jobs
from tools.rag.access import readable_documents
from tools.rag.models import Document
from tools.rag.tests._helpers import _workstream, make_agent, make_job_ctx

pytestmark = pytest.mark.django_db

_names = itertools.count()


class _FakeInferenceEngine:
    """Standalone copy of `test_consolidate.py`'s own fixture engine —
    same shape, same reason (that module's docstring): `build_llm`/
    `build_embedder` return a harmless `MagicMock()` since
    `run_consolidate` calls `get_llm_for(...)` for real even when
    `distil_conversation` itself is patched out.
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
def _seams(tmp_path, settings):
    """The same three seams `test_consolidate.py`'s own
    `_consolidation_seams` fixture isolates: a throwaway
    `DOCUMENTS_DIR`/`NOTES_DIR`, the fake inference engine bound to both
    roles `run_consolidate` resolves, and `tools.rag.ingest.rag_index`/
    `gateway` mocked so the real prose-ingest round-trip never needs a
    reachable model server."""
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


def _conversation_with_turns(stream, *, count, principal, title="A thread"):
    """A stream conversation OWNED BY `principal`, with `count` completed
    root-depth turns — the same shape `test_consolidate.py`'s own helper
    of the same name builds, duplicated per this module's own
    standalone-copy convention."""
    from django.apps import apps

    Conversation = apps.get_model("agents", "Conversation")
    Turn = apps.get_model("agents", "Turn")
    conversation = Conversation.objects.create(
        agent=make_agent(), workstream=stream, title=title, **owner_fields(principal))
    Turn.objects.bulk_create([
        Turn(conversation=conversation, index=i, depth=0,
             role="user" if i % 2 == 0 else "assistant",
             text=f"turn-{i}", state="done")
        for i in range(count)
    ])
    return conversation


def _payload(conversation, *, actor):
    """The job payload `chat-workstream-consolidate` enqueues — `actor`
    is REQUIRED here (unlike `test_consolidate.py`'s own default-`None`
    helper) because every test in this module cares specifically about
    the difference between the JOB's acting principal and the
    CONVERSATION's own owner."""
    from agents.visibility import latest_completed_turn_index
    from identity.contracts.principals import payload_fields

    return {"conversation": str(conversation.pk),
            "workstream": conversation.workstream_id,
            "title": conversation.title,
            "through_index": latest_completed_turn_index(conversation),
            **payload_fields(actor)}


def _ref_for(role: str):
    """One `ModelRef` for `role`, resolved the way `plan_consolidate`
    resolves it — the same shape `test_consolidate.py`'s own helper of
    the same name uses."""
    from models.contracts.bindings import resolve

    return jobs._ref(role, resolve(role))


def _refs():
    from models.contracts.roles import CHAT_CONVERSE_ROLE, RAG_EMBED_ROLE

    return [_ref_for(CHAT_CONVERSE_ROLE), _ref_for(RAG_EMBED_ROLE)]


def _consolidate(conversation, *, actor, note_body="The note."):
    """`note_body` is keyword-only with a fixed default so most callers
    never think about it; a RE-consolidation test that needs `stage_
    document` to see a changed `file_hash` (the same reason `test_
    consolidate.py::test_re_consolidating_overwrites_the_same_row_and_
    re_ingests` uses two different bodies) passes a different one on its
    second call."""
    with patch("tools.rag.jobs.distil_conversation", return_value=note_body):
        jobs.run_consolidate(_payload(conversation, actor=actor), _refs(), make_job_ctx())
    return Document.objects.get(notes_conversation_id=conversation.pk)


def test_the_note_is_attributed_to_the_conversations_owner_not_the_jobs_actor():
    """Ruling C: the acting principal enqueuing consolidation can be the
    STREAM owner, consolidating a RECIPIENT's own thread — and it is the
    recipient's ownership the note should carry, never the button-
    presser's. `owner_kind`/`owner_key` come from `agents.workstreams.
    owner_fields_for_conversation`, read off the CONVERSATION row, not
    from `principal_from_payload(payload)`."""
    stream_owner = make_user()
    recipient = make_user()
    stream = _workstream(**owner_fields(user_principal(stream_owner)))
    conversation = _conversation_with_turns(
        stream, count=2, principal=user_principal(recipient))

    with posture("enterprise"):
        note = _consolidate(conversation, actor=user_principal(stream_owner))

    recipient_fields = owner_fields(user_principal(recipient))
    assert note.owner_kind == recipient_fields["owner_kind"]
    assert note.owner_key == recipient_fields["owner_key"]
    # NOT the job's own acting principal (the stream owner who pressed
    # Consolidate on somebody else's thread).
    assert note.owner_key != str(stream_owner.pk)


def test_the_note_stays_workstream_contained_scope_is_never_conversation():
    """A note is workstream-only by construction (round-2 ruling): it
    inherits `workstream_id` from the conversation it was distilled from
    and is NEVER `Document.Scope.CONVERSATION` — that scope is mutually
    exclusive with containment (`tools.rag.ingest.stage_document`'s own
    guard) and would remove the note from its stream's corpus entirely,
    the opposite of spec §10's "retrievable by that stream's next turn"."""
    owner = make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    conversation = _conversation_with_turns(stream, count=2, principal=user_principal(owner))

    with posture("enterprise"):
        note = _consolidate(conversation, actor=user_principal(owner))

    assert note.workstream_id == stream.pk
    assert note.scope == Document.Scope.UNIVERSAL


def test_workstream_id_is_restamped_and_holds_across_a_re_consolidation():
    """H32 review round 1, IMPORTANT 2: `run_consolidate` now stamps
    `workstream_id` EXPLICITLY in step 4's write, not only implicitly via
    `stage_document`'s own re-stage branch (which never touches
    `existing.workstream` for this job's `actor=None` case, but that was
    an IMPLICIT invariant, never a stated one). Exercised across TWO
    consolidations of the same conversation, so a regression that only
    breaks the second write (the re-stage path) cannot hide behind a
    single-call test."""
    owner = make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    conversation = _conversation_with_turns(stream, count=2, principal=user_principal(owner))

    with posture("enterprise"):
        first = _consolidate(conversation, actor=user_principal(owner), note_body="First.")
        assert first.workstream_id == stream.pk

        # A DIFFERENT body, so `stage_document` sees a CHANGED `file_hash`
        # and takes the real re-stage branch (the same reason
        # `test_consolidate.py`'s own re-consolidation test uses two
        # different bodies), rather than the unchanged-hash short-circuit
        # that returns the existing row without touching it further.
        second = _consolidate(conversation, actor=user_principal(owner), note_body="Second, longer.")

    assert second.pk == first.pk
    assert second.workstream_id == stream.pk


def test_a_contained_conversations_note_is_not_readable_outside_its_wall():
    """Reuses `readable_documents`, the same predicate
    `test_consolidate.py::test_the_note_is_one_contained_document_
    labelled_with_the_conversations_tags` already exercises: the note's
    WALL comes from the conversation's inherited taint labels (step 5,
    unchanged by this task) — attribution (this task) is a separate
    axis and must not loosen or replace that wall."""
    owner = make_user()
    outsider = make_user()
    entitlement = make_entitlement()
    grant(entitlement, user=owner)
    stream = _workstream(**owner_fields(user_principal(owner)))
    conversation = _conversation_with_turns(stream, count=2, principal=user_principal(owner))
    from django.apps import apps

    ConversationTaint = apps.get_model("agents", "ConversationTaint")
    ConversationTaint.objects.create(conversation=conversation, entitlement=entitlement)

    with posture("enterprise"):
        note = _consolidate(conversation, actor=user_principal(owner))

        # The outsider holds no grant for the taint the note inherited --
        # OUTSIDE the wall, refused, even though the note is otherwise a
        # perfectly ordinary contained document in the same stream.
        assert not readable_documents(
            user_principal(outsider), workstream_id=stream.pk).filter(pk=note.pk).exists()
        # The owner holds the grant -- INSIDE the wall, readable.
        assert readable_documents(
            user_principal(owner), workstream_id=stream.pk).filter(pk=note.pk).exists()
