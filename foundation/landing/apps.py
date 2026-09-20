"""The front door at `/` — see foundation/landing/README.md."""
from __future__ import annotations

from django.apps import AppConfig


class LandingConfig(AppConfig):
    """The landing page. Holds no models, registers no roles and owns no
    migration: it is a pure reading surface over the availability signal
    (`models.registry.context_processors.availability`), exactly as
    `foundation.setup` is a pure reading surface over the engine registry.

    Its own app, rather than a module inside `foundation.setup`, for the
    reason `agents.chat` is its own app: a surface with its own template
    directory and its own tests. `label` is set explicitly, like every
    other AppConfig here -- see
    `foundation/ops/tests/test_app_labels.py` for why that matters.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "foundation.landing"
    label = "landing"
