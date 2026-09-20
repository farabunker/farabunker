"""The chat-attachment registry leaf -- SIX singleton slots, no Django.
Round 11 review I-4: the four pins `test_workstreams.py` already
carries for its own `WorkstreamPanel` registry, mirrored here for this
registry's own (narrower, one-slot-at-a-time) contract -- round 11
re-review minor 4 for the delete-time sibling slot, ROUND-13 REVIEW
FIX I-4 for the THREE round-13 slots (`_UPLOADER`/`_DETACHER`/
`_TEXT_PROVIDER`) that shipped with zero contract tests at all, and
for the SIXTH slot (`_ORPHAN_CLEANUP`) round-13's own review fix I-2
just added.

PARAMETRIZED ACROSS ALL SIX SLOTS, not six copies of the same four
tests: `agents/contracts/tests/test_workstreams.py`'s own `@pytest.
mark.parametrize` precedent for a registry with several near-identical
slots, applied here now that this registry has grown to the same
shape (six single-value slots, each answering ONE question) rather
than the two/three it had when this file was first written by hand.
"""
from __future__ import annotations

import pytest

from agents.contracts import attachments
from agents.contracts.attachments import (
    attachment_cleanup, attachment_detacher, attachment_orphan_cleanup, attachment_provider,
    attachment_text_provider, attachment_uploader, register_attachment_cleanup,
    register_attachment_detacher, register_attachment_orphan_cleanup,
    register_attachment_provider, register_attachment_text_provider,
    register_attachment_uploader,
)
from agents.contracts.tests._helpers import isolated_attachment_registry  # noqa: F401

# One row per slot: (register fn, read fn, a real dotted path each one's
# own docstring names as ITS registered provider -- real strings, not
# invented ones, the same "a driver names the field the code really
# reads" discipline this column's own view-layer tests already follow).
_SLOTS = [
    pytest.param(register_attachment_provider, attachment_provider,
                "tools.rag.access.attached_documents", id="provider"),
    pytest.param(register_attachment_cleanup, attachment_cleanup,
                "tools.rag.access.delete_attachments", id="cleanup"),
    pytest.param(register_attachment_uploader, attachment_uploader,
                "tools.rag.services.stage_turn_attachments", id="uploader"),
    pytest.param(register_attachment_detacher, attachment_detacher,
                "tools.rag.access.detach_attachment", id="detacher"),
    pytest.param(register_attachment_text_provider, attachment_text_provider,
                "tools.rag.access.inline_text_for", id="text_provider"),
    pytest.param(register_attachment_orphan_cleanup, attachment_orphan_cleanup,
                "tools.rag.access.remove_staged_documents", id="orphan_cleanup"),
]


@pytest.mark.parametrize("register, read, real_path", _SLOTS)
def test_a_registered_provider_is_returned(
        isolated_attachment_registry, register, read, real_path):
    register(real_path)
    assert read() == real_path


@pytest.mark.parametrize("register, read, real_path", _SLOTS)
def test_nothing_registered_answers_none(
        isolated_attachment_registry, register, read, real_path):
    """A box with `tools.rag` uninstalled -- every slot's own `attachment_
    *` reader docstring names this case explicitly."""
    assert read() is None


@pytest.mark.parametrize("register, read, real_path", _SLOTS)
def test_registering_again_replaces_the_previous_one(
        isolated_attachment_registry, register, read, real_path):
    """Idempotent, like every sibling registry in this codebase -- a
    second registration simply replaces the first, rather than raising
    or silently keeping the earlier one (an `AppConfig.ready()` can run
    twice in one process)."""
    register("pkg.mod.one")
    register("pkg.mod.two")
    assert read() == "pkg.mod.two"


@pytest.mark.parametrize("register, read, real_path", _SLOTS)
def test_a_path_with_no_dot_is_refused_at_registration(
        isolated_attachment_registry, register, read, real_path):
    """Construction-time error, not a render-time `ImportError` deep
    inside `import_string` -- the same `WorkstreamPanel.__post_init__`
    shape this registry's own sibling already enforces."""
    with pytest.raises(ValueError) as exc:
        register("notdotted")
    assert "dotted path" in str(exc.value)
    # THE REFUSED CALL NEVER TOUCHED THE SLOT -- an earlier registration
    # (there is none here, but a real `ready()` re-run could race one)
    # must survive a malformed second call untouched.
    assert read() is None


def test_the_six_slots_are_independent(isolated_attachment_registry):
    """Registering one slot must not touch any other -- each answers a
    DIFFERENT question (what is attached / forget what was attached on
    delete / stage newly-submitted files / remove one attachment /
    extract one file's own text / delete orphaned managed-store bytes
    on a rolled-back turn), and this registry is six independent
    singletons, never a dict one write could clobber another entry in."""
    register_attachment_provider("tools.rag.access.attached_documents")
    assert attachment_cleanup() is None
    assert attachment_uploader() is None
    assert attachment_detacher() is None
    assert attachment_text_provider() is None
    assert attachment_orphan_cleanup() is None

    register_attachment_cleanup("tools.rag.access.delete_attachments")
    register_attachment_uploader("tools.rag.services.stage_turn_attachments")
    register_attachment_detacher("tools.rag.access.detach_attachment")
    register_attachment_text_provider("tools.rag.access.inline_text_for")
    register_attachment_orphan_cleanup("tools.rag.access.remove_staged_documents")

    assert attachment_provider() == "tools.rag.access.attached_documents"
    assert attachment_cleanup() == "tools.rag.access.delete_attachments"
    assert attachment_uploader() == "tools.rag.services.stage_turn_attachments"
    assert attachment_detacher() == "tools.rag.access.detach_attachment"
    assert attachment_text_provider() == "tools.rag.access.inline_text_for"
    assert attachment_orphan_cleanup() == "tools.rag.access.remove_staged_documents"


def test_isolated_attachment_registry_actually_isolates_all_six_slots():
    """ROUND-13 REVIEW FIX, I-4's own second finding: the isolation
    FIXTURE ITSELF used to save/clear/restore only two of what were,
    by round 13, already five slots -- silently failing to isolate the
    other three (now four, with I-2's own sixth). Proven here WITHOUT
    the fixture (this test deliberately does not request it, since
    testing the fixture BY USING it would be circular): drive its own
    generator by hand and confirm every slot reads back to its
    PRE-fixture value -- the exact property a leak would violate.

    `try`/`finally` around the WHOLE body, restoring whatever the
    registry genuinely held before this test ran (real `tools.rag`
    registrations from `apps.py::ready()`, in an ordinary run) -- this
    test does not use `isolated_attachment_registry` (it cannot, that
    is what it is testing), so it is responsible for its own cleanup on
    every exit path, including a failed assertion, or it would leave
    every LATER test in the same process running against six poisoned
    globals.
    """
    from agents.contracts import attachments
    from agents.contracts.tests._helpers import isolated_attachment_registry as _fixture

    truly_original = (
        attachments._PROVIDER, attachments._CLEANUP, attachments._UPLOADER,
        attachments._DETACHER, attachments._TEXT_PROVIDER, attachments._ORPHAN_CLEANUP,
    )
    try:
        register_attachment_provider("real.provider")
        register_attachment_cleanup("real.cleanup")
        register_attachment_uploader("real.uploader")
        register_attachment_detacher("real.detacher")
        register_attachment_text_provider("real.text_provider")
        register_attachment_orphan_cleanup("real.orphan_cleanup")

        # `.__wrapped__`, not a bare `_fixture()`: modern pytest refuses
        # to call a `@pytest.fixture`-decorated function directly (it
        # is meant to be requested as a parameter, never invoked by
        # hand) -- `__wrapped__` is the plain generator function
        # underneath, reached the same way `functools.wraps` always
        # exposes the original callable.
        generator = _fixture.__wrapped__()
        next(generator)  # enters the fixture -- every slot cleared to `None`
        assert attachment_provider() is None
        assert attachment_cleanup() is None
        assert attachment_uploader() is None
        assert attachment_detacher() is None
        assert attachment_text_provider() is None
        assert attachment_orphan_cleanup() is None

        # Register something DIFFERENT while "inside" the fixture, the
        # shape a real test body takes -- proving restoration below
        # recovers the ORIGINAL value, not merely whatever was live
        # when the fixture happened to snapshot it.
        register_attachment_uploader("temporary.value")

        with pytest.raises(StopIteration):
            next(generator)  # exits the fixture -- every slot restored

        assert attachment_provider() == "real.provider"
        assert attachment_cleanup() == "real.cleanup"
        assert attachment_uploader() == "real.uploader"
        assert attachment_detacher() == "real.detacher"
        assert attachment_text_provider() == "real.text_provider"
        assert attachment_orphan_cleanup() == "real.orphan_cleanup"
    finally:
        (
            attachments._PROVIDER, attachments._CLEANUP, attachments._UPLOADER,
            attachments._DETACHER, attachments._TEXT_PROVIDER, attachments._ORPHAN_CLEANUP,
        ) = truly_original


_SETTERS = [
    ("register_attachment_provider", "attachment_provider"),
    ("register_attachment_cleanup", "attachment_cleanup"),
    ("register_attachment_uploader", "attachment_uploader"),
    ("register_attachment_detacher", "attachment_detacher"),
    ("register_attachment_text_provider", "attachment_text_provider"),
    ("register_attachment_orphan_cleanup", "attachment_orphan_cleanup"),
]


@pytest.mark.parametrize(("setter", "accessor"), _SETTERS)
def test_every_setter_refuses_a_path_with_no_dot_and_names_itself(setter, accessor, isolated_attachment_registry):
    """C-26. All six carry the same four-line validation body, and the
    only thing that varies is the function's own name in the message.
    Factoring the body must not quietly make all six say
    `_register` instead -- an operator reading a traceback needs the name
    of the thing they called."""
    with pytest.raises(ValueError) as excinfo:
        getattr(attachments, setter)("notdotted")
    assert setter in str(excinfo.value)
    assert "notdotted" in str(excinfo.value)


@pytest.mark.parametrize(("setter", "accessor"), _SETTERS)
def test_every_setter_still_writes_only_its_own_slot(setter, accessor, isolated_attachment_registry):
    """The six slots stay six. This task factors a body, not a registry."""
    getattr(attachments, setter)("a.b")
    assert getattr(attachments, accessor)() == "a.b"
    others = [a for _, a in _SETTERS if a != accessor]
    assert all(getattr(attachments, a)() is None for a in others)
