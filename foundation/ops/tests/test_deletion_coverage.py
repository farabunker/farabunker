"""A model that holds user content must be reachable by a deletion.

THIS GATE EXISTS SO A FEATURE THAT STARTS STORING CONTENT SOMEWHERE NEW
CANNOT SHIP WITHOUT JOINING THE DELETION REGISTRY. It fails on two
conditions, and only two:

  (a) a model in `_COVERED` whose ticket kind has NO registered
      retention handler, or whose registered handler does not resolve --
      which catches a handler deleted, renamed, or dropped from an
      `AppConfig.ready()`;

  (b) a model carrying the `owner_kind`/`owner_key` PAIR that is in
      neither `_COVERED` nor `_EXEMPT`. The pair is the
      marker because `identity.access.owner_fields` is the ONE
      definition of how ownership is stamped in this codebase -- its own
      docstring records that it was moved there so every owned table in
      every column shares one definition -- and the walk is over
      `apps.get_models()`, so a new owned table is seen the day it is
      added, with no list to update first.

WHAT IT DOES NOT DO: it runs no purge, touches no database beyond model
introspection, and asserts nothing about whether a handler is CORRECT --
each column's own tests do that. It asserts that the WIRING EXISTS,
which is the failure mode that arrives silently.

AN EXEMPTION IS A SENTENCE SOMEBODY WRITES AND A REVIEWER READS. That is
the point of making it a list rather than a default.

IT LIVES IN `foundation/ops/tests/` beside `test_import_law.py` and
`test_column_boundaries.py`, because `foundation.ops` is the app that
already reaches across every column by design and this is a repo-wide
structural assertion.

Known, accepted residue: `agents.ToolInvocation` does not carry the
`owner_kind`/`owner_key` pair (it is stamped with `principal_kind`/
`principal_key` instead, a different question -- who a call was made
for, not who owns the row), so condition (b) above never reaches it.
Its content fields (`args`, `text`, `error`) are scrubbed through the
conversation purge for every invocation a conversation's turns point at
-- but a tool call whose job died between the invocation row being
written and its own tool `Turn` being written has no turn and no
conversation to reach it through, and there is no reaper for that row
today. It is named in `_EXEMPT` below anyway, in the same reasoned-list
shape as every genuinely owned exemption, purely so this fact is a
recorded decision rather than something a future reader has to
rediscover -- not because the gate's own walk would otherwise have
caught it.
"""
from __future__ import annotations

import pytest
from django.apps import apps
from django.utils.module_loading import import_string

from identity.access import owner_fields
from identity.contracts.cascades import retention_handlers
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.contracts.retention import KIND_CONVERSATION, KIND_LABELS, RETENTION_KINDS

# Every model carrying user content, mapped to the ticket kinds whose
# registered handlers reach it. Written from the content inventory in
# the design spec, section 2.1.
_COVERED: dict[str, tuple[str, ...]] = {
    "agents.Conversation": (KIND_CONVERSATION,),
    "agents.Turn": (KIND_CONVERSATION,),
    # A chat-scoped document dies with its conversation through the
    # attachment seam; a library document has its own kind in slice 2.
    "rag.Document": (KIND_CONVERSATION,),
    "rag.DocumentRow": (KIND_CONVERSATION,),
    "rag.DocumentAttachment": (KIND_CONVERSATION,),
    # The KIND-LEVEL check this gate runs passes today because `agents`
    # registered the conversation handler; the queue ROW itself is only
    # actually reached once the queue half of this feature lands (ADR
    # 0019 decision 7 records the residue).
    "jobs.InferenceJob": (KIND_CONVERSATION,),
}

# Owned tables a deletion does not reach, one reasoned line each -- and
# an exemption is a SENTENCE SOMEBODY WRITES AND A REVIEWER READS, which
# is the point of making it a list rather than a default.
_EXEMPT: dict[str, str] = {
    "agents.Agent": "an agent definition is a setting a person authored, "
                    "deleted from its own page, not content a deletion reaches",
    "agents.Flow": "a flow definition, for the same reason as Agent above",
    "agents.Workstream": "a container, deliberately not a ticket kind -- its "
                         "contents each have their own cliff (spec section 10.2)",
    "identity.DeletionTicket": "the deletion bookkeeping itself -- its owner "
                               "columns name the ITEM's owner, and the ticket is "
                               "destroyed by the purge it records",
    "rag.AskRecord": "gains a registered handler in Slice 2",
    "vision.GenerationJob": "gains a registered handler in Slice 2",
    # Not owner-marked (see the module docstring above) -- recorded here
    # anyway so the residue is a decision a reviewer has read, not one a
    # future reader has to rediscover.
    "agents.ToolInvocation": "its content fields are scrubbed through the "
                             "conversation purge for every invocation a "
                             "conversation's turns point at, but a tool call "
                             "whose job died before its own turn was written "
                             "has no turn and no conversation to reach it "
                             "through -- crash-orphaned rows are a known, "
                             "accepted residue with no reaper",
}


def _label(model) -> str:
    return f"{model._meta.app_label}.{model.__name__}"


def _all_model_labels() -> set[str]:
    """Every model Django knows about, labelled the same way `_COVERED`
    and `_EXEMPT` spell their own keys."""
    return {_label(model) for model in apps.get_models()}


def _owned_models() -> list[str]:
    """Every model carrying BOTH owner columns, discovered by walking the
    app registry -- so a new owned table is seen the day it is added."""
    marker = tuple(owner_fields(OPEN_PRINCIPAL))
    found = []
    for model in apps.get_models():
        names = {field.name for field in model._meta.get_fields()
                 if hasattr(field, "attname")}
        if all(column in names for column in marker):
            found.append(_label(model))
    return sorted(found)


def test_the_owner_marker_is_the_one_definition_of_ownership():
    """Anti-vacuous: if `owner_fields` ever stopped returning those two
    keys, `_owned_models` would silently match everything or nothing."""
    assert tuple(owner_fields(OPEN_PRINCIPAL)) == ("owner_kind", "owner_key")


def test_the_walk_actually_finds_the_owned_tables():
    """Anti-vacuous the other direction: a discovery that quietly stopped
    seeing a column would pass every assertion below."""
    owned = _owned_models()
    assert "agents.Conversation" in owned
    assert "rag.Document" in owned
    assert len(owned) >= 4


@pytest.mark.parametrize("label", sorted(_COVERED))
def test_every_covered_model_has_a_registered_handler_that_resolves(label):
    for kind in _COVERED[label]:
        handlers = retention_handlers(kind)
        assert handlers, (
            f"{label} is covered by the {kind!r} kind, but no retention handler "
            f"is registered for it. Register one from the owning column's "
            f"AppConfig.ready(), or move the model to _EXEMPT with a reason.")
        for spec in handlers:
            import_string(spec.handler)


def test_every_owned_model_is_covered_or_exempt():
    """CONDITION (b): a new table holding what a person typed must join
    the deletion registry, or say in one line why it does not."""
    unaccounted = sorted(set(_owned_models()) - set(_COVERED) - set(_EXEMPT))
    assert unaccounted == [], (
        "these owned models are in neither _COVERED nor _EXEMPT -- wire each "
        "into deletion with a retention handler, or add it to _EXEMPT with "
        "one line saying why a deletion never reaches it: "
        f"{unaccounted}")


def test_every_listed_model_still_exists():
    """`_COVERED` and `_EXEMPT` are hand-written labels, not a live
    query -- so renaming or removing a model does not by itself touch
    either dict, and every OTHER test above only walks forward, from
    `apps.get_models()` to the dicts, never back. A stale key (a model
    renamed in a migration, or dropped outright) would sit there
    unnoticed, silently describing a table that no longer exists, and
    the wiring or the exemption it once documented would no longer be
    checked by anything. This test walks backward instead, so a rename
    fails here rather than leaving a dead entry for nobody to revisit."""
    known = _all_model_labels()
    stale = sorted((set(_COVERED) | set(_EXEMPT)) - known)
    assert stale == [], (
        "these _COVERED/_EXEMPT keys no longer name a real model -- the "
        "model was renamed or removed; update or remove the entry: "
        f"{stale}")


def test_the_two_lists_do_not_overlap():
    assert not set(_COVERED) & set(_EXEMPT)


def test_every_exemption_carries_a_reason():
    for label, reason in _EXEMPT.items():
        assert reason.strip(), label


def test_the_gate_would_actually_catch_a_missing_handler():
    """The failure this exists for, exercised: a kind nothing has
    registered for answers an empty list."""
    assert retention_handlers("not-a-kind") == []


def test_labels_that_meet_in_one_purge_map_are_distinct():
    """A LABEL IS A KEY, NOT A CAPTION. One permanent delete returns one
    `{label: count}` map, and two sets of labels meet in it: the
    handlers registered for the item's own kind, and -- merged in -- the
    handlers of each child ticket that went with it. TWO RULES FOLLOW,
    and deliberately only two.

    WITHIN ONE KIND every handler label is distinct, because
    `run_retention` writes them into a single dict and the second would
    silently overwrite the first.

    AND NO HANDLER WEARS ANOTHER KIND'S PLAIN NAME. A merged line reads
    as the kind it destroyed -- "Generated image" -- so a handler
    answering for something else under that same word would put two
    different things on one line. A kind's own handler matching its own
    name is the intended case, not a clash.

    TWO DIFFERENT KINDS MAY SHARE A HANDLER LABEL (the queue's rows are
    "Queue jobs" for more than one kind): those are separate maps unless
    one kind is the other's child, and the case that can actually meet
    is the one the second rule covers.
    """
    for kind in RETENTION_KINDS:
        labels = [spec.label for spec in retention_handlers(kind)]
        assert sorted(labels) == sorted(set(labels)), (
            f"two handlers registered for {kind!r} share a label, so "
            f"run_retention would report one count for both")

    for kind in RETENTION_KINDS:
        for spec in retention_handlers(kind):
            worn = sorted(other for other, name in KIND_LABELS.items()
                          if name == spec.label and other != kind)
            assert worn == [], (
                f"the handler {spec.key!r} answers for {kind!r} but is "
                f"labelled {spec.label!r}, which is what a {worn} ticket is "
                f"called on the Deleted page")
