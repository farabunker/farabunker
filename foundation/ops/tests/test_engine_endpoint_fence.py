"""The permanent gate on the engine-endpoint test fence.

THE HAZARD THIS EXISTS FOR, stated once. The execution queue's eviction
pass widens its swept endpoint set on any tick that admits an EXCLUSIVE
job (`models.queue.worker.Worker._eviction_targets`, spec §3.3e): it
unions the running jobs' own endpoints with `models.registry.bindings.
registered_endpoints()`, which reads `settings.INFERENCE_DEFAULT_ENDPOINTS`
-- REAL addresses, against REAL adapters registered at import time. A test
module that drives `Worker.tick()` (or the pass directly, or `run_jobs
--once`) with an exclusive admission therefore probes whatever engine is
listening on the developer's own machine and then calls `unload()` against
it: a warm model dropped mid-run, silently, with the suite still reporting
all green. That is not hypothetical -- it was proven against
`models/registry/tests/test_jobs.py`, whose `rag.reencode` planner declares
`exclusive=True`.

THE FENCE is `models.contracts.testing.hermetic_engine_endpoints`, an
autouse fixture that blanks the map. A test module gets it by IMPORTING IT
BY NAME, which is the house pattern for shared fixtures (`models/contracts/
testing.py`'s own docstring: a plain importable module, never a
`conftest.py` -- the repo forbids those anywhere).

WHY A GATE AND NOT A GLOBAL. A root `conftest.py` would fence the tree
invisibly and is forbidden regardless; an import that each module makes for
itself is visible in the file it protects, but is exactly the kind of thing
a future module forgets. This gate is the answer to "what stops the next
one forgetting": it walks the tracked test modules, finds every one that
drives the worker, and fails naming any that does not import the fence.

Lives beside `test_import_law.py`/`test_column_boundaries.py` for the same
reason those do -- pure file text (an AST parse plus a source scan), no
Django ORM, no database.
"""
from __future__ import annotations

import ast
import subprocess

from foundation.ops.tests._helpers import REPO_ROOT, _is_test_file

# The fence, by the two names a module has to name to get it.
FENCE_MODULE = "models.contracts.testing"
FENCE_NAME = "hermetic_engine_endpoints"

# What "drives the worker" means, as source text. Deliberately textual
# rather than semantic: a test that mentions any of these is a test that
# can reach the eviction pass, and the cost of a false positive is one
# unused import with a `noqa`, while the cost of a false negative is a
# live `unload()` against the developer's own engine.
#
# `.tick(` and `_evict_to_match_plan(` are the pass's two entry points;
# `Worker(` catches a module that builds one to call something else on it
# today and starts ticking tomorrow; `"run_jobs"` is the management
# command whose `--once` flag IS one tick (see `run_jobs.py`'s docstring).
DRIVERS = (".tick(", "_evict_to_match_plan(", "Worker(", '"run_jobs"')

# This module and the fence's own docstring both quote the driver strings
# in prose, which would otherwise make them match themselves.
EXEMPT = (
    "foundation/ops/tests/test_engine_endpoint_fence.py",
)


def _tracked_test_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", "*.py"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if _is_test_file(p)]


def _imports_the_fence(source: str) -> bool:
    """Whether `source` imports the fence BY NAME -- `from
    models.contracts.testing import hermetic_engine_endpoints`, in any
    grouping, with or without an alias.

    AST rather than a substring match, so a mention of the name in a
    docstring or a comment does not count as a fence. An `import
    models.contracts.testing` without the name would not bind an autouse
    fixture into the module's namespace and correctly does not count
    either."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 0:
            continue
        if node.module != FENCE_MODULE:
            continue
        if any(alias.name == FENCE_NAME for alias in node.names):
            return True
    return False


def test_every_test_module_that_drives_the_worker_imports_the_engine_fence():
    offenders: dict[str, list[str]] = {}
    for relative in _tracked_test_files():
        if relative in EXEMPT:
            continue
        source = (REPO_ROOT / relative).read_text(encoding="utf-8", errors="replace")
        drivers = [driver for driver in DRIVERS if driver in source]
        if not drivers:
            continue
        if not _imports_the_fence(source):
            offenders[relative] = drivers

    assert not offenders, (
        "these test modules drive the queue worker but do not import the engine-endpoint "
        "fence, so a run of them can issue a real unload() against whatever engine is "
        "configured on this machine -- add `from %s import %s  # noqa: F401` to each: %r"
        % (FENCE_MODULE, FENCE_NAME, offenders)
    )


def test_the_gate_is_not_vacuous_it_finds_the_modules_it_is_meant_to_police():
    """A pin on the gate's own reach: if `DRIVERS` were narrowed (or the
    scan broken) this test goes red rather than the gate quietly passing
    because it inspects nothing. The four modules below are the ones that
    drove the worker when this gate was written."""
    driving = set()
    for relative in _tracked_test_files():
        if relative in EXEMPT:
            continue
        source = (REPO_ROOT / relative).read_text(encoding="utf-8", errors="replace")
        if any(driver in source for driver in DRIVERS):
            driving.add(relative)

    assert {
        "models/queue/tests/test_worker.py",
        "models/registry/tests/test_jobs.py",
        "tools/rag/tests/test_jobs.py",
        "tools/rag/tests/test_consolidate.py",
    } <= driving
