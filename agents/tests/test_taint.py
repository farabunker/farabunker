"""What the work has touched — the stamp, the union upward, the audit,
and the two things that would have made both share gates decorative."""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from agents.contracts.artifacts import ArtifactLabels, register_artifact_labels
from agents.contracts.tests._helpers import isolated_labels_registry  # noqa: F401
from agents.contracts.tools import Param, ToolResult, ToolSpec, register_tool
from agents.contracts.toolschema import wire_name
from agents.models import Conversation, ConversationTaint, Turn, WorkstreamTaint
from agents.runtime import loop as loop_module
from agents.runtime.taint import stamp_turn_taint
from agents.tests._helpers import (  # noqa: F401
    _workstream, bind_chat_role, isolated_tool_registry, make_agent, make_job_ctx, make_tool_ctx,
)
from identity.contracts.principals import payload_fields
from agents.visibility import create_conversation, duplicate_conversation
from identity import audit
from identity.contracts import actions
from identity.contracts.principals import OPEN_PRINCIPAL
from models.contracts.roles import CHAT_CONVERSE_ROLE
from identity.testing import (
    make_admin, make_entitlement, make_user, posture, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def agent():
    """A chat-bound `Agent` row granted `rag.ask` -- what the four
    end-to-end drivers below need to run a REAL turn (and a real
    delegation: `_run_delegate_that_cites` looks this same row up by its
    own slug). Every other test in this module calls `stamp_turn_taint`
    directly and never touches the model binding or the tool registry at
    all, so binding the role and granting the tool here costs those
    tests nothing but one extra row."""
    bind_chat_role(CHAT_CONVERSE_ROLE)
    return make_agent(tool_keys=["rag.ask"])


def test_a_turn_that_returned_a_labelled_document_leaves_one_tag_at_each_level(agent):
    """§19.2 done-when 1: one conversation tag, one stream tag and two
    audit rows naming the causing turn."""
    ent = make_entitlement(name="E")
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc = _labelled_document(ent)
    turn = _tool_turn(conversation, [f"document:{doc.pk}"])
    added = stamp_turn_taint(turn, conversation, turn.artifacts)
    assert added == frozenset({ent.pk})
    assert ConversationTaint.objects.filter(conversation=conversation,
                                            entitlement=ent).count() == 1
    ws_tag = WorkstreamTaint.objects.get(workstream=stream, entitlement=ent)
    assert ws_tag.first_conversation == conversation.pk
    assert ws_tag.first_turn == turn.pk
    assert {r.action for r in audit.recent()} >= {
        actions.CONVERSATION_TAINTED, actions.WORKSTREAM_TAINTED}


def test_the_second_such_turn_leaves_nothing(agent):
    """One audit row per (stream, entitlement) FIRST SIGHTING, because
    the question an operator asks of this trail is "when did E get into
    this stream, and what brought it in" — and a row per turn would bury
    it under every subsequent turn that returned the same document.
    `bulk_create(..., ignore_conflicts=True)` against the unique
    constraints is what makes the second turn free."""
    ent = make_entitlement()
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc = _labelled_document(ent)
    first = _tool_turn(conversation, [f"document:{doc.pk}"])
    stamp_turn_taint(first, conversation, first.artifacts)
    before = len(audit.recent())
    second = _tool_turn(conversation, [f"document:{doc.pk}"])
    assert stamp_turn_taint(second, conversation, second.artifacts) == frozenset()
    assert ConversationTaint.objects.count() == 1
    assert len(audit.recent()) == before


def test_a_turn_with_no_document_artifact_costs_nothing(agent, django_assert_num_queries):
    """§7.2's early return, asserted with a QUERY COUNT. The
    overwhelmingly common case — a turn that called no retrieval tool —
    must cost nothing."""
    conversation = Conversation.objects.create(agent=agent, workstream=_workstream())
    turn = _tool_turn(conversation, ["output:12", "input:3"])
    with django_assert_num_queries(0):
        assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset()


def test_a_loose_conversation_is_tainted_too_and_stops_at_step_one(agent):
    """AUTHOR DECISION 9. `ConversationTaint` hangs off the conversation,
    not the stream, so the stamp has no "am I in a stream" branch and a
    loose conversation accumulates tags nothing reads in v1 — which is
    what makes conversation-level share gating a later change with NO
    BACK-FILL."""
    ent = make_entitlement()
    conversation = Conversation.objects.create(agent=agent)
    doc = _labelled_document(ent)
    turn = _tool_turn(conversation, [f"document:{doc.pk}"])
    stamp_turn_taint(turn, conversation, turn.artifacts)
    assert ConversationTaint.objects.count() == 1
    assert WorkstreamTaint.objects.count() == 0


def test_a_titled_document_reference_parses_to_the_bare_id(agent):
    """`parse_artifact` is THE ONE PARSER and taint uses it, so the taint
    stamp cannot come to disagree with the chat page's own rendering
    about what a reference means. `mint_artifact` may append a
    `:<title>` suffix, which `parse_artifact` peels off."""
    ent = make_entitlement()
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc = _labelled_document(ent)
    turn = _tool_turn(conversation, [f"document:{doc.pk}:Attention%20Is%20All%20You%20Need"])
    assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset({ent.pk})


def test_a_malformed_reference_is_skipped_rather_than_raising(agent):
    """`parse_artifact` is the one parser and it RAISES on a bad
    reference; this runs inside a turn's own transaction, so one bad
    string must never fail a turn."""
    conversation = Conversation.objects.create(agent=agent, workstream=_workstream())
    turn = _tool_turn(conversation, ["document:not-a-number", "", "nonsense"])
    assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset()


def test_a_turn_that_retrieves_and_then_FAILS_still_taints(
        agent, monkeypatch, isolated_tool_registry):
    """M5, and the test that would have PASSED against the earlier
    `_finish` placement while asserting the wrong thing.

    `run_loop`'s caller wraps `_run_turn` in a `try/except` that flips the
    assistant turn to FAILED with one `.update()`, and `_finish` is
    reached only on the SUCCESS path — while the tool turns are created
    and COMMITTED as they run, each carrying the retrieved text and its
    `document:` references. Stamping in `_finish` would leave `E`
    material committed and rendered in the thread with NO taint row, and
    both share gates would then pass a stream that holds `E` material.
    That is the opposite of the conservative direction: "material is in
    there and no gate knows", not "a share you expected does not work"."""
    # Drive a real turn whose retrieval tool succeeds and whose assistant
    # call then raises, then assert the tags exist and the assistant turn
    # is FAILED.
    ent = make_entitlement(name="E")
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc = _labelled_document(ent)

    # A retrieval tool that SUCCEEDS, then an assistant call that raises.
    # `run_loop`'s caller flips the assistant turn to FAILED with one
    # `.update()`; the tool turn is already committed by then.
    monkeypatch.setattr(loop_module.gateway, "get_llm_for",
                        lambda *a, **k: _llm_that_cites_then_raises(doc))
    with pytest.raises(RuntimeError):
        _drive_turn(conversation)

    assert conversation.taint_tags.filter(entitlement=ent).exists()
    assert stream.taint_tags.filter(entitlement=ent).exists()
    assert Turn.objects.filter(conversation=conversation,
                               role=Turn.Role.ASSISTANT,
                               state=Turn.State.FAILED).exists()
    assert Turn.objects.filter(conversation=conversation,
                               role=Turn.Role.TOOL,
                               state=Turn.State.DONE).exists()


def test_a_turn_that_stops_between_two_tool_turns_taints_from_the_first(
        agent, monkeypatch, isolated_tool_registry):
    """The same property by a different route."""
    first_ent, second_ent = make_entitlement(), make_entitlement()
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    first_doc = _labelled_document(first_ent)
    _labelled_document(second_ent)

    # THERE IS NO CANCELLATION EXCEPTION IN THIS TREE. `grep -rn
    # JobCancelled` finds nothing: a QUEUED job is cancelled by
    # `models.queue.backend.cancel_job`, and a RUNNING handler is not
    # interrupted by an exception at all -- it finishes, or it raises for
    # some other reason. Spec §7.2's *"A cancellation between tool turns
    # is the same shape"* is a statement about the SHAPE, so this test
    # asserts that shape with the only mechanism that exists: the turn
    # stops between two tool turns, for any reason.
    def _stop_after_the_first_tool_turn(*args, **kwargs):
        if Turn.objects.filter(conversation=conversation,
                               role=Turn.Role.TOOL).exists():
            raise RuntimeError("the worker went away between tool turns")
        return _tool_selection_citing(first_doc)

    monkeypatch.setattr(loop_module.gateway, "get_llm_for",
                        lambda *a, **k: _llm_calling(_stop_after_the_first_tool_turn))
    with pytest.raises(RuntimeError):
        _drive_turn(conversation)

    assert stream.taint_tags.filter(entitlement=first_ent).exists()
    assert not stream.taint_tags.filter(entitlement=second_ent).exists()


def test_the_union_across_two_retrievals_in_one_turn_is_complete(agent):
    """Falls out of the loop rather than depending on an accumulator,
    which is one of the three reasons the stamp is on the tool turn."""
    one, two = make_entitlement(), make_entitlement()
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc_one, doc_two = _labelled_document(one), _labelled_document(two)

    # ONE tool turn returning BOTH references -- the shape
    # `_document_artifacts` mints when one `rag.ask` cites two documents.
    turn = _tool_turn(conversation,
                      [f"document:{doc_one.pk}", f"document:{doc_two.pk}"])
    assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset(
        {one.pk, two.pk})

    # And across TWO tool turns in the same run, which is the case that
    # falls out of the loop rather than depending on an accumulator.
    three = make_entitlement()
    second = _tool_turn(conversation, [f"document:{_labelled_document(three).pk}"])
    stamp_turn_taint(second, conversation, second.artifacts)
    assert set(stream.taint_tags.values_list("entitlement_id", flat=True)) == {
        one.pk, two.pk, three.pk}


def test_a_delegates_documents_are_in_the_union(agent, isolated_tool_registry):
    """§7.1's NAMED RISK, pinned rather than assumed: a document returned
    by a DELEGATE contributes only if the delegate's `ToolResult.
    artifacts` propagate to a tool turn the stamp actually rides. They do
    today (`agents/runtime/delegate.py` writes artifacts onto the
    delegate's own turn and the delegate's `ToolResult` carries them back
    to whatever tool turn wraps the call), and this is the one path
    where "the union" could quietly stop being the union.

    `run_agent_tool` is invoked directly here (bypassing the root's own
    `invoke_tool` dispatch), so the delegate's OWN inner tool turn --
    created and stamped for real, inside its own `run_loop` call, at
    `agents/runtime/loop.py`'s tool-turn creation site -- is the row that
    carries `document:<pk>` into the tables on the ORIGINAL conversation.
    That is HALF ONE, asserted below.

    HALF TWO -- the actual propagation review finding 2 asks pinned
    rather than assumed -- is that `result.artifacts` (what a PARENT's
    own `agent.<slug>` tool turn would carry) is a reference
    `stamp_turn_taint` can turn into a tag on its OWN, independent of the
    delegate's inner stamp. Asserted on a FRESH conversation/stream that
    has never been touched, so the delegate's own real stamp (half one)
    cannot be what satisfies it -- only the propagation from `result.
    artifacts` into a synthetic parent tool turn can.
    """
    ent = make_entitlement()
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc = _labelled_document(ent)

    result = _run_delegate_that_cites(conversation, agent, doc)

    assert f"document:{doc.pk}" in " ".join(result.artifacts)
    # Half one: the delegate's own inner tool turn, stamped for real
    # inside `_run_delegate_that_cites`.
    assert conversation.taint_tags.filter(entitlement=ent).exists()
    assert stream.taint_tags.filter(entitlement=ent).exists()

    # Half two: a PARENT tool turn built from `result.artifacts`, on a
    # conversation/stream neither the delegate's own stamp nor anything
    # else above has ever touched -- so this can only pass if the
    # propagation itself works.
    other_stream = _workstream()
    other_conversation = Conversation.objects.create(agent=agent, workstream=other_stream)
    parent_turn = _tool_turn(other_conversation, list(result.artifacts))
    assert stamp_turn_taint(parent_turn, other_conversation,
                            parent_turn.artifacts) == frozenset({ent.pk})


def test_duplicating_a_conversation_copies_its_tags_row_for_row(agent):
    """RULING D's second half. A copy that dropped the tags would launder
    a loose conversation exactly as it would a stream one — so the copy
    happens ALWAYS, not only for a stream conversation."""
    ent = make_entitlement()
    user = make_user()
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        source = create_conversation(user_principal(user), agent, workstream=stream)
        ConversationTaint.objects.create(conversation=source, entitlement=ent, first_turn=7)
        copy = duplicate_conversation(user_principal(user), source, title="Copy")
    tags = list(copy.taint_tags.values_list("entitlement_id", "first_turn"))
    assert tags == [(ent.pk, 7)]


def test_duplicating_a_loose_conversation_carries_its_tags_too(agent):
    ent = make_entitlement()
    user = make_user()
    with posture("enterprise"):
        source = create_conversation(user_principal(user), agent)
        ConversationTaint.objects.create(conversation=source, entitlement=ent)
        copy = duplicate_conversation(user_principal(user), source, title="Copy")
    assert copy.taint_tags.count() == 1


def test_an_administrator_duplicating_under_admin_sees_content_launders_nothing(agent):
    """`may_manage_conversation`'s first branch admits them, which is
    exactly the case ruling D's own finding names."""
    ent = make_entitlement()
    owner, admin = make_user(), make_admin()
    stream = _workstream(user_principal(owner))
    with posture("enterprise", admin_sees_content=True):
        source = create_conversation(user_principal(owner), agent, workstream=stream)
        ConversationTaint.objects.create(conversation=source, entitlement=ent)
        copy = duplicate_conversation(user_principal(admin), source, title="Copy")
    assert copy.workstream_id == stream.pk
    assert copy.taint_tags.count() == 1


def test_the_generalisation_needs_no_runtime_edit(agent, isolated_labels_registry):
    """§7.4 and §17.4's last item, and the whole test of whether the seam
    was drawn in the right place. When a generated image becomes a
    labelled thing, `tools/vision/apps.py` registers one
    `ArtifactLabels("output", ...)` and every turn that returned one
    starts tainting — with NO change to `agents/runtime/taint.py`, NO
    change to `_finish`, and NO migration."""
    ent = make_entitlement()
    register_artifact_labels(ArtifactLabels(
        "output", "agents.tests.test_taint._fake_output_resolver"))
    _FAKE_OUTPUT_LABELS[12] = ent.pk
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    turn = _tool_turn(conversation, ["output:12"])
    assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset({ent.pk})


_FAKE_OUTPUT_LABELS: dict[int, int] = {}


def _fake_output_resolver(pks):
    return frozenset(_FAKE_OUTPUT_LABELS[p] for p in pks if p in _FAKE_OUTPUT_LABELS)


def test_a_kind_with_no_registered_resolver_contributes_nothing_silently(agent):
    """Which is what makes owner decision 11's "generalises mechanically"
    true, and why §7.4 is a SCOPE STATEMENT rather than a code branch."""
    conversation = Conversation.objects.create(agent=agent, workstream=_workstream())
    turn = _tool_turn(conversation, ["input:5"])
    assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset()


def test_the_two_taint_levels_keep_their_own_audit_vocabulary(agent):
    """C-29. The two writers share a shape and must not share their audit
    rows: the workstream row carries a `conversation` field the
    conversation row does not, and the two actions are read separately."""
    ent = make_entitlement(name="E")
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc = _labelled_document(ent)
    turn = _tool_turn(conversation, [f"document:{doc.pk}"])
    stamp_turn_taint(turn, conversation, turn.artifacts)

    rows = audit.recent()
    conv_rows = [r for r in rows if r.action == actions.CONVERSATION_TAINTED]
    stream_rows = [r for r in rows if r.action == actions.WORKSTREAM_TAINTED]
    assert len(conv_rows) == 1
    assert len(stream_rows) == 1

    conv_row = conv_rows[0]
    assert conv_row.target_type == "conversation"
    assert conv_row.target_key == str(conversation.pk)
    assert conv_row.target_label == conversation.title
    assert conv_row.detail == {"entitlement": ent.pk, "turn": turn.pk}

    stream_row = stream_rows[0]
    assert stream_row.target_type == "workstream"
    assert stream_row.target_key == str(stream.pk)
    assert stream_row.target_label == stream.name
    assert stream_row.detail == {
        "entitlement": ent.pk, "conversation": str(conversation.pk), "turn": turn.pk}


def test_stamping_taint_outside_a_transaction_is_not_how_it_is_called():
    """C-29's real constraint, made visible. `loop.py` opens ONE atomic
    block around the tool `Turn` row and this stamp, because the tags and
    the turn that caused them must land together or not at all. This test
    documents that the helper does not open its own -- if it ever does,
    two nested transactions with different failure modes appear where one
    was intended.

    A plain `"transaction.atomic" not in source` substring check is not
    what is asserted here: the module's OWN docstring already names
    `transaction.atomic()` in prose, describing the block `loop.py`
    opens around this stamp, so that substring is legitimately present
    without a transaction ever being opened in this file. The real
    constraint is that `taint.py` never IMPORTS `transaction` at all --
    without the import, `with transaction.atomic():` cannot appear as
    code here, prose notwithstanding."""
    from pathlib import Path

    from django.conf import settings

    source = (Path(settings.BASE_DIR) / "agents/runtime/taint.py").read_text()
    assert "import transaction" not in source
    assert "with transaction.atomic" not in source


def _labelled_document(entitlement):
    from tools.rag.tests._helpers import make_document

    doc = make_document()
    doc.entitlement_labels.create(entitlement=entitlement)
    return doc


def _tool_turn(conversation, artifacts):
    return Turn.objects.create(
        conversation=conversation, index=Turn.next_index(conversation),
        role=Turn.Role.TOOL, text="", artifacts=list(artifacts),
        state=Turn.State.DONE, depth=0)


# --- the four end-to-end drivers, so the turn really runs -------------
#
# These four tests are the only ones in this module that drive a REAL
# turn rather than calling `stamp_turn_taint` directly, because the
# property each asserts is about WHEN the stamp happens relative to the
# turn's own commits -- which is exactly what a direct call cannot show.

_RAG_ASK_KEY = "rag.ask"


def _register_rag_ask() -> None:
    """A stand-in `rag.ask` tool, registered under `isolated_tool_registry`
    so it never leaks between tests. `_stand_in_rag_ask_runner` reads
    `_ARTIFACTS_FOR`, keyed by tool key -- set by `_selection` below --
    which is what lets the four drivers below cite a real, labelled
    document through a real `invoke_tool` call rather than faking the
    tool turn's own `artifacts` column directly."""
    register_tool(ToolSpec(
        key=_RAG_ASK_KEY, label="Ask", description="A stand-in retrieval tool.",
        params=(Param("question", "text", "Question"),), roles=(),
        runner="agents.tests.test_taint._stand_in_rag_ask_runner", mutates=False))


def _stand_in_rag_ask_runner(args: dict, ctx) -> ToolResult:
    return ToolResult(text="cited", artifacts=_ARTIFACTS_FOR.get(ctx.tool_key, ()))


def _drive_turn(conversation, principal=OPEN_PRINCIPAL):
    """Run one turn through `agents.runtime.loop._run_turn` with the
    payload `agents.runtime.jobs` would build. Re-raises whatever the
    engine raised, so a test can assert both the exception and the rows
    the turn left behind.

    `_run_turn(payload, models, ctx)` RESOLVES ITS OWN ROWS -- its first
    act is `Turn.objects.select_related(...).get(pk=payload["turn"])`
    (`agents/runtime/loop.py:171-174`) -- so this helper creates the
    placeholder assistant turn the queue would have created and hands
    over nothing but the payload.

    Calls the PUBLIC `run_turn`, not `_run_turn` directly: `run_turn` is
    the wrapper that flips the assistant turn to FAILED with one
    `.update()` on any exception (and re-raises the original) -- the
    exact behaviour `test_a_turn_that_retrieves_and_then_FAILS_still_
    taints` and `test_a_turn_that_stops_between_two_tool_turns_taints_
    from_the_first` both assert on. Calling `_run_turn` alone would
    leave no FAILED row for either test to find.
    """
    _register_rag_ask()
    turn = Turn.objects.create(
        conversation=conversation, index=Turn.next_index(conversation),
        role=Turn.Role.ASSISTANT, text="", state=Turn.State.QUEUED, depth=0)
    return loop_module.run_turn(
        {"turn": turn.pk, **payload_fields(principal)}, [], make_job_ctx())


def _fake_llm(selections):
    """A stand-in for the bound chat model: it returns `selections` (a
    callable, or a list consumed one call at a time) instead of calling
    an engine. The same "mock at the HTTP boundary" convention every
    other runtime test in this app uses.

    `get_tool_calls_from_response` reads the `ToolSelection` list
    `_selection` already stashed in `additional_kwargs` -- the real
    engine's own version reads `ChatResponse.message.blocks` instead, but
    nothing here needs a second, competing shape of the same fact."""
    llm = MagicMock()
    llm.chat.side_effect = selections if callable(selections) else list(selections)
    llm.get_tool_calls_from_response.side_effect = (
        lambda response, error_on_no_tool_call=True: list(
            response.message.additional_kwargs.get("tool_calls", ())))
    return llm


def _tool_selection_citing(document):
    """One `rag.ask` tool call whose result carries `document`'s
    reference -- the exact shape `tools.rag.tools._document_artifacts`
    mints."""
    return _selection("rag.ask", {"question": "what?"},
                      artifacts=(f"document:{document.pk}",))


def _llm_calling(selection_or_callable):
    return _fake_llm(selection_or_callable)


def _llm_that_cites_then_raises(document):
    """First call: the tool selection above. Second call: an engine
    error. The tool turn commits; the assistant turn never arrives."""
    return _fake_llm([_tool_selection_citing(document),
                      RuntimeError("the engine went away")])


def _run_delegate_that_cites(conversation, agent, document):
    """Run one `agent.<slug>` delegation whose inner turn cites
    `document`, and return the delegate's own `ToolResult` -- the value
    `agents/runtime/delegate.py::run_agent_tool` hands back to the
    parent."""
    from agents.runtime import delegate

    _register_rag_ask()
    ctx = make_tool_ctx(conversation_id=str(conversation.pk), principal=OPEN_PRINCIPAL)
    with patch("agents.runtime.loop.gateway.get_llm_for",
               return_value=_llm_that_cites_then_answers(document)):
        return delegate.run_agent_tool({"task": "look it up"},
                                       replace(ctx, tool_key=f"agent.{agent.slug}"))


def _llm_that_cites_then_answers(document):
    return _fake_llm([_tool_selection_citing(document), _plain_answer("done")])


def _selection(tool_key, kwargs, *, artifacts=()):
    """One `ChatResponse` carrying a single tool call, plus the artifacts
    the runner behind it will report.

    A LOCAL BUILDER, not a shared fixture: nothing in
    `agents/runtime/tests/_helpers.py` mints one today, and the repo's
    convention (`tools/rag/tests/test_jobs.py:41-45`) is a standalone
    copy per test module rather than a new shared surface for one caller.
    """
    from llama_index.core.base.llms.types import ChatMessage, ChatResponse, MessageRole
    from llama_index.core.tools import ToolSelection

    response = ChatResponse(message=ChatMessage(role=MessageRole.ASSISTANT, content=""))
    response.message.additional_kwargs["tool_calls"] = [
        ToolSelection(tool_id=tool_key, tool_name=wire_name(tool_key), tool_kwargs=kwargs)
    ]
    _ARTIFACTS_FOR[tool_key] = tuple(artifacts)
    return response


def _plain_answer(text):
    from llama_index.core.base.llms.types import ChatMessage, ChatResponse, MessageRole

    return ChatResponse(message=ChatMessage(role=MessageRole.ASSISTANT, content=text))


# What the fake runner reports back, keyed by tool key -- set by
# `_selection` and read by the registered stand-in runner
# `_register_rag_ask` installs.
_ARTIFACTS_FOR: dict[str, tuple[str, ...]] = {}
