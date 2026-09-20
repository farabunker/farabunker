"""`python manage.py ingest_watch <path>` — watch a folder and ingest new or
changed files into the RAG index as they appear. Runs until interrupted."""
from django.core.management.base import BaseCommand, CommandError

from foundation.files import create_owner_only_dir
from tools.rag.ingest import watch_folder


class Command(BaseCommand):
    help = "Watch a folder and ingest new/changed files into the RAG index (Ctrl+C to stop)."

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="Directory to watch.")

    def handle(self, *args, **options):
        path = options["path"]
        self.stdout.write(f"Watching {path} for changes (Ctrl+C to stop)...")
        try:
            from pathlib import Path
            # `path` is an operator-named, likely pre-existing folder --
            # not this command's own leaf -- so a missing one is created
            # and tightened, but one that already exists keeps whatever
            # mode it had (H13 review round 1, finding 4's own rule).
            create_owner_only_dir(Path(path), only_if_created=True)
            watch_folder(path)
        except NotADirectoryError as exc:
            raise CommandError(str(exc)) from exc
