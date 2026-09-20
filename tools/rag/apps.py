from __future__ import annotations

from django.apps import AppConfig
from django.conf import settings


class RagConfig(AppConfig):
    """The offline RAG tool — see tools/rag/README.md and
    docs/adr/0005-rag-module-architecture.md."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "tools.rag"
    label = "rag"

    def ready(self) -> None:
        """Register RAG roles and job kinds at app startup.

        Imports `roles`/`jobkinds` locally to keep the import graph clean
        (no DB, no heavy imports at startup) -- registration only stores the
        dotted-path strings below, it never imports `tools.rag.jobs`
        itself (see that module's docstring: its `planner`/`handler`/
        `summarizer` are resolved lazily, only when a job is actually
        enqueued/run).
        """
        from models.contracts.jobkinds import JobKind, register_job_kind
        from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE, RoleSpec, register_role

        register_role(RoleSpec(RAG_ANSWER_ROLE, "RAG answer", "chat"))
        register_role(
            RoleSpec(
                RAG_EMBED_ROLE,
                "RAG embeddings",
                "embeddings",
                rematerialize="tools.rag.services.reencode_all",
            )
        )

        # Media-ingestion roles (media-into-RAG plan, T1; ADR 0014),
        # gated the same way `tools/vision/apps.py` gates `vision.generate`
        # (D9): only while "media" is enabled. `rematerialize=None` on both
        # -- re-transcription/re-extraction is a re-ingest of the SOURCE
        # FILE (a new job through the pipeline T2 adds), not a role
        # rematerialization like `RAG_EMBED_ROLE`'s reencode-in-place.
        if "media" in settings.FARABUNKER_FEATURES:
            from models.contracts.roles import RAG_EXTRACT_ROLE, RAG_TRANSCRIBE_ROLE

            register_role(RoleSpec(RAG_TRANSCRIBE_ROLE, "RAG transcription", "transcription"))
            register_role(RoleSpec(RAG_EXTRACT_ROLE, "RAG image text extraction", "vision"))

        # `on_terminal=None` (the default, left unset here): a cancelled or
        # twice-orphaned `rag.ask` job has no Document row (or any other
        # durable side-effect) to leave stranded -- `run_ask`'s own
        # `record_ask` history write only ever happens on a SUCCESSFUL
        # run, never partially, so there is nothing this job kind's
        # `on_terminal` could fix up that isn't already fine as-is. Same
        # reasoning covers `rag.embed`'s re-encode-in-place rematerialize
        # path (not a `JobKind` at all -- see `RAG_EMBED_ROLE`'s own
        # `rematerialize` registration above) and `vision.generate`
        # (`tools/vision/apps.py`): neither has a stranded-row problem
        # `rag.ingest`'s Document row does (T9.5 audit §5).
        register_job_kind(
            JobKind(
                key="rag.ask",
                label="Ask a question",
                planner="tools.rag.jobs.plan_ask",
                handler="tools.rag.jobs.run_ask",
                summarizer="tools.rag.jobs.summarize_ask",
                default_priority=None,
            )
        )

        # rag.ingest (media-into-RAG plan, T2; ADR 0014) -- registered
        # unconditionally (NOT behind the "media" feature flag above): plain
        # prose/tabular document ingestion moves onto the queue for every
        # deployment, whether or not media mediums are enabled.
        # `default_priority=200` (a HIGHER number than rag.ask's `None` ->
        # the queue-wide default of 100 -- lower number runs first, see
        # `models/queue/models.py`): ingest is background work that should
        # queue BEHIND an operator's own interactive questions, not compete
        # with them for the machine.
        # `on_terminal="tools.rag.jobs.on_ingest_terminal"` (T9.5 audit
        # §5, the stranded-Document fix): unlike `rag.ask` above, a
        # `rag.ingest` job DOES have a durable side-effect that predates
        # the job itself -- the Document row `stage_document` already
        # created at PENDING before this job was ever enqueued
        # (`tools.rag.ingest.enqueue_ingest`/`enqueue_reingest`). A job
        # cancelled while still queued, or permanently failed by a second
        # orphan sweep, never reaches `run_ingest_or_fail`'s own FAILED
        # write -- without this hook, that Document row is stuck at
        # PENDING/PROCESSING forever, invisible to Retry. See
        # `tools.rag.jobs.on_ingest_terminal`'s own docstring.
        register_job_kind(
            JobKind(
                key="rag.ingest",
                label="Ingest a document",
                planner="tools.rag.jobs.plan_ingest",
                handler="tools.rag.jobs.run_ingest",
                summarizer="tools.rag.jobs.summarize_ingest",
                default_priority=200,
                on_terminal="tools.rag.jobs.on_ingest_terminal",
            )
        )

        # `rag.consolidate` (spec §10.2). REGISTERED BY `tools/rag`, NOT
        # BY `agents` (author decision 18), and that is the placement
        # decision this job kind turns on: the handler needs the
        # transcript (an `agents` row), a model call, and a document
        # write plus a re-ingest. Only this column can reach both ends --
        # through `agents.workstreams`, the permitted direction -- while
        # `agents` may not reach documents at all.
        #
        # `on_terminal` for the same reason `rag.ingest`'s exists: the
        # action writes a durable row before the handler runs, and a job
        # cancelled while queued, or permanently orphaned, must clear it.
        register_job_kind(
            JobKind(
                key="rag.consolidate",
                label="Consolidate into stream notes",
                planner="tools.rag.jobs.plan_consolidate",
                handler="tools.rag.jobs.run_consolidate",
                summarizer="tools.rag.jobs.summarize_consolidate",
                default_priority=200,
                on_terminal="tools.rag.jobs.on_consolidate_terminal",
            )
        )

        # Tool registration (spec section 4.3). `tools/rag/tools.py`
        # imports nothing heavier than the pure contracts at module scope
        # -- every runner does its `retrieval`/`ingest`/`views` imports
        # lazily, inside the function body -- so importing it here keeps
        # this method's no-DB-no-heavy-imports promise (see the docstring
        # above). The runners themselves stay dotted-path STRINGS, never
        # live callables, exactly as the job kinds above do.
        from agents.contracts.tools import register_tool
        from tools.rag.tools import RAG_ASK, RAG_INGEST, RAG_SEARCH

        register_tool(RAG_SEARCH)
        register_tool(RAG_ASK)
        register_tool(RAG_INGEST)

        # Taint (spec §7.2): which entitlements label the documents a
        # turn actually returned. A DOTTED-PATH STRING, so
        # `agents/runtime/taint.py` never imports this column.
        from agents.contracts.artifacts import (
            ArtifactLabels, register_artifact_file_resolver, register_artifact_labels,
        )

        register_artifact_labels(
            ArtifactLabels("document", "tools.rag.labels.entitlement_ids_for"))

        # WHERE A `document:<id>`'s BYTES ARE (chat image artifacts,
        # 2026-09-16), through the SIBLING registry beside the labels one
        # above and for the identical reason: `tools/vision` may not
        # import `tools/rag` at all, so a tool with a file input asks the
        # registry which function owns the kind it was handed instead of
        # importing the column that owns it. REGISTERED IN THE SAME
        # COMMIT AS THE HANDLER, the same rule every registration in this
        # method already follows.
        register_artifact_file_resolver("document", "tools.rag.access.artifact_file_for")

        # The one owned table in this column, so `manage.py
        # adopt_open_rows` and `manage.py reassign_owner` can walk it
        # without `identity/` importing `tools` (import-law rule 4).
        from identity.contracts.ownership import OwnedRows, register_owned_rows

        register_owned_rows(OwnedRows("rag.askrecord", "Ask history", "rag.AskRecord"))

        # This column's answer to "an entitlement is being deleted", as a
        # DOTTED-PATH STRING so `identity/` can run it without importing
        # `tools/` (import-law rule 4). It does MORE than the agents
        # column's twin: a document that loses its last label becomes
        # unlabelled and follows the library posture, so the chunk
        # metadata cache has to be re-stamped in the same transaction --
        # see `tools.rag.labels.unlabel_all_for_entitlement`.
        #
        # REGISTERED IN THE SAME COMMIT AS THE HANDLER, deliberately.
        # `identity/cascades.py::_run` resolves every registered path with
        # `import_string` and never swallows, so a registration that
        # landed before its module would make every `delete_entitlement`
        # and every entitlement-page GET raise `ImportError`.
        from identity.contracts.cascades import (
            EntitlementCascade, register_entitlement_cascade,
        )

        register_entitlement_cascade(EntitlementCascade(
            key="rag.document_labels",
            label="Document labels",
            handler="tools.rag.labels.unlabel_all_for_entitlement",
        ))

        # THE OTHER DIRECTION ON THE SAME TABLE, COUNTED ONLY. The
        # entitlements list shows one reach column per registered axis,
        # and a Documents column that was simply missing would read as
        # "this entitlement labels no documents" rather than as "ask the
        # library". Registered WITHOUT the editing trio on purpose --
        # `tools/rag/axes.py`'s own docstring says why a transfer panel
        # is the wrong control for a library that scales to thousands.
        from identity.contracts.axes import EntitlementAxis, register_entitlement_axis

        from tools.rag import axes as rag_axes

        register_entitlement_axis(EntitlementAxis(
            key="rag.documents", label="Documents", order=50,
            counts="tools.rag.axes.document_counts",
            # THE DOOR, as a dotted path like everything else (fix round
            # 2, P2-I1). Counted-only means there is no editor on the
            # entitlement page, not that there is nowhere to go: this is
            # how that page offers the library WITHOUT `identity/`
            # naming `rag-documents`. The heading is `label`, the
            # sentence is `hint`, and both are this column's words.
            hint=rag_axes.DOCUMENTS_HINT,
            link="tools.rag.axes.document_link",
        ))

        # The stream page's Documents section (spec §4.2, Direction B).
        # `agents/` may not import `tools/` AT ALL, so the page renders
        # whatever is REGISTERED, in registration order, through one
        # include -- and a second panel later (generated images in a
        # stream, say) is a REGISTRATION, not an edit to a view that
        # would otherwise silently skip it.
        #
        # REGISTERED IN THE SAME COMMIT AS THE HANDLER, for the same
        # reason the cascade above is: the resolver uses `import_string`
        # and never swallows, so a registration that landed before its
        # module would make every stream page raise `ImportError`.
        from agents.contracts.workstreams import (
            WorkstreamPanel, register_workstream_panel,
        )

        register_workstream_panel(WorkstreamPanel(
            "rag.documents", "Documents", "tools.rag.workstreams.panel",
            "rag/panels/documents.html"))

        # Round 11 (owner feedback): the conversation page's own
        # attachments strip and the agent's own prompt steering block
        # both need "what has this conversation attached", and
        # `agents/` may not import `tools/` at all -- the identical
        # reason the panel registry just above exists. REGISTERED IN
        # THE SAME COMMIT AS THE HANDLER, for the identical reason
        # that registration's own comment gives.
        from agents.contracts.attachments import (
            register_attachment_cleanup, register_attachment_detacher,
            register_attachment_orphan_cleanup, register_attachment_provider,
            register_attachment_text_provider, register_attachment_uploader,
        )

        register_attachment_provider("tools.rag.access.attached_documents")
        # Round 11 re-review, minor 4: the delete-time twin of the read
        # provider just above -- `tools.rag` owns `DocumentAttachment`,
        # so `tools.rag` is where its rows get cleaned up when a
        # conversation is deleted, resolved through the identical seam.
        register_attachment_cleanup("tools.rag.access.delete_attachments")
        # Round 13 (message-bound attachments): the WRITE-shaped twins of
        # the two registrations above, resolved through the identical
        # seam for the identical reason (`agents/` may not import
        # `tools/` at all). `stage_turn_attachments` is `agents.chat.
        # service.start_turn`'s one caller; `detach_attachment` is
        # `agents.chat.views.turns.attachment_detach`'s one caller.
        register_attachment_uploader("tools.rag.services.stage_turn_attachments")
        register_attachment_detacher("tools.rag.access.detach_attachment")
        # OWNER ADDENDUM (2026-09-08): the carrying turn's own inline
        # text-injection seam -- see `agents.contracts.attachments.
        # register_attachment_text_provider`'s own docstring.
        register_attachment_text_provider("tools.rag.access.inline_text_for")
        # Round-13 REVIEW FIX I-2: the sixth slot -- see `agents.
        # contracts.attachments.register_attachment_orphan_cleanup`'s
        # own docstring for why this is not the same question `register_
        # attachment_cleanup` above already answers.
        register_attachment_orphan_cleanup("tools.rag.access.remove_staged_documents")
