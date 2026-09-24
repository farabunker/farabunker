"""Shared test helpers for `agents/runtime/tests`.

Plain importable module -- not a `conftest.py`. Per-package, per the
repo convention; nothing here is imported by another app's tests.

`FakeToolLLM` is a SEAM double, not a method mock. It is patched in at
`models.contracts.gateway.get_llm_for` -- where `docs/DEV.md:181-186`
says every test already mocks the gateway -- and it implements the two
methods the loop actually calls, returning the same TYPES the installed
integration returns (`ChatResponse` with `ToolCallBlock`s;
`ToolSelection`). It deliberately does NOT implement `chat_with_tools`
or `predict_and_call`: this design does not use them, and a double that
offered them would let a loop start using them without a test noticing.
"""
from __future__ import annotations

import contextlib
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone
from llama_index.core.base.llms.types import ChatResponse, TextBlock, ToolCallBlock
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.llms.llm import ToolSelection

from agents.contracts.tools import ToolRefused, ToolResult
from agents.models import Turn
from agents.reconcile import STRANDED_TURN_GRACE_SECONDS
# Captured at import time -- before any test can have monkeypatched it -- so
# `patch_llm` can tell "still the real function" from "a test already
# overrode this for the capability-gate itself" purely by identity.
from agents.runtime.loop import supports_tool_calling as _REAL_SUPPORTS_TOOL_CALLING
from models.contracts.operations import ParamError


class FakeToolLLM:
    """Scripted with an ordered list of turns, each either
    `("tool", "<wire or dotted name>", {args})` or `("final", "text")`.

    Records every `(messages, tools)` pair it was called with in
    `.calls`, so a test can assert what the model was actually offered
    -- which is the only way to prove `granted_tools` filtering reached
    the prompt.

    A script entry may also be a LIST of `("tool", ...)` triples, which
    produces ONE response carrying several `ToolCallBlock`s -- the case
    that proves this platform's take-`calls[0]` policy, since plain
    `Ollama.chat` does not cap them (base.py:400-445).
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[list, object]] = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append((list(messages), tools))
        if not self.script:
            raise AssertionError("FakeToolLLM ran out of script entries")
        entry = self.script.pop(0)
        if isinstance(entry, list):
            blocks = [
                ToolCallBlock(tool_name=self._wire(name), tool_kwargs=args)
                for _, name, args in entry
            ]
            return self._response(blocks)
        kind = entry[0]
        if kind == "tool":
            _, name, args = entry
            return self._response([
                ToolCallBlock(tool_name=self._wire(name), tool_kwargs=args),
            ])
        _, text = entry
        return self._response([TextBlock(text=text)])

    def get_tool_calls_from_response(self, response, error_on_no_tool_call=True):
        """Mirrors `llama_index.llms.ollama.base.Ollama.
        get_tool_calls_from_response` (base.py:365-397), INCLUDING its
        one surprising behaviour: `tool_id` is set to the tool NAME,
        because Ollama supplies no ids. A double that invented real ids
        would hide the exact bug this platform must not have."""
        blocks = [b for b in response.message.blocks if isinstance(b, ToolCallBlock)]
        if not blocks:
            if error_on_no_tool_call:
                raise ValueError("Expected at least one tool call, but got 0 tool calls.")
            return []
        return [
            ToolSelection(tool_id=b.tool_name, tool_name=b.tool_name, tool_kwargs=b.tool_kwargs)
            for b in blocks
        ]

    @staticmethod
    def _wire(name: str) -> str:
        from agents.contracts.toolschema import wire_name

        return wire_name(name)

    @staticmethod
    def _response(blocks) -> ChatResponse:
        return ChatResponse(message=ChatMessage(role=MessageRole.ASSISTANT, blocks=blocks))


@contextlib.contextmanager
def patch_llm(llm):
    """Patch exactly two things for the duration of a loop test, and
    nothing else.

    1. `models.contracts.gateway.get_llm_for` -> `llm`. This is the seam
       `docs/DEV.md`'s offline-suite paragraph names, and patching it
       leaves the loop's own resolution, filtering, message building,
       and schema rendering really running. `loop.py` (and, once it
       exists, `delegate.py`) call `gateway.get_llm_for(...)` through the
       MODULE rather than a hoisted `from`-import, which is what lets the
       patch on `models.contracts.gateway` actually be seen.

    2. `agents.runtime.loop.supports_tool_calling` -> `None`. Load
       bearing, not convenience: `bind_chat_role` gives these tests a
       REAL `ModelConnection`/`RoleBinding`, so `resolve()` returns a
       real `ResolvedModel`, and the unpatched function would reach
       `get_engine(...).supports_tool_calling(...)` -- a live HTTP call
       to Ollama's `/api/show`. `docs/DEV.md` promises the suite needs no
       Ollama running, so leaving this unpatched would break that promise
       on every loop test. `None` is also the honest default: it is what
       an engine that does not report the fact returns, and it is the
       state a test that is not about the capability gate should be in.
       The two tests that ARE about the gate override it themselves, via
       `monkeypatch.setattr` called BEFORE entering this context manager
       -- so this only installs the `None` default when nothing has
       already overridden `supports_tool_calling`; a blanket override
       here would silently clobber a monkeypatch that ran first.

    Deliberately does NOT patch `models.contracts.bindings.resolve`: it
    is code under test (the loop, the planner, and every delegate call it
    for real), and a test that patched it would be exercising a
    different function than the one that ships.
    """
    from agents.runtime import loop as loop_module

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("models.contracts.gateway.get_llm_for", return_value=llm))
        if loop_module.supports_tool_calling is _REAL_SUPPORTS_TOOL_CALLING:
            stack.enter_context(
                patch("agents.runtime.loop.supports_tool_calling", return_value=None)
            )
        yield


# Same APP, not another one -- `agents/tests/_helpers.py` is this app's
# helper module and `agents/runtime/` is a package inside it. The
# per-app duplication rule exists to stop one app's test scaffolding
# becoming load-bearing for another's; a sixth copy of `make_job_ctx` (or
# of `make_tool_ctx`, folded into `agents/tests/_helpers.py` per Task 4's
# deferred ruling) INSIDE the same app would be duplication with no
# boundary to justify it. Re-exported here so a test module in this
# package can import everything it needs from one place.
from agents.tests._helpers import (  # noqa: F401
    CALLS, bind_chat_role, bound_chat_role, bound_embed_role, isolated_tool_registry,
    make_agent, make_budget, make_conversation, make_entitlement, make_flow, make_job_ctx,
    make_principal, make_tool_ctx, make_turn, make_user, posture, user_principal,
)


def _assistant_turn(*, role=Turn.Role.ASSISTANT, state, queue_job_id, age_seconds: int):
    """One turn (ASSISTANT by default) in `state`, pointing at
    `queue_job_id` (which may name no row at all), whose row has been in
    that state for `age_seconds` -- the facts the stranded condition
    reads. `role` is overridable so a test can build the one shape the
    stranded sweep must never touch: a USER turn."""
    turn = make_turn(
        conversation=make_conversation(agent=make_agent()),
        role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
    )
    Turn.objects.filter(pk=turn.pk).update(
        role=role, state=state, queue_job_id=queue_job_id,
        created_at=timezone.now() - timedelta(seconds=age_seconds),
    )
    turn.refresh_from_db()
    return turn


def _stranded_assistant_turn():
    """The shape the poll path reconciles: past the grace, pointing at a
    job row that does not exist."""
    return _assistant_turn(
        state=Turn.State.RUNNING, queue_job_id=4242,
        age_seconds=STRANDED_TURN_GRACE_SECONDS + 10,
    )


# The six stub runners `agents/runtime/tests/test_invoke.py` names by
# dotted path. Module-level (never a closure) because `invoke_tool`
# resolves a runner via `resolve_dotted_path`, which needs a real
# importable name -- the same reason `agents/tests/_helpers.py`'s
# `stub_runner` is module-level.
class _RefusedSubclass(ToolRefused):
    """A `ToolRefused` SUBCLASS, planted so a test proves the except
    ordering catches it by class hierarchy, not by exact type."""


class _ParamErrorSubclass(ParamError):
    """A `ParamError` SUBCLASS, planted for the same reason."""


def runner_ok(args: dict, ctx) -> ToolResult:
    CALLS.append((args, ctx))
    return ToolResult(text="it worked", data={"args": args})


def runner_artifacts(args: dict, ctx) -> ToolResult:
    """A runner that produces FILES as well as words -- the shape every
    image and document tool has. Its references are what the loop stores
    on the tool `Turn` and what `tool_turn_messages` must speak, so the
    in-turn half of artifact visibility has something real to assert on.
    """
    CALLS.append((args, ctx))
    return ToolResult(text="it worked", data={"args": args},
                      artifacts=("output:36", "output:37"))


def runner_degraded(args: dict, ctx) -> ToolResult:
    CALLS.append((args, ctx))
    return ToolResult(text="the queue is down; this ran degraded", data={"degraded": True})


def runner_refused_subclass(args: dict, ctx) -> ToolResult:
    CALLS.append((args, ctx))
    raise _RefusedSubclass("this tool is not granted")


def runner_param_error_subclass(args: dict, ctx) -> ToolResult:
    CALLS.append((args, ctx))
    raise _ParamErrorSubclass({"top_k": "50 is too large"})


def runner_value_error(args: dict, ctx) -> ToolResult:
    CALLS.append((args, ctx))
    raise ValueError("no row matches that reference")


def runner_runtime_error(args: dict, ctx) -> ToolResult:
    CALLS.append((args, ctx))
    raise RuntimeError("the engine connection reset")


def runner_os_error(args: dict, ctx) -> ToolResult:
    """C-6 (security round 3, H38): an unclassified exception whose
    message names an absolute path -- exactly the shape
    `test_invoke_unclassified.py` proves never reaches `outcome.text`,
    the turn, or the tool card, only `ToolInvocation.error` and the log.
    """
    CALLS.append((args, ctx))
    raise OSError("/srv/data/secret.env: no such file or directory")


# The three below are for `agents/runtime/tests/test_flow.py` only --
# stub STEP tools a flow's `steps` JSON can name, distinct from the six
# above (which stand in for a single `ToolSpec`'s own runner in
# `test_invoke.py`/`test_delegate.py`).


def runner_flow_step_one(args: dict, ctx) -> ToolResult:
    """The FIRST of two fixed, distinguishable step results a flow test
    chains together -- fixed text/data/artifacts, not echoed from
    `args`, so "the returned text is the LAST step's" and "artifacts are
    the union of every step's" have something real to tell apart."""
    CALLS.append((args, ctx))
    return ToolResult(
        text="step-one-text", data={"citations": [{"title": "one"}], "echo": args},
        artifacts=("document:1", "output:2"),
    )


def runner_flow_step_two(args: dict, ctx) -> ToolResult:
    """The SECOND of the pair -- see `runner_flow_step_one`."""
    CALLS.append((args, ctx))
    return ToolResult(
        text="step-two-text", data={"citations": [{"title": "two"}], "echo": args},
        artifacts=("output:2", "document:3"),
    )


def runner_expire_deadline(args: dict, ctx) -> ToolResult:
    """Does real step work, THEN pushes the shared budget's deadline
    into the past -- proving `agents.runtime.flow._run_steps` checks
    `ctx.budget.expired` before EACH step, not once before the whole
    flow. `ctx.budget` is the SAME `StepBudget` the flow runner and its
    caller share (mutable by design, `agents/contracts/tools.py`), so
    mutating it here is exactly what a slow real tool running past a
    turn's deadline would look like from the next step's point of view.
    """
    import time

    CALLS.append((args, ctx))
    ctx.budget.deadline_monotonic = time.monotonic() - 1
    return ToolResult(text="step0 finished just before the deadline moved",
                       data={"echo": args})
