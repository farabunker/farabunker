"""Pinning — an association, capped, and refused by name rather than
404-shaped, because this is a button on a page listing rows the caller
can already see."""
from __future__ import annotations

import pytest

from agents.workstreams import workstream_scope
from tools.rag.models import WorkstreamPin
from tools.rag.tests._helpers import _workstream, make_document
from tools.rag.workstreams import (
    MAX_PINS_PER_STREAM, pin_document, scope_with_pins, stream_documents, unpin_document,
)
from identity.access import owner_fields
from identity.testing import make_entitlement, make_user, posture, user_principal

pytestmark = pytest.mark.django_db


def test_pinning_a_universal_document_succeeds_and_fills_the_scopes_pin_set():
    stream = _workstream()
    doc = make_document()
    with posture("open"):
        scope = _scope_open(stream)
        assert pin_document(_open(), scope, doc) is None
        filled = scope_with_pins(scope)
    assert filled.pinned_file_ids == frozenset({doc.pk})
    # And the stream half is untouched — same frozen value, one field on.
    assert filled.workstream_id == scope.workstream_id
    assert filled.wall == scope.wall
    assert filled.default_upload_placement == scope.default_upload_placement
    assert filled.may_upload == scope.may_upload


def test_a_contained_document_may_not_be_pinned_anywhere_including_its_own_stream():
    """AUTHOR DECISION 13, asserted rather than left to a constraint that
    Postgres will not accept."""
    stream, other = _workstream(), _workstream()
    contained = make_document(workstream=stream)
    with posture("open"):
        message = pin_document(_open(), _scope_open(other), contained)
        assert message is not None and "already lives in" in message
        message = pin_document(_open(), _scope_open(stream), contained)
        assert message is not None
    assert WorkstreamPin.objects.count() == 0


def test_the_pin_cap_is_refused_with_an_honest_message_naming_it():
    """A pin set becomes an `ANY` array in every query the stream runs,
    so it is unbounded work per turn. A cap, not a paginator (author
    decision 7)."""
    stream = _workstream()
    with posture("open"):
        scope = _scope_open(stream)
        for _ in range(MAX_PINS_PER_STREAM):
            assert pin_document(_open(), scope, make_document()) is None
        message = pin_document(_open(), scope, make_document())
    assert message is not None
    assert str(MAX_PINS_PER_STREAM) in message


def test_pinning_a_chat_scoped_document_is_refused_by_name():
    """ROUND 12 WHOLE-BRANCH REVIEW B-1: `readable_documents(principal)`
    -- no `workstream_id`, this function's own third check below --
    RE-ADMITS chat-scoped rows on the plain-library read (round 12
    review I-2's own rule: the uploader's own, or every one of them for
    `sees_all_content` -- `_open()`/`posture("open")` here is exactly
    that latter case), so without a DEDICATED refusal a chat-scoped
    document could be "pinned" -- a write that SUCCEEDS into a pin
    invisible in the stream panel's own pinned list (that surface
    filters through `readable_documents(…, workstream_id=W)`, which
    correctly excludes a chat-scoped row) and inert at retrieval either
    way."""
    from tools.rag.models import Document

    stream = _workstream()
    doc = make_document()
    doc.scope = Document.Scope.CONVERSATION
    doc.save(update_fields=["scope", "updated_at"])
    with posture("open"):
        message = pin_document(_open(), _scope_open(stream), doc)
    assert message is not None
    assert "chat only" in message
    assert WorkstreamPin.objects.count() == 0


def test_a_refused_chat_scoped_pin_never_consumes_the_cap():
    """The chat-scope refusal is checked BEFORE the cap (`pin_document`'s
    own docstring, "CHECKED FIRST") -- a doomed chat-scoped pin attempt
    must never eat into `MAX_PINS_PER_STREAM`, or a run of them could
    starve out legitimate pins with nothing to show for it."""
    from tools.rag.models import Document

    stream = _workstream()
    doc = make_document()
    doc.scope = Document.Scope.CONVERSATION
    doc.save(update_fields=["scope", "updated_at"])
    with posture("open"):
        scope = _scope_open(stream)
        for _ in range(MAX_PINS_PER_STREAM):
            pin_document(_open(), scope, doc)
        # The cap is untouched -- zero REAL pins were ever created, so a
        # genuine universal document can still be pinned afterward.
        assert WorkstreamPin.objects.count() == 0
        assert pin_document(_open(), scope, make_document()) is None
    assert WorkstreamPin.objects.count() == 1


def test_pinning_a_document_the_caller_may_not_read_is_refused_by_name():
    """The third refusal is a 404 at the ROUTE and is repeated here
    because this function is also the CLI-facing one, and a predicate
    stated in the view alone is a predicate the second caller does not
    get."""
    user = make_user()
    secret = make_entitlement()
    doc = make_document()
    doc.entitlement_labels.create(entitlement=secret)
    # OWNER COLUMNS, or `visible_workstreams` excludes the row for an
    # ordinary user under `enterprise` (`owned_rows_q` compares
    # `owner_kind`/`owner_key` against the principal, and the helper
    # leaves both blank), `workstream_scope` answers `None`, and
    # `pin_document` dereferences `scope.workstream_id` on it.
    stream = _workstream(name="s", **owner_fields(user_principal(user)))
    with posture("enterprise"):
        scope = workstream_scope(user_principal(user), stream.pk)
        message = pin_document(user_principal(user), scope, doc)
    assert message is not None
    assert "may not" in message.lower() or "cannot" in message.lower()


def test_unpinning_is_the_same_module_keyed_on_the_pin_id():
    stream = _workstream()
    doc = make_document()
    with posture("open"):
        scope = _scope_open(stream)
        pin_document(_open(), scope, doc)
        pin = WorkstreamPin.objects.get()
        assert unpin_document(_open(), scope, pin.pk) is None
    assert WorkstreamPin.objects.count() == 0


def test_unpinning_a_pin_belonging_to_another_stream_is_refused():
    """The same IDOR the conversation share revoke already guards
    against: an owner of stream A must not unpin from stream B by
    guessing a sequential id."""
    a, b = _workstream(), _workstream()
    doc = make_document()
    with posture("open"):
        pin_document(_open(), _scope_open(a), doc)
        pin = WorkstreamPin.objects.get()
        assert unpin_document(_open(), _scope_open(b), pin.pk) is not None
    assert WorkstreamPin.objects.count() == 1


def test_stream_documents_is_the_orm_mirror_of_the_corpus_formula():
    """Contained here, plus pinned, plus universal — narrowed by the
    reader's own grants and by the wall. Exercised in full by
    `test_workstream_corpus.py`; this pins the shape."""
    stream, other = _workstream(), _workstream()
    here = make_document(workstream=stream)
    elsewhere = make_document(workstream=other)
    universal = make_document()
    pinned = make_document()
    with posture("open"):
        scope = _scope_open(stream)
        pin_document(_open(), scope, pinned)
        rows = set(stream_documents(_open(), scope_with_pins(scope))
                   .values_list("pk", flat=True))
    assert here.pk in rows
    assert universal.pk in rows
    assert pinned.pk in rows
    assert elsewhere.pk not in rows


@pytest.mark.parametrize("pin_count", [1, 25])
def test_the_panel_is_flat_in_the_number_of_pins(pin_count, django_assert_num_queries):
    """GLOBAL CONSTRAINT 6, on the list this task adds. Asserted as an
    EQUALITY between 1 pin and 25 rather than a fixed number, so the
    absolute count can move with the platform while the per-row cost
    stays zero -- which is the property the constraint actually needs."""
    from django.test.utils import CaptureQueriesContext
    from django.db import connection

    from tools.rag.workstreams import panel

    stream = _workstream()
    with posture("open"):
        scope = _scope_open(stream)
        for _ in range(pin_count):
            pin_document(_open(), scope, make_document())
        with CaptureQueriesContext(connection) as captured:
            panel(_open(), stream.pk)
    _PANEL_QUERY_COUNTS[pin_count] = len(captured.captured_queries)
    if len(_PANEL_QUERY_COUNTS) == 2:
        assert _PANEL_QUERY_COUNTS[1] == _PANEL_QUERY_COUNTS[25], _PANEL_QUERY_COUNTS


# Shared across the two parametrized runs above, so the assertion is a
# COMPARISON rather than a hardcoded number.
_PANEL_QUERY_COUNTS: dict[int, int] = {}


def _open():
    from identity.contracts.principals import OPEN_PRINCIPAL
    return OPEN_PRINCIPAL


def _scope_open(stream):
    return workstream_scope(_open(), stream.pk)
