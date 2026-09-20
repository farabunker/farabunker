"""Re-run the chunk-label stamp for one document or for all of them.

IT EXISTS FOR THE INGEST-TIME FAILURE PATH. `restamp_document_chunks`
logs rather than raises when it is called from ingest, so a store that
was briefly unreachable leaves a document whose TABLES say "Finance" and
whose CHUNKS say nothing. This is the repair, and it is cheap: no
embedding, no engine, one `UPDATE` per document.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from tools.rag import index as rag_index
from tools.rag.labels import restamp_document_chunks
from tools.rag.models import Document


class Command(BaseCommand):
    help = "Re-stamp document entitlement labels onto their vector chunks."

    def add_arguments(self, parser):
        parser.add_argument("--document", type=int, default=None,
                            help="One document id. Omit for every document.")

    def handle(self, *args, **options):
        doc_id = options["document"]
        rows = (Document.objects.filter(pk=doc_id) if doc_id is not None
                else Document.objects.all())
        if doc_id is not None and not rows.exists():
            # ORCHESTRATOR RULING (T10 review finding 4b), overriding the
            # brief's original stderr+exit-0 text: an unknown document id
            # is the same user-error class `manage.py ingest` raises
            # `CommandError` for (a bad path), and the sibling command
            # does exactly that rather than printing to stderr and
            # returning 0. `relabel_chunks --document 9999 && echo ok`
            # must not print ok -- a caller scripting the repair around
            # this command needs a non-zero exit to notice the id was
            # wrong, not a message it may not be capturing.
            raise CommandError(f"There is no document {doc_id} on this box.")
        count = 0
        try:
            shape = rag_index.live_store_shape()   # C-09: once for the whole run
            for pk in rows.values_list("pk", flat=True):
                restamp_document_chunks(pk, raising=True, shape=shape)
                count += 1
        except Exception as exc:
            # The store being briefly unreachable is an honest,
            # operator-actionable message, not a raw psycopg traceback --
            # the same translation `manage.py ask` applies to a resolve()
            # failure (see that command's own comment).
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Re-stamped {count} document(s).")
