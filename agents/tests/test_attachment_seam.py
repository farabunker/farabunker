"""`agents.attachments` -- the resolver half of the chat-attachment
registry seam (`agents.contracts.attachments`). Round 11 review I-4:
the "provider raises -> degrades" pin `agents/tests/test_workstream_
seam.py::test_a_panel_whose_provider_raises_degrades_to_a_named_empty_
section` already carries for `WorkstreamPanel`, mirrored here for this
registry's own (single-slot) contract -- `agents.attachments.
attached_documents`'s own docstring capitalises the claim ("NEVER
RAISES"); this is what keeps that claim honest.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from agents.attachments import (
    attached_documents, attachment_image_path, cleanup_staged_documents,
    delete_attachments_for, detach_attachment, inline_attachment_text, stage_turn_attachments,
)
from agents.contracts.artifacts import ArtifactFile, register_artifact_file_resolver
from agents.contracts.attachments import (
    AttachmentUploadResult, DetachOutcome, register_attachment_cleanup,
    register_attachment_detacher, register_attachment_orphan_cleanup,
    register_attachment_provider, register_attachment_text_provider,
    register_attachment_uploader,
)
from agents.contracts.tests._helpers import (  # noqa: F401
    isolated_attachment_registry, isolated_file_resolver_registry,
)
from identity.contracts.principals import OPEN_PRINCIPAL


def _conversation():
    """A stand-in with just the one attribute `attached_documents`
    reads (`.id`) -- no real `Conversation` row needed, since neither
    provider below ever gets far enough to read anything off it."""
    return SimpleNamespace(id=uuid.uuid4())


def _fake_provider(principal, *, conversation_id, stream, settings_row):
    return [{"id": 1, "title": "a.txt", "status": "ready", "status_detail": ""}]


def _raising_provider(principal, *, conversation_id, stream, settings_row):
    raise RuntimeError("the store is down")


def test_a_registered_provider_is_resolved_at_call_time(isolated_attachment_registry):
    """The provider is a DOTTED PATH resolved with `import_string` at
    call time, never imported by `agents/` itself."""
    register_attachment_provider("agents.tests.test_attachment_seam._fake_provider")
    result = attached_documents(OPEN_PRINCIPAL, _conversation())
    assert result == [{"id": 1, "title": "a.txt", "status": "ready", "status_detail": ""}]


def test_no_provider_registered_answers_no_attachments(isolated_attachment_registry):
    """A box with `tools.rag` uninstalled -- `attachment_provider()` is
    `None` and nothing raises."""
    assert attached_documents(OPEN_PRINCIPAL, _conversation()) == []


def test_a_raising_provider_degrades_to_no_attachments_not_a_500(
        isolated_attachment_registry, caplog):
    """AUTHOR DECISION 11 (the SAME posture `agents.workstreams.
    panels_for` already takes for its own registry, and the one this
    module's own docstring names): a `tools.rag` provider that raises at
    call time -- a migration half-applied, a store that is down -- must
    not take the conversation page, or a running turn's own prompt, down
    with it."""
    register_attachment_provider("agents.tests.test_attachment_seam._raising_provider")
    result = attached_documents(OPEN_PRINCIPAL, _conversation())
    assert result == []
    assert "could not be resolved" in caplog.text


def _fake_cleanup(conversation_id) -> int:
    return 3


def _raising_cleanup(conversation_id) -> int:
    raise RuntimeError("the store is down")


@pytest.mark.django_db
def test_a_registered_cleanup_is_resolved_at_call_time(isolated_attachment_registry):
    """`django_db` (round 11 fix-2 verify, Important N-1): `delete_
    attachments_for` now wraps its provider call in its own SAVEPOINT
    (`transaction.atomic()`, nested) -- entering one needs a real DB
    connection even when the registered provider itself never touches
    the database, which `_fake_cleanup` here does not."""
    register_attachment_cleanup("agents.tests.test_attachment_seam._fake_cleanup")
    assert delete_attachments_for(uuid.uuid4()) == 3


def test_no_cleanup_registered_answers_zero(isolated_attachment_registry):
    """A box with `tools.rag` uninstalled -- same posture as the read
    side's `test_no_provider_registered_answers_no_attachments`. NO
    `django_db`: nothing is registered, so `delete_attachments_for`
    returns before ever reaching the savepoint."""
    assert delete_attachments_for(uuid.uuid4()) == 0


@pytest.mark.django_db
def test_a_raising_cleanup_degrades_to_zero_not_a_500(isolated_attachment_registry, caplog):
    """A conversation delete the actor already confirmed must not be
    blocked by a broken `tools.rag` cleanup provider -- the identical
    posture the read side takes, pinned again for the delete side.
    `django_db`: the savepoint this call now opens (Important N-1)
    needs a real connection."""
    register_attachment_cleanup("agents.tests.test_attachment_seam._raising_cleanup")
    assert delete_attachments_for(uuid.uuid4()) == 0
    assert "could not be resolved" in caplog.text


# ROUND-13 REVIEW FIX, I-4: the three "NEVER RAISES PAST THIS BOUNDARY"
# seams round 13 (message-bound attachments) added -- `stage_turn_
# attachments`, `detach_attachment`, `inline_attachment_text` -- shipped
# with ZERO seam tests, the exact round-11 I-4 gap reopened a third
# time (the review's own words). Each gets the SAME three-test shape
# the read/cleanup pair above already established: registered-and-
# resolved, nothing-registered, and a raising provider degrading to
# the function's own typed refusal rather than an unhandled exception.

def _fake_uploader(principal, *, conversation_id, turn_id, files, workstream_id, placement,
                   remember_placement, actor):
    return AttachmentUploadResult(ok=True, queued=len(files))


def _raising_uploader(principal, *, conversation_id, turn_id, files, workstream_id, placement,
                      remember_placement, actor):
    raise RuntimeError("the store is down")


def test_a_registered_uploader_is_resolved_at_call_time(isolated_attachment_registry):
    register_attachment_uploader("agents.tests.test_attachment_seam._fake_uploader")
    result = stage_turn_attachments(
        OPEN_PRINCIPAL, conversation_id=uuid.uuid4(), turn_id=1, files=["a", "b"],
        workstream_id=None, placement="conversation", remember_placement=False,
        actor=OPEN_PRINCIPAL,
    )
    assert result == AttachmentUploadResult(ok=True, queued=2)


def test_no_uploader_registered_answers_a_503_refusal(isolated_attachment_registry):
    """A box with `tools.rag` uninstalled -- `stage_turn_attachments`'s
    own docstring: this must NOT degrade to "as if nothing was
    attached" (that would silently drop files the operator just
    submitted), so the typed refusal carries `ok=False`, unlike the
    read side's plain `[]`."""
    result = stage_turn_attachments(
        OPEN_PRINCIPAL, conversation_id=uuid.uuid4(), turn_id=1, files=["a"],
        workstream_id=None, placement="conversation", remember_placement=False,
        actor=OPEN_PRINCIPAL,
    )
    assert result.ok is False
    assert result.status == 503


def test_a_raising_uploader_degrades_to_a_503_refusal_not_a_500(
        isolated_attachment_registry, caplog):
    register_attachment_uploader("agents.tests.test_attachment_seam._raising_uploader")
    result = stage_turn_attachments(
        OPEN_PRINCIPAL, conversation_id=uuid.uuid4(), turn_id=1, files=["a"],
        workstream_id=None, placement="conversation", remember_placement=False,
        actor=OPEN_PRINCIPAL,
    )
    assert result.ok is False
    assert result.status == 503
    assert "could not be resolved" in caplog.text


def _fake_detacher(principal, *, conversation_id, doc_id):
    return DetachOutcome(True, 200)


def _raising_detacher(principal, *, conversation_id, doc_id):
    raise RuntimeError("the store is down")


def test_a_registered_detacher_is_resolved_at_call_time(isolated_attachment_registry):
    register_attachment_detacher("agents.tests.test_attachment_seam._fake_detacher")
    result = detach_attachment(OPEN_PRINCIPAL, conversation_id=uuid.uuid4(), doc_id=1)
    assert result == DetachOutcome(True, 200)


def test_no_detacher_registered_answers_a_404_refusal(isolated_attachment_registry):
    result = detach_attachment(OPEN_PRINCIPAL, conversation_id=uuid.uuid4(), doc_id=1)
    assert result.ok is False
    assert result.status == 404


def test_a_raising_detacher_degrades_to_a_404_refusal_not_a_500(
        isolated_attachment_registry, caplog):
    register_attachment_detacher("agents.tests.test_attachment_seam._raising_detacher")
    result = detach_attachment(OPEN_PRINCIPAL, conversation_id=uuid.uuid4(), doc_id=1)
    assert result.ok is False
    assert result.status == 404
    assert "could not be resolved" in caplog.text


def _fake_text_provider(doc_id) -> str:
    return "the file's own text"


def _raising_text_provider(doc_id) -> str:
    raise RuntimeError("the store is down")


def test_a_registered_text_provider_is_resolved_at_call_time(isolated_attachment_registry):
    register_attachment_text_provider("agents.tests.test_attachment_seam._fake_text_provider")
    assert inline_attachment_text(1) == "the file's own text"


def test_no_text_provider_registered_answers_an_empty_string(isolated_attachment_registry):
    """`inline_attachment_text`'s own docstring: `""` for EVERY failure
    mode, no provider registered included -- the caller's own "still
    processing" fallback already covers this identically to a real
    extraction that found nothing."""
    assert inline_attachment_text(1) == ""


def test_a_raising_text_provider_degrades_to_an_empty_string_not_a_500(
        isolated_attachment_registry, caplog):
    register_attachment_text_provider(
        "agents.tests.test_attachment_seam._raising_text_provider")
    assert inline_attachment_text(1) == ""
    assert "could not be resolved" in caplog.text


# ROUND-13 REVIEW FIX, I-2's OWN sixth slot -- the identical three-test
# shape, one more time: `cleanup_staged_documents` degrades to a plain
# `None` (there is no typed refusal to return -- its ONE caller, `agents.
# chat.service.start_turn`'s own failure branches, is already mid-way
# through answering its OWN refusal, and a broken cleanup provider must
# not turn that 503 into an unhandled exception on top of it).

_CLEANUP_CALLS: list[tuple] = []


def _fake_orphan_cleanup(document_ids) -> None:
    _CLEANUP_CALLS.append(tuple(document_ids))


def _raising_orphan_cleanup(document_ids) -> None:
    raise RuntimeError("the store is down")


def test_a_registered_orphan_cleanup_is_resolved_at_call_time(isolated_attachment_registry):
    _CLEANUP_CALLS.clear()
    register_attachment_orphan_cleanup(
        "agents.tests.test_attachment_seam._fake_orphan_cleanup")
    cleanup_staged_documents((1, 2, 3))
    assert _CLEANUP_CALLS == [(1, 2, 3)]


def test_no_orphan_cleanup_registered_is_a_silent_no_op(isolated_attachment_registry):
    """No exception, no return value worth checking -- `cleanup_staged_
    documents`'s own docstring: a box with `tools.rag` uninstalled has
    nothing to clean up on disk in the first place."""
    cleanup_staged_documents((1, 2, 3))  # must not raise


def test_an_empty_id_tuple_never_even_resolves_the_provider(isolated_attachment_registry):
    """`cleanup_staged_documents`'s own docstring: `()` is a plain
    no-op, checked BEFORE the provider is ever looked up -- proven here
    by registering a provider that would raise if called at all, and
    confirming it never fires."""
    register_attachment_orphan_cleanup(
        "agents.tests.test_attachment_seam._raising_orphan_cleanup")
    cleanup_staged_documents(())  # must not raise -- the provider is never reached


def test_a_raising_orphan_cleanup_is_swallowed_not_a_500(isolated_attachment_registry, caplog):
    register_attachment_orphan_cleanup(
        "agents.tests.test_attachment_seam._raising_orphan_cleanup")
    cleanup_staged_documents((1,))  # must not raise
    assert "could not be resolved" in caplog.text


# TASK 3 (native image input, gated): `attachment_image_path` resolves
# through a DIFFERENT registry than every seam above -- `agents.
# contracts.artifacts.register_artifact_file_resolver`, the artifact-
# file registry `tools.vision.services._stored_document_input` already
# resolves the identical `document` kind through (this helper's own
# WHY-comment cites it as precedent: the SAME `media_type.startswith(
# "image/")` decision, reused rather than re-derived). `isolated_file_
# resolver_registry` (not `isolated_attachment_registry` above) is its
# own registry's isolation fixture.

_FAKE_ARTIFACT_FILES: dict[int, ArtifactFile] = {}


def _fake_document_resolver(pk, principal) -> ArtifactFile:
    try:
        return _FAKE_ARTIFACT_FILES[pk]
    except KeyError:
        raise LookupError(f"document:{pk} does not name a readable document.") from None


def _raising_image_resolver(pk, principal) -> ArtifactFile:
    raise RuntimeError("the store is down")


def test_no_resolver_registered_answers_none(isolated_file_resolver_registry):
    assert attachment_image_path(1, OPEN_PRINCIPAL) is None


def test_a_readable_image_answers_its_path(isolated_file_resolver_registry, tmp_path):
    register_artifact_file_resolver(
        "document", "agents.tests.test_attachment_seam._fake_document_resolver")
    path = tmp_path / "photo.png"
    path.write_bytes(b"stand-in bytes -- the seam never reads them, only stats the path")
    _FAKE_ARTIFACT_FILES[1] = ArtifactFile(path=str(path), name="photo.png",
                                           media_type="image/png")
    try:
        assert attachment_image_path(1, OPEN_PRINCIPAL) == str(path)
    finally:
        _FAKE_ARTIFACT_FILES.clear()


def test_a_non_image_media_type_answers_none(isolated_file_resolver_registry, tmp_path):
    """`is_image` on an attachment row is only a pre-filter -- this is
    the LIVE decision, and a readable, on-disk PDF still fails it."""
    register_artifact_file_resolver(
        "document", "agents.tests.test_attachment_seam._fake_document_resolver")
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4")
    _FAKE_ARTIFACT_FILES[2] = ArtifactFile(path=str(path), name="report.pdf",
                                           media_type="application/pdf")
    try:
        assert attachment_image_path(2, OPEN_PRINCIPAL) is None
    finally:
        _FAKE_ARTIFACT_FILES.clear()


def test_a_lookuperror_from_the_resolver_answers_none(isolated_file_resolver_registry):
    """`LookupError` is the registered resolver's ONE exception for "no
    such row", "no file on disk" AND "this principal may not read it"
    (`agents.contracts.artifacts.ArtifactFile`'s own docstring) -- this
    module's own never-raise posture folds all three into `None`,
    exactly as `inline_attachment_text` above folds every text-provider
    failure into `""`."""
    register_artifact_file_resolver(
        "document", "agents.tests.test_attachment_seam._fake_document_resolver")
    assert attachment_image_path(999, OPEN_PRINCIPAL) is None


def test_a_resolver_that_raises_something_else_also_answers_none(
        isolated_file_resolver_registry, caplog):
    register_artifact_file_resolver(
        "document", "agents.tests.test_attachment_seam._raising_image_resolver")
    assert attachment_image_path(1, OPEN_PRINCIPAL) is None
    assert "could not be resolved" in caplog.text


def test_a_file_missing_on_disk_answers_none(isolated_file_resolver_registry, tmp_path):
    register_artifact_file_resolver(
        "document", "agents.tests.test_attachment_seam._fake_document_resolver")
    _FAKE_ARTIFACT_FILES[3] = ArtifactFile(
        path=str(tmp_path / "gone.png"), name="gone.png", media_type="image/png")
    try:
        assert attachment_image_path(3, OPEN_PRINCIPAL) is None
    finally:
        _FAKE_ARTIFACT_FILES.clear()
