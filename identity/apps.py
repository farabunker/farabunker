"""The `identity/` column's Django app.

`label = "identity"` is set EXPLICITLY, as all eight existing app
configs do -- the property the 2026-08-25 regroup depends on, and the
one that makes `apps.get_model("identity", "User")` resolve from a data
migration, a management command and a test alike.
"""
from __future__ import annotations

from django.apps import AppConfig


class IdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "identity"
    label = "identity"

    def ready(self) -> None:
        """Register this column's six system checks.

        Local import so app import stays light (no DB, no HTTP at
        startup), matching every other AppConfig in this tree. Nothing
        here touches the database: the checks themselves read the
        settings row, and they do it when `manage.py check` runs, not
        now.
        """
        from django.core.checks import register

        from identity import checks

        register(checks.check_debug_is_off)
        register(checks.check_secret_key_is_not_the_default)
        register(checks.check_secure_cookies)
        register(checks.check_open_box_with_existing_users)
        register(checks.check_open_box_is_anonymous_administrator)
        register(checks.check_allowed_hosts_is_not_a_wildcard)
