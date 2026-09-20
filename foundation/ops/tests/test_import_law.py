"""The permanent import-law gate on ADR import law rule 2 (spec section 3.3).

`models.registry.models` and `models.registry.views` are off-limits to
every column, with no exception -- the one sanctioned cross-column import
under rule 2 for `models.registry` is `models.registry.bindings`, and only
that module; this gate is not about `bindings` at all.

`models.queue.models` carries a second, narrower named exception:
`foundation.ops` may import it, and only for the active-work refusal
(RUNNING jobs), and nothing else (`models/README.md` and
`foundation/README.md`'s rule-2 text both name it). ALLOWED below is the
closed set that exception applies to -- exactly the one production file
that actually imports it (`foundation/ops/backup.py`; `foundation/ops/
restore.py` reaches the same rows through `backup.running_inference_jobs`
and does not import `models.queue.models` itself, so it carries no
carve-out). Any other file importing `models.queue.models` is still a
rule 2 violation and this gate still catches it.

Production, non-test code is what this gate polices. Test modules are
EXEMPT BY DESIGN: `tools/vision/tests/`, `tools/rag/tests/`, and
`foundation/setup/tests/` all import `models.registry.models.
{ModelConnection,RoleBinding}` directly today (22 call sites at the time
this gate was written), to seed the DB rows a cross-app view test needs.
That is a legitimate reach into another column's model layer for test
fixtures -- this codebase has no shared factory/fixture layer to route it
through instead, and building one is not this task's business. A
PRODUCTION import of the same names (or of `models.queue.models` outside
the one ALLOWED file) is a different thing entirely: a load-bearing
coupling rule 2 forbids outright, and catching it here is what makes
hand-auditing that coupling in code review unnecessary.

`models/` is not scanned by this gate: `models.registry.models`/`.views`/
`models.queue.models` reached by `models/queue` or by `models/registry`
itself is intra-column, not a rule 2 violation. Rule 2 is about crossing
OUT of the `models/` column, not about traffic within it.

Lives beside `test_column_boundaries.py` for the same reason -- pure file
text (an AST parse of each file's imports), no Django ORM, no database.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from foundation.ops.tests._helpers import REPO_ROOT, _is_test_file

# The three names rule 2 forbids to every column. Two (`models.registry.
# models`, `.views`) have no exception anywhere. The third
# (`models.queue.models`) has exactly one, scoped by ALLOWED below.
FORBIDDEN_MODULES = ("models.registry.models", "models.registry.views", "models.queue.models")

# The one named exception to `models.queue.models` under rule 2, as a
# closed set: `foundation.ops` may import it, and only for the
# active-work refusal (RUNNING jobs), and nothing else. Any file not
# listed here that imports a FORBIDDEN_MODULES name is still an offender.
ALLOWED: dict[str, tuple[str, ...]] = {
    "foundation/ops/backup.py": ("models.queue.models",),
}

# The columns that do NOT own `models.registry`/`models.queue` -- see
# module docstring for why `models/` itself is excluded from the scan.
_SCANNED_COLUMNS = ("tools", "foundation", "agents")


def _scanned(*columns: str) -> list[str]:
    """Every tracked, non-test `.py` file under `columns` (e.g.
    `_scanned(*_SCANNED_COLUMNS)` for the three columns that do not own
    `models.registry`)."""
    out = subprocess.run(["git", "ls-files", "--", *columns], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines()
            if p.endswith(".py") and not _is_test_file(p)]


def _forbidden_imports(source: str) -> list[str]:
    """Parse `source` and return which of FORBIDDEN_MODULES it imports, via
    `import X`, `from X import ...`/`from X.Y import ...`, AND the
    `from X import Y` form where `Y` itself is the forbidden leaf (e.g.
    `from models.registry import models` -- `node.module` alone is
    `models.registry`, which does not equal or prefix-match either
    forbidden name; the imported NAME has to be joined onto it first).

    Relative imports (`node.level > 0`, e.g. `from . import models` or
    `from .models import X`) are deliberately NOT matched: `node.module`
    for those is either `None` or a name relative to the importing
    package, not an absolute dotted path, so joining it onto the module
    the way an absolute import's name is joined would produce a
    string that looks like a match by accident without actually naming
    `models.registry.models`/`.views`. This codebase does not use
    relative imports today, so this is a correctness guard against a
    false positive, not a live carve-out.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in FORBIDDEN_MODULES:
                    if alias.name == forbidden or alias.name.startswith(forbidden + "."):
                        hits.append(forbidden)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                for forbidden in FORBIDDEN_MODULES:
                    if full == forbidden or full.startswith(forbidden + "."):
                        hits.append(forbidden)
    return hits


def test_no_production_module_outside_models_imports_registry_models_or_views():
    offenders: dict[str, list[str]] = {}
    for relative in _scanned(*_SCANNED_COLUMNS):
        path = REPO_ROOT / relative
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = [h for h in _forbidden_imports(source) if h not in ALLOWED.get(relative, ())]
        if hits:
            offenders[relative] = hits
    assert offenders == {}, (
        "these production modules import a FORBIDDEN_MODULES name directly, "
        f"with no exception under import-law rule 2: {offenders}"
    )


def test_the_gate_is_reading_real_files():
    """Anti-vacuous pin: a broken `git ls-files` call would make the test
    above pass by looking at nothing."""
    files = _scanned(*_SCANNED_COLUMNS)
    assert len(files) > 20, len(files)
    assert "tools/rag/apps.py" in files
    assert not any("tests" in Path(f).parts for f in files)


def test_test_files_are_exempt_and_that_is_deliberate():
    """Confirms the exemption this gate documents actually holds today:
    the pre-existing direct imports of `models.registry.models` the P0
    regroup's Task 4 review found are all in test files (the rest are
    intra-column, which this gate does not scan at all -- see module
    docstring)."""
    assert _is_test_file("tools/vision/tests/test_gateway.py")
    assert _is_test_file("foundation/setup/tests/test_views.py")
    assert not _is_test_file("tools/rag/views.py")


def test_the_gate_would_actually_catch_a_forbidden_import():
    """Anti-vacuous pin: a gate that cannot fail proves nothing. Both the
    `from X.Y import Z` form and the `from X import Y` form (where the
    imported NAME, not the module path, is the forbidden leaf) must be
    caught -- the second form was missed by an earlier version of this
    gate that only ever looked at `node.module`."""
    planted_models = "from models.registry.models import ModelConnection\n"
    assert _forbidden_imports(planted_models) == ["models.registry.models"]
    planted_views = "import models.registry.views\n"
    assert _forbidden_imports(planted_views) == ["models.registry.views"]
    planted_models_via_package = "from models.registry import models\n"
    assert _forbidden_imports(planted_models_via_package) == ["models.registry.models"]
    planted_views_via_package = "from models.registry import views\n"
    assert _forbidden_imports(planted_views_via_package) == ["models.registry.views"]
    planted_queue_models = "from models.queue.models import InferenceJob\n"
    assert _forbidden_imports(planted_queue_models) == ["models.queue.models"]


def test_the_queue_models_exception_is_a_closed_set():
    """`models.queue.models` is a FORBIDDEN_MODULES name with exactly one
    named exception -- `foundation.ops` may import it, and only for the
    active-work refusal, and nothing else. Planting the same import in a
    file OTHER than the one ALLOWED entry must still be caught: ALLOWED is
    not a blanket carve-out for the module, it is scoped per-file."""
    assert ALLOWED == {"foundation/ops/backup.py": ("models.queue.models",)}
    planted = "from models.queue.models import InferenceJob\n"
    hits = _forbidden_imports(planted)
    # Simulate the offenders check for a non-allowed path, e.g.
    # `foundation/ops/restore.py`, which carries no ALLOWED entry.
    surviving = [h for h in hits if h not in ALLOWED.get("foundation/ops/restore.py", ())]
    assert surviving == ["models.queue.models"]
    # The one allowed path really does suppress the hit.
    suppressed = [h for h in hits if h not in ALLOWED.get("foundation/ops/backup.py", ())]
    assert suppressed == []


# --- P1 Task 10: rule-1 purity of `models/contracts/` --------------------
#
# `models/contracts/` is a rule-1 pure-ish leaf -- its own module
# docstrings already say "no DB, no dependency on tools/ or
# models/registry/" (jobkinds.py, roles.py) -- but it is NOT as strictly
# Django-free as `agents/contracts/` (that stricter rule is pinned by a
# SUBPROCESS with no Django configured at all,
# `agents/contracts/tests/test_purity.py`, because `agents/contracts/`
# must be importable with no Django project behind it at all). `models/
# contracts/` already legitimately imports two narrow slices of Django at
# module scope: `django.conf.settings` (`bindings.py`, `queue.py`) and
# `django.utils.module_loading.import_string` (`bindings.py`, and
# `jobkinds.py:34` -- the live tripwire this guard's sibling subprocess
# test documents importing `JobContext` for real would trip). What it must
# NEVER do is reach into Django's DATABASE layer (`django.db` -- the ORM),
# its APP REGISTRY (`django.apps` -- `AppConfig`/app-loading machinery), or
# a VIEW layer (`django.views`) -- any of those would mean this "pure
# registry/definition" package secretly depends on a configured Django
# project rather than staying importable, in-memory, definition-only data,
# same as `models.registry`'s two forbidden names above are about staying
# out of a DIFFERENT column's storage/HTTP layer.
MODELS_CONTRACTS_FORBIDDEN_DJANGO = ("django.db", "django.apps", "django.views")


def _models_contracts_files() -> list[str]:
    """Every tracked, non-test `.py` file under `models/contracts/` --
    there is no `models/contracts/tests/` package (its own coverage lives
    beside a sibling `models/registry/` app instead, same reason
    `models/registry/tests/test_registry_paths.py`'s own docstring gives),
    so the test-file filter below costs nothing today but keeps this
    reusable if that ever changes."""
    out = subprocess.run(
        ["git", "ls-files", "--", "models/contracts"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [
        line for line in out.stdout.splitlines()
        if line.endswith(".py") and not _is_test_file(line)
    ]


def _forbidden_django_imports_at_module_scope(source: str) -> list[str]:
    """Which of `MODELS_CONTRACTS_FORBIDDEN_DJANGO` `source` imports, at
    MODULE SCOPE ONLY (`tree.body`, not `ast.walk`) -- a lazily,
    in-function import would not make this package depend on a configured
    Django project merely to be IMPORTED, which is the property this
    guard actually protects (matching the same module-scope-only shape
    `foundation/ops/tests/test_column_boundaries.py`'s registration-lazy
    guard uses for the identical reason)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in MODELS_CONTRACTS_FORBIDDEN_DJANGO:
                    if alias.name == forbidden or alias.name.startswith(forbidden + "."):
                        hits.append(forbidden)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for forbidden in MODELS_CONTRACTS_FORBIDDEN_DJANGO:
                if node.module == forbidden or node.module.startswith(forbidden + "."):
                    hits.append(forbidden)
    return hits


def test_models_contracts_imports_no_django_db_apps_or_views_at_module_scope():
    """`django.conf` and `django.utils.module_loading` are the documented
    carve-out (see module docstring above); `django.db`, `django.apps`,
    and `django.views` are not, anywhere in `models/contracts/`, at module
    scope."""
    offenders: dict[str, list[str]] = {}
    for relative in _models_contracts_files():
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        hits = _forbidden_django_imports_at_module_scope(source)
        if hits:
            offenders[relative] = hits
    assert offenders == {}, offenders


def test_models_contracts_purity_gate_is_reading_real_files():
    """Anti-vacuous pin: a broken `git ls-files` call would make the test
    above pass by looking at nothing."""
    files = _models_contracts_files()
    assert len(files) > 10, files
    assert "models/contracts/jobkinds.py" in files
    assert "models/contracts/bindings.py" in files


def test_the_jobkinds_carve_out_is_real_not_assumed():
    """Pins the live tripwire this guard's own docstring names: `jobkinds.
    py:34` really does import `django.utils.module_loading` at module
    scope today, and that import is correctly NOT one of this guard's
    forbidden names."""
    source = (REPO_ROOT / "models/contracts/jobkinds.py").read_text(encoding="utf-8")
    assert "from django.utils.module_loading import import_string" in source
    assert _forbidden_django_imports_at_module_scope(source) == []


def test_the_models_contracts_purity_gate_would_actually_catch_a_violation():
    """Anti-vacuous pin: a gate that cannot fail proves nothing. All three
    forbidden names, plus the legitimate carve-out surviving alongside
    them unflagged."""
    planted = (
        "from django.utils.module_loading import import_string\n"
        "from django.db import models\n"
        "import django.apps\n"
        "from django.views import View\n"
    )
    assert sorted(_forbidden_django_imports_at_module_scope(planted)) == [
        "django.apps", "django.db", "django.views",
    ]
    # A lazy, in-function import of the same forbidden name is NOT a
    # module-scope hit -- this guard is about module scope only.
    lazy = "def f():\n    from django.db import models\n    return models\n"
    assert _forbidden_django_imports_at_module_scope(lazy) == []


# --- P2 Task 14: import-law rule 3, `agents/` never imports `tools/` -----


def test_no_agents_module_imports_a_tools_package():
    """Import-law rule 3's third clause. `agents/runtime` reaches a
    tool's implementation ONLY through a dotted-path STRING resolved at
    call time by `models.contracts.jobkinds.resolve_dotted_path` -- the
    same mechanism an AppConfig.ready() already uses to register a job
    kind without importing its handler.

    This is what makes the dependency direction one-way and acyclic:
    `tools/*` imports `agents.contracts` (a rule-1 pure leaf), and
    `agents/*` imports nothing of `tools/` at all. NOT just at module
    scope -- a lazy in-body `from tools.rag import retrieval` would
    still be a cross-column dependency, just a later one, so this walks
    EVERY import node in the file rather than only `tree.body`.
    """
    out = subprocess.run(["git", "ls-files", "--", "agents"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    offenders = {}
    for relative in out.stdout.splitlines():
        if not relative.endswith(".py") or _is_test_file(relative):
            continue
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module] if isinstance(node, ast.ImportFrom) and node.module
                else []
            )
            for name in names:
                if name == "tools" or name.startswith("tools."):
                    offenders.setdefault(relative, []).append(name)
    assert offenders == {}, offenders


def test_the_tools_gate_would_actually_catch_a_lazy_import():
    """Anti-vacuous pin: an in-BODY import must be caught too, or the
    guard only enforces a style rule instead of a dependency rule."""
    lazy = "def f():\n    from tools.rag import retrieval\n    return retrieval\n"
    found = [
        node.module for node in ast.walk(ast.parse(lazy))
        if isinstance(node, ast.ImportFrom) and node.module
        and node.module.startswith("tools.")
    ]
    assert found == ["tools.rag"]


# --- P3 Task 4: where a `Principal(...)` may be CONSTRUCTED ---------------

# IA-1 moved the type out of `agents/` entirely, so the sweep below
# walks every column rather than `agents/` alone -- a sweep still scoped
# to `agents/` would pass by looking at a directory that no longer
# contains the thing it polices.
#
# THE SET SHRANK IN THREE STEPS, and each entry landed (or left) with
# the file it names, because `test_the_principal_constructors_list_is_
# not_silently_empty` asserts every listed path exists on disk:
#   Task 1  -- `identity/contracts/principals.py` replaces
#              `agents/contracts/tools.py`; `agents/chat/principal.py`
#              and `agents/runtime/bindings.py` stay.
#   Task 5  -- `identity/request.py` replaces `agents/chat/principal.py`.
#   Task 11 -- `agents/runtime/bindings.py` goes, because the acting
#              rule deletes `principal_for` outright rather than moving
#              it. Two files remain.
_PRINCIPAL_CONSTRUCTORS = frozenset({
    "identity/contracts/principals.py",  # the type and the three constants
    "identity/request.py",               # who is on this REQUEST
    # TEST SUPPORT, not a production request-actor decision:
    # `identity/testing.py::user_principal` mints the `Principal` a
    # fixture asks for (round-2 consolidation FIX-NOW 1) -- it answers
    # no real request, and `identity/testing.py` is closed to every
    # caller OUTSIDE `identity/` by `test_no_column_imports_identitys_
    # private_modules`'s own allowlist (that gate scans `tools/`,
    # `foundation/`, `agents/` and `models/` only -- it is the guard
    # that actually matters for it, and it does not reach `identity/`
    # itself, which is why this file needs its own entry here).
    "identity/testing.py",
})
# `agents/runtime/bindings.py` is GONE FROM THIS SET (Task 11), not
# shrunk to it. `principal_for` answered "who is this AGENT acting as",
# which the acting rule (spec section 5.3) makes the wrong question: a
# turn acts as the USER named in the payload
# (`identity.contracts.principals.principal_from_payload`), and the
# function is deleted outright, not moved. A file reappearing at this
# path with a `Principal(...)` construction in it is a NEW hand-rolled
# copy of the question the acting rule already answered, never a
# restoration of what used to be there -- and this gate would catch it
# exactly like any other unlisted file.

_PRINCIPAL_SCANNED_COLUMNS = ("agents", "identity", "tools", "models", "foundation")


def _principal_scanned_files() -> list[str]:
    """Every tracked, non-test `.py` file in every column the principal
    sweep covers. Extracted from `_principal_construction_sites()` so
    the anti-vacuous pin below can assert on the FILE LIST rather than
    on the hit dict, which is empty when the gate is green."""
    out = subprocess.run(
        ["git", "ls-files", "--"] + list(_PRINCIPAL_SCANNED_COLUMNS),
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in out.stdout.splitlines()
            if line.endswith(".py") and not _is_test_file(line)]


def _principal_construction_sites() -> dict[str, int]:
    """relative path -> count of `Principal(...)` calls, over every
    tracked, non-test `.py` file in every column the sweep covers."""
    hits: dict[str, int] = {}
    for relative in _principal_scanned_files():
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
        count = _principal_calls(tree)
        if count:
            hits[relative] = count
    return hits


def _principal_calls(tree: ast.AST) -> int:
    """How many `ast.Call` nodes in `tree` name `Principal` as their
    callable -- `ast.Call(func=Name(id="Principal"))`, which catches it
    in a function body as readily as at module scope."""
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "Principal"
    )


def test_a_principal_is_only_constructed_in_the_files_that_may():
    """RULING 4a, enforced rather than described.

    A principal's KIND is the field a future grants table joins on, so
    a fifth place minting one is a fifth opinion about who is acting --
    and in open mode every one of them would look correct, because
    every one of them would say "open". The failure only surfaces once
    accounts exist and one call site is still hardcoding a kind, which
    is far too late.
    """
    offenders = {
        relative: count for relative, count in _principal_construction_sites().items()
        if relative not in _PRINCIPAL_CONSTRUCTORS
    }
    assert offenders == {}, offenders


def test_the_principal_constructors_list_is_not_silently_empty():
    """Anti-vacuous pin on `_PRINCIPAL_CONSTRUCTORS` itself: an explicit
    positive set that quietly went empty would make the guard above
    pass over nothing. Also proves every path in it exists on disk."""
    assert _PRINCIPAL_CONSTRUCTORS
    for relative in _PRINCIPAL_CONSTRUCTORS:
        assert (REPO_ROOT / relative).is_file(), relative


def test_the_principal_constructor_gate_would_actually_catch_a_violation():
    """Anti-vacuous pin: a gate that cannot fail proves nothing. A
    synthetic `Principal("open", "x")` source really is flagged by the
    same walk this gate uses."""
    planted = 'Principal("open", "x")\n'
    assert _principal_calls(ast.parse(planted)) == 1


def test_the_principal_sweep_reaches_every_column():
    """Anti-vacuous pin. The type moved OUT of `agents/`, so a sweep
    still scoped to `agents/` would pass by looking at a directory that
    no longer contains the thing it polices."""
    files = _principal_scanned_files()
    assert len(files) > 100, len(files)
    for column in _PRINCIPAL_SCANNED_COLUMNS:
        assert any(f.startswith(column + "/") for f in files), column
    assert "identity/contracts/principals.py" in files


# --- IA-1: import-law rule 4, `identity/` imports no other column ------

_OTHER_COLUMNS = ("agents", "tools", "models")


def _identity_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", "identity"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines()
            if line.endswith(".py") and not _is_test_file(line)]


def _cross_column_imports(source: str) -> list[str]:
    """Every import of another column in `source`, at ANY scope -- a lazy
    in-body `from agents.models import Agent` would still be a
    cross-column dependency, just a later one, so this walks every
    import node rather than only `tree.body` (mirroring
    `test_no_agents_module_imports_a_tools_package`)."""
    tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        names = (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module] if isinstance(node, ast.ImportFrom) and node.module
            else []
        )
        for name in names:
            for column in _OTHER_COLUMNS:
                if name == column or name.startswith(column + "."):
                    hits.append(name)
    return hits


def test_no_identity_module_imports_another_column():
    """Import-law rule 4 (spec section 4.2). `identity/` may import
    `foundation`, Django, and its own `contracts` -- and nothing else.

    This is what makes the column a BASE rather than a hub, and it has
    two load-bearing consequences: identity cannot answer "which
    documents" (it answers "which entitlement ids", and `tools/rag`
    turns that into a queryset), and the owned-rows registry names its
    models as strings resolved by `apps.get_model` rather than importing
    them.
    """
    offenders: dict[str, list[str]] = {}
    for relative in _identity_files():
        hits = _cross_column_imports((REPO_ROOT / relative).read_text(encoding="utf-8"))
        if hits:
            offenders[relative] = hits
    assert offenders == {}, offenders


def test_the_identity_sweep_is_reading_real_files():
    """Anti-vacuous pin: a broken `git ls-files` call would make the test
    above pass by looking at nothing."""
    files = _identity_files()
    assert len(files) >= 4, files
    assert "identity/contracts/principals.py" in files


def test_the_identity_rule_four_gate_would_catch_a_lazy_import():
    """Anti-vacuous pin: an in-BODY import must be caught too, or the
    guard enforces a style rule instead of a dependency rule."""
    lazy = "def f():\n    from agents.models import Agent\n    return Agent\n"
    assert _cross_column_imports(lazy) == ["agents.models"]
    assert _cross_column_imports("import tools.rag.views\n") == ["tools.rag.views"]
    assert _cross_column_imports("from foundation.format import bytes_to_gb\n") == []


def test_the_agents_import_sweeps_actually_reach_the_chat_app():
    """Anti-vacuous pin. `test_no_agents_module_imports_a_tools_package`
    and `test_agents_reaches_models_registry_through_bindings_and_
    nothing_else` both walk `git ls-files -- agents`, so `agents/chat`
    is covered by construction -- and a sweep that quietly stopped
    seeing a whole app would still pass every assertion in it."""
    out = subprocess.run(["git", "ls-files", "--", "agents"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    swept = [p for p in out.stdout.splitlines()
             if p.endswith(".py") and not _is_test_file(p)]
    assert any(p.startswith("agents/chat/") for p in swept)
    assert "agents/chat/rendering.py" in swept


def test_agents_reaches_models_registry_through_bindings_and_nothing_else():
    """Rule 2's single sanctioned exception, pinned as a CLOSED set. The
    existing gate forbids `models.registry.models`/`.views` to everyone;
    this one says that for `agents/`, `bindings` is the ONLY submodule
    of that package it may touch at all."""
    out = subprocess.run(["git", "ls-files", "--", "agents"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    offenders = {}
    for relative in out.stdout.splitlines():
        if not relative.endswith(".py") or _is_test_file(relative):
            continue
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = (
                node.module if isinstance(node, ast.ImportFrom) and node.module
                else node.names[0].name if isinstance(node, ast.Import) else None
            )
            if module and module.startswith("models.registry.") \
                    and module != "models.registry.bindings":
                offenders.setdefault(relative, []).append(module)
    assert offenders == {}, offenders


# --- IA-1: identity's private modules are off-limits to every column ---

# The sanctioned public seams (spec section 4.2's named seams), as an
# ALLOWLIST rather than a blocklist of today's known-private modules.
# Round-2 FIX-NOW 3: the blocklist form this replaced named exactly five
# modules while its own comment claimed "everything else in the column
# is private" -- but `identity.ownership` (created by the very
# consolidation pass this gate is meant to police) plus `identity.gate`,
# `.checks`, `.routes`, `.context_processors`, `.admin` and `.apps` were
# all reachable by any column, unflagged, and a module added in IA-2
# would have been too. An allowlist closes a NEW private module BY
# DEFAULT, with no second list to remember to update.
#
# `identity.testing` is deliberately NOT here: it is test SUPPORT
# (`identity/testing.py`), importable by every column's TEST files --
# which this sweep already exempts entirely via `_is_test_file`, below
# -- and, being absent from this allowlist, closed to PRODUCTION code by
# the exact same mechanism as `identity.services` or `identity.models`.
IDENTITY_PERMITTED = (
    "identity.contracts", "identity.access", "identity.request", "identity.audit",
)

# `identity/` itself is excluded, for the same intra-column reason
# `models/` is excluded from the sweep above: `identity/views.py`
# importing `identity.services` is not a violation of anything.
_IDENTITY_SCANNED_COLUMNS = ("tools", "foundation", "agents", "models")


def _identity_permitted(dotted: str) -> bool:
    """Whether `dotted` (an `identity...` import target) is one of the
    sanctioned seams, or lies beneath one (`identity.contracts.principals`
    under `identity.contracts`)."""
    return any(dotted == seam or dotted.startswith(seam + ".") for seam in IDENTITY_PERMITTED)


def _identity_forbidden_imports(source: str) -> list[str]:
    """Every `identity...` import target NOT covered by `IDENTITY_PERMITTED`,
    named by the actual dotted path seen -- including the `from identity
    import services` form where the imported NAME, not the module path,
    is the part that matters."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "identity" or alias.name.startswith("identity."):
                    if not _identity_permitted(alias.name):
                        hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "identity":
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    if not _identity_permitted(full):
                        hits.append(full)
            elif node.module.startswith("identity."):
                if not _identity_permitted(node.module):
                    hits.append(node.module)
    return hits


def test_no_column_imports_identitys_private_modules():
    """Import-law rule 2, as amended (spec section 4.2), enforced as an
    ALLOWLIST (`IDENTITY_PERMITTED`), not a blocklist of today's known
    -private modules. A column asks identity a QUESTION (`identity.access`,
    `identity.request`, `identity.audit`) or names a TYPE
    (`identity.contracts.*`); everything else under `identity/` -- the
    storage, the views, the forms, the services, the middleware, and any
    module added after this test was written -- is reached by no other
    column, with nothing here to update when a new one arrives.

    Cross-column foreign keys use STRINGS, never imports:
    `settings.AUTH_USER_MODEL`, `"identity.Entitlement"`, `"auth.Group"`.
    That is exactly what `AUTH_USER_MODEL` exists for, and it means no
    column ever imports `identity.models`.
    """
    out = subprocess.run(
        ["git", "ls-files", "--"] + list(_IDENTITY_SCANNED_COLUMNS),
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    offenders: dict[str, list[str]] = {}
    for relative in out.stdout.splitlines():
        if not relative.endswith(".py") or _is_test_file(relative):
            continue
        hits = _identity_forbidden_imports(
            (REPO_ROOT / relative).read_text(encoding="utf-8"))
        if hits:
            offenders[relative] = hits
    assert offenders == {}, offenders


def test_the_identity_private_module_gate_would_catch_a_violation():
    """Anti-vacuous pin: both import forms, and the four permitted seams
    surviving alongside them unflagged."""
    assert _identity_forbidden_imports(
        "from identity.models import User\n") == ["identity.models"]
    assert _identity_forbidden_imports(
        "from identity import services\n") == ["identity.services"]
    assert _identity_forbidden_imports(
        "import identity.middleware\n") == ["identity.middleware"]
    permitted = (
        "from identity.access import is_admin\n"
        "from identity.audit import record\n"
        "from identity.request import principal_for_request\n"
        "from identity.contracts.principals import Principal\n"
    )
    assert _identity_forbidden_imports(permitted) == []


# --- Rule 4's OTHER half: identity names no other column's ROUTE ----------
#
# Rule 4 is enforced above as an import law, which is where it bites in
# Python. It has a second half that no AST walk can see: a view calling
# `reverse("rag-documents")` and a template writing
# `{% url 'chat-workstream' %}` import nothing, resolve at request time,
# and couple `identity/` to another column's URL conf just as firmly.
#
# IT IS THE BRANCH'S HEADLINE CLAIM, stated in `identity/views.py`'s own
# comment above the entitlement panels ("a sixth labelled kind later is a
# REGISTRATION in that column's own `apps.py`, not an edit to this view
# and not a new template block"), in ADR 0016's amendment, and in
# `docs/EXTENDING.md` ("no route, no view, no template"). It was false
# for exactly one line and one template section for the length of phase
# 2 -- the Documents door, before `EntitlementAxis.link` existed -- which
# is what this gate exists so nobody rediscovers by grep.
#
# `settings-index` IS ALLOWED, and it is the one exception: it belongs to
# `foundation/`, which `identity/` may import outright (rule 4 names it),
# and it is the settings area's own landing route rather than a feature
# column's page.
_IDENTITY_ROUTE_PREFIXES = ("identity-",)
_IDENTITY_ROUTE_EXTRAS = frozenset({"settings-index"})

_ROUTE_RESOLVERS = frozenset({
    "reverse", "reverse_lazy", "redirect", "resolve_url", "settings_redirect",
})
_URL_TAG_RE = re.compile(r"""\{%\s*url\s+["']([\w.-]+)["']""")
_TEMPLATE_COMMENT_RE = re.compile(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", re.S)


def _is_identity_route(name: str) -> bool:
    return name in _IDENTITY_ROUTE_EXTRAS or name.startswith(_IDENTITY_ROUTE_PREFIXES)


def _foreign_route_names(text: str) -> list[str]:
    """Every route NAME this text really resolves that is not one of
    `identity/`'s own (or the settings landing route).

    PARSED, NOT GREPPED, and that is not fussiness: this gate's own
    explanation names `rag-documents` four times in prose, and so does
    the comment in `identity/views.py` recording why the door became a
    registration. A regex over raw text flags its own documentation.
    Python goes through `ast`, so only a real CALL counts; templates get
    their `{% comment %}` regions stripped first, the same treatment
    `foundation/ops/tests/test_css_ownership.py` gives them and for the
    same reason.

    Four spellings: `reverse(...)` / `reverse_lazy(...)` /
    `resolve_url(...)`, the `redirect("name")` /
    `settings_redirect(request, "name")` sugar that takes a route name
    positionally, and `{% url '...' %}`. Keyword arguments are read as
    well as positional ones, so `reverse(viewname="rag-documents")` is
    caught. A path or a built URL passed to any of them is not a route
    name: it contains a `/`, which no route name does, so it is skipped.

    FOUR HOLES IT CANNOT CLOSE, named rather than left to be discovered:
    a route name reached through a module constant, built by
    concatenation or an f-string, read out of a variable, or written as
    `{% url some_variable %}` in a template. No static gate sees any of
    those, and this one does not pretend to -- what it closes is every
    spelling a person actually types.
    """
    found: set[str] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:                                   # a template, not Python
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name not in _ROUTE_RESOLVERS:
                continue
            keywords = [word.value for word in node.keywords if word.arg == "viewname"]
            for argument in list(node.args) + keywords:
                if not isinstance(argument, ast.Constant):
                    continue
                if not isinstance(argument.value, str) or "/" in argument.value:
                    continue
                if not _is_identity_route(argument.value):
                    found.add(argument.value)
    else:
        for name in _URL_TAG_RE.findall(_TEMPLATE_COMMENT_RE.sub("", text)):
            if not _is_identity_route(name):
                found.add(name)
    return sorted(found)


def _identity_route_naming_files():
    """`identity/`'s own production Python and templates."""
    out = subprocess.run(
        ["git", "ls-files", "--", "identity"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    for relative in out.stdout.splitlines():
        if _is_test_file(relative):
            continue
        if relative.endswith(".py") or relative.endswith(".html"):
            yield relative


def test_identity_names_no_other_columns_route():
    """Rule 4's second half. A route name is a dependency on another
    column's URL conf; `identity/` reaches a column through the
    registries it owns (`identity/contracts/cascades.py`,
    `identity/contracts/axes.py`) and through nothing else -- an axis
    that wants a door registers a `link` and supplies the URL itself.
    """
    offenders = {}
    for relative in _identity_route_naming_files():
        hits = _foreign_route_names((REPO_ROOT / relative).read_text(encoding="utf-8"))
        if hits:
            offenders[relative] = hits
    assert offenders == {}, (
        "identity/ names another column's route by name -- rule 4's second half. "
        "A column's page is reached through a registered dotted path that returns the "
        f"URL (`EntitlementAxis.link`), never by naming the route here: {offenders}")


def test_the_route_naming_gate_would_catch_a_violation():
    """Anti-vacuous pin: all three spellings caught, identity's own
    names and the settings landing route surviving unflagged."""
    assert _foreign_route_names('reverse("rag-documents")') == ["rag-documents"]
    assert _foreign_route_names('settings_redirect(request, "inference-console")') == [
        "inference-console"]
    assert _foreign_route_names('reverse_lazy("vision-gallery")') == ["vision-gallery"]
    # THE TWO SPELLINGS NR-1 CLOSED. Neither appears in `identity/`
    # today -- `resolve_url` is used once in the whole repo and
    # `viewname=` nowhere -- which is exactly why they were free to
    # close and worth closing before somebody types one.
    assert _foreign_route_names('resolve_url("rag-documents")') == ["rag-documents"]
    assert _foreign_route_names('reverse(viewname="rag-documents")') == ["rag-documents"]
    assert _foreign_route_names(
        "{% url 'chat-workstream' stream.pk %}") == ["chat-workstream"]
    # PROSE IS NOT A CALL, which is the whole reason this reads Python
    # with `ast` -- this gate's own comments name `rag-documents`.
    assert _foreign_route_names('# see reverse("rag-documents") for why\n') == []
    assert _foreign_route_names('"""reverse("rag-documents")"""\n') == []
    assert _foreign_route_names(
        "{% comment %}{% url 'rag-documents' %}{% endcomment %}") == []
    # A BUILT URL IS NOT A ROUTE NAME.
    assert _foreign_route_names('redirect("/rag/documents/")') == []
    permitted = (
        'reverse("identity-entitlements")\n'
        'settings_redirect(request, "settings-index")\n'
    )
    assert _foreign_route_names(permitted) == []
    assert _foreign_route_names(
        "{% url 'identity-entitlement-edit' entitlement.pk %}") == []


def test_the_allowlist_closes_a_module_added_after_it_was_written():
    """Round-2 FIX-NOW 3's whole point, pinned: the OLD blocklist named
    five modules that existed when it was written and would have stayed
    silent forever about a sixth. `identity.ownership` is exactly that
    sixth -- created by this consolidation pass itself -- and this
    allowlist catches it (and every other private module it did not
    already know to name) with no edit to this file."""
    for module in ("identity.ownership", "identity.gate", "identity.checks",
                   "identity.routes", "identity.context_processors",
                   "identity.admin", "identity.apps"):
        assert _identity_forbidden_imports(f"import {module}\n") == [module], module


def test_identity_testing_is_closed_to_production_by_the_same_mechanism():
    """`identity/testing.py` is test SUPPORT, not a sanctioned production
    seam -- it is deliberately absent from `IDENTITY_PERMITTED`, so a
    production import of it is caught exactly like `identity.services`
    already is. Its test-file callers are never reached by this sweep at
    all (`_is_test_file` skips them below), which is HOW every column's
    tests may import it while production code may not -- not a special
    case this gate has to carve out."""
    assert _identity_forbidden_imports("import identity.testing\n") == ["identity.testing"]
    assert _identity_forbidden_imports(
        "from identity.testing import make_admin\n") == ["identity.testing"]
    # Anti-vacuous the other direction: at least one real test file in
    # each of the three non-identity columns already imports it, so the
    # "tests may, production may not" split is actually exercised, not
    # merely permitted in principle.
    for helpers in ("models/queue/tests/_helpers.py", "tools/rag/tests/_helpers.py",
                    "tools/vision/tests/_helpers.py"):
        source = (REPO_ROOT / helpers).read_text(encoding="utf-8")
        assert "identity.testing" in source, helpers


def test_the_identity_private_module_sweep_reaches_all_four_columns():
    """Anti-vacuous pin: a sweep that quietly stopped seeing a column
    would still pass every assertion in it."""
    out = subprocess.run(
        ["git", "ls-files", "--"] + list(_IDENTITY_SCANNED_COLUMNS),
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    swept = [p for p in out.stdout.splitlines()
             if p.endswith(".py") and not _is_test_file(p)]
    for column in _IDENTITY_SCANNED_COLUMNS:
        assert any(p.startswith(column + "/") for p in swept), column
    assert "tools/rag/views.py" in swept


# --- IA-1: rule 2's second sanctioned seam -- models.queue.visibility --

# The columns that do NOT own `models.queue` -- `agents/` and `tools/`,
# the two columns whose views actually need "may this caller see queue
# job N" (`tools/rag/views.py::AskJobStatusView`, `tools/vision/
# views.py::queue_job_status`). `foundation/` is excluded on purpose:
# `foundation/ops/backup.py` already carries its OWN named exception to
# the FULL `models.queue.models` module (see `ALLOWED` above), a
# different and wider carve-out this gate is not about.
_QUEUE_SCANNED_COLUMNS = ("tools", "agents")


def test_other_columns_reach_the_queues_visibility_and_nothing_else_of_models_queue():
    """Rule 2's second sanctioned seam. The existing gate forbids
    `models.queue.models` outright; this one says that for `tools/`
    and `agents/`, `visibility` is the only OTHER submodule of
    `models.queue` they may touch at all -- so a later view cannot
    reach `models.queue.backend` or `models.queue.scheduler` for a
    visibility answer that already has one home."""
    out = subprocess.run(["git", "ls-files", "--"] + list(_QUEUE_SCANNED_COLUMNS),
                         cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    offenders = {}
    for relative in out.stdout.splitlines():
        if not relative.endswith(".py") or _is_test_file(relative):
            continue
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = (
                node.module if isinstance(node, ast.ImportFrom) and node.module
                else node.names[0].name if isinstance(node, ast.Import) else None
            )
            if module and module.startswith("models.queue.") \
                    and module != "models.queue.visibility":
                offenders.setdefault(relative, []).append(module)
    assert offenders == {}, offenders


def test_the_queues_visibility_gate_would_actually_catch_a_violation():
    """Anti-vacuous pin: a synthetic import of `models.queue.backend`
    from a scanned column really is flagged by the same walk this gate
    uses."""
    planted = "from models.queue.backend import queue_snapshot\n"
    tree = ast.parse(planted)
    hits = [
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        and node.module.startswith("models.queue.")
        and node.module != "models.queue.visibility"
    ]
    assert hits == ["models.queue.backend"]


def test_the_queues_visibility_sweep_reaches_both_columns():
    """Anti-vacuous pin: a sweep that quietly stopped seeing a column
    would still pass every assertion in it."""
    out = subprocess.run(
        ["git", "ls-files", "--"] + list(_QUEUE_SCANNED_COLUMNS),
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    swept = [p for p in out.stdout.splitlines()
             if p.endswith(".py") and not _is_test_file(p)]
    for column in _QUEUE_SCANNED_COLUMNS:
        assert any(p.startswith(column + "/") for p in swept), column
    assert "tools/rag/views.py" in swept
    assert "tools/vision/views.py" in swept


# --- Workstreams: the tools/models -> agents allow-list ------------------

# The CLOSED set of `agents.`-prefixed names another column may import.
# Three names, and each is here because it is already a NAMED SEAM:
#
#   `agents.contracts`     -- the rule-1 pure leaves, imported by
#                             `tools/rag/tools.py` and `tools/vision/`
#                             since P2.
#   `agents.entitlements`  -- `tools/vision/views.py:824` calls it "a
#                             NAMED CROSS-COLUMN SEAM" in those words,
#                             and both `tools/vision/views.py` and
#                             `tools/rag/views.py` import
#                             `tool_access_for` from it today. A
#                             two-name list would fail on the FIRST
#                             workstreams commit, before
#                             `agents/workstreams.py` exists at all.
#   `agents.workstreams`   -- the seam this phase adds (spec §4.2).
#
# Everything else under `agents.` is closed to `tools/` and `models/` by
# DEFAULT, with no second list to remember to update -- the same
# allowlist-not-blocklist shape `IDENTITY_PERMITTED` above already uses.
AGENTS_PERMITTED = ("agents.contracts", "agents.entitlements", "agents.workstreams")

# `agents/` itself is excluded for the same intra-column reason `models/`
# is excluded from the registry sweep: `agents/chat/views/thread.py`
# importing `agents.visibility` is not a violation of anything.
_AGENTS_SCANNED_COLUMNS = ("tools", "models")


def _agents_permitted(dotted: str) -> bool:
    """Whether `dotted` (an `agents...` import target) is one of the
    sanctioned seams, or lies beneath one (`agents.contracts.workstreams`
    under `agents.contracts`)."""
    return any(dotted == seam or dotted.startswith(seam + ".") for seam in AGENTS_PERMITTED)


def _agents_forbidden_imports(source: str) -> list[str]:
    """Every `agents...` import target NOT covered by `AGENTS_PERMITTED`,
    named by the actual dotted path seen -- including the `from agents
    import visibility` form where the imported NAME, not the module path,
    is the part that matters."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "agents" or alias.name.startswith("agents."):
                    if not _agents_permitted(alias.name):
                        hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "agents":
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    if not _agents_permitted(full):
                        hits.append(full)
            elif node.module.startswith("agents."):
                if not _agents_permitted(node.module):
                    hits.append(node.module)
    return hits


def test_tools_reaches_agents_through_contracts_entitlements_and_workstreams_and_nothing_else():
    """The counterpart of `test_no_agents_module_imports_a_tools_package`,
    running the other way. `agents/` may import NOTHING of `tools/`; the
    reverse direction is permitted but CLOSED -- exactly three names.

    Not just at module scope: a lazy in-body `from agents.visibility
    import visible_conversations` inside a `tools/rag` view would still
    be a cross-column dependency on a module that is not a seam, so this
    walks EVERY import node rather than only `tree.body`.
    """
    offenders = {}
    for relative in _scanned(*_AGENTS_SCANNED_COLUMNS):
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        hits = _agents_forbidden_imports(source)
        if hits:
            offenders[relative] = hits
    assert offenders == {}, offenders


def test_the_tools_to_agents_allowlist_is_not_vacuous():
    """Anti-vacuous pin (author decision 4). A sweep that stopped
    matching anything would pass every assertion in the test above, so
    this asserts the sweep really reaches both columns AND that each
    allowed name really is imported by some production file -- which is
    what makes the allow-list a description of the real seams rather
    than an aspiration.
    """
    swept = _scanned(*_AGENTS_SCANNED_COLUMNS)
    assert any(p.startswith("tools/") for p in swept)
    assert any(p.startswith("models/") for p in swept)
    sources = "\n".join((REPO_ROOT / p).read_text(encoding="utf-8") for p in swept)
    assert "agents.contracts" in sources
    assert "agents.entitlements" in sources
    assert "agents.workstreams" in sources
    # And the gate would really catch a violation, rather than only
    # tolerating the imports that happen to exist.
    planted = "def f():\n    from agents.visibility import visible_conversations\n"
    assert _agents_forbidden_imports(planted) == ["agents.visibility"]


def test_agents_workstreams_imports_no_tools_package():
    """The narrow, NAMED twin of `test_no_agents_module_imports_a_tools_
    package`, pinning that the one `agents` module other columns may
    import does not itself reach back across.

    Redundant with the broad sweep by construction, and kept because
    this module is the one whose accidental widening would be least
    visible in review: it is the file an implementer edits when a
    workstream needs to know something about a document, and the correct
    answer there is always a registry, never an import.
    """
    path = REPO_ROOT / "agents/workstreams.py"
    if not path.is_file():
        pytest.skip("agents/workstreams.py does not exist yet (added in a later task)")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        names = (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module] if isinstance(node, ast.ImportFrom) and node.module
            else []
        )
        hits += [n for n in names if n == "tools" or n.startswith("tools.")]
    assert hits == [], hits


def test_the_tools_package_docstring_names_every_allowlist_this_gate_enforces():
    """C-47. `tools/__init__.py`'s docstring is what a reader consults
    before deciding whether an import in this column is legal. It named
    ONE of the nine sanctioned names, so eight legal imports read as
    violations. This pin is the reason it cannot drift again: the
    docstring must name every module the allowlists in THIS FILE permit a
    `tools/*` module to import."""
    docstring = (REPO_ROOT / "tools" / "__init__.py").read_text()
    permitted = {"models.registry.bindings", "models.queue.visibility"}
    permitted |= set(IDENTITY_PERMITTED)
    permitted |= set(AGENTS_PERMITTED)
    missing = sorted(name for name in permitted if name not in docstring)
    assert not missing, (
        "tools/__init__.py's docstring does not name these sanctioned "
        f"cross-column imports: {missing}"
    )


# --- The peer-tool pair: `tools/vision` and `tools/rag` are private to --
# each other (chat image artifacts, 2026-09-16) ---------------------------

# STATED IN CODE ALREADY, NEVER PINNED UNTIL NOW. `tools/vision/store.py`
# says outright that "`tools.rag` is column-private to `tools.vision`
# under the import law's rule 2" and duplicates a nine-line MIME table
# rather than import it -- but nothing failed the build if a later commit
# simply imported it. The chat-image-artifacts change makes the rule
# load-bearing: `tools/vision` resolves a `document:` reference through a
# REGISTERED DOTTED PATH precisely because it may not import the column
# that owns documents, and a lazy in-body import would make that whole
# registry pointless while every other test stayed green.
#
# BOTH DIRECTIONS, as one closed rule. Neither peer may reach the other;
# each reaches shared ground (`foundation/`, `models/contracts`,
# `identity/`) or the sanctioned `agents.contracts` seam instead.
_PEER_TOOL_COLUMNS = (("tools/vision", "tools.rag"), ("tools/rag", "tools.vision"))


def _peer_column_imports(source: str, forbidden: str) -> list[str]:
    """Every import target in `source` that names `forbidden` or lies
    beneath it -- EVERY import node, not only `tree.body`: a lazy in-body
    import is still a cross-column dependency, just a later one.

    FIX ROUND 1: this used to read only `node.module` for an `ImportFrom`,
    which misses `from tools import rag` entirely -- `node.module` there is
    `"tools"`, which neither equals nor prefixes `forbidden` ("tools.rag"),
    so the imported NAME has to be joined onto it first, exactly the fix
    `_agents_forbidden_imports` above already applies to the "from agents
    import visibility" shape. Two cases, mirroring that function's own
    branching:

    - `node.module` already names `forbidden` or something beneath it
      (`from tools.rag import access`, `from tools.rag.ingest import x`) --
      the dependency is the MODULE, so the hit is `node.module` itself,
      regardless of what is imported from it.
    - `node.module` is a strict prefix of `forbidden` (`from tools import
      rag`, `node.module == "tools"`) -- the imported NAME might BE the
      forbidden leaf, so it is joined onto `node.module` and checked the
      same way `_agents_forbidden_imports` checks `f"{node.module}.
      {alias.name}"`.

    Relative imports (`node.level > 0`, e.g. `from ..rag import access`)
    are deliberately NOT matched, for the same reason `_forbidden_imports`
    above documents: `node.module` for those is `None` or a name relative
    to the importing package, not an absolute dotted path, so joining it
    the way an absolute import's name is joined would produce a string
    that looks like a match by accident without actually naming the
    forbidden package. Neither tool column uses a relative import today,
    so this is a correctness guard against a false positive, not a live
    carve-out.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == forbidden or alias.name.startswith(forbidden + "."):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == forbidden or node.module.startswith(forbidden + "."):
                hits.append(node.module)
            elif forbidden.startswith(node.module + "."):
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    if full == forbidden or full.startswith(forbidden + "."):
                        hits.append(full)
    return hits


def test_neither_tool_column_imports_its_peer():
    offenders = {}
    for column, forbidden in _PEER_TOOL_COLUMNS:
        for relative in _scanned(column):
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            hits = _peer_column_imports(source, forbidden)
            if hits:
                offenders[relative] = hits
    assert offenders == {}, offenders


def test_the_peer_tool_gate_is_reading_real_files():
    """Anti-vacuous pin: a sweep that stopped matching anything would
    pass the assertion above over an empty loop."""
    swept = set()
    for column, _forbidden in _PEER_TOOL_COLUMNS:
        swept |= set(_scanned(column))
    assert "tools/vision/services.py" in swept
    assert "tools/rag/access.py" in swept


def test_the_peer_tool_gate_would_actually_catch_a_lazy_import():
    lazy = "def f():\n    from tools.rag import access\n    return access\n"
    assert _peer_column_imports(lazy, "tools.rag") == ["tools.rag"]
    assert _peer_column_imports("import tools.vision.store\n", "tools.vision") == [
        "tools.vision.store"
    ]
    # FIX ROUND 1: `from tools import rag` used to slip through -- its
    # `node.module` is `"tools"` alone, which neither equals nor prefixes
    # `"tools.rag"` without joining the imported NAME onto it first.
    from_parent = "def f():\n    from tools import rag\n    return rag\n"
    assert _peer_column_imports(from_parent, "tools.rag") == ["tools.rag"]
    # A DOCSTRING or comment naming the peer is not an import, and must
    # not be flagged -- both columns' docstrings name each other today.
    prose = '"""See tools.rag.ingest._media_type_for for the same table."""\n'
    assert _peer_column_imports(prose, "tools.rag") == []
    # A RELATIVE import is deliberately NOT matched (see this function's
    # own docstring) -- pinned here so that decision cannot silently
    # become a second hole the way the unjoined `node.module` was.
    relative = "def f():\n    from ..rag import access\n    return access\n"
    assert _peer_column_imports(relative, "tools.rag") == []


# --- Whole-branch review, final wave (Minor): foundation/fence.py is
# stdlib-only, as its own docstring claims -------------------------------

_STDLIB_MODULE_NAMES = set(sys.stdlib_module_names) | {"__future__"}


def _non_stdlib_imports(source: str) -> list[str]:
    """Every top-level module `source` imports that is not in the
    running interpreter's `sys.stdlib_module_names` (plus `__future__`,
    a compiler directive `sys.stdlib_module_names` does not itself list,
    which every module in this codebase uses via `from __future__ import
    annotations`). A relative import (`node.level > 0`) is always an
    offender -- `foundation/fence.py` has no package-relative sibling to
    import and a pure-stdlib module has no legitimate reason to grow
    one."""
    tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in _STDLIB_MODULE_NAMES:
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                prefix = "." * node.level + (node.module + "." if node.module else "")
                hits.extend(prefix + alias.name for alias in node.names)
                continue
            top = (node.module or "").split(".")[0]
            if top not in _STDLIB_MODULE_NAMES:
                hits.append(node.module)
    return hits


def test_fence_module_imports_only_the_standard_library():
    """`foundation/fence.py`'s own docstring says so in prose: "NO Django
    import, NO project import: this module is pure text manipulation and
    the standard library's own CSPRNG, nothing else." This is the same
    claim, pinned as an AST fact rather than left as a claim a docstring
    makes and nothing checks -- the fence machinery is what stands
    between a hostile document/tool-result body and the surrounding
    prompt (H5 review round 1), so its own dependency surface staying
    exactly as small as advertised is worth a permanent gate, the same
    way `models/contracts/`'s purity is gated above."""
    source = (REPO_ROOT / "foundation" / "fence.py").read_text(encoding="utf-8")
    assert _non_stdlib_imports(source) == []


def test_the_fence_purity_gate_would_actually_catch_a_violation():
    """Anti-vacuous pin: a third-party import, a Django import, a
    same-repo project import, and a relative import all trip the gate
    above; the standard library and `__future__` do not."""
    planted = (
        "from __future__ import annotations\n"
        "import re\n"
        "import secrets\n"
        "import httpx\n"
        "from django.conf import settings\n"
        "from foundation.files import create_owner_only_dir\n"
        "from . import sibling\n"
    )
    assert sorted(_non_stdlib_imports(planted)) == [
        ".sibling", "django.conf", "foundation.files", "httpx",
    ]
    clean = "from __future__ import annotations\nimport re\nimport secrets\n"
    assert _non_stdlib_imports(clean) == []


def test_the_fence_purity_gate_is_reading_the_real_file():
    """Anti-vacuous pin: a broken path would make the test above pass by
    parsing nothing."""
    path = REPO_ROOT / "foundation" / "fence.py"
    assert path.is_file()
    source = path.read_text(encoding="utf-8")
    assert "import re" in source
    assert "import secrets" in source
