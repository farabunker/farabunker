"""What an entitlement REACHES, per kind -- a registry, not a list.

An entitlement is the same object seen from two directions. From a
resource you ask "which entitlements label this document/tool/agent",
which is what the per-column label pages answer. From the ENTITLEMENT
you ask "which resources carry me" -- the direction an operator works in
when they are administering fifty of them, and the direction
`identity/` could not answer at all without importing every column that
owns a join table.

It cannot import them (import-law rule 4), so each column REGISTERS one
AXIS from its own `AppConfig.ready()`, exactly as entitlement cascades,
job kinds, roles, tools and `OwnedRows` already do -- and for the same
reason: a seventh labelled kind later is a REGISTRATION, not an edit to
a view that would otherwise silently skip it.

Pure: every handler is a DOTTED-PATH STRING, resolved at request time by
`identity/axes.py`, never imported here. `identity/tests/test_purity.py`
imports this package with no `DJANGO_SETTINGS_MODULE` set at all.

TWO CAPABILITIES, ONE DESCRIPTOR. Every axis can COUNT itself across all
entitlements at once (`counts`, one aggregate query for the whole
entitlements list, whatever its length). An axis may ALSO be EDITABLE
(`rows`/`ids_for`/`set_for` together), which is what earns it a transfer
panel on one entitlement's own page. Document labels are counted but not
yet editable from this direction; the owner-managed workstream walls are
neither, and are deliberately not registered at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The editing trio: present together or absent together. Named once so
# `__post_init__` below and `identity/axes.py`'s own `editable` read
# cannot drift apart on WHICH fields make an axis editable.
_EDITING_FIELDS = ("rows", "ids_for", "set_for")


@dataclass(frozen=True)
class AxisRow:
    """One resource on one axis, as an operator picks it.

    `id`   -- the string this axis's own `set_for` takes back. A string
              even when the underlying key is an integer primary key,
              because it arrives from a form body either way and one
              vocabulary beats two (`agents.ToolEntitlement` is keyed by
              a tool-key STRING; agents, flows and model sets by pk).
    `name` -- what the operator reads.
    `note` -- OPTIONAL, "": a short qualifier rendered beside the name
              (the tools axis marks the tools no agent can be granted).
    """

    id: str
    name: str
    note: str = ""


@dataclass(frozen=True)
class EntitlementAxis:
    """One column's answer to "which of your rows carry this
    entitlement".

    `key`    -- stable identifier, e.g. "agents.tools".
    `label`  -- the PLURAL noun both projections print: a column heading
                on the entitlements list, a section heading on one
                entitlement's own page.
    `counts` -- "package.module.function", `() -> dict[int, int]`:
                `{entitlement_id: number of rows on this axis}` for
                EVERY entitlement, in ONE query. The entitlements list
                renders fifty rows off one call per axis, so this is the
                shape that keeps that page's query count flat -- a
                per-entitlement counter would be the sidebar N+1 again.
                An entitlement with no rows may be absent; the reader
                defaults it to zero.
    `order`  -- display order across ALL axes, low first, ties broken by
                registration order. EXPLICIT rather than inherited from
                `INSTALLED_APPS`: registration order is the right rule
                for a cascade (nobody reads a delete confirmation for
                its ordering) and the wrong one for a table heading,
                where moving an app in the settings file would silently
                reshuffle a page.
    `hint`   -- OPTIONAL, "": one sentence under the section heading,
                for an axis whose catalogue needs a word of warning.

    THE EDITING TRIO, all three or none of them:

    `rows`    -- `() -> list[AxisRow]`, the full catalogue of this
                 axis's resources in display order. The transfer panel's
                 two panes are this list split by `ids_for`, so a row
                 absent here can never be submitted -- which is what
                 makes the catalogue the validator too.
    `ids_for` -- `(entitlement_id: int) -> set[str]`, which of them
                 carry this entitlement. ONE query.
    `set_for` -- `(entitlement_id: int, *, add: set[str],
                 remove: set[str], actor, actor_user) -> dict[str, int]`,
                 returning `{"added": n, "removed": n}`.

                 ADD AND REMOVE, NOT A WHOLE WANTED SET, and the
                 difference is the point: two administrators editing
                 different rows of the same axis at the same time must
                 not clobber each other, which a submitted whole-set
                 write does by construction (the second save ships the
                 first one's stale pane back). It also keeps every write
                 going through the COLUMN'S OWN existing single writer
                 -- `agents.labels.set_tool_labels` and its siblings,
                 which diff per resource and write their own audit rows
                 -- rather than round a new write path past them.

                 A BATCH IS N TRANSACTIONS, NOT ONE, and that follows
                 from the line above: each of those writers opens its
                 own `transaction.atomic()` around one resource, so a
                 crash part-way through leaves a PARTIAL change with an
                 honest audit trail for the part that landed. An
                 implementation must not wrap the batch in a
                 transaction of its own -- doing that means bypassing
                 the writers that audit, which is the one thing this
                 seam exists to prevent. Recorded in ADR 0016's
                 2026-09-16 amendment.

    `link`   -- OPTIONAL: "package.module.function",
                `(entitlement_id: int) -> str`, answering the URL of
                THIS COLUMN'S OWN page narrowed to this entitlement.
                When given, the entitlement page renders a DOOR for this
                axis -- a heading, the `hint`, and that link.

                IT EXISTS SO `identity/` NAMES NO COLUMN'S ROUTE. An
                axis that is counted but not editable here (document
                labels) still wants somewhere to send an operator, and
                the first version of that door was a hand-written
                section in `identity/views.py` calling
                `reverse("rag-documents")` -- the one place in the whole
                column that named another column's route, and a direct
                contradiction of the claim this registry exists to make.
                A door is a REGISTRATION now, like everything else: a
                second counted-only kind adds one field here and no
                edit to any `identity/` file.

                THE COLUMN OWNS THE WORDS TOO. `label` heads the
                section and `hint` is the sentence under it, both
                supplied by the column that registered the axis, so
                `identity/` composes a door without knowing what is
                behind it.
    """

    key: str
    label: str
    counts: str
    order: int = 100
    hint: str = ""
    rows: str | None = field(default=None)
    ids_for: str | None = field(default=None)
    set_for: str | None = field(default=None)
    link: str | None = field(default=None)

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("EntitlementAxis needs a non-blank key")
        if not self.label:
            raise ValueError(f"EntitlementAxis({self.key!r}) needs a non-blank label")
        _require_dotted(self.key, "counts", self.counts)
        given = [name for name in _EDITING_FIELDS if getattr(self, name) is not None]
        if given and len(given) != len(_EDITING_FIELDS):
            # ALL THREE OR NONE. A `rows` with no `set_for` renders a
            # transfer panel whose buttons do nothing; a `set_for` with
            # no `rows` has nothing to validate a submitted id against,
            # which is the whole of this axis's input validation.
            raise ValueError(
                f"EntitlementAxis({self.key!r}) is editable only with all of "
                f"{', '.join(_EDITING_FIELDS)}; got {', '.join(given)}"
            )
        for name in given:
            _require_dotted(self.key, name, getattr(self, name))
        if self.link is not None:
            _require_dotted(self.key, "link", self.link)

    @property
    def has_door(self) -> bool:
        """True when this axis names its own column's page -- the one
        test the entitlement page makes before it offers a door."""
        return self.link is not None

    @property
    def editable(self) -> bool:
        """True when this axis carries the editing trio -- the one test
        both projections make before they offer a control."""
        return self.rows is not None


def _require_dotted(key: str, name: str, value: str) -> None:
    if not value or "." not in value:
        raise ValueError(
            f"EntitlementAxis({key!r}).{name} must be a dotted path, got {value!r}")


_AXES: dict[str, EntitlementAxis] = {}


def register_entitlement_axis(spec: EntitlementAxis) -> None:
    """Register `spec` under its `.key`, replacing any existing entry.
    Idempotent, like every sibling registry in this codebase -- an
    `AppConfig.ready()` that runs twice registers one axis, not two."""
    _AXES[spec.key] = spec


def all_entitlement_axes() -> list[EntitlementAxis]:
    """Every registered axis, in DISPLAY order: `order` ascending, ties
    in registration order (Python's sort is stable)."""
    return sorted(_AXES.values(), key=lambda spec: spec.order)


def entitlement_axis(key: str) -> EntitlementAxis | None:
    """The axis registered under `key`, or `None` -- what a POST
    validates a submitted `axis=` field against."""
    return _AXES.get(key)
