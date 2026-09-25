"""Shared test helpers for `agents/chat/tests`.

Plain importable module -- **not** a `conftest.py` (the repo forbids
them anywhere). Each test module imports what it needs explicitly;
autouse fixtures stay *defined* per test module but delegate their
bodies to the functions below.

IT IMPORTS ROW BUILDERS FROM `agents/tests/_helpers.py`, AND THAT IS A
RULING, NOT A SLIP. P2's rule is "helpers are duplicated per APP, never
imported across apps", with `agents/runtime/tests/_helpers.py` carved
out as "one app, not two". `agents.chat` is a second Django app but the
SAME COLUMN and the same top-level package directory, which is the
boundary that rule actually polices -- one COLUMN's test scaffolding
becoming load-bearing for another's. A sixth copy of `make_agent` /
`make_conversation` / `make_turn` / `bind_chat_role` inside `agents/`
would be duplication with no boundary to justify it. Nothing here
imports from `tools/*/tests/` or `models/*/tests/`, and that rule is
unchanged.

`posture`, `seed_sweep_posture`, `make_user`, `make_admin`,
`user_principal`, `sign_in` come from `identity/testing.py`
(consolidation round 2) rather than being typed here a fifth time --
re-exported below so every existing `from agents.chat.tests._helpers
import ...` keeps working unchanged.
"""
from __future__ import annotations

import pytest

from agents.tests._helpers import (           # noqa: F401 -- re-exported on purpose
    bind_chat_role, bound_chat_role, make_agent, make_conversation, make_editable_thread,
    make_flow, make_turn,
)
from identity.testing import (                # noqa: F401 -- re-exported on purpose
    grant, make_admin, make_entitlement, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, sign_in, user_principal,
)


def make_thread(**overrides):
    """A conversation with a finished USER + ASSISTANT pair -- the
    minimum a thread page has anything to render."""
    from agents.models import Turn

    conversation = overrides.pop("conversation", None) or make_conversation()
    make_turn(conversation=conversation, role=Turn.Role.USER, text="hello")
    make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
              text="hi", state=Turn.State.DONE)
    return conversation


def _job(state, **overrides):
    """A `models.queue.backend.JobStatus`-shaped stand-in -- a
    `SimpleNamespace` with the REAL field names, so a test breaks if
    that shape changes rather than quietly agreeing with a double
    nobody updated. Mirrors `agents/tests/_helpers.py::_finished_job`,
    duplicated per app for the same ruling that duplicates the row
    builders' shape (Global Constraints, the helper-duplication rule).

    CQ-8: one builder for every queue state this package doubles
    (queued, queued-in-line, running) -- `position`, `priority`,
    `progress`, and `id` are all keyword overrides, so a caller who
    wants a specific position (Task 10's `turn_status` test asserts
    one) or a running job's progress dict passes it here rather than
    reaching for a differently-named copy of the same thirteen fields.
    """
    from types import SimpleNamespace

    fields = dict(
        id=1, kind="agent.turn", state=state, position=1, priority=100,
        created_at=None, started_at=None, finished_at=None, models=[],
        result={}, error="", progress=None, summary="a turn",
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _patch_queue(monkeypatch, *, on_enqueue=None, job_id=1, raises=None):
    """Patch `enqueue`/`get_job` on `agents.chat.service`.

    Seam-level, on the module under test, so the view's own call path
    runs for real. `on_enqueue` simulates the handler's writeback
    synchronously -- a double that only flipped a state without writing
    rows would make every output assertion pass against an empty
    thread (P2 review, finding M3). `raises`, when given, is raised
    from `enqueue` itself -- `QueueUnavailable` for the unmigrated-
    window case, or any other exception for the unexpected-failure
    path -- and `on_enqueue` never runs in that case."""
    from models.contracts.queue import QUEUED

    module = "agents.chat.service"

    def _enqueue(kind, payload, **kwargs):
        if raises is not None:
            raise raises
        if on_enqueue is not None:
            on_enqueue(payload)
        return job_id

    monkeypatch.setattr(f"{module}.enqueue", _enqueue)
    monkeypatch.setattr(f"{module}.get_job",
                        lambda jid: _job(QUEUED, id=job_id))


@pytest.fixture
def fake_turn_queue(monkeypatch):
    """A turn that is really queued -- `enqueue` succeeds and `get_job`
    reports it QUEUED. Nothing writes the assistant row further: this
    fixture is about `turn_create`'s own contract (queue it, stamp it),
    never about a job finishing, which is a different task's concern."""
    _patch_queue(monkeypatch)


@pytest.fixture
def fake_queue_down(monkeypatch):
    """`enqueue` raises `QueueUnavailable` -- the unmigrated-window
    case `models/contracts/queue.py:83` names."""
    from models.contracts.queue import QueueUnavailable

    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))


@pytest.fixture
def fake_queued_job(monkeypatch):
    """`turn_status`'s own queue double -- patches `get_job` on
    `agents.chat.views.turns`, NOT `agents.chat.service` (that module's
    `_patch_queue` above is `turn_create`'s seam; `turn_status` reads
    `get_job` directly, per its own brief, so the double must patch
    where the view actually imports it). Reports a turn third in line
    -- `position=3` (not `_patch_queue`'s default of 1) is the point:
    `turn_status`'s own test asserts this specific position, and a
    coincidentally-matching default would make that a coincidence
    rather than a check."""
    from models.contracts.queue import QUEUED

    monkeypatch.setattr(
        "agents.chat.views.turns.get_job", lambda job_id: _job(QUEUED, position=3)
    )


@pytest.fixture
def fake_running_job(monkeypatch):
    """`turn_status`'s own queue double for the running state -- same
    seam as `fake_queued_job` above. Carries a `report_progress`-shaped
    `progress` dict -- `{"done", "total", "unit", "label"}` -- passed
    through unchanged by `turn_status`'s running body, exactly as
    `AskJobStatusView`'s own running body does."""
    from models.contracts.queue import RUNNING

    monkeypatch.setattr(
        "agents.chat.views.turns.get_job",
        lambda job_id: _job(
            RUNNING, position=None,
            progress={"done": 2, "total": 5, "unit": "items", "label": "thinking"},
        ),
    )
