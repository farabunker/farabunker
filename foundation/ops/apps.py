"""Platform operator tooling: `manage.py backup`/`restore` (W3, ADR 0014
§18). See `foundation/ops/backup.py` and `foundation/ops/restore.py`."""
from __future__ import annotations

from django.apps import AppConfig


class OpsConfig(AppConfig):
    """Holds no models, no migrations, no URLs -- a pure home for
    operator-facing management commands that belong to no single feature
    tool. `models/contracts/` and `foundation/format.py`/`files.py` are not
    installed apps, so they cannot host commands, and `foundation.setup`
    means "engine install guides" -- neither fits backup/restore, hence
    this small sibling app.

    Reads Postgres tables owned by `tools.rag`/`tools.vision` (row
    counts, active-work checks, the chunk-table size) via raw SQL against
    their known table names, and never imports `tools.rag`/`tools.
    vision` themselves -- the same column-privacy boundary the import law
    draws in the other direction (a `tools/*` or `agents/*` app may only
    reach into `models.registry.bindings`). See `backup.py`'s module
    docstring for the table names and the one duplicated constant this
    entails.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "foundation.ops"
    label = "ops"
