"""What a column must do when an entitlement is deleted -- a registry,
not a list.

Deleting an entitlement removes its grants (a `CASCADE` inside this
column), its document labels and its tool labels (two other columns),
and -- because a document that loses its last label becomes unlabelled
and follows the library posture -- it must RE-STAMP every affected
document's chunk metadata inside the same transaction.

`identity/` may not import `agents/` or `tools/` (import-law rule 4), so
the delete cannot name those functions. They register themselves from
each column's `AppConfig.ready()`, exactly as job kinds, roles, tools and
`OwnedRows` already do -- and for the same reason: a third labelled thing
later is a REGISTRATION, not an edit to a service that would otherwise
silently skip it.

Pure: `handler` is a DOTTED-PATH STRING, resolved at delete time by
`identity/cascades.py`, never imported here.
"""
from __future__ import annotations

from dataclasses import dataclass

from identity.contracts.retention import RETENTION_KINDS


@dataclass(frozen=True)
class EntitlementCascade:
    """One column's answer to "this entitlement is going away".

    `key`     -- stable identifier, e.g. "rag.document_labels".
    `label`   -- what the delete confirmation prints, e.g. "Document labels".
    `handler` -- "package.module.function", with the signature
                 `(entitlement_id: int, *, commit: bool) -> int`.

    ONE HANDLER, TWO MODES, deliberately -- rather than a counter and a
    detacher registered separately. `commit=False` COUNTS the rows this
    column would remove (the delete confirmation names the count first,
    spec section 12.1); `commit=True` removes them AND repairs whatever
    cache hung off them, and returns the same count. Two registrations
    would be two things to keep in agreement about what "affected" means.
    """

    key: str
    label: str
    handler: str

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("EntitlementCascade needs a non-blank key")
        if not self.label:
            raise ValueError(f"EntitlementCascade({self.key!r}) needs a non-blank label")
        if "." not in self.handler:
            raise ValueError(
                f"EntitlementCascade({self.key!r}).handler must be a dotted path, "
                f"got {self.handler!r}"
            )


_CASCADES: dict[str, EntitlementCascade] = {}


def register_entitlement_cascade(spec: EntitlementCascade) -> None:
    """Register `spec` under its `.key`, replacing any existing entry.
    Idempotent, like every sibling registry in this codebase."""
    _CASCADES[spec.key] = spec


def all_entitlement_cascades() -> list[EntitlementCascade]:
    """Every registered cascade, in registration order."""
    return list(_CASCADES.values())


# --- the retention namespace -------------------------------------------
# A SECOND DATACLASS AND A SECOND REGISTRY DICT, IN THIS SAME MODULE --
# not a second registry module. Deleting an entitlement and purging a
# deleted item are two questions with one shape: "this thing is going
# away; what does your column have to do about it". Same purity, same
# dotted-path discipline, same `AppConfig.ready()` self-registration,
# and one file a reader has to hold in their head instead of two.

# Two named bands, and only two. The runner runs every ORDER_ROWS
# handler before any ORDER_FILES handler, stable within a band by
# registration order.
#
# THIS IS THE FILESYSTEM-LAST RULE, and its whole purpose is the one
# thing a database transaction cannot undo: a filesystem delete has no
# rollback (`tools/rag/services.py::delete_document`'s own docstring),
# so a row handler that raised AFTER files were removed would leave a
# resurrected row pointing at bytes that are gone. Rows first means the
# common failure -- a database error -- aborts the purge with nothing on
# disk touched.
#
# A handler that must both READ an item's rows and REMOVE its bytes
# registers in the FILES band and does its own reads before its own
# writes, internally. That keeps this rule to one field with two values
# instead of a general dependency graph nothing else needs.
ORDER_ROWS = 100
ORDER_FILES = 200


@dataclass(frozen=True)
class RetentionHandler:
    """One column's answer to "this deleted item's content is going".

    `kind`    -- which ticket kind this answers for, from
                 `identity.contracts.retention.RETENTION_KINDS`.
    `key`     -- stable identifier, e.g. "agents.conversation".
    `label`   -- the key in the audit event's content-free `removed` map,
                 e.g. "Conversation and turns".
    `handler` -- "package.module.function", with the signature
                 `(key: str) -> int`.
    `order`   -- ORDER_ROWS (default) or ORDER_FILES.

    ONE HANDLER, ONE MODE -- deliberately unlike `EntitlementCascade`
    above, and the difference is worth stating where a reader meets it.
    That registry needs `commit=False` because deleting an entitlement
    is irreversible the instant it is confirmed: the count IS the
    confirmation. A deletion has a better confirmation than any number
    -- the Deleted page itself, where the item sits named and restorable
    for as many days as the policy says. So there is no count-only mode,
    no `commit` flag, and no per-item count anywhere a person looks; the
    returned integer has exactly one consumer, the content-free
    `removed={label: count}` detail on the `content.purged` event.

    EVERY HANDLER MUST BE IDEMPOTENT. Re-running one on a
    partially-purged item must COMPLETE rather than raise. That is not
    an assumption, it is this contract's obligation, and it is the whole
    recovery story for a purge that failed part-way: a FILES-band
    handler that raised after some bytes were gone leaves the rows
    standing, the ticket standing and the item still hidden, and the
    next sweep retries.
    """

    kind: str
    key: str
    label: str
    handler: str
    order: int = ORDER_ROWS

    def __post_init__(self) -> None:
        if self.kind not in RETENTION_KINDS:
            raise ValueError(
                f"{self.kind!r} is not a retention kind; must be one of "
                f"{list(RETENTION_KINDS)}")
        if not self.key:
            raise ValueError("RetentionHandler needs a non-blank key")
        if not self.label:
            raise ValueError(f"RetentionHandler({self.key!r}) needs a non-blank label")
        if "." not in self.handler:
            raise ValueError(
                f"RetentionHandler({self.key!r}).handler must be a dotted path, "
                f"got {self.handler!r}")


_RETENTION: dict[str, RetentionHandler] = {}


def register_retention_handler(spec: RetentionHandler) -> None:
    """Register `spec` under its `.key`, replacing any existing entry.
    Idempotent, like every sibling registry in this codebase."""
    _RETENTION[spec.key] = spec


def retention_handlers(kind: str) -> list[RetentionHandler]:
    """Every handler registered for `kind`, ROWS band before FILES band,
    stable within a band by registration order.

    A kind nothing has registered for answers `[]` -- which is not an
    error: it is what a box with a feature uninstalled looks like, and
    what every kind looks like before its column's slice lands.
    """
    matching = [spec for spec in _RETENTION.values() if spec.kind == kind]
    return sorted(matching, key=lambda spec: spec.order)
