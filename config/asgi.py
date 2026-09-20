"""ASGI config for Farabunker.

Served via uvicorn (see Dockerfile / compose.yaml). ASGI is the primary
entrypoint because RAG query responses want to stream (ADR 0004 watch-outs).
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()

# S13: THE REFUSAL TRAVELS WITH THE APPLICATION, not only with the
# `migrate` step that happens to precede it in the container CMD. After
# `get_asgi_application()` -- which calls `django.setup()`, so the app
# registry and the database connection both exist by here.
#
# THIS READS-OR-CREATES A ROW, not merely reads: the checks reach
# `IdentitySettings.get_solo()`, which is `get_or_create(pk=1)`. On a
# migrated box that is a read; on a fresh one it writes the default
# settings row, exactly as the first request would have.
#
# AND IT IS SILENT WHEN THE DATABASE IS UNREACHABLE. `_accounts_on`
# catches `django.db.Error` and answers `None`, which every check treats
# as "not in violation" -- so a box that cannot reach Postgres at ASGI
# import boots rather than refusing. That is the right call (a box
# mid-migration is not misconfigured) and it is the honest bound on what
# this delivers: the refusal travels with the application, but only as
# far as the application can see.
#
# `refuse_if_boot_problems` is the ONE shared helper `config/wsgi.py`
# calls too, so the refusal is identical at either entry point -- see
# `identity/checks.py`'s docstring for why.
from identity.checks import refuse_if_boot_problems  # noqa: E402

# This DB access happens once, AT IMPORT TIME -- before uvicorn would
# fork any worker processes, if `--workers` were ever added to this
# image's CMD (round-3 task H22 builds on this function). A per-worker
# variant would need this call moved into a lifespan/startup hook
# instead of module import.
refuse_if_boot_problems()
