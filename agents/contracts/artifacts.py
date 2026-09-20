"""The artifact-reference vocabulary.

An artifact is a JSON-safe REFERENCE STRING -- never a filesystem path,
never bytes. A tool that produces a file hands back `"output:12"` or
`"document:451"`; the chat template reverses the matching URL name and
serves it. No layer below the view ever learns a path on a disk the
caller cannot see, which is the same rule `tools/vision/services.py:742`
(`job_json`) already enforces for the vision page.

`"output"`/`"input"` are vision's own, minted by `tools.vision.services.
stage_upload` and by `templates/vision/_output_actions.html`;
`"document"` is a RAG source file. ALL THREE PARSE HERE, and since chat
image artifacts (2026-09-16) `tools.vision.services.parse_input_reference`
DELEGATES to `parse_artifact` below rather than keeping a second copy of
the splitting rule -- which is what makes the two agree by construction
rather than by vigilance, and is how vision learned to peel the title
suffix a `"document"` reference may carry. Vision still owns which kinds
are a legal generation INPUT (`INPUT_REFERENCE_KINDS`) and which of its
OWN tables a reference names; this module owns only the shape.

A `"document"` reference MAY carry a display TITLE too --
`"document:451:Attention%20Is%20All%20You%20Need"` (chat-polish P3.1,
R5) -- `urllib.parse.quote`d after a second `:`, so a renderer can show a
document's own name instead of a bare id with no second, cross-column
lookup (`agents/chat/rendering.py`'s own module docstring forbids this
package importing anything from `tools.*` at all). `"output"`/`"input"`
never carry one -- a THIRD colon on either is still refused outright,
unchanged from before this addition (`test_artifacts.py`'s own
`"output:12:extra"` case pins it) -- because nothing here ever needs to
show an image a name.

Pure: no Django. The URL NAMES below are string literals; reversing them
is the caller's job (a template, a view), not this module's.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote

ARTIFACT_KINDS = ("output", "input", "document")

# Only "document" ever carries a title suffix (see the module docstring).
_TITLED_KINDS = ("document",)

_SHAPE = "an artifact reference looks like output:<id>, input:<id>, or document:<id>"

# Which view serves each kind's bytes. `tools/vision/urls.py:23-24` and
# `tools/rag/urls.py:34`. URL NAMES, never dotted paths -- so a package
# move never touches this map.
_URL_NAMES = {
    "output": "vision-output-file",
    "input": "vision-input-file",
    "document": "rag-document-file",
}


def parse_artifact(reference: str) -> tuple[str, int]:
    """`(kind, pk)` for `reference`, or `ValueError` naming the shape.

    The same `partition(":")` + `isdecimal()` shape as vision's own parser,
    deliberately -- a reference that vision minted must parse identically
    here. This value arrives from a queue payload, a link, or a tool
    call, so it is untrusted input and a clear refusal beats a confusing
    failure later.

    A `"document"` reference's `raw_id` is only the FIRST segment after
    the kind -- `mint_artifact`'s own title suffix (R5) sits behind a
    SECOND `:`, peeled off here and ignored (`artifact_title`, below, is
    the one place that reads it). Every other kind keeps the original,
    stricter shape: anything past the id at all is still a refusal
    (`"output:12:extra"`, pinned in `test_artifacts.py`) -- an image
    never needs a name, and widening every kind at once would quietly
    accept a shape nothing has ever minted for them.
    """
    kind, _, rest = str(reference or "").partition(":")
    raw_id = rest.partition(":")[0] if kind in _TITLED_KINDS else rest
    if kind not in ARTIFACT_KINDS or not raw_id.isdecimal():
        raise ValueError(f"{reference!r} is not an artifact reference — {_SHAPE}.")
    return kind, int(raw_id)


def mint_artifact(kind: str, pk: int, title: str = "") -> str:
    """Build a reference string for `kind`/`pk`, carrying a display
    `title` alongside it when `kind` is one of `_TITLED_KINDS` and
    `title` is non-blank (chat-polish P3.1, R5).

    `tools/rag/tools.py::_document_artifacts` is the one caller today --
    minting the title HERE, at the moment the reference is created, is
    what lets `agents/chat/rendering.py` show a document's own name
    without a second lookup this module's own import-law ban would
    otherwise force back across the column boundary. `quote()` escapes
    the title (colons included), so `parse_artifact`'s second
    `partition(":")` always finds exactly the id/title boundary,
    whatever the title itself says.
    """
    if kind not in ARTIFACT_KINDS:
        raise ValueError(
            f"{kind!r} is not an artifact kind; must be one of {list(ARTIFACT_KINDS)}"
        )
    reference = f"{kind}:{pk}"
    if title and kind in _TITLED_KINDS:
        reference = f"{reference}:{quote(title)}"
    return reference


def artifact_title(reference: str) -> str:
    """The display title `mint_artifact` embedded in `reference`, or
    `""` when there is none -- every reference minted before R5, any
    kind outside `_TITLED_KINDS`, or a malformed reference
    (`parse_artifact` is the one place that raises; this one degrades
    to blank instead, since a missing title is never worse than a bare
    id the caller already has).
    """
    kind, _, rest = str(reference or "").partition(":")
    if kind not in _TITLED_KINDS:
        return ""
    _raw_id, sep, title = rest.partition(":")
    return unquote(title) if sep else ""


def artifact_url_name(kind: str) -> str:
    """The URL name that serves this artifact kind's bytes.

    The chat template reverses this; no layer below the view ever learns
    a filesystem path.
    """
    try:
        return _URL_NAMES[kind]
    except KeyError:
        raise ValueError(
            f"{kind!r} is not an artifact kind; must be one of {list(ARTIFACT_KINDS)}"
        ) from None


@dataclass(frozen=True)
class ArtifactLabels:
    """One column's answer to "what entitlements label the rows behind
    this artifact kind".

    `kind`     -- an entry of `ARTIFACT_KINDS`.
    `resolver` -- "package.module.function", with the signature
                  `(pks: frozenset[int]) -> frozenset[int]`, returning
                  the UNION of the entitlement ids labelling those rows.

    A DOTTED PATH, resolved at stamp time by `agents/runtime/taint.py`,
    never imported here -- the same mechanism `identity/contracts/
    cascades.py` and `models/contracts/jobkinds.py` already use, and for
    the same reason: `agents/` may not import `tools/` at all.

    A KIND WITH NO REGISTERED RESOLVER CONTRIBUTES NOTHING, SILENTLY.
    That is what makes owner decision 11's "the stamp generalises
    mechanically" true: when a generated image becomes a labelled thing,
    `tools/vision/apps.py` registers `ArtifactLabels("output", ...)` and
    every turn that returned one starts tainting, with NO change to any
    runtime module and NO migration. `agents/tests/test_taint.py` pins
    that with a fake resolver, so the claim is not a hope.
    """

    kind: str
    resolver: str

    def __post_init__(self) -> None:
        if self.kind not in ARTIFACT_KINDS:
            raise ValueError(
                f"{self.kind!r} is not an artifact kind; must be one of "
                f"{list(ARTIFACT_KINDS)}")
        if "." not in self.resolver:
            raise ValueError(
                f"ArtifactLabels({self.kind!r}).resolver must be a dotted path, "
                f"got {self.resolver!r}")


_ARTIFACT_LABELS: dict[str, ArtifactLabels] = {}


def register_artifact_labels(spec: ArtifactLabels) -> None:
    """Register `spec` under its `.kind`, replacing any existing entry.
    Idempotent, like every sibling registry in this codebase."""
    _ARTIFACT_LABELS[spec.kind] = spec


def labels_resolver_for(kind: str) -> str | None:
    """The dotted path for `kind`, or `None` when nothing is registered
    -- which is the common case and is not an error."""
    spec = _ARTIFACT_LABELS.get(kind)
    return spec.resolver if spec is not None else None


@dataclass(frozen=True)
class ArtifactFile:
    """One artifact kind's answer to "where are this row's bytes".

    `path`       -- an absolute filesystem path the RESOLVER's own column
                    owns. It never leaves the process: a caller copies
                    the bytes and hands a reference onward, the same rule
                    `job_json` already enforces for the vision page.
    `name`       -- a SAFE BASENAME, never a path. What the consuming
                    column records as the input's filename.
    `media_type` -- a MIME string, or `""` when the owning column does
                    not know. `""`, never `None`: every consumer asks
                    `media_type.startswith("image/")` on it, and a `None`
                    there is an AttributeError at the one moment a
                    refusal is being composed.

    A RESOLVER has the signature `(pk: int, principal) -> ArtifactFile`
    and raises `LookupError` -- ONE exception, never three -- for a row
    that does not exist, has no file on disk, OR is not visible to
    `principal`. That is deliberate and is the same rule `tools.vision.
    services._visible_referenced_row` already applies to its own kinds
    (IA-1): an invisible row must be indistinguishable from a missing
    one, or a member can derive from another principal's file by naming
    its id and reading which refusal comes back.
    """

    path: str
    name: str
    media_type: str = ""


_ARTIFACT_FILE_RESOLVERS: dict[str, str] = {}


def register_artifact_file_resolver(kind: str, dotted_path: str) -> None:
    """Register `dotted_path` as the resolver for `kind`, replacing any
    existing entry. Idempotent, like every sibling registry here.

    A DOTTED PATH, resolved at CALL time by whoever needs the bytes,
    never imported here -- the same mechanism `ArtifactLabels` above
    already uses, for the same reason: `tools/vision` may not import
    `tools/rag` (they are peer columns) and `agents/` may not import
    either. A tool with a file input asks this registry which function
    owns the kind it was handed.

    TWO POSITIONAL ARGUMENTS RATHER THAN A DATACLASS (unlike
    `ArtifactLabels`): there is nothing to carry beyond the kind and the
    path, and no second caller that needs to hold the pair as a value
    before registering it. `ArtifactLabels` is a dataclass because
    `agents/runtime/taint.py` reads `.resolver` off a stored spec; this
    registry's one reader wants a string.
    """
    if kind not in ARTIFACT_KINDS:
        raise ValueError(
            f"{kind!r} is not an artifact kind; must be one of {list(ARTIFACT_KINDS)}"
        )
    if "." not in dotted_path:
        raise ValueError(
            f"register_artifact_file_resolver({kind!r}) needs a dotted path, "
            f"got {dotted_path!r}"
        )
    _ARTIFACT_FILE_RESOLVERS[kind] = dotted_path


def file_resolver_for(kind: str) -> str | None:
    """The dotted path registered for `kind`, or `None` when nothing is
    -- which is the common case and is not an error (a box with the
    owning column uninstalled; `output`/`input`, whose bytes
    `tools/vision` still resolves in-column).

    PERMISSIVE WHERE THE WRITER IS STRICT: an unknown `kind` answers
    `None` rather than raising. The caller here is holding a string that
    came off a tool call and has already decided how to refuse a
    reference it cannot use; making the LOOKUP raise as well would give
    it two refusal paths for one condition.
    """
    return _ARTIFACT_FILE_RESOLVERS.get(kind)
