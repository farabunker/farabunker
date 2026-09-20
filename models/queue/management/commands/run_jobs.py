"""`python manage.py run_jobs` -- run the execution queue's worker
(`models.queue.worker.Worker`) until interrupted (Ctrl+C / SIGTERM), or,
with `--once`, run exactly one tick and exit (tests/diagnostics). Thin on
purpose -- the same division `tools/rag`'s `ingest_watch` command draws
between itself and `tools.rag.ingest.watch_folder`: this command owns
none of the worker's actual behavior, it only constructs a `Worker` and
starts it.

`--once` semantics, precisely: it calls
`Worker.tick()` exactly ONE time then returns -- claim+admit+evict run
synchronously within that call, but any job `tick()` admits is only
SUBMITTED to the thread pool, not waited on; this command does NOT drain
(`Worker._drain_inflight`/`_shutdown` are never called at all -- there is
no signal handling, no grace period, nothing to drain for). Whether an
admitted job's handler actually finishes before this process exits depends
entirely on normal Python interpreter shutdown: `ThreadPoolExecutor`
registers an `atexit` hook that JOINS every pool thread before the
interpreter is allowed to exit, so a real `manage.py run_jobs --once`
invocation blocks until any job it just admitted actually finishes (which
IS the compose `worker` service's ordinary `run_forever()` path, just
without the loop) -- but a test calling `Worker.tick()`/this command
in-process (`django.core.management.call_command`) does NOT get that
implicit join, since the process itself never exits; such a test must wait
on the worker's own futures explicitly if it needs the job to have
finished. `--once` exists for exactly this kind of diagnostic/test use --
it is never what `compose.yaml`'s `worker` service runs."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from models.queue.worker import Worker


class Command(BaseCommand):
    help = "Run the execution queue's worker (Ctrl+C / SIGTERM to stop)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help=(
                "Run exactly one claim+admit+evict tick, submit whatever it admits "
                "to the pool, then return -- no drain, no signal handling. "
                "Diagnostics/tests only; never used by compose's worker service."
            ),
        )

    def handle(self, *args, **options):
        worker = Worker()
        if options["once"]:
            worker.tick()
            return

        self.stdout.write(f"Starting worker {worker.worker_id} (Ctrl+C to stop)...")
        worker.run_forever()
