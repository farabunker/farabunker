"""The chat-attachment vocabulary every column may import.

WHY A REGISTRY AND NOT AN IMPORT (round 11, owner feedback: an
attached PDF ingested fine but nothing told the model, or the
conversation page, that it existed). Identical reasoning to `agents.
contracts.workstreams`'s own module docstring, which this module is
deliberately a sibling of: `agents/` may not import `tools/` at all
(import-law rule 3, `foundation/ops/tests/test_import_law.py::
test_no_agents_module_imports_a_tools_package`), and BOTH the
conversation page's own attachments strip (`agents.chat.views.thread`)
and a turn's own prompt (`agents.runtime.prompt`) need to know what
`tools.rag`'s `Document` table has on file as attached to a
conversation. `tools/rag` REGISTERS a provider at import time (its own
`AppConfig.ready()`, the SAME commit as the handler -- the identical
rule `agents.contracts.workstreams.WorkstreamPanel`'s own docstring
states, for the identical reason: a registration that landed before
its module would make every caller raise `ImportError`); `agents.
attachments` resolves it at call time, never importing `tools.rag`
itself.

SIX SLOTS NOW (grew from two at round 11, to five at round 13's
message-bound attachments, to six at round 13's own review fix I-2),
EACH STILL A SINGLETON, NOT A DICT KEYED BY MANY (unlike
`WorkstreamPanel`/`EntitlementCascade`, this file's own siblings):
there is exactly ONE kind of each fact/action below, each uniquely
owned by `tools.rag` -- no second column will ever register a second
answer to any of them, so a registry built to hold many answers per
slot would be generality this need never asked for.

- `_PROVIDER` (round 11) -- "what is attached to this conversation".
- `_CLEANUP` (round 11 re-review, minor 4) -- "forget every attachment
  a DELETED conversation claimed" (`agents.visibility.
  delete_conversation`'s own cascade).
- `_UPLOADER` (round 13) -- "stage these newly-submitted files".
- `_DETACHER` (round 13) -- "remove one attachment, uploader-only".
- `_TEXT_PROVIDER` (round 13, owner addendum) -- "this document's own
  text, extracted model-free, for inlining into the carrying turn's
  own prompt".
- `_ORPHAN_CLEANUP` (round 13 review fix, I-2) -- "delete the managed-
  store bytes for these document ids" -- see `register_attachment_
  orphan_cleanup`'s own docstring for why this is NOT the same
  question `_CLEANUP` already answers.

Every slot resolved through the identical dotted-path-string shape,
for the identical reason (`agents/` may not import `tools/`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

_PROVIDER: str | None = None
_CLEANUP: str | None = None
_UPLOADER: str | None = None
_DETACHER: str | None = None
_TEXT_PROVIDER: str | None = None
_ORPHAN_CLEANUP: str | None = None


def _validated_dotted_path(dotted_path: str, *, setter: str) -> str:
    """`dotted_path` if it looks like one, else `ValueError` naming
    `setter` (C-26).

    The four-line body every `register_attachment_*` function below used
    to carry verbatim. `setter` is the CALLER'S OWN NAME, passed
    explicitly rather than derived from the stack: an operator reading the
    traceback of a bad registration needs the name of the function they
    called, and every one of the six messages said its own name before
    this was factored. Passing it keeps that true and keeps this helper
    free of frame inspection.
    """
    if "." not in dotted_path:
        raise ValueError(f"{setter} needs a dotted path, got {dotted_path!r}")
    return dotted_path


@dataclass(frozen=True)
class AttachmentUploadResult:
    """What `agents.attachments.stage_turn_attachments` (round 13,
    message-bound attachments) got back from the registered uploader --
    a PURE DATA shape, not a queryset or a `Document`, so both `agents/`
    (the caller, which may not import `tools/`) and `tools/rag`
    (the implementer, which returns one) can share it: this module is a
    rule-1 pure leaf (no ORM, no request), the same reason `Workstream
    Scope`'s own sibling contracts live here rather than in either
    column's own package.

    `ok=False` covers TWO different refusals that the view (`agents.
    chat.views.turns.turn_create`, through `agents.chat.service.
    start_turn`) tells apart by STATUS, never by string-matching
    `error`: a malformed/unrecognised `placement` (400 -- the same
    "server never silently guesses a placement" rule `tools.rag.views.
    document_upload` has always enforced) and "no uploader is
    registered at all, or it raised" (503-shaped, "attachments are not
    available right now" -- `tools.rag` uninstalled, or a broken
    provider, never this principal's fault). `status`, not a second
    boolean, keeps this shape as small as `DetachOutcome` below.

    `queued`/`unchanged`/`rejected`/`failed`/`tabular` are tuples of
    FILE NAMES, mirroring `tools.rag.views.document_upload`'s own
    pre-round-13 flash-message categories exactly -- the view turns
    each non-empty tuple into the identical sentence that view already
    wrote, so an operator's experience of "what happened to my files"
    is unchanged by WHERE the staging code now runs.

    `staged_document_ids` (round-13 REVIEW FIX, I-2): the ids of every
    `Document` row whose bytes were PHYSICALLY MOVED into the managed
    store during THIS call (`tools.rag.store.move_file`, reached
    through `tools.rag.ingest.stage_document`) -- never the "unchanged"
    files, which reuse an already-stored copy and moved nothing new.
    `agents.chat.service.start_turn` calls this function INSIDE its own
    `transaction.atomic()` block; a LATER step in that same block
    (`enqueue`, the placeholder `Turn` write) can still fail and roll
    every DB row this call wrote back out -- but a filesystem move is
    not a database write and no savepoint can undo it. This list is
    what lets `start_turn` name EXACTLY which managed-store directories
    to remove on that failure path (`agents.attachments.
    cleanup_staged_documents`), so a rolled-back turn leaves no orphan
    bytes behind it. Empty on the ok=False placement-refusal path
    (nothing was ever moved) and empty for every "unchanged"/"rejected"
    file within an otherwise-successful call.
    """

    ok: bool
    status: int = 200
    error: str = ""
    queued: int = 0
    unchanged: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    tabular: tuple[str, ...] = field(default_factory=tuple)
    staged_document_ids: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DetachOutcome:
    """What `agents.attachments.detach_attachment` (round 13, requirement
    E's "uploader-only ✕ on the chip") got back from the registered
    detacher. `status` is an HTTP number (404 -- no such attachment on
    this conversation, including a doc_id that does not exist at all;
    403 -- a real attachment this principal did not upload; 200 -- done)
    because the view renders it verbatim, the same reason `agents.chat.
    service.TurnStart.status` is a number rather than an enum.
    """

    ok: bool
    status: int
    message: str = ""


def register_attachment_provider(dotted_path: str) -> None:
    """`dotted_path` -- "package.module.function", resolved at CALL
    time (never here) by `agents.attachments.attached_documents`.
    Signature: `(principal, *, conversation_id, stream, settings_row) ->
    list[dict]`, each dict shaped `{"id": int, "title": str, "status":
    str, "status_detail": str, "in_corpus": bool, "chat_scoped": bool,
    "turn_id": int | None, "may_detach": bool, "is_image": bool,
    "reference": str, "caption": str, "caption_state": str}`. `stream`
    is an `agents.contracts.workstreams.WorkstreamScope | None`.

    `turn_id`/`may_detach` (round 13, message-bound attachments): which
    TURN's own bubble this attachment renders its chip on (`None` for a
    legacy row predating this column, or one `stage_turn_attachments`
    could not attribute to a turn -- see `DocumentAttachment.turn_id`'s
    own docstring for both cases), and whether the CALLING principal
    specifically is the one who may remove it (`tools.rag.access.
    may_detach_attachment`, computed here rather than by the template,
    since only this function already holds both `principal` and the
    `Document` row in the same scope). `agents.chat.rendering.turn_card`
    groups this list BY `turn_id` and renders each group as chips on
    that turn's own card; a `None`-turn row renders nowhere in the UI
    (the retired conversation-level strip is gone) but still counts
    toward the prompt block, which reads this SAME list unfiltered.
    `in_corpus` (round 11 re-review R-1): `False` for a document this
    principal may READ but that this conversation's own retrieval
    corpus does not admit (a walled stream's wall excluding an
    unlabelled universal attach, most commonly) -- returned, not
    dropped, so both surfaces can say so HONESTLY rather than the file
    simply vanishing with no explanation (R-1's own finding: silent
    absence reproduces the owner's original symptom). Neither caller
    re-filters, and neither surface can ever claim `rag__search`/
    `rag__ask` will return a document flagged `in_corpus=False`.

    `is_image`/`reference`/`caption` (chat image artifacts, 2026-09-16),
    all THREE read off the row and its already-stored extraction, never
    from a model call and never by waiting on ingest -- the owner
    addendum's own rule, which this seam is on the turn-building side of.
    `is_image` is the `media_type` prefix. `reference` is the artifact
    reference string (`agents.contracts.artifacts.mint_artifact`) for
    EVERY row, images included but not only them -- a prose attachment is
    equally nameable by a future tool with a file input, and the
    providing column mints it because `agents/` may not import that
    column to mint its own. `caption` is one bounded line describing an
    image whose extraction has FINISHED, and `""` for everything else
    (a non-image, an extraction still running, one that produced no
    text) -- the caller renders an honest "not ready yet" line for the
    blank, never a blank bullet.

    `caption_state` (preview UAT, 2026-09-17): WHY a `caption` is blank,
    one of `"pending"` (extraction has not finished), `"ready"` (there
    is a caption), `"empty"` (it finished and produced nothing),
    `"undescribed"` (it finished before this box described images at
    all, so nobody has actually looked), or `"n/a"` (not an image --
    there was never a description to wait for, and a consumer renders
    nothing at all for it). Several genuinely different
    facts used to arrive as the same `""`, and the prompt rendered all
    of them as "still processing" -- so a finished extraction read as
    still-running forever and the chat model told the operator to keep
    waiting. A row with no `caption_state` at all is a provider
    predating this key, and the caller derives it the way it always
    behaved (a caption means ready, no caption means pending).

    `readable` (packet finding A, 2026-09-17): whether THIS principal
    may read the row's BYTES -- the same `readable_documents` predicate
    `/rag/documents/<id>/file/` enforces. `True` for every ordinary
    attachment (they came out of that filter to begin with) and, for a
    CHAT-SCOPED one, `True` only for its uploader: the round-12 ruling
    puts such a document's TITLE and STATUS in front of anyone who can
    see the conversation while its BYTES stay with the uploader, and a
    CAPTION is content, not a title. The provider BLANKS `caption` for a
    `readable=False` row, so the text never crosses this seam at all;
    both consumers gate on the key as well -- the prompt renders an
    honest "attached by someone else" line instead of a description,
    and the conversation page's chip renders no thumbnail (the file
    route would 404 and hand that reader a broken-image glyph). A row
    with no `readable` key is a provider predating it and is treated as
    readable, exactly as before.

    Idempotent, like every sibling registry in this codebase: a second
    registration simply replaces the first, rather than raising or
    silently keeping the earlier one.
    """
    global _PROVIDER
    _PROVIDER = _validated_dotted_path(dotted_path, setter="register_attachment_provider")


def attachment_provider() -> str | None:
    """The registered provider's dotted path, or `None` if nothing has
    ever registered one (a box with `tools.rag` uninstalled, say)."""
    return _PROVIDER


def register_attachment_cleanup(dotted_path: str) -> None:
    """`dotted_path` -- "package.module.function", resolved at CALL
    time by `agents.attachments.delete_attachments_for`. Signature:
    `(conversation_id) -> int`, the count of `DocumentAttachment` rows
    removed -- called from `agents.visibility.delete_conversation`
    (round 11 re-review minor 4), in the SAME transaction the
    conversation's own `Share` rows and the conversation itself are
    deleted in, so a `DocumentAttachment` naming a deleted conversation
    never outlives it. Deliberately NARROWER than `identity.contracts.
    cascades.EntitlementCascade`'s `commit=False`/`commit=True` dry-run
    shape: a conversation delete is already the ACTOR's own confirmed
    action (the view's own POST, behind `may_manage_conversation`), not
    a bulk admin operation a preview count protects.

    Idempotent, the same reason `register_attachment_provider` above
    is.
    """
    global _CLEANUP
    _CLEANUP = _validated_dotted_path(dotted_path, setter="register_attachment_cleanup")


def attachment_cleanup() -> str | None:
    """The registered cleanup provider's dotted path, or `None` if
    nothing has ever registered one."""
    return _CLEANUP


def register_attachment_uploader(dotted_path: str) -> None:
    """`dotted_path` -- "package.module.function", resolved at CALL time
    by `agents.attachments.stage_turn_attachments` (round 13, requirement
    A/B: files ride the turn-create POST itself, never a second,
    separate upload request). Signature: `(principal, *, conversation_id,
    turn_id, files, workstream_id, placement, remember_placement, actor)
    -> AttachmentUploadResult` (above) -- `files` a list of Django
    `UploadedFile`s, exactly what `request.FILES.getlist("files")`
    already hands `tools.rag.views.document_upload`.

    Idempotent, the same reason `register_attachment_provider` above is.
    """
    global _UPLOADER
    _UPLOADER = _validated_dotted_path(dotted_path, setter="register_attachment_uploader")


def attachment_uploader() -> str | None:
    """The registered uploader's dotted path, or `None` if nothing has
    ever registered one."""
    return _UPLOADER


def register_attachment_detacher(dotted_path: str) -> None:
    """`dotted_path` -- "package.module.function", resolved at CALL time
    by `agents.attachments.detach_attachment` (round 13, requirement E's
    post-send remove). Signature: `(principal, *, conversation_id,
    doc_id) -> DetachOutcome` (above).

    Idempotent, the same reason `register_attachment_provider` above is.
    """
    global _DETACHER
    _DETACHER = _validated_dotted_path(dotted_path, setter="register_attachment_detacher")


def attachment_detacher() -> str | None:
    """The registered detacher's dotted path, or `None` if nothing has
    ever registered one."""
    return _DETACHER


# THE CHAT DOOR'S OWN PLACEMENT VOCABULARY (round 12 owner ruling;
# carried here unchanged by round 13's message-bound rewrite -- SCOPE
# semantics stay exactly what round 12 ruled, only the request that
# carries them moved). A PURE tuple-of-strings, not an import, so BOTH
# `agents.chat.service.start_turn` (which validates a POSTed `placement`
# BEFORE writing a single row -- the one field a server-side fallback
# would silently undo an owner's decision, per author decision 17) and
# `tools.rag.services.stage_turn_attachments` (which re-validates
# defensively -- never trust one layer alone) read the SAME two tuples,
# rather than each keeping its own copy that could drift. "conversation"
# ("This chat only") is the THIRD value a plain workstream upload never
# offers at all -- see `tools.rag.models.Document.Scope` for what it
# resolves to.
CHAT_PLACEMENTS_IN_STREAM = ("conversation", "contained", "universal")
CHAT_PLACEMENTS_LOOSE = ("conversation", "universal")

# ROUND-13 REVIEW FIX, NIT 1: the SAME two layers' own refusal SENTENCE
# for an unrecognised `placement` used to be typed twice -- once in
# `agents.chat.service._BAD_PLACEMENT` (the POST-time refusal, before a
# row is written), once inline in `tools.rag.services.stage_turn_
# attachments` (the defensive re-check just above's own comment names)
# -- identical wording, kept in sync only by whoever remembered to edit
# both. One string, here, beside the vocabulary it describes, for the
# identical "read the same thing, never keep two copies that could
# drift" reasoning `CHAT_PLACEMENTS_*` above already states.
BAD_PLACEMENT_MESSAGE = (
    "Choose where these files should live — this chat only, the universal library, or "
    "(inside a workstream) this workstream only."
)


def placement_is_valid(placement: str, *, in_stream: bool) -> bool:
    """Whether `placement` is one of the values `CHAT_PLACEMENTS_IN_
    STREAM`/`CHAT_PLACEMENTS_LOOSE` above actually offers for this turn
    (CONSOLIDATION WAVE, audit B S12).

    `BAD_PLACEMENT_MESSAGE` was already deduplicated into text, here, by
    ROUND-13 REVIEW FIX NIT 1 -- but the BRANCH it names ("if there's a
    resolved workstream use `CHAT_PLACEMENTS_IN_STREAM`, else `CHAT_
    PLACEMENTS_LOOSE`, then test membership") stayed typed out twice,
    in both columns: `agents.chat.service.start_turn` (validating a
    POSTed `placement` BEFORE writing a row) and `tools.rag.services.
    stage_turn_attachments` (re-validating defensively -- never trust
    one layer alone). Both keep their own DEFENSIVE call -- this
    function does not remove either check, it makes them check the
    SAME rule rather than two copies of it.
    """
    allowed = CHAT_PLACEMENTS_IN_STREAM if in_stream else CHAT_PLACEMENTS_LOOSE
    return placement in allowed


def register_attachment_text_provider(dotted_path: str) -> None:
    """`dotted_path` -- "package.module.function", resolved at CALL time
    by `agents.attachments.inline_attachment_text` (OWNER ADDENDUM,
    2026-09-08, binding: "when I add a file to the chat, it shouldn't
    have to do rag to find it... have that as a reference within the
    context"). Signature: `(doc_id) -> str` -- the document's OWN text,
    extracted MODEL-FREE (`tools.rag.readers`' prose/pdf-text paths,
    never a model call: this runs synchronously inside a turn, and a
    second model call just to read a file would be exactly the kind of
    invisible cost `agents.chat.service.start_turn`'s own consolidation
    docstring already refuses elsewhere) at TURN-BUILD time, not at
    ingest time -- the async `rag.ingest` job may not have finished
    (may not even have STARTED) by the moment the carrying turn's own
    prompt is built, and this must never wait on it (ruling 2: "NEVER
    block or delay the turn on ingestion"). Returns `""` for anything
    the model-free readers cannot handle inline (tabular, media, a
    scanned/textless PDF, a missing/unreadable file) -- the caller
    (`agents.runtime.prompt._carrying_attachments_block`) renders an
    honest "still processing" line for an empty result rather than
    guessing why extraction came back empty.

    Idempotent, the same reason `register_attachment_provider` above is.
    """
    global _TEXT_PROVIDER
    _TEXT_PROVIDER = _validated_dotted_path(dotted_path, setter="register_attachment_text_provider")


def attachment_text_provider() -> str | None:
    """The registered inline-text provider's dotted path, or `None` if
    nothing has ever registered one."""
    return _TEXT_PROVIDER


def register_attachment_orphan_cleanup(dotted_path: str) -> None:
    """`dotted_path` -- "package.module.function", resolved at CALL time
    by `agents.attachments.cleanup_staged_documents` (round-13 REVIEW
    FIX, I-2). Signature: `(document_ids) -> None`.

    THE SIXTH SLOT, NOT A REUSE OF `_CLEANUP`: that slot's own
    registered function answers a different question at a different
    time -- "forget every `DocumentAttachment` naming this CONVERSATION"
    (`agents.visibility.delete_conversation`'s own cascade, whole rows
    that DID commit) -- while this one answers "delete the managed-store
    BYTES for these DOCUMENT ids, which never finished committing at
    all" (`agents.chat.service.start_turn`'s own failure path, right
    after a later step in the SAME transaction rolled the rows back
    out from under a file move nothing can undo). Conflating the two
    would make one function's docstring lie about the other half of
    what it does.

    Idempotent, the same reason `register_attachment_provider` above
    is.
    """
    global _ORPHAN_CLEANUP
    _ORPHAN_CLEANUP = _validated_dotted_path(dotted_path, setter="register_attachment_orphan_cleanup")


def attachment_orphan_cleanup() -> str | None:
    """The registered orphan-file-cleanup provider's dotted path, or
    `None` if nothing has ever registered one."""
    return _ORPHAN_CLEANUP
