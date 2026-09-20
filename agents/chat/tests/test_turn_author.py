"""C-1 (High, H25): `agents.models.Turn.author` -- the column that
records who wrote a turn, at the TWO places a turn is actually written:
`agents.chat.service.start_turn`'s USER-turn row, and `agents.runtime.
loop.run_loop`'s TOOL-turn row.

`agents/runtime/tests/test_prompt_turn_author.py::
TestC1ForeignAuthoredTurnsAreFenced` is the REPLAY half (a
foreign-authored USER turn is fenced when it comes back into a later
prompt) -- this module is the WRITE half only: does the row end up with
the right `author_id` at all. Lives under `agents/chat/tests/` (H25
review round 1, minors), not `agents/tests/`: its own two USER-turn
tests exercise `agents.chat.service.start_turn`, this column's own
write door, the same reason `agents/chat/tests/test_turn_attachments.py`
lives here rather than in `agents/tests/`.
"""
from __future__ import annotations

import pytest

from agents.contracts.tools import ToolSpec, register_tool
from agents.models import Turn
from agents.runtime.loop import run_turn
from agents.runtime.tests._helpers import (  # noqa: F401
    FakeToolLLM, bind_chat_role, isolated_tool_registry, make_agent, make_conversation,
    make_job_ctx, make_turn, make_user, patch_llm, posture, user_principal,
)
from identity.contracts.postures import POSTURE_OPEN
from identity.contracts.principals import OPEN_PRINCIPAL, payload_fields
from identity.testing import reset_settings, seed_sweep_posture
from models.contracts.roles import CHAT_CONVERSE_ROLE

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]


@pytest.fixture(autouse=True)
def _settings():
    """Every test in this module runs on the shipped default posture
    (OPEN) unless it pins its own -- the same discipline `agents/tests/
    test_shares.py`'s own `_settings` fixture follows, so a sweep run
    that pins `FARABUNKER_TEST_POSTURE` elsewhere cannot leak into these
    assertions."""
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


def _patch_queue(monkeypatch, job_id: int = 1) -> None:
    """The minimum double `agents.chat.service.start_turn` needs to
    enqueue without a real queue backend -- `enqueue` succeeds, `get_job`
    (read only for the response's own position/priority, `_job_facts`'s
    own docstring: "a position we cannot read is a nicety") answers
    `None` rather than reaching a real table. The same seam `agents/
    chat/tests/_helpers.py::_patch_queue` patches, reproduced narrowly
    here rather than imported: that module is `agents/chat/tests`'
    OWN scaffolding (a different app under this same column), and this
    is the one call this file needs from it."""

    def _enqueue(kind, payload, **kwargs):
        return job_id

    monkeypatch.setattr("agents.chat.service.enqueue", _enqueue)
    monkeypatch.setattr("agents.chat.service.get_job", lambda jid: None)


def test_a_turn_records_the_principal_that_wrote_it(monkeypatch):
    from agents.chat.service import start_turn

    bind_chat_role(CHAT_CONVERSE_ROLE)
    _patch_queue(monkeypatch)
    member = make_user()
    conversation = make_conversation(agent=make_agent())

    start = start_turn(conversation, "hello", actor=user_principal(member))

    assert start.ok, start.error
    turn = conversation.turns.get(role=Turn.Role.USER)
    assert turn.author_id == member.pk


def test_an_open_posture_turn_has_no_author_and_that_is_not_an_error(monkeypatch):
    from agents.chat.service import start_turn

    bind_chat_role(CHAT_CONVERSE_ROLE)
    _patch_queue(monkeypatch)
    conversation = make_conversation(agent=make_agent())

    with posture(POSTURE_OPEN):
        start = start_turn(conversation, "hello", actor=OPEN_PRINCIPAL)

    assert start.ok, start.error
    turn = conversation.turns.get(role=Turn.Role.USER)
    assert turn.author_id is None


def test_a_tool_turn_records_the_principal_the_loop_ran_as():
    """The loop runs a turn as the ACTING principal named in the
    payload (the acting rule, `agents.runtime.loop._run_turn`'s own
    comment), never as the agent -- and the TOOL turn `run_loop` writes
    for a real tool call is stamped with that same principal, an audit
    fact distinct from a USER turn's own "who wrote this"."""
    register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                           runner="agents.runtime.tests._helpers.runner_ok"))
    bind_chat_role(CHAT_CONVERSE_ROLE)
    member = make_user()
    agent = make_agent(slug="general", tool_keys=["stub.safe"])
    conv = make_conversation(agent=agent)
    make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="a question",
             author_id=member.pk)
    assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                          state=Turn.State.QUEUED)
    payload = {
        "conversation": str(conv.id), "turn": assistant.pk, "agent": agent.slug,
        "text": "a question", "connection": None, "mode": "chat",
        **payload_fields(user_principal(member)),
    }
    llm = FakeToolLLM([("tool", "stub.safe", {}), ("final", "done")])

    with patch_llm(llm):
        run_turn(payload, [], make_job_ctx())

    tool_turn = conv.turns.get(role=Turn.Role.TOOL)
    assert tool_turn.author_id == member.pk


def test_a_tool_turn_whose_payload_names_a_stale_user_id_still_writes_with_no_author():
    """`principal_from_payload`'s own contract is "NEVER RAISES" -- a
    payload's `actor_key` can outlive the account it named (deleted
    after enqueue) or be hand-edited
    (`agents/runtime/tests/test_acting_rule.py`'s own junk-`actor_kind`
    fixtures use the identical shape). Before `_tool_turn_author_id`
    (`agents/runtime/loop.py`) this turned into a raised
    `IntegrityError` on the tool turn's own `author` FK, inside the SAME
    atomic block that also stamps taint -- turning an unrelated stale id
    into a failed job. It must not: the tool turn is written, with
    `author_id=None`, the same honest "not attributable" `Turn.author`'s
    own docstring already means for any other untraceable case."""
    register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                           runner="agents.runtime.tests._helpers.runner_ok"))
    bind_chat_role(CHAT_CONVERSE_ROLE)
    agent = make_agent(slug="general", tool_keys=["stub.safe"])
    conv = make_conversation(agent=agent)
    make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="a question")
    assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                          state=Turn.State.QUEUED)
    payload = {
        "conversation": str(conv.id), "turn": assistant.pk, "agent": agent.slug,
        "text": "a question", "connection": None, "mode": "chat",
        "actor_kind": "user", "actor_key": "999999",   # no such account
    }
    llm = FakeToolLLM([("tool", "stub.safe", {}), ("final", "done")])

    with patch_llm(llm):
        run_turn(payload, [], make_job_ctx())          # must not raise

    tool_turn = conv.turns.get(role=Turn.Role.TOOL)
    assert tool_turn.author_id is None


def test_a_tool_turn_whose_payload_names_a_non_numeric_actor_key_still_writes_with_no_author():
    """H25 review round 1, IMPORTANT 1: `_tool_turn_author_id`
    (`agents/runtime/loop.py`) now guards PARSEABILITY before it ever
    queries -- the identical `not isinstance(key, str) or not key.
    isdecimal()` idiom `identity.access._user_pk` already uses (`identity/
    access.py`). Before that guard, a non-decimal `actor_key` reached
    `get_user_model().objects.filter(pk=key)`, which raises `ValueError`
    on a non-numeric lookup value against an integer primary key --
    turning a hand-edited or malformed payload into a failed job exactly
    the way the stale-numeric-id case above did before its own fix."""
    register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                           runner="agents.runtime.tests._helpers.runner_ok"))
    bind_chat_role(CHAT_CONVERSE_ROLE)
    agent = make_agent(slug="general", tool_keys=["stub.safe"])
    conv = make_conversation(agent=agent)
    make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="a question")
    assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                          state=Turn.State.QUEUED)
    payload = {
        "conversation": str(conv.id), "turn": assistant.pk, "agent": agent.slug,
        "text": "a question", "connection": None, "mode": "chat",
        "actor_kind": "user", "actor_key": "not-a-number",
    }
    llm = FakeToolLLM([("tool", "stub.safe", {}), ("final", "done")])

    with patch_llm(llm):
        run_turn(payload, [], make_job_ctx())          # must not raise

    tool_turn = conv.turns.get(role=Turn.Role.TOOL)
    assert tool_turn.author_id is None
