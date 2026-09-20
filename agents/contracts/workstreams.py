"""The workstream vocabulary every column may import.

A RULE-1 PURE LEAF: no Django, no models, no settings. It carries two
things and nothing else — the frozen VALUE a turn's stream scope travels
as, and the REGISTRY through which a column that owns rows the stream
page must display announces itself.

WHY A REGISTRY AND NOT AN IMPORT. `agents/` may not import `tools/` at
all (import-law rule 3, swept by `foundation/ops/tests/test_import_law.
py::test_no_agents_module_imports_a_tools_package`), and the stream page
is `agents/chat` code that must display documents. So the page renders
whatever is REGISTERED, in registration order, through one include, and
a second panel later — generated images in a stream, say — is a
REGISTRATION, not an edit to a view that would otherwise silently skip
it. That sentence is copied from `identity/contracts/cascades.py`'s own
docstring because it is the same argument.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkstreamScope:
    """One turn's (or one page's) stream scope, as plain data.

    BUILT ONCE per turn or per request and THREADED DOWN; never
    re-derived inside a runner, which is the drift the single filter
    point exists to prevent (`tools.rag.access.DocumentVisibility`'s own
    docstring makes the same promise for the same reason).
    """

    workstream_id: int
    # Entitlement ids; empty = no narrowing. ALWAYS EMPTY when
    # `identity.access.accounts_on()` is False -- the wall is inert on an
    # open box and its rows are never read there (ruling A, spec §13).
    wall: frozenset[int]
    # "" = ask every time (owner decision 5). One of
    # `agents.models.Workstream.UploadPlacement`'s values otherwise.
    default_upload_placement: str
    # False for a share recipient (spec §12.4), so a page that renders
    # the upload form and the pin control asks ONE question rather than
    # re-deriving ownership on a surface that cannot see the row.
    may_upload: bool
    # ROUND 17 (owner: "a bool in settings to use all rag documents ...
    # default it on"). `agents.models.Workstream.include_universal`,
    # carried unchanged -- DEFAULT TRUE HERE TOO (and, like `pinned_
    # file_ids` below, a field WITH a default, so it has to sort after
    # every field above that has none), so every existing caller that
    # builds a `WorkstreamScope` by hand (every test file that
    # constructed one before this field existed, and any future one
    # with no opinion on it) keeps meaning "consult the universal
    # library" -- the identical safe-default contract `wall`'s own
    # empty-set default and `pinned_file_ids`' own empty-frozenset
    # default already keep. False drops the (universal ∩ wall) leg from
    # the corpus formula ENTIRELY, at the same two seams `wall` itself
    # narrows (`tools.rag.workstreams.stream_documents`, `tools.rag.
    # retrieval._visibility_filters`) -- never merely narrows it
    # further, and never touches the pinned/contained/conversation legs
    # beside it.
    include_universal: bool = True
    # THE LAST FIELD IS FILLED BY THE OTHER COLUMN. `agents/workstreams.
    # py` returns this value with `pinned_file_ids=frozenset()`; only
    # `tools/rag/workstreams.py::scope_with_pins` fills it, because the
    # pin table lives in `tools/rag` (author decision 6, spec §6.2).
    pinned_file_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class WorkstreamPanel:
    """One column's answer to "the stream page needs to show my rows".

    `key`      -- stable identifier, e.g. "rag.documents".
    `label`    -- the page's section heading, e.g. "Documents".
    `provider` -- "package.module.function", with the signature
                  `(principal, workstream_id) -> dict`, resolved at
                  RENDER time by `agents/workstreams.py` and never
                  imported here.
    `template` -- the template path that renders this panel's own
                  `data`, `{% include %}`d by the one wrapper.

    `template` IS WHAT MAKES THE GENERALISATION CLAIM TRUE (M12). Without
    it the wrapper has to branch on `panel.key`, a second registered
    panel renders as a heading and nothing else, and adding one DOES
    require an edit to the very view this registry exists to keep out of
    it -- the opposite of what the paragraph below promises. The template
    lives in the REGISTERING column's own template directory, so the
    markup for another column's rows is written by that column, which is
    the same reason its `provider` is.

    REGISTERED IN THE SAME COMMIT AS THE HANDLER, deliberately, for the
    reason `tools/rag/apps.py` already records about its cascade: the
    resolver uses `import_string` and never swallows, so a registration
    that landed before its module would make every stream page raise
    `ImportError`.
    """

    key: str
    label: str
    provider: str
    template: str

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("WorkstreamPanel needs a non-blank key")
        if not self.label:
            raise ValueError(f"WorkstreamPanel({self.key!r}) needs a non-blank label")
        if "." not in self.provider:
            raise ValueError(
                f"WorkstreamPanel({self.key!r}).provider must be a dotted path, "
                f"got {self.provider!r}"
            )
        if not self.template.endswith(".html"):
            raise ValueError(
                f"WorkstreamPanel({self.key!r}).template must be a template path, "
                f"got {self.template!r}"
            )


_PANELS: dict[str, WorkstreamPanel] = {}


def register_workstream_panel(spec: WorkstreamPanel) -> None:
    """Register `spec` under its `.key`, replacing any existing entry.
    Idempotent, like every sibling registry in this codebase."""
    _PANELS[spec.key] = spec


def all_workstream_panels() -> list[WorkstreamPanel]:
    """Every registered panel, in registration order."""
    return list(_PANELS.values())
