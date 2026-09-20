"""Spec §13's posture table, asserted mechanism by mechanism rather than
described.

THIS SWEEP SPANS `identity`, `agents` AND `tools/rag` -- exactly the
cross-cutting shape `foundation/ops/tests` already holds for import law
and column boundaries (`test_import_law.py`, `test_column_boundaries.py`),
and no single column's own test package is the right home for a table
that names three of them at once. Test files are exempt from import law
(`test_import_law.py`'s own docstring), so every import below is a
direct one -- production modules, never another column's `tests/
_helpers.py`: consolidating test SCAFFOLDING across columns would make
one column's private fixtures load-bearing for another's, which is
exactly the coupling `tools/rag/tests/_helpers.py::_workstream`'s own
docstring argues past for row builders. This module writes its own,
small and local, the same way `identity/tests/_helpers.py` does for the
route matrix.

EIGHT MECHANISMS, EIGHT TESTS. Five are asserted LIVE in every posture --
the stream itself, containment, pinning, upload placement, and
consolidation-and-notes; three are asserted INERT (but consistent) in
`open` -- the wall, taint, and sharing. `open`'s single caller is
`OPEN_PRINCIPAL` throughout the five LIVE tests, because `may_read_owned_
row` (`identity/access.py`) compares `owner_kind`/`owner_key` to the
caller's own `principal.kind`/`.key` and never touches a `User` row -- a
row owned by `OPEN_PRINCIPAL` is read by `OPEN_PRINCIPAL` identically in
every posture, which is what lets one caller walk the `personal`/
`enterprise` half too wherever a real signed-in owner is not otherwise
required (`accounts_on()` makes a signed-in owner the only shape a
caller can take there for an HTTP round trip, so the two HTTP-driven
tests -- the stream page and consolidation -- sign a real user in for
those two columns instead).
"""
from __future__ import annotations

import itertools

import pytest
from django.urls import reverse

from agents.models import Conversation, Turn, Workstream, WorkstreamScopeEntitlement, WorkstreamTaint
from agents.runtime.taint import stamp_turn_taint
from agents.visibility import (
    create_workstream, rename_workstream, set_workstream_archived, set_workstream_upload_default,
    visible_workstreams,
)
from agents.workstreams import wall_ids, workstream_scope
from identity.access import owner_fields, share_subjects
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.testing import make_admin, make_entitlement, make_user, posture, sign_in, user_principal
from tools.rag.access import readable_documents
from tools.rag.models import Document
from tools.rag.workstreams import pin_document, scope_with_pins

pytestmark = pytest.mark.django_db

_names = itertools.count()


# --- local row builders, duplicated on purpose (module docstring) --------

def _stream(*, principal=None, **overrides):
    fields = {"name": f"posture-stream-{next(_names)}"}
    fields.update(owner_fields(principal if principal is not None else OPEN_PRINCIPAL))
    fields.update(overrides)
    return Workstream.objects.create(**fields)


def _document(**overrides):
    fields = dict(title=f"posture-doc-{next(_names)}", source_path="/dev/null",
                  file_hash=f"{next(_names):064d}", doc_type=Document.DocType.PROSE,
                  status=Document.Status.READY)
    fields.update(overrides)
    return Document.objects.create(**fields)


def _agent(**overrides):
    from agents.models import Agent

    fields = dict(slug=f"posture-agent-{next(_names)}", name="Posture agent",
                  description="", system_prompt="You are a test agent.",
                  tool_keys=[], resident=False, enabled=True)
    fields.update(overrides)
    return Agent.objects.create(**fields)


def _completed_conversation(stream, *, principal=None):
    """A stream conversation with one completed, root-depth turn -- the
    one thing `latest_completed_turn_index` requires before
    `workstream_consolidate` will enqueue anything."""
    conversation = Conversation.objects.create(
        agent=_agent(), workstream=stream, title="A thread",
        **owner_fields(principal if principal is not None else OPEN_PRINCIPAL))
    Turn.objects.create(conversation=conversation, index=0, depth=0,
                        role=Turn.Role.USER, text="hello", state=Turn.State.DONE)
    return conversation


def _bind_role(role_key, *, name, capability, embed_dim=None):
    """A real `ModelConnection` bound to `role_key`, so `resolve()`
    succeeds for real rather than being patched away -- the same
    reasoning `agents/tests/_helpers.py::bind_chat_role`'s own docstring
    gives, duplicated here rather than imported (module docstring)."""
    from models.registry.models import ModelConnection, RoleBinding

    fields = dict(name=name, engine="ollama", endpoint="http://localhost:1",
                  model_id="a-model", capabilities=[capability])
    if embed_dim is not None:
        fields["embed_dim"] = embed_dim
    connection = ModelConnection.objects.create(**fields)
    RoleBinding.objects.update_or_create(role_key=role_key, defaults={"connection": connection})
    return connection


# --- the five LIVE mechanisms ---------------------------------------------

def test_the_stream_itself_is_fully_live_in_every_posture():
    """§13 row 1: name, instructions, conversations, archive -- create,
    read back, rename and archive all succeed identically whatever the
    posture."""
    with posture(POSTURE_OPEN):
        stream = create_workstream(OPEN_PRINCIPAL, "Open household stream")
        assert stream is not None
        assert stream.pk in set(
            visible_workstreams(OPEN_PRINCIPAL).values_list("pk", flat=True))
        assert rename_workstream(OPEN_PRINCIPAL, stream, "Renamed open stream")
        assert set_workstream_archived(OPEN_PRINCIPAL, stream, archived=True)
        stream.refresh_from_db()
        assert stream.name == "Renamed open stream"
        assert stream.archived_at is not None

    for posture_name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
        owner = make_user()
        principal = user_principal(owner)
        with posture(posture_name):
            stream = create_workstream(principal, f"{posture_name} stream")
            assert stream is not None
            assert stream.pk in set(
                visible_workstreams(principal).values_list("pk", flat=True))
            assert rename_workstream(principal, stream, "Renamed")
            assert set_workstream_archived(principal, stream, archived=True)
            stream.refresh_from_db()
            assert stream.name == "Renamed"
            assert stream.archived_at is not None


def test_containment_is_fully_live_in_every_posture():
    """§13 row 2: a CORPUS rule, not a permission rule (spec §13, §8.1) --
    it binds even for the one caller `sees_all_content` always admits
    (`open`) and for an administrator with the content setting ON,
    because the containment clause sits OUTSIDE the `unrestricted`
    branch of `readable_documents` on purpose."""
    stream = _stream()
    contained = _document(workstream=stream)
    universal = _document()

    with posture(POSTURE_OPEN):
        ids = set(readable_documents(OPEN_PRINCIPAL).values_list("pk", flat=True))
        assert universal.pk in ids and contained.pk not in ids
        ids_in_stream = set(
            readable_documents(OPEN_PRINCIPAL, workstream_id=stream.pk)
            .values_list("pk", flat=True))
        assert contained.pk in ids_in_stream

    for posture_name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
        principal = user_principal(make_admin())
        with posture(posture_name, admin_sees_content=True):
            ids = set(readable_documents(principal).values_list("pk", flat=True))
            assert universal.pk in ids and contained.pk not in ids
            ids_in_stream = set(
                readable_documents(principal, workstream_id=stream.pk)
                .values_list("pk", flat=True))
            assert contained.pk in ids_in_stream


def test_pinning_is_fully_live_in_every_posture():
    """§13 row 3: pinning a universal, readable document into a stream
    succeeds and is reflected by `scope_with_pins`, in every posture."""
    with posture(POSTURE_OPEN):
        stream = _stream()
        document = _document()
        scope = workstream_scope(OPEN_PRINCIPAL, stream.pk)
        assert scope is not None
        assert pin_document(OPEN_PRINCIPAL, scope, document) is None
        assert document.pk in scope_with_pins(scope).pinned_file_ids

    for posture_name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
        owner = make_user()
        principal = user_principal(owner)
        with posture(posture_name):
            stream = _stream(principal=principal)
            document = _document()
            scope = workstream_scope(principal, stream.pk)
            assert scope is not None
            assert pin_document(principal, scope, document) is None
            assert document.pk in scope_with_pins(scope).pinned_file_ids


def test_upload_placement_is_fully_live_in_every_posture():
    """§13 row 4: setting the stream's default upload placement -- where
    a document lands by default -- succeeds and persists in every
    posture."""
    with posture(POSTURE_OPEN):
        stream = _stream()
        assert set_workstream_upload_default(OPEN_PRINCIPAL, stream, "contained")
        stream.refresh_from_db()
        assert stream.default_upload_placement == "contained"

    for posture_name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
        owner = make_user()
        principal = user_principal(owner)
        with posture(posture_name):
            stream = _stream(principal=principal)
            assert set_workstream_upload_default(principal, stream, "contained")
            stream.refresh_from_db()
            assert stream.default_upload_placement == "contained"


def test_consolidation_is_fully_live_in_every_posture(client, monkeypatch):
    """§13 row 5: `chat-workstream-consolidate` is admitted (a 302,
    never a posture-shaped refusal) in every posture, given a completed
    conversation and both roles bound.

    `enqueue` IS MONKEYPATCHED, the same double `agents/chat/tests/
    test_never_500.py::_workstream_consolidate_normal` uses -- this test
    is about the POSTURE, not about a real worker picking the job up."""
    monkeypatch.setattr("agents.chat.views.workstreams.enqueue", lambda kind, payload: 1)
    _bind_role("chat.converse", name="posture-chat", capability="chat")
    _bind_role("rag.embed", name="posture-embed", capability="embeddings", embed_dim=768)

    with posture(POSTURE_OPEN):
        stream = _stream()
        conversation = _completed_conversation(stream)
        response = client.post(reverse("chat-workstream-consolidate", args=[stream.pk]),
                               {"conversation": str(conversation.pk)})
    assert response.status_code == 302

    for posture_name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
        owner = make_user()
        principal = user_principal(owner)
        stream = _stream(principal=principal)
        conversation = _completed_conversation(stream, principal=principal)
        with posture(posture_name):
            sign_in(client, owner)
            response = client.post(reverse("chat-workstream-consolidate", args=[stream.pk]),
                                   {"conversation": str(conversation.pk)})
        assert response.status_code == 302


# --- the three INERT (but consistent) mechanisms --------------------------

def test_the_wall_is_inert_in_open_and_the_rows_survive_a_switch(django_assert_num_queries):
    """§13 row 6, ruling A (§23.A): `wall_ids` returns the empty set on
    an open box WITHOUT READING `WorkstreamScopeEntitlement` at all, and
    the row it declines to read is not deleted -- it is dormant, and
    binds again the moment posture returns.

    PRECEDENTED CATALOGUE COLLATERAL: `agents/tests/test_wall_seams.py::
    test_wall_for_returns_empty_on_an_open_box_without_reading_the_table`
    already pins the same fact in isolation. This copy is deliberate --
    §13's table names eight mechanisms and this module's whole job is to
    have all eight asserted in the one place the table lives, including
    the ones (like this one) that already had a home of their own."""
    ent = make_entitlement(name="Wall entitlement")
    stream = _stream()
    with posture(POSTURE_ENTERPRISE):
        WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
        assert wall_ids(stream.pk) == frozenset({ent.pk})

    with posture(POSTURE_OPEN):
        with django_assert_num_queries(1):     # the posture singleton, and nothing else
            assert wall_ids(stream.pk) == frozenset()

    assert WorkstreamScopeEntitlement.objects.filter(workstream=stream).exists()
    with posture(POSTURE_ENTERPRISE):
        assert wall_ids(stream.pk) == frozenset({ent.pk})


def test_taint_is_inert_in_a_box_that_has_never_had_accounts():
    """§13 row 7: no entitlement can be granted to anything under
    `open` -- the granting pages are all account-gated -- so a genuinely
    open box never has a `DocumentEntitlement` row for `entitlement_ids_
    for` to find, and `stamp_turn_taint` costs nothing beyond the one
    artifact scan it always does in memory (`agents/runtime/taint.py`'s
    own docstring)."""
    with posture(POSTURE_OPEN):
        stream = _stream()
        conversation = Conversation.objects.create(
            agent=_agent(), workstream=stream, **owner_fields(OPEN_PRINCIPAL))
        document = _document()          # unlabelled: nothing could have labelled it
        turn = Turn.objects.create(
            conversation=conversation, index=0, depth=0, role=Turn.Role.TOOL, text="",
            artifacts=[f"document:{document.pk}"], state=Turn.State.DONE)
        added = stamp_turn_taint(turn, conversation, turn.artifacts)
    assert added == frozenset()
    assert not WorkstreamTaint.objects.filter(workstream=stream).exists()


def test_sharing_is_inert_in_open():
    """§13 row 8: there is no second account to share to, so
    `share_subjects` -- the function the share form is built from, which
    already excludes the caller -- returns nothing at all on an open
    box, whoever else exists.

    THE OTHER ACCOUNT EXISTS AND IS STILL EXCLUDED, non-vacuously: a
    version of this test with no second user would pass just as well if
    `share_subjects` ignored posture entirely and simply excluded every
    account, which is not the claim §13 makes.

    PRECEDENTED CATALOGUE COLLATERAL, the same shape as this module's own
    wall test: `identity/tests/test_entitlements_access.py::
    test_both_readers_answer_empty_in_open_posture` already pins this
    exact fact, and `::test_share_subjects_lists_active_accounts_and_
    groups` pins the `enterprise` half `share_subjects`'s own "live, §12
    in full" row promises. This copy exists so §13's table has all eight
    of its rows asserted from the one place the table itself lives."""
    make_user()  # a second account exists -- and is still excluded, below
    with posture(POSTURE_OPEN):
        assert share_subjects(OPEN_PRINCIPAL) == {"users": (), "groups": ()}
