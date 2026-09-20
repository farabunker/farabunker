"""Every registered dotted-path string actually resolves (spec section 3.6.2).

The registration strings in this repo fail LAZILY: `register_role`/
`register_job_kind`/`register_tool` store the string without importing it,
and `models.contracts.jobkinds.resolve_dotted_path` (a thin wrapper over
Django's `import_string`, `jobkinds.py:273-288`) re-imports fresh at
execution time with no caching. A package move that misses one boots
cleanly, renders every page, and passes every test that mocks the
handler -- and then throws ImportError the first time a real background
job of that kind runs (or, for a `ToolSpec.runner`, the first time an
agent actually calls that tool), on a worker, in production.

The existing string-EQUALITY assertions (`tools/rag/tests/
test_apps.py:42,101-103,119-121`, `tools/vision/tests/test_apps.py`,
`models/registry/tests/test_jobkinds.py`) prove the string is what the
author typed. This module proves it is a string that RESOLVES. Both are
wanted: the first catches a typo, the second catches a stale path.

Lives in `models/registry/tests/` for the same reason `test_roles.py`,
`test_jobkinds.py`, `test_queue_seam.py`, `test_engines.py`, and
`test_catalog.py` do -- `models/contracts` is a pure leaf with no Django
app (and so no `tests/` package) of its own, so its coverage lives
beside a sibling `models/` app instead.

Reads whatever the run's own `FARABUNKER_FEATURES` registered and
overrides nothing: this file makes no HTTP request and calls no
`reverse()`, so the VISION-FLAG RULE does not apply to it, and asserting
against a fixed set of kinds would make it a second, drifting copy of
`test_apps.py`'s registration assertions.
"""
from __future__ import annotations

from agents.contracts.tools import all_tools
from models.contracts.jobkinds import all_job_kinds, resolve_dotted_path
from models.contracts.roles import all_roles

_JOB_KIND_PATH_FIELDS = ("planner", "handler", "summarizer", "on_terminal")


def _registered_paths() -> list[tuple[str, str]]:
    """`(where, dotted_path)` for every registered path string. `where` is
    an operator-readable locator so a failure names the registration, not
    just the path."""
    found: list[tuple[str, str]] = []
    for kind in all_job_kinds():
        for field in _JOB_KIND_PATH_FIELDS:
            path = getattr(kind, field)
            if path:
                found.append((f"JobKind({kind.key!r}).{field}", path))
    for role in all_roles():
        if role.rematerialize:
            found.append((f"RoleSpec({role.key!r}).rematerialize", role.rematerialize))
    for spec in all_tools():
        found.append((f"ToolSpec({spec.key!r}).runner", spec.runner))
    return found


def test_the_registries_are_populated_at_all():
    """Anti-vacuous-pass guard: an empty registry would make the test
    below green while proving nothing.

    Counted at P1's end with the `vision` flag on (the suite's only two
    supported gate states, `'vision,media'` and `'vision'`, both carry it --
    `FARABUNKER_FEATURES=''` is not a supported state to run the suite
    under, `docs/DEV.md`): 14 P0 registrations (`rag.ask` 3 + `rag.ingest` 4
    + `vision.generate` 3 + `rag.reencode` 3 + `rag.embed`'s one
    `rematerialize`) plus 6 P1 tool runners (`rag.search`, `rag.ask`,
    `rag.ingest`, `models.status`, `vision.operations`, `vision.generate`).

    P2 measured 20: 14 pre-existing registrations plus the six v1 tool
    runners. P2 adds `agent.turn`'s four (`planner`, `handler`,
    `summarizer`, `on_terminal`) and the two agent-as-tool runners
    (`agent.library`, `agent.illustrator`), for 26 -- measured directly
    with `manage.py shell` printing `len(_registered_paths())`, not just
    added up, and the arithmetic and the measurement agree. The floor is
    a FLOOR, not an equality -- a phase that adds a registration should
    not have to edit this line -- but it must move up when a phase adds
    six, or it stops proving the walk saw anything new."""
    paths = _registered_paths()
    assert len(paths) >= 26, paths


def test_every_registered_dotted_path_resolves():
    unresolved: dict[str, str] = {}
    for where, path in _registered_paths():
        try:
            resolve_dotted_path(path)
        except Exception as exc:  # noqa: BLE001 -- report every failure, not the first
            unresolved[where] = f"{path} -> {type(exc).__name__}: {exc}"
    assert unresolved == {}, (
        "registered dotted paths that do not resolve (a package moved and a "
        f"registration string did not follow it): {unresolved}"
    )
