"""Shared test helpers for the `agents` app's own tests.

Plain importable module -- **not** a `conftest.py` (the repo forbids
them anywhere). Each test module imports what it needs explicitly, by
name -- pytest discovers a fixture present in a test module's namespace,
imported or defined locally.

`isolated_tool_registry` below is duplicated per APP, never imported
across apps: the same fixture, of the same shape, also lives in
`tools/rag/tests/_helpers.py`, `tools/vision/tests/_helpers.py`,
`models/registry/tests/_helpers.py`, and `agents/contracts/tests/
_helpers.py`. That duplication is deliberate and was ruled on in this
phase's Global Constraints -- consolidating ACROSS apps would make one
app's test scaffolding load-bearing for another's. `agents/runtime/
tests/_helpers.py` is the same APP and imports from here rather than
making a sixth copy.

`REPO_ROOT` and `_is_test_file` below are the SAME rule applied the
other direction: `agents/tests/test_defaults.py` used to declare a
byte-identical copy of both rather than import them from here, even
though it already imports `isolated_tool_registry` from this very
module. The cross-app duplication rule above does not apply within one
column -- these two modules are siblings in `agents/tests/`, not two
apps -- so the copy was simply unconsolidated, not deliberately
independent (the same shape `foundation/ops/tests/_helpers.py`
documents for its own two siblings).
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest        # Tasks 6 and 12 add `@pytest.fixture`s to this module.
from django.conf import settings

from identity.testing import (  # noqa: F401 -- re-exported for this package's tests
    grant, make_admin, make_entitlement, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, sign_in, user_principal,
)
# The queue's REAL job-state vocabulary, via the sanctioned seam (import-law
# rule 2 -- `agents/` may not import `models.queue.*` directly). Task 12's
# fixtures once spelled a state literal ("done") themselves, which the real
# vocabulary has never had; importing these rather than hardcoding is what
# keeps a fixture from being able to quietly agree with a typo again.
from models.contracts.queue import FAILED, RUNNING, SUCCEEDED

REPO_ROOT = Path(settings.BASE_DIR)

CALLS: list = []
_names = (f"{n}" for n in itertools.count(1))


def _is_test_file(relative: str) -> bool:
    """Test modules are exempt from the gates that walk `agents/`'s own
    source tree -- anything under a `tests/` directory, any
    `test_*.py`/`*_test.py` module, or a `conftest.py` (none exist in
    this repo, but the check costs nothing)."""
    parts = Path(relative).parts
    if "tests" in parts:
        return True
    name = Path(relative).name
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def make_agent(**overrides):
    """An `Agent` row. `slug` defaults to a fixed test value; pass one
    when a test needs two agents."""
    from agents.models import Agent

    fields = dict(
        slug="test-agent",
        name="Test agent",
        description="",
        system_prompt="You are a test agent.",
        tool_keys=[],
        resident=False,
        enabled=True,
    )
    fields.update(overrides)
    return Agent.objects.create(**fields)


def make_flow(**overrides):
    """A `Flow` row. `slug` defaults to a fixed test value; pass one
    when a test needs two. `steps` defaults to one step naming a tool
    nothing here registers by default -- pass a real registered tool key
    when a test needs the step to actually resolve a role."""
    from agents.models import Flow

    fields = dict(
        slug="test-flow",
        name="Test flow",
        description="",
        inputs=[],
        steps=[{"tool": "stub.safe", "args": {}}],
        resident=False,
        enabled=True,
    )
    fields.update(overrides)
    return Flow.objects.create(**fields)


def make_conversation(**overrides):
    """A `Conversation` row. `owner_kind`/`owner_key` default to blank
    (unowned) -- pass either to exercise the owner-principal filter."""
    from agents.models import Conversation

    fields = dict(agent=overrides.pop("agent", None) or make_agent())
    fields.update(overrides)
    return Conversation.objects.create(**fields)


def make_turn(**overrides):
    from agents.models import Turn

    conversation = overrides.pop("conversation", None) or make_conversation()
    fields = dict(
        conversation=conversation,
        index=overrides.pop("index", Turn.next_index(conversation)),
        role=Turn.Role.USER,
        text="",
    )
    fields.update(overrides)
    return Turn.objects.create(**fields)


_editable_thread_slugs = itertools.count()


def make_editable_thread(owner, *, texts=("first", "second"), slug=None):
    """A conversation owned by `owner` whose every turn is a finished,
    root-depth USER turn -- so each is individually editable
    (`agents.visibility.is_editable_turn_row`) and the conversation as a
    whole is (`may_edit_any_turn`).

    `slug` AUTO-GENERATES WHEN NONE IS GIVEN, the stricter of this
    helper's two prior spellings: a caller that needs two threads in one
    test still passes its own two slugs, and a caller that needs only
    one never collides with another test's row by sharing a fixed
    default.
    """
    from agents.models import Turn
    from identity.access import owner_fields

    conversation = make_conversation(
        agent=make_agent(slug=slug or f"editable-thread-{next(_editable_thread_slugs)}"),
        **owner_fields(user_principal(owner)))
    turns = [make_turn(conversation=conversation, role=Turn.Role.USER, text=text,
                       state=Turn.State.DONE) for text in texts]
    return conversation, turns


def _workstream(principal=None, **overrides):
    """A `Workstream` row for a test that needs one to exist.

    The `agents`-side half of spec §17.9's "no new fixtures beyond a
    `_workstream(...)` helper and its `tools/rag` twin". Written
    directly, not through `agents.visibility.create_workstream`, for the
    same reason `identity.testing.make_entitlement` writes its row
    directly: a test that merely needs a stream to EXIST should not have
    to satisfy the writer's refusals to get one.
    """
    from agents.models import Workstream
    from identity.access import owner_fields
    from identity.contracts.principals import OPEN_PRINCIPAL

    fields = {"name": f"stream-{next(_names)}"}
    fields.update(owner_fields(principal if principal is not None else OPEN_PRINCIPAL))
    fields.update(overrides)
    return Workstream.objects.create(**fields)


def stub_runner(args: dict, ctx):
    """A module-level runner a `ToolSpec` can name by dotted path.
    Records its calls in `CALLS` so a test can assert what reached it."""
    from agents.contracts.tools import ToolResult

    CALLS.append((args, ctx))
    return ToolResult(text="stub ran", data={"args": args})


def make_job_ctx(**overrides):
    """A `models.contracts.jobkinds.JobContext` with inert writers -- no
    real worker and no DB behind it."""
    from models.contracts.jobkinds import JobContext

    fields = dict(
        job_id=1,
        attempt=0,
        checkpoint_state=None,
        _report=lambda progress: None,
        _checkpoint=lambda state: None,
    )
    fields.update(overrides)
    return JobContext(**fields)


def make_budget(**overrides):
    """A `StepBudget` with room to spare and a deadline far enough out
    that a test never trips it by accident."""
    import time

    from agents.contracts.tools import StepBudget

    fields = dict(steps=8, deadline_monotonic=time.monotonic() + 900.0, recoveries=1)
    fields.update(overrides)
    return StepBudget(**fields)


def make_principal(**overrides):
    from agents.contracts.tools import Principal

    fields = dict(kind="resident_agent", key="test-agent")
    fields.update(overrides)
    return Principal(**fields)


def make_tool_ctx(**overrides):
    """A `ToolContext` for calling a runner or a loop directly. Built on
    the sibling helpers above rather than re-deriving them."""
    from agents.contracts.tools import ToolContext

    fields = dict(
        conversation_id="00000000-0000-0000-0000-000000000000",
        principal=make_principal(),
        depth=0,
        budget=make_budget(),
        job=make_job_ctx(),
        tool_key="",
    )
    fields.update(overrides)
    return ToolContext(**fields)


def bind_chat_role(role_key, *, name="test-chat", capability="chat",
                    model_id="chat-model", embed_dim=None):
    """A real `ModelConnection` bound to `role_key`, so
    `models.contracts.bindings.resolve` RESOLVES instead of raising.

    The loop and the planner both call `resolve()` for real (they are
    the code under test), and a delegate resolves its own agent's role
    too -- so a loop test that patched `resolve` would be testing a
    different function than the one that ships. A row is cheaper and
    more honest than two patches.

    A test importing `models.registry.models` directly is the exemption
    `foundation/ops/tests/test_import_law.py:19-30` already documents:
    this codebase has no shared factory layer, and the gate polices
    PRODUCTION imports only.
    """
    from models.registry.models import ModelConnection, RoleBinding

    # `capabilities` (plural) is a JSONField LIST drawn from
    # `models.contracts.roles.CAPABILITIES` -- `models/registry/models.py:63`.
    # There is no singular `capability` field; passing one is a silent
    # TypeError at construction. Precedent:
    # `models/registry/tests/_helpers.py:20-35`'s `make_chat_connection`.
    fields = dict(
        name=name, engine="ollama", endpoint="http://localhost:11434",
        model_id=model_id, capabilities=[capability],
    )
    if embed_dim is not None:
        fields["embed_dim"] = embed_dim
    connection = ModelConnection.objects.create(**fields)
    RoleBinding.objects.update_or_create(
        role_key=role_key, defaults={"connection": connection},
    )
    return connection


@pytest.fixture
def bound_chat_role(db):
    from models.contracts.roles import CHAT_CONVERSE_ROLE

    return bind_chat_role(CHAT_CONVERSE_ROLE)


@pytest.fixture
def bound_embed_role(db):
    from models.contracts.roles import RAG_EMBED_ROLE

    return bind_chat_role(
        RAG_EMBED_ROLE, name="test-embed", capability="embeddings", embed_dim=768,
    )


def _finished_job(state=SUCCEEDED, error=""):
    """A `models.queue.backend.JobStatus`-shaped stand-in
    (`backend.py:77-115`). A `SimpleNamespace` with the REAL field names,
    so a test breaks if that shape changes rather than quietly agreeing
    with a double nobody updated."""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=1, kind="agent.turn", state=state, position=None, priority=100,
        created_at=None, started_at=None, finished_at=None, models=[],
        result={}, error=error, progress=None, summary="a turn",
    )


def _write_assistant(payload, text="the answer"):
    """What `agents.runtime.loop.run_turn` would have written. The
    command reports from rows, so a double that skips this makes every
    output assertion pass against an empty turn."""
    from agents.models import Turn

    Turn.objects.filter(pk=payload["turn"]).update(
        state=Turn.State.DONE, text=text,
    )


def _write_tool_turn(payload, tool_key="rag.search", queue_job_id=1):
    """A TOOL turn plus its ToolInvocation, written BEFORE the assistant
    turn is moved to the end -- the same order the real loop uses.

    `queue_job_id` defaults to `1` to match `_patch_queue`'s hardcoded
    `_enqueue` return value, so this tool turn carries the SAME id the
    command stamps onto the placeholder right after `enqueue()` returns --
    exactly like the real loop's own tool turns do (`agents/runtime/
    loop.py`'s `Turn.objects.create(..., queue_job_id=job_ctx.job_id)`).
    The command's `_report` filters on that id, not `index__lt`, so a
    test proving that filtering (a continued conversation's second turn)
    passes a DIFFERENT id here.
    """
    from agents.models import ToolInvocation, Turn

    placeholder = Turn.objects.get(pk=payload["turn"])
    conversation = placeholder.conversation
    invocation = ToolInvocation.objects.create(
        principal_kind="resident_agent", principal_key=payload["agent"],
        tool_key=tool_key, args={"query": "q"},
        outcome=ToolInvocation.Outcome.OK, text="two results",
    )
    Turn.objects.create(
        conversation=conversation, index=Turn.next_index(conversation),
        role=Turn.Role.TOOL, text="two results",
        tool_call={"tool": tool_key, "args": {"query": "q"},
                   "agent": payload["agent"], "id": "", "discarded": []},
        artifacts=["document:7"], invocation=invocation, state=Turn.State.DONE,
        queue_job_id=queue_job_id,
    )
    placeholder.index = Turn.next_index(conversation)
    placeholder.save(update_fields=["index"])


def _patch_queue(monkeypatch, *, on_enqueue=None, state=SUCCEEDED, error=""):
    module = "agents.management.commands.agent_turn"

    def _enqueue(kind, payload, **kwargs):
        if on_enqueue is not None:
            on_enqueue(payload)
        return 1

    monkeypatch.setattr(f"{module}.enqueue", _enqueue)
    monkeypatch.setattr(f"{module}.get_job", lambda job_id: _finished_job(state, error))


@pytest.fixture
def fake_queue(monkeypatch):
    """A turn that runs and answers."""
    _patch_queue(monkeypatch, on_enqueue=_write_assistant)


@pytest.fixture
def fake_queue_with_tool(monkeypatch):
    """A turn that calls one tool, then answers."""
    def _run(payload):
        _write_tool_turn(payload)
        _write_assistant(payload)

    _patch_queue(monkeypatch, on_enqueue=_run)


@pytest.fixture
def fake_failed_queue(monkeypatch):
    """The handler ran and raised: it wrote its own Turn(FAILED) (spec
    section 6.2 step 8) and the job is failed."""
    def _fail(payload):
        from agents.models import Turn

        Turn.objects.filter(pk=payload["turn"]).update(
            state=Turn.State.FAILED, error="boom",
        )

    _patch_queue(monkeypatch, on_enqueue=_fail, state=FAILED, error="boom")


@pytest.fixture
def fake_running_queue(monkeypatch):
    """A job that never reaches a terminal state, so the command's
    timeout branch is what ends the wait. Nothing is written: the point
    is that the command reports honestly about a turn still in flight
    and does NOT cancel it."""
    _patch_queue(monkeypatch, state=RUNNING)


@pytest.fixture
def isolated_tool_registry():
    """Snapshot `agents.contracts.tools._TOOLS` on entry, restore it on
    exit, and clear `CALLS` so one test's recorded calls never leak into
    the next's.

    Same shape as `tools/vision/tests/_helpers.py:26-55`'s fixture of the
    same name (CQ-4): requestable by any test module in this app that
    imports it BY NAME, e.g. `from agents.tests._helpers import
    isolated_tool_registry  # noqa: F401` -- directly, or via
    `pytestmark = pytest.mark.usefixtures("isolated_tool_registry")` for
    a whole module. A module-global registry surviving between tests is
    exactly the state that makes a suite pass in one collection order and
    fail in the other, which is why the repo runs both orders.
    """
    from agents.contracts import tools as tools_module

    saved = dict(tools_module._TOOLS)
    yield
    tools_module._TOOLS.clear()
    tools_module._TOOLS.update(saved)
    CALLS.clear()


def make_document(**overrides):
    """A minimal `rag.Document` row, through `apps.get_model` -- the
    `agents` twin of `tools/rag/tests/_helpers.py::make_document`
    (round 11: `agents.attachments.attached_documents`, and the
    conversation page's own attachments strip, both need a REAL
    `Document` row to filter/render). Reached through `apps.get_model`
    so this module imports nothing of `tools.rag.models` at module
    scope -- the same "duplicated per column, never imported across"
    rule `_workstream`/`make_agent` already follow in this exact file,
    applied to the one column whose OWN test helpers this file has
    never needed to reach into before this round.
    """
    from django.apps import apps

    model = apps.get_model("rag", "Document")
    fields = dict(
        title=f"doc-{next(_names)}.txt",
        source_path="/tmp/does-not-matter.txt",
        file_hash="a" * 64,
        doc_type="prose",
    )
    fields.update(overrides)
    return model.objects.create(**fields)
