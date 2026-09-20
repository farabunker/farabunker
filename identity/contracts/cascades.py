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
