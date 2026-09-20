"""Which tables carry owner columns -- a registry, not a list.

Five tables in three columns carry `owner_kind`/`owner_key`, and two
operations (adoption, section 10.4 of the spec; reassignment, section
10.5) must walk all of them. `identity/` may not import `agents` or
`tools` (import-law rule 4), so the command cannot name the models. They
register themselves from each column's `AppConfig.ready()`, exactly as
job kinds, roles and tools already do -- and for the same reason: a
sixth owned table later is a REGISTRATION, not an edit to a command that
would otherwise silently skip it.

Pure: `model` is an `app_label.ModelName` STRING, resolved at command
time by `django.apps.apps.get_model`, never imported.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OwnedRows:
    """One table carrying owner columns.

    `key`   -- stable identifier, e.g. "agents.conversation". It is what
               `manage.py reassign_owner --kind` takes, so it is
               operator-facing and must not change casually.
    `label` -- what the adoption command prints, e.g. "Conversations".
    `model` -- "app_label.ModelName", for `apps.get_model`.
    """

    key: str
    label: str
    model: str

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("OwnedRows needs a non-blank key")
        if not self.label:
            raise ValueError(f"OwnedRows({self.key!r}) needs a non-blank label")
        if not self.model:
            raise ValueError(f"OwnedRows({self.key!r}) needs a non-blank model")
        if "." not in self.model:
            raise ValueError(
                f"OwnedRows({self.key!r}).model must be 'app_label.ModelName', "
                f"got {self.model!r}"
            )


_OWNED: dict[str, OwnedRows] = {}


def register_owned_rows(spec: OwnedRows) -> None:
    """Register `spec` under its `.key`, replacing any existing entry.

    Idempotent, like every sibling registry in this codebase, so
    re-importing a module that registers at import time is safe.
    """
    _OWNED[spec.key] = spec


def all_owned_rows() -> list[OwnedRows]:
    """Every registered table, in registration order."""
    return list(_OWNED.values())
