"""The universal setup page — see foundation/setup/README.md."""
from __future__ import annotations

from django.apps import AppConfig


class SetupConfig(AppConfig):
    """Explains how to install every registered model engine and how a model
    reaches a role. Holds no models and registers no roles: it is a pure
    reading surface over `models.contracts.engines` and the role registry."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "foundation.setup"
    label = "setup"
