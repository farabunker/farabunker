"""
Django models for the RAG module's own metadata and tabular rows (ADR 0005
data model).

Note: chunk text + embeddings for *prose* documents are NOT modeled here.
They live in the `rag_chunks` table that LlamaIndex's PGVectorStore manages
directly (see tools/rag/index.py::get_vector_store). That table is created
and written by LlamaIndex, not Django migrations, so keep it out of the ORM.
"""
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from foundation.format import format_timecode

# The label used for a Document with no Category (category = None), both in
# the library UI and -- critically -- as the exact string ingest writes into
# every uncategorized chunk's `category` node metadata, which retrieval's
# category filter matches on (ADR 0009). One definition, imported everywhere
# it's needed, so the filter can never silently drift from a retyped literal.
UNCATEGORIZED = "Uncategorized"


class Category(models.Model):
    """A single-level document category (ADR 0009).

    Seeded with a handful of defaults (see the
    0004_seed_default_categories data migration) but fully user-editable:
    operators can rename, add, or remove categories. A `Document` with
    `category = None` is "Uncategorized" (a pseudo-group, not a row here) --
    deleting a Category therefore reassigns its documents to Uncategorized
    automatically via `on_delete=SET_NULL` on Document.category, rather than
    deleting or orphaning them.
    """

    name = models.CharField(max_length=255)  # was unique=True; see CI constraint below
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="uniq_category_name_ci"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class Document(models.Model):
    """One row per ingested source file (prose or tabular)."""

    class DocType(models.TextChoices):
        PROSE = "prose", "Prose"
        TABULAR = "tabular", "Tabular"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    title = models.CharField(max_length=512)
    # The *managed store* copy (ADR 0009) -- set by ingest.py via
    # tools.rag.store.store_file, e.g. `<DOCUMENTS_DIR>/<id>/<basename>`.
    # This is what document_file (views.py) and delete_document (services.py)
    # read/remove -- not the original upload/watch-folder location.
    source_path = models.CharField(max_length=1024)
    # The original upload/watch-folder path ingest was given (before it was
    # copied into the managed store). Dedup/re-ingest is keyed on this, not
    # on source_path, since source_path now points at our own copy. Null for
    # any Document created before this field existed.
    original_path = models.CharField(max_length=1024, null=True, blank=True, db_index=True)
    file_hash = models.CharField(max_length=64, db_index=True)
    doc_type = models.CharField(max_length=16, choices=DocType.choices)
    # For tabular docs: recorded column names/types so text-to-SQL and the
    # admin can introspect the shape without re-reading the source file.
    tabular_schema = models.JSONField(null=True, blank=True)
    # Null means "Uncategorized" (rendered as a pseudo-group in the library
    # UI, later wave) -- not represented by a Category row. SET_NULL so
    # deleting a Category reassigns its documents to Uncategorized instead
    # of deleting them or blocking the delete.
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="documents"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Lifecycle state (media-into-RAG plan, T1; ADR 0014): a media
    # document's ingest isn't one synchronous step -- transcription/
    # extraction runs on the execution queue (T2+) while the row already
    # exists. Every EXISTING Document, and every prose/tabular document
    # ingest still handles synchronously, is created and stays READY -- no
    # data migration needed, since READY means exactly "nothing pending",
    # true of every row that predates this column.
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.READY)
    # The engine's own words for a PROCESSING/FAILED status (never a
    # rewritten guess) -- same rationale as `JobStatus.error` in
    # `models.contracts.engines.base`. Blank while READY/PENDING.
    status_detail = models.TextField(blank=True, default="")
    # The source file's MIME type (e.g. "video/mp4", "audio/wav",
    # "application/pdf", "text/markdown") -- `tools.rag.ingest.
    # _media_type_for` sets this for EVERY staged document, prose/tabular
    # included, not only media ones (a stale earlier note here said "blank
    # for prose/tabular today"; that stopped being true once
    # `_MEDIA_TYPE_BY_EXT` grew an entry for every supported extension,
    # not only the AV/image ones T7/T8 added). Same column shape as
    # `tools.vision.models.JobInput.media_type` / `GeneratedOutput.
    # media_type`. Division of labor from `doc_type`:
    # `doc_type` is the SHAPE of the queryable content this Document
    # produced (prose chunks vs. DataFrame rows, and video/audio/image to
    # come), `media_type` is what the SOURCE FILE actually was.
    media_type = models.CharField(max_length=128, blank=True, default="")
    # Audio/video length in seconds, once known (T2+ probes the source);
    # None for prose/tabular and for media not yet probed.
    duration_seconds = models.FloatField(null=True, blank=True)
    # A SNAPSHOT of what produced this Document's text, not a live
    # reference -- the same reasoning as `AskRecord.connection_name`/
    # `model_id` above (see that model's own docstring): modules must not
    # FK into `models.registry.models`, and a snapshot is what keeps this truthful
    # after a connection is renamed or deleted. Shape:
    # `{"method": "transcription"|"extraction", "engine": ..., "model_id":
    # ..., "connection_name": ..., "produced_at": <ISO datetime>}`, or
    # `None` for a document that was never transcribed/extracted.
    extraction = models.JSONField(null=True, blank=True)
    # NEW. CONTAINMENT (owner decision 4). Null = the universal library,
    # which is every existing row and needs no back-fill. Set = this
    # document exists ONLY in that stream's corpus: absent from
    # `readable_documents`' default, from the library page's member
    # listing, from Ask and Search, and from every other stream,
    # REGARDLESS of entitlements (spec §8.1). A STRING reference, so
    # `tools/rag` never imports `agents.models` -- the same mechanism
    # `DocumentEntitlement.entitlement` uses for `identity.Entitlement`.
    workstream = models.ForeignKey("agents.Workstream", null=True, blank=True,
                                   on_delete=models.PROTECT,
                                   related_name="documents")

    class Origin(models.TextChoices):
        UPLOAD = "upload", "Uploaded"
        NOTES = "notes", "Consolidated notes"

    # NEW. What PUT this row here -- distinct from `extraction` (what
    # produced its TEXT) and `media_type` (what the source file was).
    # Every existing row is an upload, which is why the default is
    # `UPLOAD` and no data migration is needed (author decision 12).
    origin = models.CharField(max_length=16, choices=Origin.choices,
                              default=Origin.UPLOAD)

    # NEW. The conversation a `notes` document was distilled from -- the
    # key that makes re-consolidation an OVERWRITE rather than a second
    # note (spec §10.4). A UUID BY VALUE, never a FK: `tools/rag` may not
    # import `agents.models`, and this is the same by-value reference
    # `Turn.queue_job_id` already is in the other direction.
    notes_conversation_id = models.UUIDField(null=True, blank=True)

    class Scope(models.TextChoices):
        UNIVERSAL = "universal", "Universal"
        CONVERSATION = "conversation", "This chat only"

    # NEW (round 12, owner ruling verbatim: "if I submit a document but
    # have scope for chat, then it should only be used in that chat. If,
    # however, I change the scope to workstream, it should be made
    # available via the rag framework"). A SECOND axis from `workstream`
    # above, not a replacement for it: `workstream` answers "which
    # stream's corpus, if any, contains this document" (CONTAINMENT);
    # `scope` answers "is this document part of the RAG framework at
    # all, or does it live and die inside one chat" (EXCLUSIVITY).
    # `CONVERSATION` documents are NEVER contained (`workstream` stays
    # NULL for them -- `document_upload`'s own placement logic never
    # sets both) and are excluded from EVERY corpus -- universal,
    # contained, pinned, search, Ask, reencode, consolidation -- except
    # the ONE conversation named by their own (and only) `DocumentAttachment`
    # row (spec: round 12 amendment, `tools/rag/retrieval.py`'s own
    # "conversation leg" comment has the mechanism). `UNIVERSAL` --
    # every existing row, no data migration needed -- covers BOTH a
    # genuinely universal document and a stream-CONTAINED one; whether
    # it is reachable from one stream or from everywhere is still
    # `workstream`'s question alone.
    #
    # THE INVARIANT (round 12 brief): a CONVERSATION-scoped document
    # carries EXACTLY ONE `DocumentAttachment` row, for the ONE
    # conversation it was attached from. Not a database constraint --
    # `DocumentAttachment` must stay general (a workstream/universal
    # document may collect attachment rows from many conversations, each
    # just provenance) -- enforced in the write path instead
    # (`tools.rag.views._attach`'s own docstring has the guard).
    scope = models.CharField(max_length=16, choices=Scope.choices,
                             default=Scope.UNIVERSAL, db_index=True)

    # NEW (round 12). WHO uploaded this row, stamped once at CREATION for
    # every browser-upload door (`tools.rag.ingest.stage_document`, the
    # identical "applied at creation, never re-read on a re-stage" rule
    # `workstream` above already documents) -- the shape and the columns
    # are `identity.access.owner_fields(principal)`'s own two-column
    # convention (`tools.rag.models.AskRecord.owner_kind`/`owner_key`'s
    # docstring has the full rationale: one definition of ownership,
    # reused rather than a fourth column pair that agrees only by
    # convention). A SECOND WRITER, OUTSIDE `stage_document` (round-3
    # hardening H32/C-3, round 2): the notes-consolidation job
    # (`tools.rag.jobs.run_consolidate`) stamps these two columns
    # directly, from the distilled conversation's own owner, right after
    # staging its note -- `tools.rag.access.is_owner`'s own docstring
    # has the full account, including why `stage_document`'s own
    # creation-only rule above still holds for that call itself.
    # STAMPED FOR EVERY UPLOAD, not only conversation-scoped ones --
    # zero extra migration cost for a fact worth keeping regardless, but
    # its ONE READER today is `tools.rag.access.readable_documents`'
    # conversation-scope branch: a `CONVERSATION`-scoped document is
    # readable by its uploader and by `sees_all_content` ONLY --
    # entitlement labels never apply to it (labelling is corpus
    # machinery, and a chat-scoped document has no corpus). ROUND 12
    # REVIEW MINOR 6: "uploader" here is a DIRECT `Q(owner_kind=
    # principal.kind, owner_key=principal.key)` match, DELIBERATELY not
    # `identity.access.may_read_owned_row` -- that predicate ALSO admits
    # a `"service"`-owned row to any administrator (the shell-path
    # widening `identity.README.md` §5b documents), which this column
    # does not want: a chat-scoped document the watcher happened to
    # touch (`SERVICE_PRINCIPAL`, `owner_kind="service"`) should not
    # become every administrator's to read merely for being unattended,
    # the way a shell-made row elsewhere in this codebase is. Blank ("")
    # for every row created before this column existed, and for a
    # service-stamped one -- neither ever matches a real principal's own
    # `Q()` here, so both remain `sees_all_content`-only in EFFECT,
    # without borrowing that predicate's own wider rule to get there.
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        # THIS MODEL'S FIRST `Meta` EVER. It has had none since ADR 0005,
        # so this migration carries `AddIndex`, not `AlterModelOptions` -- indexes-only
        # Meta never generates that op; see the migration's own docstring -- rather than
        # only `AddField` -- named in spec §18 because a reader of the migration would
        # otherwise wonder where the index operation came from. No `ordering`: nothing
        # about the existing library listing changes, and adding a default order here
        # would silently re-sort four surfaces.
        indexes = [
            models.Index(fields=["workstream"], name="rag_document_ws"),
            models.Index(fields=["owner_kind", "owner_key"], name="rag_document_owner"),
        ]
        constraints = [
            # ONE NOTE PER CONVERSATION (owner decision 6), enforced by
            # the DATABASE and not only by the job that writes it: a
            # re-consolidation racing itself would otherwise produce two
            # notes and the stream page would show both.
            models.UniqueConstraint(fields=["notes_conversation_id"],
                                    condition=models.Q(notes_conversation_id__isnull=False),
                                    name="uniq_notes_per_conversation"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.title

    @property
    def extraction_summary(self) -> str:
        """Renders as "Transcribed by {model_id} · {date}" (or "Extracted
        by ...") for the library UI -- "" when `extraction` is unset.
        `date` is the `produced_at` snapshot's date portion, no
        time-of-day noise.

        T10 review MINOR 2: `model_id` reads via `.get("model_id") or ""`,
        not `.get("model_id", "")` -- the two only differ when the
        `extraction` dict has the KEY but its value is `None` (rather than
        the key being absent entirely), which the default-arg form doesn't
        catch (`.get(key, default)`'s default only fires for a MISSING
        key). Without the `or ""`, that snapshot rendered the literal
        string "Transcribed by None · 2024-01-01" -- `None` interpolated
        into an f-string reads as the four characters "None", not as
        "unknown"/blank.

        When `model_id` comes back blank (`None`, missing, or an empty
        string already), the "by {model_id} · {date}" tail is omitted
        entirely -- just `"Transcribed"`/`"Extracted"`/`"Processed"` --
        rather than rendering a naked "by" with nothing after it or a
        date with no model attached to it; there is no honest "by ... ·
        ..." to show without a model_id, so this doesn't invent one.

        T10 re-review MAJOR: `extraction` is operator/pipeline-controlled
        JSON, not a validated schema -- `model_id`/`produced_at` are only
        EXPECTED to be strings, not guaranteed to be. Both are read
        type-safely rather than trusted: `model_id` is used only when
        `isinstance(..., str)` (a non-str truthy value, e.g. an int/list/
        dict, renders as the plain verb instead of crashing `str()`-ing
        it into the f-string -- which itself wouldn't raise, but would
        render a nonsense value no operator ever typed), and `produced_at`
        is `.split("T", 1)`'d only when it's also a `str` (else `date` is
        `""`) -- a non-string `produced_at` (int/list/dict) used to raise
        `AttributeError` on `.split`, an uncaught 500 for `/rag/documents/`
        (Django templates re-raise property exceptions, they don't
        swallow them into an empty render).
        """
        if not self.extraction:
            return ""
        method = self.extraction.get("method", "")
        verb = {"transcription": "Transcribed", "extraction": "Extracted"}.get(method, "Processed")
        model_id = self.extraction.get("model_id")
        model_id = model_id if isinstance(model_id, str) else ""
        if not model_id:
            return verb
        produced_at = self.extraction.get("produced_at")
        date = produced_at.split("T", 1)[0] if isinstance(produced_at, str) else ""
        return f"{verb} by {model_id} · {date}"

    @property
    def source_kind(self) -> str:
        """One-word, human-facing badge for the library table's Source
        column (T10) -- "Video"/"Audio"/"Image"/"PDF" from `media_type`'s
        MIME prefix (`tools.rag.ingest._media_type_for` sets this for
        every staged document, media or not -- see that field's own
        comment), "Table" for a tabular Document (`doc_type` answers this
        precisely; `media_type` alone can't, since it's ambiguous between
        `text/csv` and the xlsx spreadsheet MIME type), else the generic
        "Document" (plain text/markdown/docx, or any future prose format
        this badge has no specific word for)."""
        if self.media_type.startswith("video/"):
            return "Video"
        if self.media_type.startswith("audio/"):
            return "Audio"
        if self.media_type.startswith("image/"):
            return "Image"
        if self.media_type == "application/pdf":
            return "PDF"
        if self.doc_type == self.DocType.TABULAR:
            return "Table"
        return "Document"

    @property
    def duration_display(self) -> str:
        """`foundation.format.format_timecode(duration_seconds)` for the library
        table (T10) -- "" for a document with no known duration (prose/
        tabular, or a video/audio Document not yet probed)."""
        return format_timecode(self.duration_seconds)

    @property
    def has_transcript(self) -> bool:
        """True when this Document has an `extract.json` sidecar on disk
        (T10) -- `tools.rag.media.transcribe_to_sidecar`/`extract_to_
        sidecar`'s own output -- gating the library table's Transcript
        link. A filesystem check, not a DB flag: `extraction` (the
        JSONField snapshot above) is stamped at the same moment the
        sidecar finishes, so the two should never disagree in practice,
        but this checks the sidecar directly since that's the exact file
        `tools.rag.views.document_transcript` itself reads.

        A STAT ONLY (`Path.is_file()`), never a read/parse of the
        sidecar's own bytes -- it answers "does a file exist here", not
        "is it well-formed" (`tools.rag.sidecar.read_sidecar`/
        `document_transcript` own that question; a stale/corrupt sidecar
        can make this `True` while the transcript page itself still 404s,
        which is the honest, non-overlapping division of labor between
        the two, not a bug for this property to close). This runs once
        PER ROW rendered by the library table (T10 review MAJOR 1) --
        acceptable because that table is paginated at `DOCUMENTS_PAGE_SIZE`
        (25) rows, so one page view is at most 25 filesystem stats, not an
        unbounded scan; this property would need a batched/cached rework
        before any future surface iterates every `Document` row at once
        rather than one page at a time.

        `tools.rag.store` (not `tools.rag.media`, which HTTP-request
        threads must never import -- see that module's own docstring) is
        the light, filesystem-only import this needs; imported lazily so a
        model import never pays for it unless this property is actually
        read."""
        from tools.rag import store

        return store.sidecar_path(self.id).is_file()

    @property
    def transcript_label(self) -> str:
        """W1 review MINOR 5: the honest label for `has_transcript`'s link
        (the library table) and its target page's own heading -- "Transcript"
        for a video/audio Document (a time-keyed transcription sidecar,
        `[mm:ss] text` segments), "Extracted pages" for everything else with
        a sidecar (a page-keyed vision-extraction sidecar, `{page, text}`
        segments -- PDF/image). `has_transcript` alone is a bare filesystem
        stat with no idea which shape the sidecar it found actually is; W1
        made that distinction matter for the first time -- an ordinary
        text-layer PDF with one stray blank/scanned page now gets a genuine
        one-page extraction sidecar despite being an ordinary document in
        every other sense, and calling that a "Transcript" would be a lie
        ("Extracted pages" isn't).

        Derived from `media_type` -- the same signal `source_kind` already
        reads -- rather than opening the sidecar itself: the mapping is 1:1
        by construction (`ingest.run_ingest_for` routes video/audio through
        `media.transcribe_to_sidecar`, image/PDF through `media.
        extract_to_sidecar`, and there is no third sidecar-writing path), so
        no extra file read is needed just to pick a label."""
        if self.media_type.startswith(("video/", "audio/")):
            return "Transcript"
        return "Extracted pages"


class DocumentRow(models.Model):
    """A single row of a tabular source (CSV/XLSX), kept as SQL — not
    embedded — so numeric/aggregate questions stay accurate (ADR 0005)."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="rows")
    row_index = models.IntegerField()
    data = models.JSONField()

    class Meta:
        indexes = [
            models.Index(fields=["document", "row_index"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.document_id}#{self.row_index}"


class RagSettings(models.Model):
    """A single, always-present row of operator-editable RAG module
    settings: `history_limit` (Ask history retention) and three per-
    document ingestion caps -- `max_upload_bytes`, `max_media_seconds`
    (media-into-RAG plan T1/T7), and `max_document_pages` (T8).

    Nothing else in the codebase is per-entity-table shaped for a single
    settings row, so this is a plain "one row, `pk=1`, get-or-create it"
    model. Always go through `get_solo()` rather than querying directly, so
    "the row doesn't exist yet" (a fresh install, before anyone has saved
    the settings form) is handled in one place. The singleton pattern avoids
    the need for a migration or fixture to ensure a seeded row exists.
    """

    HISTORY_LIMIT_DEFAULT = 100
    MAX_UPLOAD_BYTES_DEFAULT = 2 * 1024**3  # 2 GiB
    MAX_MEDIA_SECONDS_DEFAULT = 7200  # 2 hours
    MAX_DOCUMENT_PAGES_DEFAULT = 500  # T8 review minor 4
    # W4 (ADR 0014 §14): the two retrieval knobs below. `RETRIEVAL_TOP_K_MIN`/
    # `_MAX` and `RETRIEVAL_SCORE_FLOOR_MIN`/`_MAX` are the bounds
    # `tools.rag.views._retrieval_top_k_update`/
    # `_retrieval_score_floor_update` enforce -- not Django field
    # validators (this codebase's existing settings fields don't use them
    # either; every bound here is enforced by the view handler, matching
    # `max_document_pages`/`max_media_seconds`'s own real-Postgres-ceiling
    # checks) -- named here so the model and the two handlers can't drift on
    # what "in range" means.
    RETRIEVAL_TOP_K_DEFAULT = 5
    RETRIEVAL_TOP_K_MIN = 1
    RETRIEVAL_TOP_K_MAX = 50
    RETRIEVAL_SCORE_FLOOR_DEFAULT = 0.0
    RETRIEVAL_SCORE_FLOOR_MIN = 0.0
    RETRIEVAL_SCORE_FLOOR_MAX = 1.0

    # How many `AskRecord` rows to keep. Pruned on every successful Ask,
    # oldest rows first -- see `tools.rag.services.record_ask`. An
    # operational bound, like
    # `models.contracts.engines.ollama.DEFAULT_CONTEXT_WINDOW` -- not a model
    # name or a hidden default, just a cap on how much history is kept.
    history_limit = models.PositiveIntegerField(default=HISTORY_LIMIT_DEFAULT)
    # Largest source file ingest will accept (media-into-RAG plan, T1;
    # ADR 0014), in bytes. Same operational-bound register as
    # `history_limit` above -- a cap, not a model name or a hidden default.
    # Enforced (T4) at both doors: `tools.rag.views.document_upload`
    # rejects an oversized upload before writing a byte, and
    # `tools.rag.ingest._IngestEventHandler.poll_once`'s watch-folder
    # quiescence check refuses to enqueue an oversized inbox drop (leaving
    # it untouched, logged once) -- see both call sites for the exact
    # honest-message grammar.
    max_upload_bytes = models.BigIntegerField(default=MAX_UPLOAD_BYTES_DEFAULT)
    # Longest audio/video ingest will process, in seconds. Same
    # operational-bound register as `max_upload_bytes` above. Enforced (T7)
    # at both doors, the exact `max_document_pages` shape below:
    # `tools.rag.ingest._check_media_duration` (STAGE time, the primary
    # enforcement point -- rejects an over-cap video/audio file before a
    # `Document` row or a queue job ever exists for it) and `tools.rag.
    # media.transcribe_to_sidecar`'s own defensive re-check (RUN time --
    # guards the window between staging and a queued job actually running,
    # e.g. an operator lowering this cap in between). Rendered on the RAG
    # settings form as minutes (`tools.rag.views`'s `max_media_minutes`
    # context value / `_media_duration_update`'s own minutes<->seconds
    # round trip) -- the field itself stays seconds, matching every other
    # duration this module already stores that way.
    max_media_seconds = models.PositiveIntegerField(default=MAX_MEDIA_SECONDS_DEFAULT)
    # Most pages a single vision-extraction document (a scanned PDF) will
    # process, T8 review minor 4 -- the PAGE-count sibling of
    # `max_media_seconds`'s SECOND-count: same operational-bound register
    # (a cap on how much per-page vision-model work one document can
    # trigger, not a model name or a hidden default -- a real vision
    # extraction is one full LLM call PER PAGE, so an unbounded page count
    # is an unbounded number of model calls a single stray document could
    # trigger). Enforced at both doors, the exact `max_media_seconds`
    # shape: `tools.rag.ingest._check_document_pages` (STAGE time, the
    # primary enforcement point -- rejects an over-cap scanned PDF/image
    # before a `Document` row or a queue job ever exists for it) and
    # `tools.rag.media.extract_to_sidecar`'s own defensive re-check (RUN
    # time -- guards the window between staging and a queued job actually
    # running, e.g. an operator lowering this cap in between). An image is
    # ALWAYS exactly 1 page (`extract_to_sidecar`'s single-shot branch), so
    # this cap only ever meaningfully rejects a scanned PDF in practice --
    # image intake stays effectively uncapped by this field, by
    # construction, not by a special case.
    max_document_pages = models.PositiveIntegerField(default=MAX_DOCUMENT_PAGES_DEFAULT)
    # How many vector chunks `tools.rag.retrieval.answer_question` asks the
    # index for per question (W4, ADR 0014 §14) -- the operator-tunable
    # sibling of the fixed `VECTOR_SIMILARITY_TOP_K` this field replaces as
    # the value actually threaded into `similarity_top_k` (that module-level
    # constant is gone; this is now the one source of truth). Bounded
    # `RETRIEVAL_TOP_K_MIN`..`RETRIEVAL_TOP_K_MAX` by
    # `tools.rag.views._retrieval_top_k_update`, which additionally
    # rejects a value that can't fit the bound `rag.answer` model's own
    # context window (`top_k * tools.rag.ingest.CHUNK_TOKENS +
    # tools.rag.ingest.RESPONSE_RESERVE` -- see that handler's docstring
    # for the exact rule and its "unknown window, accept" fallback).
    retrieval_top_k = models.PositiveIntegerField(default=RETRIEVAL_TOP_K_DEFAULT)
    # The minimum vector-similarity score a retrieved chunk must clear to be
    # handed to synthesis/citations at all (W4, ADR 0014 §14) -- `0.0` (the
    # default) is OFF, matching every "no cap" convention this model already
    # uses (`max_upload_bytes`/`max_media_seconds`/`max_document_pages` have
    # no "off" value because zero would mean "reject everything"; a score
    # floor's natural off-state IS its minimum, so `0.0` needs no separate
    # sentinel). Enforced in `tools.rag.retrieval.answer_question`, BEFORE
    # synthesis and citation-building -- a node scoring below the floor never
    # reaches the LLM or a citation list. When every retrieved node scores
    # below a non-zero floor, `answer_question` skips the LLM call entirely
    # and returns an honest no-match answer with zero citations (never a
    # silent/misleading synthesis over noise, never a 500). Bounded
    # `RETRIEVAL_SCORE_FLOOR_MIN`..`RETRIEVAL_SCORE_FLOOR_MAX` by
    # `tools.rag.views._retrieval_score_floor_update` (also
    # rejecting NaN/inf, the same `math.isfinite` guard every other float
    # field on this model already gets).
    retrieval_score_floor = models.FloatField(default=RETRIEVAL_SCORE_FLOOR_DEFAULT)
    # W5 (ADR 0014 §18): the ONE operator toggle for hybrid keyword+vector
    # search -- `tools.rag.index.desired_store_shape()` reads this
    # directly, and `use_jsonb` (the pgvector chunk table's `metadata_`
    # column type) rides along with it rather than being its own knob: the
    # only reason `use_jsonb` is ever `True` is that a rebuild is already
    # happening for hybrid, so tying the two together means an install that
    # never touches this field can never be surprise-rebuilt by an
    # unrelated embeddings drift rematerialize (`tools.rag.services.
    # reencode_all`'s `shape_changed` check -- see that function's own
    # docstring). Flipping this does NOT change what's actually queried:
    # `tools.rag.index.get_vector_store()` always builds a store that
    # describes the LIVE chunk table, never this toggle directly, until a
    # re-encode (Inference -> rag.embed -> Re-encode) actually rebuilds the
    # table in the toggled shape (`tools.rag.index.live_store_shape`).
    # Default OFF -- ADR 0014 §18's "measured decision, not a reflex"
    # posture; flip the default only after the owner's measurement plan
    # (see the ADR) shows it strictly improves recall with no new wrong
    # citations.
    hybrid_search = models.BooleanField(default=False)

    @classmethod
    def get_solo(cls) -> "RagSettings":
        """The one `RagSettings` row (`pk=1`), creating it with defaults on
        first use. Never raises `DoesNotExist` -- callers never need their
        own get-or-create dance."""
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"history_limit": cls.HISTORY_LIMIT_DEFAULT})
        return obj

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"RagSettings(history_limit={self.history_limit})"


class AskRecord(models.Model):
    """One row per successfully-answered Ask-page question -- a historical
    record of what was asked, which model answered, and what it said back
    (owner-directed, verbatim: "we should make sure we preserve/log the
    inputs/model/outputs for the ask so we have a historical record and
    limit it to 100 items or something that can be shortened/expanded").

    SNAPSHOT, not a live reference: `connection_name` and `model_id` are
    plain strings copied at answer time (T6: from the `answered_by`
    machinery `tools.rag.jobs.run_ask` computes once a queued `rag.ask`
    job actually finishes -- see `tools.rag.services.record_ask`, its
    only caller) rather than a foreign key to
    `models.registry.models.ModelConnection`. Two reasons, both binding:
    a `tools/*` app must not import `models.registry.models` (import law
    rule 2 -- see `models/registry/bindings.py`, tools/rag's one sanctioned
    import surface), and a snapshot is what keeps history *truthful* after a
    connection is renamed or deleted -- the row still reads exactly what
    answered the question at the time, never a retroactively relabeled or
    vanished name.

    Only a SUCCEEDED job (`run_ask` returning normally) is recorded. A
    queued job's own pre-enqueue 503 ("no model reachable"), a FAILED job
    (the pre-run re-check lost the race and the model went away meanwhile),
    or a CANCELLED job writes nothing -- there's no answer/model/citations
    pair worth preserving, and recording the attempt itself is future scope.

    `category` is the literal string the question was asked with ("" for
    "all categories", matching `run_ask`'s own `category or ""`), not a
    `Category` FK -- same snapshot rationale as above; a later category
    rename/delete must not retroactively rewrite what a past question
    actually searched.

    `citations` mirrors what the Ask page itself shows per citation, not
    every field `tools.rag.retrieval._vector_citations` returns: a list
    of `{"file": <title>, "score": <float>}` dicts (see
    `tools.rag.services.record_ask`).

    Retention: pruned down to `RagSettings.get_solo().history_limit` rows
    (default 100) after every insert, oldest rows first --
    `tools.rag.services.record_ask`/`_prune_ask_records`.
    """

    question = models.TextField()
    category = models.CharField(max_length=255, blank=True, default="")
    connection_name = models.CharField(max_length=255)
    model_id = models.CharField(max_length=255)
    answer = models.TextField()
    citations = models.JSONField(default=list, blank=True)
    # WHO this row belongs to, stamped at create from
    # `identity.access.owner_fields(principal)`. Same two columns, same
    # widths and same blank default as `agents/models.py`'s -- one
    # definition of ownership across five tables in three columns, not
    # three that agree by convention.
    #
    # Existing rows get `""`, which means "written before accounts
    # existed" and is exactly what `manage.py adopt_open_rows` claims.
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner_kind", "owner_key"], name="rag_askrecord_owner"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"AskRecord({self.created_at:%Y-%m-%d %H:%M} · {self.connection_name})"


class DocumentEntitlement(models.Model):
    """A document's label -- beside the thing it protects.

    IT LIVES IN THIS COLUMN, not in `identity/`, because `identity/` may
    not import `tools/` (import-law rule 4) and because a label belongs
    beside the thing it protects. The foreign key to the entitlement is a
    STRING (`"identity.Entitlement"`), which is what keeps that true.

    ZERO OR MORE LABELS PER DOCUMENT, OR-MATCHED: a principal holding ANY
    of a document's entitlements may read it. "Must hold both" is a named
    non-goal -- the answer to it is a more specific entitlement, which is
    one row on a page instead of a filter algebra nobody can read.

    A DOCUMENT HAS NO OWNER COLUMN, and that is deliberate: its access is
    decided by these labels and the library posture, and by nothing else.
    An owner column would create a second, invisible rule ("the uploader
    can always see it") that no page displays and no administrator can
    revoke.
    """

    document = models.ForeignKey(Document, on_delete=models.CASCADE,
                                 related_name="entitlement_labels")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="document_labels")
    labelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    labelled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["document", "entitlement"],
                                    name="uniq_document_entitlement"),
        ]
        # The `document` side needs no explicit index -- its foreign key
        # already has one. This one serves `unlabel_all_for_entitlement`
        # and the delete cascade, which look up BY entitlement.
        indexes = [models.Index(fields=["entitlement"], name="rag_doclabel_entitlement")]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.document_id}@{self.entitlement_id}"


class WorkstreamPin(models.Model):
    """A UNIVERSAL document associated into a stream's working set (owner
    decision 4). An association, never a copy and never a move: the
    document stays universal, stays in the library, and stays readable
    everywhere its labels already allowed.

    IN `tools/rag`, NOT IN `agents`, because the `document` half is a real
    ForeignKey with real referential integrity and the `workstream` half
    is a string reference -- and the column that owns the FK owns the
    join table. The reverse (`agents.WorkstreamPin` with a by-value
    `document_id`) would leave a dangling pin behind every document
    delete, which `delete_document` would then have to clean from a
    column it may not import.

    A CONTAINED DOCUMENT MAY NEVER BE PINNED -- not into another stream,
    and not into its own. That rule is NOT a `CheckConstraint`: the
    condition lives on the joined `Document` row and Postgres does not
    accept a check constraint that references another table. It is
    enforced in exactly one place, `tools/rag/workstreams.py::
    pin_document`, which refuses by name, and pinned by a test that tries
    it (author decision 13). Recorded here rather than left as an
    absence, so a reader reaching for the constraint later finds the
    reason instead of a failing migration.
    """

    workstream = models.ForeignKey("agents.Workstream", on_delete=models.CASCADE,
                                   related_name="pins")
    document = models.ForeignKey(Document, on_delete=models.CASCADE,
                                 related_name="workstream_pins")
    # Provenance, on the same terms as `WorkstreamScopeEntitlement.
    # set_by`: written by `pin_document`, read by nothing this phase
    # ships, and mirroring the sibling tables rather than inventing a
    # shape.
    pinned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+")
    pinned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pinned_at"]
        constraints = [models.UniqueConstraint(fields=["workstream", "document"],
                                               name="uniq_workstream_pin")]
        indexes = [models.Index(fields=["document"], name="rag_wspin_document")]


class DocumentAttachment(models.Model):
    """One conversation's claim on one document (round 11, owner
    feedback: "i added a pdf to this chat and I don't see it as part of
    my chat entry" -- the document ingested fine; nothing recorded that
    it had been ATTACHED anywhere).

    ROUND 11 REVIEW I-5 REPLACES A FIRST CUT that stored this as a
    single `Document.attached_conversation_id` column. A path-keyed
    `Document` row is SHARED (`document_upload` re-points the SAME row
    at re-upload of the same basename -- `stage_document`'s own dedup),
    so one column meant a SECOND conversation attaching the identical
    file silently STOLE it from the first: the first conversation's
    strip and prompt lost the document with no trace. A JUNCTION TABLE
    has no such ceiling -- `unique_together` on (document, conversation)
    means a re-upload from a second conversation ADDS a second row
    rather than overwriting the first, and both conversations keep the
    document, which is what "attached to a conversation" honestly means
    for a row every conversation is free to reference.

    `document` IS A REAL ForeignKey (same reasoning as `WorkstreamPin`'s
    own docstring just above: the column that owns the FK owns the join
    table) -- `CASCADE`, so a document delete cleans its own attachment
    rows rather than leaving them dangling. `conversation_id` stays a
    UUID BY VALUE, the SAME shape/precedent `notes_conversation_id`
    (`Document`, above) already carries and for the identical reason:
    `tools/rag` may not import `agents.models` at all (import-law rule
    3), so there is no `agents.Conversation` to point a real FK at from
    here, unlike `workstream` above (a STRING FK to `"agents.Workstream"`
    resolves through Django's app registry with no Python import of
    `agents.models`, but `Conversation`'s own pk is a UUID with no
    comparable cross-app FK precedent taken in this codebase, and a
    by-value column is the shape every other conversation-provenance
    field here already uses).

    `tools.rag.services.stage_turn_attachments` (round 13, message-bound
    attachments -- see that function's own docstring) writes a row here
    for every file a turn's own submit carries, resolved through
    `agents.attachments.stage_turn_attachments`, the sanctioned registry
    seam (`agents/` may not import `tools/` at all) `agents.chat.
    service.start_turn` calls. RETIRED, round 13: `tools/rag/views.py::
    document_upload` no longer writes this table at all -- the chat
    door's own separate-POST path (a `conversation` field, three checks,
    `next`-coupling) is GONE along with it; every attachment now comes
    from the turn that carried the files, never from a second request a
    watcher-race or a page reload could land between. This paragraph
    used to describe that retired path; kept only as the historical
    reason `conversation_id` (below) predates `turn_id`.

    `get_or_create`, always, at every write site: a re-upload of bytes
    already attached to this SAME conversation must not raise
    `IntegrityError` on the unique constraint, and must not create a
    second, redundant row either.
    """

    document = models.ForeignKey(Document, on_delete=models.CASCADE,
                                 related_name="attachments")
    conversation_id = models.UUIDField(db_index=True)
    # ROUND 13 (message-bound attachments, migration rag/0020): WHICH
    # TURN carried this file, so a chip can render on the user's own
    # message bubble instead of a conversation-level strip (owner
    # feedback: "After submitting the document wasn't attached to that
    # message (at least not visually)"). NULLABLE, by-value, the SAME
    # shape/precedent `conversation_id` above already carries and for
    # the identical reason: `tools/rag` may not import `agents.models`
    # at all, so there is no `agents.Turn` to point a real FK at from
    # here. `null=True` for legacy data ONLY -- every row written before
    # this column existed (permanently `NULL`, never backfilled: no
    # request-scoped turn to attribute one to after the fact). STAYS
    # NULLABLE, schema unchanged, even though the consolidation wave's
    # own S10 ruling removed the renderer (`chat/_legacy_attachments.
    # html`, ROUND-13 REVIEW FIX I-5) that used to make such a row
    # visible again: no migration on `origin/main` ever let one exist
    # outside this branch's own dev/preview history, so a `NULL turn_id`
    # row is production-unreachable, not merely unrendered -- it groups
    # under the key `None` in `agents.attachments.attachments_for`'s own
    # dict and is read by nothing.
    #
    # ROUND-13 REVIEW FIX, MINOR 4: a row `_attach` finds ALREADY
    # EXISTING at write time (a re-upload of bytes already attached to
    # this SAME conversation) now MOVES `turn_id` to the newest turn
    # that re-carried it, rather than keeping whichever turn first did
    # -- this docstring used to call that a "deliberate, minor, named
    # simplification"; the review's own re-read of requirement 3 ("an
    # attachment is visually part of the message it was sent with")
    # disagreed, since a chip on a STALE turn is not that property, it
    # is the absence of it.
    turn_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["document", "conversation_id"],
                                               name="uniq_document_attachment")]
