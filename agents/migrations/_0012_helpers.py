"""The data half of `0012_agent_box_wide`, importable by a test.

`identity/migrations/_0002_helpers.py` is the precedent: a `RunPython`
body that a unit test can call directly, so the thing that decides
whether landing day is byte-identical is proved by a test rather than by
reading a migration.
"""
from __future__ import annotations


def copy_resident_to_box_wide(apps, _schema_editor) -> None:
    """Every existing row's AUDIENCE becomes what its ORIGIN used to
    imply.

    Before this migration, `visible_agents`' third leg was
    `Q(resident=True)` -- the shipped defaults, visible to everybody as
    "the platform's own offer". After it, that leg reads `box_wide`. So
    every row that reached everybody through `resident` must reach
    exactly the same people through `box_wide`, or the migration would
    change who can see what, silently, on deploy. It does not.
    """
    Agent = apps.get_model("agents", "Agent")
    Agent.objects.filter(resident=True).update(box_wide=True)


def clear_box_wide(apps, _schema_editor) -> None:
    """Reverse. The column is dropped by the schema operation beside
    this one; clearing first keeps a partial rollback honest."""
    Agent = apps.get_model("agents", "Agent")
    Agent.objects.update(box_wide=False)
