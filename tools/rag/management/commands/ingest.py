"""`python manage.py ingest <path> [--category NAME]` — ingest a single
file, or every supported file in a directory (recursively), into the RAG
index.

Category (ADR 0009): `--category NAME` explicitly assigns/overrides the
category for everything ingested by this call. Without it, ingesting a
directory derives each file's category from its top-level subfolder name
under `<path>` (files directly in `<path>` are Uncategorized); ingesting a
single file with no `--category` is Uncategorized."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from tools.rag.ingest import category_from_subfolder, ingest_path, supported_exts


class Command(BaseCommand):
    help = "Ingest a file, or all supported files under a directory, into the RAG index."

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="File or directory to ingest.")
        parser.add_argument(
            "--category",
            type=str,
            default=None,
            help=(
                "Category to assign (created if it doesn't exist). Overrides the "
                "default per-subfolder auto-categorization when ingesting a directory. "
                "Omit for Uncategorized (or per-subfolder auto-categorization)."
            ),
        )

    def handle(self, *args, **options):
        target = Path(options["path"])
        explicit_category = options["category"]
        if not target.exists():
            raise CommandError(f"Path does not exist: {target}")

        if target.is_dir():
            root = target.resolve()
            files = sorted(p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in supported_exts())
            if not files:
                self.stdout.write(self.style.WARNING(f"No supported files found under {target}"))
                return
        else:
            root = None
            files = [target]

        ok, failed = 0, 0
        for f in files:
            if explicit_category is not None:
                category = explicit_category
            elif root is not None:
                category = category_from_subfolder(f, root)
            else:
                category = None

            try:
                doc = ingest_path(str(f), category=category)
                self.stdout.write(self.style.SUCCESS(f"OK   {f} -> Document(id={doc.id}, type={doc.doc_type})"))
                ok += 1
            except Exception as exc:  # noqa: BLE001 - report and keep going
                self.stderr.write(self.style.ERROR(f"FAIL {f}: {exc}"))
                failed += 1

        self.stdout.write(f"Ingested {ok} file(s), {failed} failure(s).")
        if failed and not ok:
            raise CommandError("All files failed to ingest.")
