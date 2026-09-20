"""WSGI config for Farabunker. Prefer the ASGI entrypoint (config.asgi) for
streaming LLM responses; WSGI is kept for tooling that expects it --
including `compose.override.yaml`'s dev `runserver` command, which is a
WSGI server, not ASGI."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

# S13, same refusal as `config/asgi.py`: THE REFUSAL TRAVELS WITH THE
# APPLICATION, not only with the `migrate` step that happens to precede
# it in the container CMD, and not only with the ASGI entrypoint.
# `compose.override.yaml`'s dev `web` command
# (`python manage.py runserver`) imports THIS module, not
# `config.asgi` -- before this change, that path booted a misconfigured
# box (`DEBUG` on with accounts, the shipped `SECRET_KEY`, a wildcard or
# empty `ALLOWED_HOSTS`) with none of ASGI's refusal. One shared helper,
# `identity.checks.refuse_if_boot_problems`, so both entry points raise
# the identical `ImproperlyConfigured` rather than two hand-kept copies
# of the same check drifting apart -- see that function's docstring for
# the full rationale, including why it is silent when the database
# cannot be reached.
from identity.checks import refuse_if_boot_problems  # noqa: E402

# Runs once, AT IMPORT TIME, exactly like `config/asgi.py`'s own call.
refuse_if_boot_problems()
