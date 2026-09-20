"""Shared test helpers for `identity/tests`.

Plain importable module -- **not** a `conftest.py` (the repo forbids
them anywhere). Each test module imports what it needs explicitly;
autouse fixtures stay *defined* per test module but delegate their
bodies to the functions below.

`posture`, `seed_sweep_posture`, `make_user`, `make_admin`,
`user_principal` and `sign_in` moved to `identity/testing.py`
(consolidation round 2, FIX-NOW 1) -- the shared body four `_helpers.py`
copies used to duplicate letter for letter. `reset_settings`, `grant`,
`make_entitlement` and `make_group` moved there too in IA-2, for the
same reason: four more packages need them now, and `identity/tests/`
is one package's private scaffolding. `make_queue_job` moved there too
(IA-2 T17 follow-up): it was a plain `InferenceJob` builder with no
route-matrix-specific shape, so a second column (`models/queue/tests/`)
reaching for it directly is the same "this is generic, not
package-specific" story as the four above, not a new one. Re-exported
here so every existing `from identity.tests._helpers import ...` keeps
working unchanged; only the body they resolve to moved. What remains
below is genuinely package-specific: the row builders for
`identity/tests/test_route_matrix.py`, and `_record_failures` for
`identity/tests/test_login_lockout.py` (H9/S7).
"""
from __future__ import annotations

import itertools
from datetime import timedelta

from django.utils import timezone

from identity import audit, throttle
from identity.contracts.actions import LOGIN_FAILED
from identity.contracts.principals import ANONYMOUS
from identity.models import AuditEvent
from identity.testing import (  # noqa: F401 -- re-exported for existing imports
    grant, make_admin, make_entitlement, make_group, make_queue_job, make_user, posture,
    reset_settings, seed_sweep_posture, sign_in, user_principal,
)

_names = itertools.count()


def _record_failures(username: str, count: int, *, age: timedelta | None = None) -> None:
    """Write `count` `login_failed` rows for `username`, optionally aged.

    Shared by `identity/tests/test_login_lockout.py`'s policy tests and
    view-level tests (H9/S7) -- one copy, since both need the same shape
    of audit history to seed a lockout.

    `at` is `auto_now_add`, so an age has to be written afterwards with a
    queryset `update()`. Touching `AuditEvent.objects` from outside
    `identity/audit.py` is barred for PRODUCTION modules by the AST sweep
    in `foundation/ops/tests/test_column_boundaries.py`, which exempts
    test files -- this is one.

    CANONICALISED before writing, same as the real write side
    (`identity.views.LoginView.form_invalid`): a test that seeds failures
    under a spelling `canonical_username` would fold (trailing whitespace,
    an NFKC-equivalent form) must land on the SAME row `throttle.
    locked_out` will later count, or it is testing a shape production
    code never produces.
    """
    canonical = throttle.canonical_username(username)
    for _ in range(count):
        audit.record(ANONYMOUS, LOGIN_FAILED, target_label=canonical)
    if age is not None:
        AuditEvent.objects.filter(action=LOGIN_FAILED, target_label=canonical).update(
            at=timezone.now() - age)


# --- row builders for identity/tests/test_route_matrix.py ------------------
#
# One per addressable row `_build_world` needs. Each resolves its model
# through `django.apps.apps.get_model` -- never imports it directly -- the
# same mechanism `identity.contracts.ownership` uses, and for the same
# reason: this module is test scaffolding for a column that may not import
# `agents`, `tools` or `models`, and a test helper that broke that rule
# would make the rule untestable.

def _create(label: str, **fields):
    """`app_label.ModelName` -> a row. Resolved, never imported."""
    from django.apps import apps
    return apps.get_model(label).objects.create(**fields)


def make_agent(**overrides):
    fields = dict(slug=f"agent-{next(_names)}", name="An agent", description="",
                  system_prompt="You are a test agent.", tool_keys=[],
                  resident=False, enabled=True)
    fields.update(overrides)
    return _create("agents.Agent", **fields)


def make_conversation(**overrides):
    fields = dict(agent=overrides.pop("agent", None) or make_agent())
    fields.update(overrides)
    return _create("agents.Conversation", **fields)


def make_turn(**overrides):
    conversation = overrides.pop("conversation", None) or make_conversation()
    fields = dict(conversation=conversation, index=0, role="user", text="a question")
    fields.update(overrides)
    return _create("agents.Turn", **fields)


def make_document(**overrides):
    # `doc_type` is `CharField(choices=DocType.choices)` with NO default
    # (`tools/rag/models.py:74`) -- omit it and the insert raises. It is
    # the SHAPE of the queryable content, and "prose" is the ordinary
    # one. NOT the same duplication `identity/testing.py` retired: that
    # was one function typed four times with no reason to differ. This
    # and `tools/rag/tests/_helpers.py::make_document` share a name and
    # a purpose but not a body FOR A REASON -- this one resolves through
    # `apps.get_model` (this module is scaffolding for the route matrix,
    # which spans every column and may not import `tools.rag.models`
    # directly), while rag's own copy, already inside that column, just
    # imports `Document`. Two genuinely different row builders, kept
    # local to the package that needs each shape.
    fields = dict(title=f"document-{next(_names)}", source_path="/dev/null",
                  file_hash=f"{next(_names):064d}", doc_type="prose",
                  status="ready")
    fields.update(overrides)
    return _create("rag.Document", **fields)


def make_category(**overrides):
    fields = dict(name=f"category-{next(_names)}")
    fields.update(overrides)
    return _create("rag.Category", **fields)


def make_connection(**overrides):
    fields = dict(name=f"connection-{next(_names)}", engine="ollama",
                  endpoint="http://localhost:1", model_id="an-identifier",
                  capabilities=["chat"])
    fields.update(overrides)
    return _create("inference.ModelConnection", **fields)


def make_model_set(**overrides):
    """A `models.registry.ModelSet` row (IA-2 T14), resolved through
    `apps.get_model` like every other row builder here -- this module is
    scaffolding for the route matrix, which spans every column and may
    not import `models.registry.models` directly."""
    fields = dict(name=f"set-{next(_names)}")
    fields.update(overrides)
    return _create("inference.ModelSet", **fields)


def make_generation(**overrides):
    fields = dict(operation="txt2img", params={"prompt": "a picture"},
                  engine="comfyui", model_id="an-identifier",
                  endpoint="http://localhost:1", status="succeeded")
    fields.update(overrides)
    return _create("vision.GenerationJob", **fields)


def make_output(**overrides):
    fields = dict(job=overrides.pop("job", None) or make_generation(),
                  index=0, path="/dev/null", media_type="image/png")
    fields.update(overrides)
    return _create("vision.GeneratedOutput", **fields)


def make_job_input(**overrides):
    # `param_key`, not `key` (`tools/vision/models.py:397`).
    fields = dict(job=overrides.pop("job", None) or make_generation(),
                  param_key="image", path="/dev/null", media_type="image/png")
    fields.update(overrides)
    return _create("vision.JobInput", **fields)


def make_workstream(**overrides):
    """An `agents.Workstream` row (Workstreams WS-1 T13), resolved
    through `apps.get_model` like every other row builder here -- this
    module is scaffolding for the route matrix, which spans every column
    and may not import `agents.models` directly. `name` defaults to a
    counter-suffixed value so two calls in the same world (or under the
    same owner) never collide on `uniq_workstream_name_ci_per_owner`."""
    fields = dict(name=f"workstream-{next(_names)}")
    fields.update(overrides)
    return _create("agents.Workstream", **fields)
