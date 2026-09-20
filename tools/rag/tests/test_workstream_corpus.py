"""The composition law, asserted as a generated matrix — spec §17.2.

Two expressions of one rule must exist, because the ORM path and the
chunk-metadata path answer the same question for different consumers
(pages versus retrieval). TWO EXPRESSIONS OF ONE RULE THAT ARE NEVER
COMPARED ARE TWO RULES, so every cell asserts both and asserts they
agree.

The chunk half is asserted as a FILTER OBJECT, not through a live store,
for the reason `_visibility_filters`' own docstring gives: a filter only
testable end to end is a filter tested rarely.
"""
from __future__ import annotations

import itertools

import pytest
from llama_index.core.vector_stores.types import FilterCondition, FilterOperator

from agents.contracts.workstreams import WorkstreamScope
from tools.rag.access import document_visibility, readable_documents
from tools.rag.retrieval import _visibility_filters
from tools.rag.tests._helpers import _workstream, make_document
from tools.rag.workstreams import stream_documents
from identity.testing import (
    grant, make_admin, make_entitlement, make_user, posture, user_principal,
)

pytestmark = pytest.mark.django_db

PLACEMENTS = ("universal", "contained-here", "contained-elsewhere", "pinned")
LABELS = ("none", "e1", "e2")
GRANTS = ("none", "e1", "both")
WALLS = ("empty", "e1", "e2")


def _flat(filters):
    """Every `MetadataFilter` in a possibly-nested `MetadataFilters`,
    flattened, so a cell can assert on keys and operators without
    caring how deep the store's recursion goes."""
    out = []
    for f in filters.filters:
        out.extend(_flat(f) if hasattr(f, "filters") else [f])
    return out


@pytest.mark.parametrize("placement,labels,grants,wall",
                         # A LIST, not the bare iterator: pytest 9 emits
                         # PytestRemovedIn10Warning for a non-Collection
                         # argvalues, and -- the part that actually bites
                         # -- an iterator is consumed exactly once, so a
                         # re-collection or `--collect-only` can silently
                         # see zero cases.
                         list(itertools.product(PLACEMENTS, LABELS, GRANTS, WALLS)))
def test_the_orm_and_the_chunk_filter_agree_on_every_cell(placement, labels, grants, wall):
    """The whole cross product. Each cell builds the world, asks the ORM
    expression (`stream_documents` / `readable_documents`) and the
    chunk-metadata expression (`_visibility_filters`), and asserts they
    describe the same corpus."""
    e1, e2 = make_entitlement(name="E1"), make_entitlement(name="E2")
    reader = make_user()
    if grants in ("e1", "both"):
        grant(e1, user=reader)
    if grants == "both":
        grant(e2, user=reader)
    stream, other = _workstream(), _workstream()
    doc = make_document(workstream={
        "universal": None, "pinned": None,
        "contained-here": stream, "contained-elsewhere": other,
    }[placement])
    if labels != "none":
        doc.entitlement_labels.create(entitlement=e1 if labels == "e1" else e2)
    pins = frozenset({doc.pk}) if placement == "pinned" else frozenset()
    if placement == "pinned":
        from tools.rag.models import WorkstreamPin
        WorkstreamPin.objects.create(workstream=stream, document=doc)
    wall_ids = {"empty": frozenset(),
                "e1": frozenset({e1.pk}),
                "e2": frozenset({e2.pk})}[wall]
    scope = WorkstreamScope(workstream_id=stream.pk, wall=wall_ids,
                            default_upload_placement="", may_upload=True,
                            pinned_file_ids=pins)

    with posture("enterprise"):
        principal = user_principal(reader)
        in_orm = stream_documents(principal, scope).filter(pk=doc.pk).exists()
        filters = _visibility_filters(
            None, document_visibility(principal, stream=scope))
        in_chunks = _chunk_filter_admits(filters, doc, stream.pk, pins)
    assert in_orm == in_chunks, (placement, labels, grants, wall, in_orm, in_chunks)
    _OUTCOMES.append(in_orm)


# Every cell's answer, accumulated across the whole product, so the two
# pins below can prove the matrix is not vacuous.
_OUTCOMES: list[bool] = []


def test_the_matrix_is_not_vacuous():
    """`in_orm == in_chunks` PASSES TRIVIALLY WHEN BOTH ARE FALSE, and 27
    of the 108 cells are `contained-elsewhere`, which is `False == False`
    by construction — as is every label/grant mismatch. A matrix that
    only ever compared two `False`s would assert nothing about the
    composition law at all, and would keep passing if
    `stream_documents` and `_visibility_filters` both started returning
    nothing.

    So: the product must produce BOTH answers, and enough of each that a
    single accidental `return False` in either expression is caught. Run
    after the parametrized cells (pytest executes in declaration order
    within a module), reading what they recorded.

    THE MODULE-LEVEL `_OUTCOMES` MAKES THIS ORDER-DEPENDENT, and that is
    accepted because of which way it fails. Under `-k`, `-x`, or a
    random-order plugin this pin can see a partial product or none at
    all -- but an empty or short list trips the first assertion, so it
    FAILS LOUDLY ("the matrix did not run") rather than passing
    vacuously, which is the failure direction that matters for a pin
    whose whole job is to catch vacuity. A full run is the only
    configuration in which it passes.
    """
    assert _OUTCOMES, "the matrix did not run"
    assert any(_OUTCOMES), "no cell admitted a document — both expressions may be empty"
    assert not all(_OUTCOMES), "every cell admitted — neither expression is filtering"
    # The 27 `contained-elsewhere` cells are the ones that MUST be False,
    # and the unwalled/held/universal cells are the ones that MUST be
    # True; between them they bound the matrix from both sides.
    assert sum(_OUTCOMES) >= 9, sum(_OUTCOMES)
    assert _OUTCOMES.count(False) >= 27, _OUTCOMES.count(False)


def _chunk_filter_admits(filters, doc, workstream_id, pins) -> bool:
    """Evaluate the built filter object against the metadata this
    document's chunks would carry — the same four-branch shape
    `restamp_document_chunks` writes (an ABSENT key, never an empty
    value)."""
    metadata = {"file_id": str(doc.pk)}
    label_ids = sorted(str(i) for i in doc.entitlement_labels.values_list(
        "entitlement_id", flat=True))
    if label_ids:
        metadata["entitlements"] = label_ids
    if doc.workstream_id:
        metadata["workstream"] = str(doc.workstream_id)
    return _evaluate(filters, metadata)


def _evaluate(node, metadata) -> bool:
    """A tiny interpreter for the three operators this phase uses —
    `EQ`, `ANY`, `IS_EMPTY` — plus `AND`/`OR` grouping. It exists so a
    cell can assert what the store WOULD do without a store: the
    operators' SQL semantics are documented in `retrieval.py` and
    `labels.py`, and `IS_EMPTY` in particular means "the key is ABSENT",
    which is the whole point of §17.2 cell 7."""
    if hasattr(node, "filters"):
        results = [_evaluate(f, metadata) for f in node.filters]
        return all(results) if node.condition == FilterCondition.AND else any(results)
    value = metadata.get(node.key)
    if node.operator == FilterOperator.IS_EMPTY:
        return value is None
    if node.operator == FilterOperator.EQ:
        return value == node.value
    if node.operator == FilterOperator.ANY:
        # `entitlements` is stored as a LIST per chunk (a document may
        # carry several labels); `file_id` is a bare SCALAR (one chunk
        # belongs to exactly one document) -- the real store's `?|`
        # operator matches a scalar jsonb string by plain membership in
        # the search array, never by exploding it into characters, so
        # this mirrors that rather than blindly `set()`-ing whatever
        # `value` turns out to be.
        if isinstance(value, str):
            return value in node.value
        return bool(set(value or []) & set(node.value))
    raise AssertionError(f"unhandled operator {node.operator}")


def test_cell_1_a_contained_document_is_invisible_everywhere_else():
    """Invisible in the library, in Ask, in Search AND IN ANOTHER STREAM,
    for a reader who holds every one of its labels."""
    reader = make_user()
    ent = make_entitlement()
    grant(ent, user=reader)
    stream, other = _workstream(), _workstream()
    doc = make_document(workstream=stream)
    doc.entitlement_labels.create(entitlement=ent)
    other_scope = WorkstreamScope(workstream_id=other.pk, wall=frozenset(),
                                  default_upload_placement="", may_upload=True)
    with posture("enterprise"):
        p = user_principal(reader)
        assert not readable_documents(p).filter(pk=doc.pk).exists()
        assert not stream_documents(p, other_scope).filter(pk=doc.pk).exists()


def test_cell_2_a_pinned_document_is_visible_in_both_and_follows_its_label():
    reader = make_user()
    ent = make_entitlement()
    grant(ent, user=reader)
    stream = _workstream()
    doc = make_document()
    doc.entitlement_labels.create(entitlement=ent)
    from tools.rag.models import WorkstreamPin
    WorkstreamPin.objects.create(workstream=stream, document=doc)
    scope = WorkstreamScope(workstream_id=stream.pk, wall=frozenset(),
                            default_upload_placement="", may_upload=True,
                            pinned_file_ids=frozenset({doc.pk}))
    with posture("enterprise"):
        p = user_principal(reader)
        assert readable_documents(p).filter(pk=doc.pk).exists()
        assert stream_documents(p, scope).filter(pk=doc.pk).exists()
    other = make_user()
    with posture("enterprise"):
        q = user_principal(other)
        assert not readable_documents(q).filter(pk=doc.pk).exists()
        assert not stream_documents(q, scope).filter(pk=doc.pk).exists()


def test_a_stale_pin_does_not_leak_a_re_homed_documents_chunks_into_its_old_stream():
    """THE RETRIEVAL HALF OF THE RE-HOMING FINDING. `set_document_
    workstream` deletes a document's `WorkstreamPin` rows the day it is
    re-homed, so a live pin naming a CONTAINED document should not exist
    in practice -- but the chunk filter must not lean on that write-side
    cleanup alone. This builds the state directly (a pin naming a
    document that is ALSO contained elsewhere, the exact state the write
    -side fix now prevents from arising) and asserts the pinned leg's
    `workstream IS_EMPTY` guard refuses it anyway, unlike the bare
    `file_id ANY [...]` leg this replaces."""
    reader = make_user()
    ent = make_entitlement()
    grant(ent, user=reader)
    old_pin_stream, new_home = _workstream(), _workstream()
    doc = make_document(workstream=new_home)
    doc.entitlement_labels.create(entitlement=ent)
    from tools.rag.models import WorkstreamPin
    WorkstreamPin.objects.create(workstream=old_pin_stream, document=doc)
    scope = WorkstreamScope(workstream_id=old_pin_stream.pk, wall=frozenset(),
                            default_upload_placement="", may_upload=True,
                            pinned_file_ids=frozenset({doc.pk}))
    with posture("enterprise"):
        p = user_principal(reader)
        filters = _visibility_filters(None, document_visibility(p, stream=scope))
        assert not _chunk_filter_admits(filters, doc, old_pin_stream.pk, scope.pinned_file_ids)


def test_cell_3_a_non_empty_wall_hides_unlabelled_universal_but_not_contained_or_pinned():
    """AUTHOR DECISION 5, and the one place a wall visibly removes
    something a person could otherwise see. The stream page says so in
    one line beside the wall editor."""
    reader = make_user()
    ent = make_entitlement()
    grant(ent, user=reader)
    stream = _workstream()
    unlabelled_universal = make_document()
    contained = make_document(workstream=stream)
    pinned = make_document()
    from tools.rag.models import WorkstreamPin
    WorkstreamPin.objects.create(workstream=stream, document=pinned)
    scope = WorkstreamScope(workstream_id=stream.pk, wall=frozenset({ent.pk}),
                            default_upload_placement="", may_upload=True,
                            pinned_file_ids=frozenset({pinned.pk}))
    with posture("enterprise"):
        keys = set(stream_documents(user_principal(reader), scope)
                   .values_list("pk", flat=True))
    assert unlabelled_universal.pk not in keys
    assert contained.pk in keys
    assert pinned.pk in keys


def test_cell_4_sees_all_content_does_not_defeat_containment_in_retrieval():
    """RULING G's other half. An administrator with the content setting
    on still does not RETRIEVE another stream's contained document into
    THIS stream's turn — which is the property containment actually owns,
    and is a different question from whether they may open the bytes
    (they may; `test_containment_routes.py` asserts the 200)."""
    admin = make_admin()
    stream, other = _workstream(), _workstream()
    elsewhere = make_document(workstream=other)
    scope = WorkstreamScope(workstream_id=stream.pk, wall=frozenset(),
                            default_upload_placement="", may_upload=True)
    with posture("enterprise", admin_sees_content=True):
        p = user_principal(admin)
        assert not stream_documents(p, scope).filter(pk=elsewhere.pk).exists()
        filters = _visibility_filters(None, document_visibility(p, stream=scope))
        assert not _chunk_filter_admits(filters, elsewhere, stream.pk, frozenset())


def test_cell_6_permits_and_readable_documents_agree_about_containment():
    """Asserted directly rather than through the page — the property
    `permits` exists for, and the one a one-sided change breaks
    invisibly. (The full version lives in `test_access_documents.py`;
    this is the matrix's own copy, so a reader of §17.2 finds all eight
    cells in one file.)"""
    stream = _workstream()
    here = make_document(workstream=stream)
    universal = make_document()
    with posture("open"):
        from identity.contracts.principals import OPEN_PRINCIPAL
        v = document_visibility(OPEN_PRINCIPAL)
        for doc in (here, universal):
            assert v.permits(doc, workstream_id=stream.pk) is readable_documents(
                OPEN_PRINCIPAL, workstream_id=stream.pk).filter(pk=doc.pk).exists()


def test_cell_5_and_7_and_8_live_in_their_own_modules():
    """Cell 5 (a contained document opens from inside its stream and 404s
    from outside) is `test_containment_routes.py`; cells 7 and 8 (the
    re-labelled universal document, and the no-op guard) are
    `test_labels.py`, because both are properties of the one chunk-cache
    WRITER rather than of the filter. Named here so a reader working
    through §17.2 finds all eight."""


def test_a_loose_turn_excludes_contained_documents_with_one_is_empty_clause():
    with posture("open"):
        from identity.contracts.principals import OPEN_PRINCIPAL
        filters = _visibility_filters(None, document_visibility(OPEN_PRINCIPAL))
    keys = [(f.key, f.operator) for f in _flat(filters)]
    assert ("workstream", FilterOperator.IS_EMPTY) in keys


def _filter_signature(filters):
    """A comparable, order-independent fingerprint of every leaf
    `MetadataFilter` in `filters` -- ROUND 17's own equality pin needs
    to compare two BUILT filter trees for sameness, and `MetadataFilter`
    is a third-party dataclass this codebase makes no promise compares
    by value; `_flat`, above, already exists for exactly this reason
    (inspecting a tree's leaves without caring how deep the nesting
    goes), so this only adds turning each leaf into a hashable tuple."""
    return sorted(
        (f.key, f.operator, tuple(f.value) if isinstance(f.value, list) else f.value)
        for f in _flat(filters)
    )


def test_round17_include_universal_false_drops_the_whole_universal_leg_not_merely_narrows_it():
    """Owner, verbatim: "a bool in settings to use all rag documents ...
    default it on." `include_universal=False` drops `(universal ∩
    wall)` ENTIRELY from BOTH expressions -- pinned and contained-here
    remain, NON-VACUOUS POSITIVE TWINS (the brief's own words) proving
    the toggle narrows nothing BESIDES the universal leg."""
    reader = make_user()
    ent = make_entitlement()
    grant(ent, user=reader)
    stream = _workstream()
    universal = make_document()
    contained = make_document(workstream=stream)
    pinned = make_document()
    from tools.rag.models import WorkstreamPin

    WorkstreamPin.objects.create(workstream=stream, document=pinned)
    scope = WorkstreamScope(workstream_id=stream.pk, wall=frozenset(),
                            default_upload_placement="", may_upload=True,
                            pinned_file_ids=frozenset({pinned.pk}),
                            include_universal=False)
    with posture("enterprise"):
        p = user_principal(reader)
        keys = set(stream_documents(p, scope).values_list("pk", flat=True))
        filters = _visibility_filters(None, document_visibility(p, stream=scope))
        chunk_universal = _chunk_filter_admits(filters, universal, stream.pk, scope.pinned_file_ids)
        chunk_contained = _chunk_filter_admits(filters, contained, stream.pk, scope.pinned_file_ids)
        chunk_pinned = _chunk_filter_admits(filters, pinned, stream.pk, scope.pinned_file_ids)
    # THE ORM HALF.
    assert universal.pk not in keys
    assert contained.pk in keys
    assert pinned.pk in keys
    # THE CHUNK HALF -- the SAME three answers, by the SAME composition
    # law this whole module's own matrix exists to hold the two
    # expressions to.
    assert not chunk_universal
    assert chunk_contained
    assert chunk_pinned


def test_round17_include_universal_defaults_true_byte_identical_to_explicit_true():
    """DEFAULT TRUE (owner: "default it on") -- an EQUALITY-STYLE PIN
    (the brief's own words): a `WorkstreamScope` that never mentions
    `include_universal` at all (every OTHER test in this module,
    `agents/workstreams.py::_scope_from_row`'s own pre-round-17 callers,
    unchanged) must resolve the IDENTICAL corpus, on both expressions,
    as one naming it `True` explicitly -- "default it on" is a promise
    about BEHAVIOUR, not merely about the field's own stored value."""
    reader = make_user()
    stream = _workstream()
    universal = make_document()
    contained = make_document(workstream=stream)
    scope_default = WorkstreamScope(workstream_id=stream.pk, wall=frozenset(),
                                    default_upload_placement="", may_upload=True)
    scope_explicit = WorkstreamScope(workstream_id=stream.pk, wall=frozenset(),
                                     default_upload_placement="", may_upload=True,
                                     include_universal=True)
    with posture("enterprise"):
        p = user_principal(reader)
        default_keys = set(stream_documents(p, scope_default).values_list("pk", flat=True))
        explicit_keys = set(stream_documents(p, scope_explicit).values_list("pk", flat=True))
        default_filters = _filter_signature(
            _visibility_filters(None, document_visibility(p, stream=scope_default)))
        explicit_filters = _filter_signature(
            _visibility_filters(None, document_visibility(p, stream=scope_explicit)))
    assert default_keys == explicit_keys
    assert default_filters == explicit_filters
    # Non-vacuous: an equality of two EMPTY sets would pass for the
    # wrong reason -- the universal document really is admitted by both.
    assert universal.pk in default_keys
    assert contained.pk in default_keys


def test_the_filter_is_never_none_any_more():
    """§6.2's documented path change, asserted so it reads as intended
    rather than as a broken test somebody repaired: the `workstream`
    clause is appended in EVERY branch, so `clauses` is never empty and
    `_visibility_filters` never returns `None`. Harmless at the store (an
    AND of one group is the group), and it retires the "no category,
    unrestricted principal" fast path."""
    with posture("open"):
        from identity.contracts.principals import OPEN_PRINCIPAL
        assert _visibility_filters(None, document_visibility(OPEN_PRINCIPAL)) is not None
