"""Management command: ask a question of the RAG knowledge base from the CLI.

Usage:
    python manage.py ask "How do I ...?"
    python manage.py ask "What is the total of the amount column?"

Deliberately bypasses the execution queue (T5): this stays a synchronous,
self-contained diagnostic that works even when the worker process is down --
useful for exactly the situation an operator most needs it, debugging why
answering isn't working. The tradeoff, accepted on purpose: a CLI-run
question's memory use is invisible to the scheduler's admission accounting
(`models/queue/scheduler.py`) the same way it always has been for any
direct, non-queued call. A `--queue` flag that enqueues a `rag.ask` job
instead and polls it to completion is a recorded fast-follow, not built here.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError, CommandParser

from identity.contracts.principals import SERVICE_PRINCIPAL
from models.contracts.bindings import resolve
from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE
from tools.rag.access import document_visibility
from tools.rag.models import Document
from tools.rag.retrieval import answer_question


class Command(BaseCommand):
    help = "Ask a question of the RAG knowledge base and print the answer + citations."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("question", type=str, help="The question to ask.")

    def handle(self, *args, **options) -> None:
        question: str = options["question"]

        # Resolves both roles itself, explicitly -- there is no longer an
        # implicit role-path default `answer_question` falls back to (T5).
        # An unbound role is a clear, operator-actionable setup problem, not
        # a traceback -- translated to CommandError like every other
        # ValueError/RuntimeError this codebase's commands surface (see
        # models/registry/management/commands/reencode.py's identical
        # pattern).
        try:
            answer_resolved = resolve(RAG_ANSWER_ROLE)
            embed_resolved = resolve(RAG_EMBED_ROLE)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        # A service principal holds no entitlements -- grants attach to a
        # user or a group, and service-account tokens are a later phase.
        # T11 review: `visibility.sees_nothing` (a LOCKED library, where
        # even the unlabelled part is off limits) is a DISTINCT, stronger
        # case from merely "restricted" -- printing the unlabelled-only
        # line there would be dishonest, since nothing at all is actually
        # retrieved. Both branches say so rather than quietly returning a
        # thinner (or, in the locked case, an entirely empty) answer than
        # the same question gets on the page without explaining why.
        visibility = document_visibility(SERVICE_PRINCIPAL)
        if visibility.sees_nothing:
            self.stdout.write(
                "This box has accounts, and the command line holds no entitlements, "
                "and this library is locked, so this answer comes from no documents at all."
            )
        elif not visibility.unrestricted:
            self.stdout.write(
                "This box has accounts, and the command line holds no entitlements, "
                "so this answer comes from unlabelled documents only."
            )

        result = answer_question(
            question, answer_resolved=answer_resolved, embed_resolved=embed_resolved,
            visibility=visibility,
        )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Answer:"))
        self.stdout.write(result["answer"])
        self.stdout.write("")

        citations = result.get("citations") or []
        if not citations:
            self.stdout.write(self.style.WARNING("Citations: (none)"))
            return

        self.stdout.write(self.style.SUCCESS(f"Citations ({len(citations)}):"))
        for i, citation in enumerate(citations, start=1):
            title = citation.get("title") or "(untitled)"
            source = citation.get("source", "?")
            self.stdout.write(f"  [{i}] {title}  (source={source}, document_id={citation.get('document_id')})")
            path = self._citation_path(citation)
            if path:
                self.stdout.write(f"      path: {path}")
            if citation.get("score") is not None:
                self.stdout.write(f"      score: {citation['score']:.4f}")
            if citation.get("sql_query"):
                self.stdout.write(f"      sql: {citation['sql_query']}")
            if citation.get("snippet"):
                snippet = citation["snippet"].replace("\n", " ").strip()
                if len(snippet) > 160:
                    snippet = snippet[:157] + "..."
                self.stdout.write(f"      snippet: {snippet}")

    def _citation_path(self, citation: dict) -> str | None:
        """The host filesystem path for one citation, for the operator
        reading THIS terminal -- an authenticated, local reader who is
        exactly who this value is for (C-5, round-3 hardening).

        `tools.rag.retrieval._vector_citations` no longer puts `source_
        path` in the citation dict at all (it is a host path, and every
        OTHER caller of `answer_question` is a renderer this command is
        not -- see that function's own docstring). This reads it off
        `Document.source_path` by `document_id` instead, which is the row
        the citation is ABOUT.

        ONE SOURCE, NOT TWO (H36, round-3 hardening): a
        `citation.get("source_path")` fallback used to be checked first
        and win when present, to keep a hand-assembled citation dict
        working. Nothing in the pipeline has written that key since C-5,
        so the branch answered for no real caller -- and a branch that
        prefers a caller-supplied host path over the row's own is a
        second definition of a value with one definition.
        """
        document_id = citation.get("document_id")
        if not document_id:
            return None
        try:
            return Document.objects.get(pk=document_id).source_path
        except (Document.DoesNotExist, ValueError, TypeError):
            return None
