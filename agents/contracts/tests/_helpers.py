"""Shared test helpers for agents/contracts/tests.

Plain importable module -- **not** a `conftest.py` (the repo forbids
`conftest.py` files anywhere). Each test module imports what it needs from
here explicitly, by name -- pytest discovers a fixture present in a test
module's namespace, imported or defined locally (see `models/registry/
tests/_helpers.py`, `tools/rag/tests/_helpers.py`, `tools/vision/tests/
_helpers.py` -- all three follow the same shape).

`make_job_ctx` is duplicated here rather than imported from another app's
`_helpers`, exactly as it is duplicated between `tools/rag/tests/
_helpers.py:77` and `tools/vision/tests/_helpers.py:89`. Helpers are
per-app by convention; a cross-app import would make one package's test
scaffolding load-bearing for another's.
"""
from __future__ import annotations

import time

import pytest

from agents.contracts.tools import Principal, StepBudget, ToolContext, ToolResult, ToolSpec
from models.contracts.jobkinds import JobContext
from models.contracts.operations import Param


def make_job_ctx(**overrides) -> JobContext:
    """A `models.contracts.jobkinds.JobContext` for calling a runner
    directly -- no real worker or DB behind it, just inert
    `_report`/`_checkpoint` writers."""
    fields = dict(
        job_id=1,
        attempt=0,
        checkpoint_state=None,
        _report=lambda progress: None,
        _checkpoint=lambda state: None,
    )
    fields.update(overrides)
    return JobContext(**fields)


def make_budget(**overrides) -> StepBudget:
    """A `StepBudget` with plenty of room and a deadline far enough out
    that a test never trips it by accident."""
    fields = dict(steps=8, deadline_monotonic=time.monotonic() + 900.0, recoveries=1)
    fields.update(overrides)
    return StepBudget(**fields)


def make_principal(**overrides) -> Principal:
    fields = dict(kind="resident_agent", key="test-agent")
    fields.update(overrides)
    return Principal(**fields)


def make_tool_ctx(**overrides) -> ToolContext:
    """A `ToolContext` for calling a runner directly."""
    fields = dict(
        conversation_id="00000000-0000-0000-0000-000000000000",
        principal=make_principal(),
        depth=0,
        budget=make_budget(),
        job=make_job_ctx(),
    )
    fields.update(overrides)
    return ToolContext(**fields)


def make_spec(**overrides) -> ToolSpec:
    """A minimal valid `ToolSpec`. Pass keyword overrides for a test whose
    point IS a specific field."""
    fields = dict(
        key="stub.tool",
        label="Stub tool",
        description="A tool that exists so a test has one.",
        params=(Param("text", "text", "Text"),),
        roles=(),
        runner="agents.contracts.tests._helpers.stub_runner",
        mutates=False,
    )
    fields.update(overrides)
    return ToolSpec(**fields)


# Module-level so `make_spec`'s default `runner` is a dotted path that
# really resolves -- the structural guard in Task 10 walks every
# registered runner and would otherwise have to special-case test specs.
CALLS: list[tuple[dict, object]] = []


def stub_runner(args: dict, ctx) -> ToolResult:
    """Records its call and returns a fixed result."""
    CALLS.append((args, ctx))
    return ToolResult(text="stub ran", data={"args": args})


@pytest.fixture
def isolated_tool_registry():
    """Snapshot `agents.contracts.tools._TOOLS` on entry, restore it on
    exit, and clear `CALLS` (`stub_runner`'s own call log) so one test's
    calls never leak into the next's.

    Same shape as `tools/vision/tests/_helpers.py:26-55`'s fixture of the
    same name (CQ-4): a real `@pytest.fixture`, defined here rather than
    in a `conftest.py` (the repo forbids those -- module docstring
    above), requestable by any test module that imports it BY NAME,
    e.g. `from agents.contracts.tests._helpers import
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


@pytest.fixture
def isolated_panel_registry():
    """Save, clear and restore the workstream-panel registry, so a test
    that registers a panel does not leak it into the next one. The same
    shape `isolated_tool_registry` in this file already has, and for the
    same reason."""
    from agents.contracts import workstreams as ws

    saved = dict(ws._PANELS)
    ws._PANELS.clear()
    try:
        yield
    finally:
        ws._PANELS.clear()
        ws._PANELS.update(saved)


@pytest.fixture
def isolated_labels_registry():
    """Save, clear and restore the `ArtifactLabels` registry, so a test
    that registers one (a fake resolver, say) does not leak it into the
    next test. The same shape `isolated_panel_registry` above already
    has, and for the same reason."""
    from agents.contracts import artifacts

    saved = dict(artifacts._ARTIFACT_LABELS)
    artifacts._ARTIFACT_LABELS.clear()
    try:
        yield
    finally:
        artifacts._ARTIFACT_LABELS.clear()
        artifacts._ARTIFACT_LABELS.update(saved)


@pytest.fixture
def isolated_file_resolver_registry():
    """Save, clear and restore the artifact FILE-RESOLVER registry, so a
    test that registers one (a fake resolver, say) does not leak it into
    the next. The same shape `isolated_labels_registry` above already
    has, and for the same reason."""
    from agents.contracts import artifacts

    saved = dict(artifacts._ARTIFACT_FILE_RESOLVERS)
    artifacts._ARTIFACT_FILE_RESOLVERS.clear()
    try:
        yield
    finally:
        artifacts._ARTIFACT_FILE_RESOLVERS.clear()
        artifacts._ARTIFACT_FILE_RESOLVERS.update(saved)


# THE SIX SINGLETON SLOTS `agents.contracts.attachments` registers into
# (`_PROVIDER`, round 11; `_CLEANUP`, the conversation-delete cascade;
# `_UPLOADER`/`_DETACHER`/`_TEXT_PROVIDER`, round 13; `_ORPHAN_CLEANUP`,
# round-13 REVIEW FIX I-2). Written ONCE, here, and iterated by the
# fixture below -- see its docstring for why that matters.
_ATTACHMENT_SLOTS = (
    "_PROVIDER", "_CLEANUP", "_UPLOADER",
    "_DETACHER", "_TEXT_PROVIDER", "_ORPHAN_CLEANUP",
)


@pytest.fixture
def isolated_attachment_registry():
    """Save, clear and restore `agents.contracts.attachments`'s own SIX
    singleton slots -- `_PROVIDER` (round 11, read), `_CLEANUP` (round
    11 re-review minor 4, conversation-delete cascade), `_UPLOADER`/
    `_DETACHER`/`_TEXT_PROVIDER` (round 13, message-bound attachments),
    `_ORPHAN_CLEANUP` (round-13 REVIEW FIX I-2) -- so a test that
    registers ANY of them does not leak it into the next.

    ROUND-13 REVIEW FIX, I-4: this fixture's own docstring used to say
    "TWO SLOTS" and this body used to save/clear/restore only `_PROVIDER`/
    `_CLEANUP` -- silently failing to isolate the other four, which is
    itself what the review flagged (and, separately, what actually
    caused a real cross-test leak this fix's own test suite hit while
    writing I-2: `agents/chat/tests/test_delete.py`'s own fake `_CLEANUP`
    registration was properly isolated, but nothing here was watching
    the OTHER five, and a later, unrelated test's own attachment-path
    exercise could have silently run with a stale value on any of
    them). Same shape as `isolated_panel_registry` above -- there is only
    ever ONE value per slot (`agents.contracts.attachments`'s own module
    docstring: "SIX SLOTS ... EACH STILL A SINGLETON"), saved here in a
    name-keyed dict so the restore cannot pair a value with the wrong
    slot.

    ONE LIST OF SLOT NAMES, ITERATED (audit 2, B7 fixture half). The
    six were hand-written THREE times here -- once to save, once to
    clear, once to restore -- which is exactly the shape that produced
    the I-4 bug above: a slot added to one list and forgotten in
    another isolates on the way in and leaks on the way out, with the
    suite green. Driven off `_ATTACHMENT_SLOTS` (just above this
    fixture), a seventh slot is either isolated in all three phases or
    in none, and "only two of six were isolated" is unrepresentable.
    A name that is not really a slot fails loudly on the first
    `getattr`, rather than silently isolating nothing.
    """
    from agents.contracts import attachments

    saved = {name: getattr(attachments, name) for name in _ATTACHMENT_SLOTS}
    for name in _ATTACHMENT_SLOTS:
        setattr(attachments, name, None)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(attachments, name, value)
