# Chat image artifacts — design

**Date:** 2026-09-16
**Status:** Draft, awaiting owner review
**Columns touched:** `agents/contracts`, `agents/runtime`, `agents/chat`, `tools/rag`, `tools/vision`

## Owner's requirement (verbatim)

> "for the chat, I can add files, but I should also be able to copy an image from
> clipboard into the chat."
>
> "so the idea is I can provide it an image and I ask it to modify the image or
> use it to modify another image, and it should be able to pick it up."
>
> "effectively at a high level I should be able to paste images as if I were to
> attach them to the message, and those images should be read by the ai so it
> understand what it is and also be able to be reference in other tasks if needed
> through the use of other tools."

Three outcomes, in the owner's order:

1. **Paste is attach.** An image pasted from the clipboard into the chat
   composer behaves exactly like one chosen through "+ Add files".
2. **The assistant knows what the image is.** Every attached image gets a
   description the chat model can read without being told to search for it.
3. **The image is a first-class artifact.** The chat model can name an attached
   image and hand it to any tool that takes an image — today the image
   generation tool, for editing it or for using it as a reference while editing
   another image — the same way it already names an image it generated.

## What exists today (so the reader knows what is NOT being built)

- The composer is one multipart form shared by the start, thread and
  workstream surfaces; files ride a single `files` field and a placement
  chooser (`agents/chat/templates/chat/_composer.html`,
  `_attach_files.html`). Drag-and-drop and click-to-browse accumulate into
  that field (`_attach_dragdrop.html`). **There is no clipboard handler.**
- Attached files are staged by `agents.chat.service.start_turn` through the
  sanctioned `agents.attachments` seam into the RAG column
  (`tools.rag.services.stage_turn_attachments`), which creates a `Document`
  plus a `DocumentAttachment` tied to the conversation and turn, and enqueues
  the asynchronous ingest job. Image types are accepted when the `media`
  feature is on. Ingest runs images through the image-text-extraction role
  and stores the resulting text (description and any legible text) for
  retrieval.
- The carrying turn's prompt inlines MODEL-FREE extracted text only (owner
  addendum 2026-09-08, binding: turn building never calls a model and never
  waits on ingest). For an image that yields the honest "still processing"
  line. Later turns list each attachment by title and status and steer the
  model toward the retrieval tools.
- The artifact vocabulary (`agents/contracts/artifacts.py`) already defines
  three kinds — `output:<id>`, `input:<id>`, `document:<id>` — with a URL
  name for each and a per-kind labels registry. Chat rendering already turns
  `output`/`input` references into inline images and `document` references
  into titled file links.
- The image generation tool (`tools/vision/tools.py`) is already offered to
  the chat agent, narrowed per turn to the bound model's operations. Its
  `image` parameter maps onto an operation's first file input; every further
  file input (for example the edit operation's reference image) is exposed as
  its own text parameter. **Both accept only `output:<id>` and `input:<id>`.**
  The result text names each produced image as `output:<id>` so a follow-up
  edit can chain.
- Vision resolves those references through one function
  (`tools.vision.services.stored_input`), which refuses a malformed, deleted or
  invisible reference with one sentence, and copies the bytes into the job's
  own input folder at submit time.

**The single gap:** an attached image is a `document` artifact that the
vision column cannot read, and the prompt never gives the model its reference.

## Design

### 1. Artifact file seam (`agents/contracts/artifacts.py`)

Add a sibling to the existing labels registry:

- `register_artifact_file_resolver(kind, dotted_path)` — a column registers,
  at app start, the resolver for the kind it owns. Idempotent, replaces any
  earlier entry, same shape as `register_artifact_labels`.
- `file_resolver_for(kind) -> str | None` — the dotted path, or `None`.
- Contract of a resolver: `resolver(pk: int, principal) -> ArtifactFile`,
  where `ArtifactFile` is a small frozen dataclass in the same module:
  `path` (absolute filesystem path), `name` (a safe basename), `media_type`
  (a MIME string or `""`). A resolver raises `LookupError` for a row that
  does not exist, has no file on disk, **or is not visible to `principal`**
  — one exception, so an invisible row is indistinguishable from a missing
  one (the same rule vision applies to its own kinds).

The RAG column registers `tools.rag.access.artifact_file_for` for the
`document` kind in `tools/rag/apps.py::ready()`, beside its attachment
providers. Vision does not register a resolver in this change; its own kinds
keep their in-column resolution. (Registering them is a follow-up that costs
nothing and is not needed for the owner's outcomes.)

Why a registry and not an import: the column rules forbid `tools/vision`
from importing `tools/rag` and forbid `agents/` from importing either. A
dotted path registered by the owner and resolved lazily is the pattern every
other cross-column seam in this repository uses.

Why this is the long-term primitive: any future tool with a file input
resolves any artifact kind through this one door. Unifying the two file
stores later changes only the registered resolvers.

### 2. Vision accepts `document:<id>` (`tools/vision/services.py`, `tools.py`)

- `INPUT_REFERENCE_KINDS` grows to `("output", "input", "document")` and
  `_REFERENCE_SHAPE` names all three.
- `stored_input(reference, principal)` gains a `document` branch: it looks up
  `file_resolver_for("document")`; if none is registered the reference is
  refused with the standard sentence (the RAG column is not installed, so
  the reference cannot be live). Otherwise it calls the resolver, maps
  `LookupError` to the same `ValueError` text a dead `output`/`input`
  reference gets, and wraps the `ArtifactFile` into the upload-shaped
  `store.StoredFile` every other input becomes. A resolved file whose
  `media_type` is not `image/*` is refused with
  `"document:<id> is <media_type>, not an image."` — the MIME string
  itself, e.g. `"document:12 is application/pdf, not an image."`, and
  `"document:12 is a file of unknown type, not an image."` when the
  owning column records no media type. A friendly word ("a PDF") would
  need that column's own display vocabulary, which `tools/vision` may not
  import. Operator-facing text, a plain `ValueError`, so the tool loop
  turns it into a refusal the model can read and recover from — and
  byte-stable, echoing the BARE `document:<id>` form, never the caller's
  own string: a reference may carry a percent-encoded display title,
  which is somebody's uploaded filename, and no refusal repeats it.
- Bytes are copied into the job's input folder at submit time, exactly as
  for the other kinds. Detaching or deleting the document afterwards never
  affects a queued, running or finished job.
- `_IMAGE_PARAM` and `_file_ref_param` descriptions say "output:<id>,
  input:<id> or document:<id> (an image attached to this conversation)".
  The narrowed per-turn spec inherits that text unchanged.
- The `vision-operations` catalog text and the operations tool's cue text
  that mention the reference shape name the third kind too.

No change to queue payload shape, job rows, the gallery, or the HTML pages:
they accept the same reference strings and pass through the same resolver.

### 3. The model gets a handle and a caption

**RAG side (`tools/rag/access.py`, the registered attachments provider).**
Each attachment row gains three keys, read from the `Document` row and its
stored extraction, never from a model call:

- `is_image` — `media_type` starts with `image/`.
- `reference` — `mint_artifact("document", pk, title)` for every row (not
  only images: a prose attachment is equally referenceable by a future
  tool). Minted here so the agent column keeps its no-cross-import rule.
- `caption` — for an image whose ingest has finished: the first 600
  characters of the stored extraction text, whitespace-collapsed, with an
  ellipsis when cut; `""` otherwise. The cap is a fixed character budget,
  the same reasoning the carrying-turn caps use.

**Prompt side (`agents/runtime/prompt.py::_attachments_block`).** The
per-attachment line becomes, for an image:

    - photo.png (ready) — image, reference document:42 — <caption>

and, before ingest has described it:

    - photo.png (processing) — image, reference document:42 — description not ready yet

For a non-image the line is unchanged except for the appended
`reference document:<id>`. The steering sentence gains one clause, offered
only when the image generation tool is among this turn's granted tools:
"To edit an attached image, or use it as a reference while editing another,
pass its reference to the image tool." Availability is read from the same
`available` dict the block already uses for the retrieval tools, so the
model is never steered toward a tool it does not hold.

The carrying-turn inline block (`_carrying_attachments_block`) is
unchanged: model-free, never waits on ingest. The caption is read from text
ingest already wrote, so a later turn's prompt build stays model-free too.

**Amendment, 2026-09-17 (preview UAT + the agents/rag steward's packet
conditions).** Three things this section describes turned out to be wrong or
incomplete in preview, and the plan's Task 9 corrects them.

*What a caption is made of.* Image extraction was OCR-only, so a textless
image — a photo, an icon, a drawing — produced no caption at all and the
"processing" line above rendered forever; the chat model read it and told the
owner to keep waiting for work that had already finished. Extraction now asks
for a short description **as well as** any legible text (images only; a scanned
PDF's page loop is untouched), and the description is preferred for the
caption. A `caption_state` key (`pending` / `ready` / `empty` / `undescribed`)
says **why** a caption is blank, so "still working", "we looked and found
nothing" and "this was ingested before descriptions existed" stop sharing one
sentence. Existing images are never re-extracted automatically.

*Where the caption is rendered.* It is **content a model extracted from a file
somebody uploaded**, so it no longer sits unfenced on a bullet. The bullet
carries `— described below` and every available description lands in one
`foundation.fence.carrying_block` under the list — the same wrap inline
attachment text, third-party tool results and replayed foreign turns already
use. The carrying turn's own image is excluded from it entirely, since
`_carrying_attachments_block` already inlines that same text, fenced, in the
same prompt.

*Who gets one.* A new `readable` row key carries the same predicate
`/rag/documents/<id>/file/` enforces on the bytes. A chat-scoped image somebody
else attached is still named on the strip and in the prompt (its title and
status belong to anyone who can see the conversation — the round-12 ruling),
but its caption is blanked in the providing column and its chip renders no
thumbnail. The steering clause's wording also gains "exactly as written", after
a UAT model read a reference off this line and then invented
`input:<filename>` for the tool call.

### 4. Paste front door (`agents/chat/templates/chat/_attach_dragdrop.html`)

The existing inline script gains a `paste` listener on the composer card:

- If the clipboard carries one or more image items, each becomes a `File`
  named `pasted-image-<YYYYMMDD-HHMMSS>[-<n>].<ext>` (extension from the
  item's MIME type; `png` only when the item carries no type; any other
  type keeps its own subtype as the extension so the server allow-list
  decides) and is added through the same accumulator drag-and-drop uses,
  so it appears as a staged chip and rides the existing `files` field and
  placement chooser.
- Text on the clipboard is left to the browser's default paste, so a mixed
  paste keeps its text and stages its image.
- Pasting anywhere in the composer card (textarea included) counts; pasting
  outside it does nothing.
- The listener is only wired when the attach block is rendered
  (`may_attach_files`), so a principal without attach rights sees the
  browser's default behaviour, and the server's refusal path is unchanged
  for anyone who bypasses the UI.

No server change. The existing size cap, extension allow-list, tool-access
gate and staging rollback apply as they do for a chosen file. With the
`media` feature off, the server refuses the image at attach and the
existing inline refusal is what the user sees.

### 5. Rendering

- The per-turn attachment chip for a row with `is_image` shows a thumbnail
  served by the existing `rag-document-file` view, inside the chip, with
  the title as alt text. Chat-owned CSS only (`agents/chat/templates/chat/base.html`),
  consistent with the CSS-ownership gate.
- Tool-card rendering of edit results is unchanged.
- `agents/chat/rendering.py` is unchanged: a `document` artifact reference in
  a tool result still renders as a titled file link. (Rendering cannot tell
  an image document from a prose one by reference alone; the chip, which has
  the row, is the honest place for the thumbnail.)

### 6. Errors and limits

| Situation | Behaviour |
|---|---|
| Reference to a deleted, detached-and-deleted, or invisible document | Refused with the same sentence as a dead `output:` reference; the model reads it in the tool result and can recover next turn. |
| Reference to a non-image document passed as an image input | Refused by name: "document:12 is application/pdf, not an image." (or "… is a file of unknown type, not an image." with no media type recorded). Byte-stable; never carries the document's title or filename. |
| RAG column not installed | `document:` references are refused as unresolvable; nothing else changes. |
| Image still ingesting when the model references it | Works: the bytes exist at `source_path` synchronously from staging; only the caption is absent. |
| Caption longer than 600 characters | Cut with an ellipsis; the model can still call `rag__search` for the full text. |
| Paste with no image data | Browser default; nothing staged. |
| Paste while attach is not offered | Browser default; no handler wired. |
| Oversized or disallowed pasted image | Server refuses exactly as for a chosen file; existing inline error. |

Four more rows, added by the 2026-09-17 amendment to §3 (see it for the
reasoning behind each):

| Situation | Behaviour |
|---|---|
| Image extraction finished and found nothing to describe or transcribe | `caption_state="empty"`; the line reads "no text or description could be extracted from this image" — never "still processing", which is what preview UAT actually showed. |
| Image ingested before descriptions existed, whose OCR found nothing | `caption_state="undescribed"`; the line reads "no description recorded; re-ingest to describe it". Nothing is re-extracted automatically, but re-ingesting from the library IS the one case that reuse check does not take a hash match at face value (review fix round 2): a matching sidecar with no `"described"` marker is refused for reuse, so re-ingesting genuinely re-extracts and describes it. |
| A chat-scoped image somebody else attached, in a shared conversation | The row is still named with its title and status (round-12 ruling), `readable=False`, no caption, no thumbnail. The reference still renders, and resolving it refuses with the standard dead-reference sentence — accepted and documented, not pre-empted. |
| The carrying turn's own image, ingest already finished | Described once, not twice: `_carrying_attachments_block` inlines its text fenced, and the bullet drops its description clause entirely. |

### 7. Tests (same commits as the code)

- `agents/contracts/tests`: registry round-trip, `ArtifactFile` shape,
  unknown kind returns `None`.
- `tools/rag/tests`: `artifact_file_for` returns the stored file for a
  visible document; raises `LookupError` for missing, file-less and
  invisible rows; provider rows carry `is_image`, `reference`, `caption`
  (ready, processing, cap and ellipsis).
- `tools/vision/tests`: `parse_input_reference` accepts the third kind;
  `stored_input` resolves a document through a registered resolver; refuses
  when none is registered; maps `LookupError` to the standard sentence;
  refuses a non-image by name; `resolve_inputs` end-to-end with a document
  reference in both the `image` and `reference_image` slots; parameter
  descriptions name the third kind; an edit submission copies the document's
  bytes into the job's input folder.
- `agents/runtime/tests`: attachments block lines for image ready, image
  processing, non-image; steering clause present only when the image tool is
  granted.
- `agents/chat/tests`: composer renders the paste hook only with attach
  rights; a PNG posted under a `pasted-image-…` name stages and attaches like
  any image; chip renders a thumbnail for an image row and none for prose.
- Gates that must stay green: CSS ownership, column boundaries, import law,
  agent standards (no absolute paths in tracked files).

### 8. Docs (same commits)

- `agents/chat/README.md` "The attach door": paste, the reference line, the
  caption, the thumbnail.
- `tools/vision/README.md` "Feeding an image back in" and "Tools": the third
  reference kind and the resolver seam.
- `tools/rag/README.md` upload section: `artifact_file_for`, the new
  provider keys.
- `docs/EXTENDING.md`: how a column registers an artifact file resolver and
  how a tool with a file input consumes one.
- No model or vendor names in any prose (repository is going public).

### 9. Delivery and coordination

- Branch `worktree-chat-image-artifacts` off main `5562a29`, one implementer
  at a time, per-task review, whole-branch review, browser verification on a
  preview stack, then a PR for the owner to merge.
- Stewardship: the vision hunks go to the vision steward for clearance
  before merge; the agents and RAG hunks go to the peer who stewards those
  columns. Both verdicts are recorded in the review chain.
- Deploy: no migration, no new settings, no container recreate.

## Out of scope (named so nobody builds them by accident)

- Sending image pixels to a vision-capable chat model directly. The chat
  model reads the description; the edit runs in the image generation
  pipeline. True multimodal chat is a separate request.
- A unified artifact store replacing the RAG and vision file stores. The
  resolver seam is the step toward it; the migration is not this change.
- Registering vision's own kinds in the new registry. Zero-cost follow-up.
- Rendering a `document` artifact in a tool result as an inline image.
