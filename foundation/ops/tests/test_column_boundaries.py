"""The permanent grep gate on the four-column regroup (spec section 3.6.5).

`core/`, `console/`, and `modules/` no longer exist. A reference to one
of them in tracked source is either a stale path (which will fail
lazily, on a worker, months from now) or a sentence that is simply no
longer true. Neither is acceptable in a codebase whose docstrings are
load bearing.

`docs/` is deliberately NOT swept here: P4 owns that, and an ADR
reproduced verbatim must keep saying what it said.

Lives beside `test_docs_sync.py` for the same reason that one does --
`foundation.ops` is the app that already reaches across every column by
design, and a repo-wide structural assertion belongs with it. Pure file
text; no Django ORM, no database.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from foundation.ops.tests._helpers import REPO_ROOT, _is_test_file
from foundation.ops.tests.test_import_law import _scanned

# A dead column named as a PACKAGE (dotted) or a PATH (slash). Bare-word
# "console" in prose is deliberately not matched -- an operator console
# is still a real thing this product has; a `console.` import is not.
#
# BUILT BY CONCATENATION, and that is not stylistic. Writing any needle
# out as a literal in this file would make THIS FILE a hit for its own
# needle: the scan below reads every tracked file's text, including its
# own, and the test would fail the moment it was written -- which is also
# why this comment does not spell one out. Splitting each needle across
# the `+` keeps the literal out of the file's text while the runtime
# string is identical.
_DEAD_PACKAGES = ("core", "console", "modules")
_DEAD_LEAVES = (
    "inference", "format", "files", "tests", "jobs", "ops", "setup",
    "rag", "vision", "home", "models", "views", "urls", "apps", "backend",
    "worker",
)
DEAD_REFERENCES = tuple(
    package + separator + leaf
    for package in _DEAD_PACKAGES
    for leaf in _DEAD_LEAVES
    for separator in (".", "/")
)

# A file may carry a dead reference ONLY if it is listed here, with a
# reason. This module is seeded in as belt-and-braces: the concatenation
# above already keeps every needle out of this file's own text, and this
# entry is what stops a future editor from reintroducing one by writing a
# plain literal in a comment and quietly turning the gate red on itself.
ALLOWED: dict[str, str] = {
    "foundation/ops/tests/test_column_boundaries.py":
        "this gate's own source -- see the concatenation note above",
}

# The needle built from package="core" and leaf="files" (dot form) is also
# a contiguous substring of Django's own upload-file helper module (its
# dotted path is "django." plus that same package-leaf pair, plus
# ".uploadedfile"), imported by several test files for
# `SimpleUploadedFile`. That import has nothing to do with the dead
# `core/` package; it is Django's own namespace, unrelated to this repo's
# regroup. Rather than allow-listing every file that happens to import it
# (a growing, easy-to-forget list), `_dead_reference_hits` below excludes
# ONLY the exact colliding context -- this needle immediately preceded by
# the literal "django." -- via a negative lookbehind. The same needle
# anywhere else in a file (no `django.` immediately before it) is still a
# real hit and is still caught.
_CORE_FILES_NEEDLE = "core" + "." + "files"


def _dead_reference_hits(text: str) -> list[str]:
    hits: list[str] = []
    for needle in DEAD_REFERENCES:
        pattern = re.escape(needle)
        if needle == _CORE_FILES_NEEDLE:
            pattern = r"(?<!django\.)" + pattern
        if re.search(pattern, text):
            hits.append(needle)
    return hits


def _tracked_files() -> list[str]:
    """Every tracked file except the two frozen-record trees.

    `docs/` is P4's sweep. `.superpowers/` holds seven owner-requirement and
    peer-handoff documents -- four of which name old paths -- and they are
    the owner's own words and another session's handoff, recorded at a
    moment in time. Rewriting either would falsify a record exactly as
    rewriting an ADR quotation would.
    """
    out = subprocess.run(
        ["git", "ls-files", "--", ":!docs/", ":!.superpowers/"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def test_no_tracked_source_file_outside_docs_names_a_dead_column():
    offenders: dict[str, list[str]] = {}
    for relative in _tracked_files():
        if relative in ALLOWED:
            continue
        path = REPO_ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: not source we can lie in
        hits = _dead_reference_hits(text)
        if hits:
            offenders[relative] = hits
    assert offenders == {}, (
        "these files still name core/, console/, or modules/ -- a package "
        f"that no longer exists: {offenders}"
    )


def test_the_gate_is_reading_real_files():
    """Anti-vacuous pin: a broken `git ls-files` call would make the test
    above pass by looking at nothing."""
    tracked = _tracked_files()
    assert len(tracked) > 200, len(tracked)
    assert "config/settings.py" in tracked
    assert not any(name.startswith("docs/") for name in tracked)
    assert not any(name.startswith(".superpowers/") for name in tracked)


def test_the_needles_are_the_ones_we_meant():
    """The concatenation above is easy to get subtly wrong. This pins the
    runtime strings without ever writing one as a literal in this file."""
    assert ("core" + "." + "inference") in DEAD_REFERENCES
    assert ("modules" + "/" + "vision") in DEAD_REFERENCES
    assert ("console" + "." + "jobs") in DEAD_REFERENCES
    assert len(DEAD_REFERENCES) == 96


def test_the_gate_would_actually_catch_a_dead_reference():
    """Anti-vacuous pin: plant a needle in a string this test owns and
    confirm the matcher sees it. A gate that cannot fail proves nothing."""
    planted = "from " + "modules" + ".rag" + " import ingest"
    assert [n for n in DEAD_REFERENCES if n in planted] == ["modules" + "." + "rag"]
    assert _dead_reference_hits(planted) == ["modules" + "." + "rag"]


def test_the_django_files_collision_is_narrowly_excluded():
    """The `core.files` exclusion is scoped to the exact colliding
    context, not the needle in general: Django's own `SimpleUploadedFile`
    import is not flagged, but a real dead-package reference spelled the
    same way -- with no `django.` immediately before it -- still is. A
    matcher that excluded the needle unconditionally would be vacuous for
    this one entry in DEAD_REFERENCES; this proves it is not."""
    excluded = "from " + "django" + "." + "core" + "." + "files" + ".uploadedfile import SimpleUploadedFile"
    assert _dead_reference_hits(excluded) == []
    real_hit = "from " + "core" + "." + "files" + " import helper"
    assert _dead_reference_hits(real_hit) == ["core" + "." + "files"]


def test_the_four_columns_exist_and_the_three_old_ones_do_not():
    """The dead-directory half of this check asks git, not the filesystem:
    an untracked `__pycache__/` leftover under one of these names (e.g. from
    a machine that ran the old suite before the regroup) would make a plain
    `.exists()` check fail on nothing that matters. `git ls-files` only ever
    reports TRACKED files, so a stray build artifact cannot turn this red."""
    for column in ("tools", "models", "agents", "foundation"):
        assert (REPO_ROOT / column / "__init__.py").is_file(), column
    for dead in ("core", "console", "modules", "templates"):
        out = subprocess.run(
            ["git", "ls-files", "--", dead],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        assert out.stdout == "", (dead, out.stdout)


# --- P1 Task 10: the tool contract's own two structural guards -----------
#
# Every module that declares tool runners. A new one is added here in the
# same commit that creates it -- `test_every_registered_runner_lives_in_a_
# swept_module` below makes forgetting impossible.
TOOL_MODULES = (
    "tools/rag/tools.py",
    "tools/vision/tools.py",
    "models/registry/tools.py",
    # The settings assistant's two read-only tools.
    "agents/settings_tools.py",
)

# `enqueue` is allowed (rag.ingest fires and forgets); `get_job` is not.
_FORBIDDEN_IN_A_RUNNER = ("get_job",)

# The subset of `TOOL_MODULES` that `AppConfig.ready()` actually imports
# to register its specs -- which is what
# `test_no_tool_module_imports_its_service_layer_at_module_scope` polices.
# An explicit POSITIVE list, not `TOOL_MODULES` minus something: a
# fifth registration module added to `TOOL_MODULES` later without also
# being added here would otherwise silently escape that guard, the
# exact hole `test_every_registered_runner_lives_in_a_swept_module`
# already closes for `TOOL_MODULES` itself --
# `test_the_registration_modules_list_is_not_silently_empty` below
# closes it here too.
#
# `agents/runtime/delegate.py` is deliberately ABSENT from BOTH lists
# above: its `ToolSpec`s are built in `agents/resident.py` (pure, no
# Django) with `runner` as a dotted-path STRING, so `ready()`
# (`agents/apps.py`) never imports `agents.runtime.delegate` at all --
# the whole reason that module is free to import `agents.models`,
# `agents.runtime.loop`, and friends at module scope (see its own
# docstring). It still needs the `get_job` sweep (it is where
# `agent.<slug>`'s runner code actually lives) and the swept-module pin
# (`agent.<slug>` ToolSpecs really do run there) -- both come from
# `RUNTIME_MODULES` below instead, which is what keeps this
# `_REGISTRATION_MODULES` list -- the module-scope-import subject --
# honest about what `ready()` actually imports.
_REGISTRATION_MODULES = (
    "tools/rag/tools.py",
    "tools/vision/tools.py",
    "models/registry/tools.py",
    # P3 Task 13: `agents/apps.py::ready()` imports this to register the
    # ONE `flow.run` spec (ruling 1). It is genuinely TWO things at
    # once -- a registration module (this list) and a runtime module
    # (`RUNTIME_MODULES` below, where its `narrowed_flow_spec`/
    # `flow_row_roles` actually run inside a turn) -- so it appears in
    # both, on purpose.
    "agents/runtime/flowtool.py",
    # `agents/apps.py::ready()` imports this to register `settings.card`
    # and `settings.overview`. It is BOTH a tool module and a
    # registration module, so it appears in both lists -- the same shape
    # `agents/runtime/flowtool.py` above already has.
    "agents/settings_tools.py",
)

# The turn runtime. Swept for the same `get_job` rule as TOOL_MODULES:
# a runner and a loop both execute INSIDE a job and hold the machine's
# one execution slot in sequential mode.
RUNTIME_MODULES = (
    "agents/runtime/prompt.py",
    "agents/runtime/invoke.py",
    "agents/runtime/loop.py",
    "agents/runtime/jobs.py",
    "agents/runtime/delegate.py",
    "agents/runtime/audit.py",
    "agents/runtime/flow.py",
    "agents/runtime/flowtool.py",
    "agents/runtime/preflight.py",
    # P3 Task 14: `bindings.py` PREDATES P3 and was never swept until
    # now -- it is runtime code (`principal_for`, `resolve_chat`) that
    # could grow a `get_job` call like any other, and the directory-
    # derived check below was red without it. After this one entry the
    # sweep is COMPLETE: every other module under `agents/runtime/`
    # joined this list in the commit that created it (Tasks 2, 9, 12,
    # 13), so this task adds nothing further -- it only verifies.
    "agents/runtime/bindings.py",
    # WS-2 Task 16: `taint.py` -- `stamp_turn_taint` runs INSIDE the tool
    # turn's own `transaction.atomic()` in `loop.py`, the same runtime
    # context as every other module here, so it joins this list in the
    # commit that created it rather than waiting for a later sweep to
    # notice it missing.
    "agents/runtime/taint.py",
)

# Everything under `agents/runtime/` is swept, with exactly one
# exclusion, named here so adding a second is a decision rather than an
# omission: `__init__.py`, which holds no code. `tests/` is not a
# module of the package for this purpose.
_RUNTIME_SWEEP_EXCLUSIONS = frozenset({"agents/runtime/__init__.py"})


def _runtime_package_modules() -> set[str]:
    """Every tracked, non-test `.py` file under `agents/runtime/`,
    minus `_RUNTIME_SWEEP_EXCLUSIONS` -- the set `RUNTIME_MODULES`
    above must equal exactly. A hand-maintained list checked by hand is
    checked once; this derives the expected set from the directory
    itself, so a module added later is swept or this test fails naming
    it, rather than a reviewer having to notice a list is short."""
    out = subprocess.run(
        ["git", "ls-files", "--", "agents/runtime"], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True,
    )
    modules = {
        line for line in out.stdout.splitlines()
        if line.endswith(".py") and "tests" not in Path(line).parts
    }
    return modules - _RUNTIME_SWEEP_EXCLUSIONS


def test_every_runtime_module_is_swept():
    """A hand-maintained list checked by hand is checked once. This
    derives the expected set from the directory, so a module added
    later is swept or this fails naming it."""
    assert set(RUNTIME_MODULES) == _runtime_package_modules()


def test_the_runtime_sweep_exclusion_is_a_closed_set_of_one():
    """Anti-vacuous pin on `_RUNTIME_SWEEP_EXCLUSIONS` itself: exactly
    one file, and it really exists and really holds no code."""
    assert _RUNTIME_SWEEP_EXCLUSIONS == frozenset({"agents/runtime/__init__.py"})
    for relative in _RUNTIME_SWEEP_EXCLUSIONS:
        assert (REPO_ROOT / relative).is_file(), relative


def test_every_registered_runner_lives_in_a_swept_module_covers_flow_run():
    """`test_every_registered_runner_lives_in_a_swept_module` (below)
    already proves every registered runner lives in a swept module in
    general; this names the specific case Task 14's brief calls out --
    `agents.runtime.flow.run_flow`, `flow.run`'s own registered
    runner -- so a reader does not have to trust the general sweep on
    faith for this one."""
    from agents.contracts.tools import all_tools

    runners = {spec.runner for spec in all_tools()}
    assert any(r.startswith("agents.runtime.flow.") for r in runners), runners


# DELIBERATELY NOT SWEPT: `agents/management/commands/agent_turn.py`
# and `agents/chat/views/turns.py` both poll `get_job`, and that is
# legal for both. This rule governs code that runs INSIDE a job and
# holds the machine's one slot; a management command holds no slot --
# it stands OUTSIDE the queue waiting for it, exactly as a browser
# poller would -- and a VIEW holds no slot either, answering one
# request and returning, exactly as `tools/vision/views.py::
# queue_job_status` already does. Naming both exclusions here is the
# point: an absent path would read as an oversight, and this is what a
# reader finds first.


def _code_only_text(relative: str) -> str:
    """`relative`'s source with its own leading MODULE docstring stripped.

    Deviation from a literal source-text scan, forced by the tree rather
    than chosen for style: `tools/vision/tools.py`'s own module docstring
    (authored by another session, out of this task's reach -- see the
    Global Constraints) explains, in prose, exactly why `run_generate`
    never calls `models.contracts.queue.get_job` -- so a bare substring
    scan of the WHOLE file trips on that explanation, the identical
    self-reference problem this file's own dead-column guard above solves
    with string concatenation (see its module docstring). Stripping only
    the leading docstring -- never a function's own -- keeps the scan
    looking at CODE, which is what this guard is actually about; no
    `TOOL_MODULES` file relies on `get_job` appearing in a later,
    per-function docstring, so this costs nothing today.
    """
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    tree = ast.parse(text)
    first = tree.body[0] if tree.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        text = "\n".join(text.splitlines()[first.end_lineno:])
    return text


def test_no_tool_runner_blocks_on_a_queue_job():
    """A tool runner may enqueue a queue job and return its id; it must
    never call `get_job` in a loop, and it must never call `enqueue` for
    work whose result it needs.

    On a default install `JobSettings.memory_budget_bytes` is null
    (models/queue/models.py:184-192), which
    models/queue/scheduler.py:374-380 reads as sequential mode: at most
    one job on the whole machine. An agent.turn that enqueued a job and
    blocked on it would hold the machine's one slot while the job it
    waits for can never be admitted -- then the orphan sweep marks it
    stale and, on a second orphaning, fails it permanently. Deadlock,
    then data loss. Certain, not probable.

    Crude source-text scan, deliberately -- the same shape as the
    existing structural guard against writing to the global LlamaIndex
    Settings (tools/rag/tests/test_retrieval.py:1046-1049, ADR
    0010:600-604). `tools.vision.services.wait_for` is NOT caught by
    this and must not be: it polls the IMAGE ENGINE about a generation
    already submitted and never touches the queue.
    """
    offenders = {}
    for relative in TOOL_MODULES:
        text = _code_only_text(relative)
        hits = [needle for needle in _FORBIDDEN_IN_A_RUNNER if needle in text]
        if hits:
            offenders[relative] = hits
    assert offenders == {}, offenders


def test_no_runtime_module_blocks_on_a_queue_job():
    """The same rule as `test_no_tool_runner_blocks_on_a_queue_job` above,
    swept over `RUNTIME_MODULES` instead of `TOOL_MODULES`: a turn's
    planner/loop/invoker/delegate all run INSIDE the `agent.turn` job and
    hold the machine's one execution slot in sequential mode exactly like
    a tool runner does."""
    offenders = {}
    for relative in RUNTIME_MODULES:
        text = _code_only_text(relative)
        hits = [needle for needle in _FORBIDDEN_IN_A_RUNNER if needle in text]
        if hits:
            offenders[relative] = hits
    assert offenders == {}, offenders


@pytest.mark.parametrize("relative", [
    "agents/management/commands/agent_turn.py",
    "agents/chat/views/turns.py",
])
def test_the_queue_polling_callers_really_poll_and_are_really_excluded(relative):
    """The never-block rule governs a TOOL RUNNER -- code executing INSIDE
    a job, holding the machine's one execution slot in sequential mode
    (`models/queue/scheduler.py:375`). A management command holds no
    slot; it stands OUTSIDE the queue waiting for it. A VIEW holds no
    slot either; it answers one request and returns, exactly as
    `tools/vision/views.py::queue_job_status` already does.

    BOTH DIRECTIONS, for both files: each really does call `get_job`,
    and neither is in `RUNTIME_MODULES`. A refactor that moved either
    into the swept list would then be a decision somebody had to make,
    rather than a rule quietly changing under a test that no longer
    described it.
    """
    text = (REPO_ROOT / relative).read_text()
    assert "get_job" in text
    assert relative not in RUNTIME_MODULES


def test_the_docstring_carve_out_does_not_hide_a_real_violation():
    """Anti-vacuous pin on `_code_only_text` itself: stripping the leading
    docstring must not make the guard above blind to a REAL `get_job` call
    sitting in a tool module's actual code -- only to its own explanation
    of why it doesn't have one. Writes the planted module to a scratch
    file so `_code_only_text` (which reads from disk) exercises the exact
    same code path the real guard does."""
    import tempfile

    planted = (
        '"""A module docstring that also happens to mention get_job, same as '
        'tools/vision/tools.py does today."""\n'
        "from __future__ import annotations\n"
        "\n"
        "def run_something(args, ctx):\n"
        "    from models.contracts.queue import get_job\n"
        "    return get_job(1)\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", dir=REPO_ROOT, delete=True) as f:
        f.write(planted)
        f.flush()
        relative = str(Path(f.name).relative_to(REPO_ROOT))
        code_only = _code_only_text(relative)

    assert "get_job" in planted  # the docstring's own mention, same as vision's real file
    assert "A module docstring" not in code_only  # the docstring itself is gone
    assert "get_job" in code_only  # but a REAL usage in the function body survives


def test_every_registered_runner_lives_in_a_swept_module():
    """Anti-vacuous pin: TOOL_MODULES is a hand-maintained list, so a new
    tool module that nobody added would silently escape the sweep.

    Built from `TOOL_MODULES + RUNTIME_MODULES`, not `TOOL_MODULES`
    alone: the `agent.<slug>` specs run
    `agents.runtime.delegate.run_agent_tool`, and that module is swept
    for the `get_job` rule under `RUNTIME_MODULES`, not under
    `TOOL_MODULES` (see that list's own docstring for why it moved)."""
    from agents.contracts.tools import all_tools

    swept = {
        relative[:-3].replace("/", ".")
        for relative in TOOL_MODULES + RUNTIME_MODULES
    }
    for spec in all_tools():
        module = spec.runner.rsplit(".", 1)[0]
        assert module in swept, f"{spec.key} runs in unswept module {module}"


def _is_allowed_registration_import(dotted: str) -> bool:
    """`dotted` is safe at MODULE scope in a `tools/*/tools.py` /
    `models/registry/tools.py` registration module: `agents.contracts.*`
    and `models.contracts.*` (the two rule-1 pure leaves every `ToolSpec`
    is built from), `foundation.settings_help` (P4 Task 4's third such
    leaf -- `agents/settings_tools.py` builds `SETTINGS_CARD`'s enum and
    description from `CARDS`/`page_choices` at import time, exactly as
    `agents.contracts.tools` supplies `ToolSpec`/`Param` -- not
    `foundation` wholesale, since `foundation.ops`/`foundation.setup`/
    `foundation.landing` are Django apps, not pure leaves), `__future__`,
    or the standard library.

    Controller ruling on this guard (P1 Task 10): stdlib is allowed and
    must NOT be flagged -- `tools/vision/tools.py` legitimately imports
    `time` (the wait-budget clamp) and `dataclasses.replace` (`_tool_
    param`'s `required` strip) at module scope, both pure stdlib with no
    Django and no I/O, exactly as that module's own docstring says. Only
    the TOP-LEVEL package name is checked against `sys.stdlib_module_
    names` (`alias.name`/`node.module` may be a dotted submodule, e.g.
    `dataclasses` itself has none here but `os.path` would -- checking the
    first segment is what a stdlib import always resolves through).
    """
    import sys

    if dotted == "foundation.settings_help":
        return True
    if dotted.startswith(("agents.contracts", "models.contracts", "__future__")):
        return True
    return dotted.split(".", 1)[0] in sys.stdlib_module_names


def test_no_tool_module_imports_its_service_layer_at_module_scope():
    """Registration imports no implementation module.
    `AppConfig.ready()` imports each module above to register its specs,
    and `ready()` promises no DB and no heavy imports at startup. Every
    runner therefore imports lazily, INSIDE its body -- the same shape
    tools/rag/categories.py:40 already uses.
    """
    offenders = {}
    # NOT allowed, and this is the point: `tools.rag.retrieval`,
    # `tools.rag.ingest`, `tools.vision.services`, `models.registry.bindings`.
    # Every one of those is a lazy, in-body import in the runners.
    for relative in _REGISTRATION_MODULES:
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
        for node in tree.body:                     # MODULE SCOPE ONLY
            if isinstance(node, ast.ImportFrom) and node.module:
                if not _is_allowed_registration_import(node.module):
                    offenders.setdefault(relative, []).append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if not _is_allowed_registration_import(alias.name):
                        offenders.setdefault(relative, []).append(alias.name)
    assert offenders == {}, offenders


def test_the_registration_modules_list_is_not_silently_empty():
    """Anti-vacuous pin on `_REGISTRATION_MODULES` itself (Task 11
    review, MINOR c): an explicit positive list that quietly went empty
    -- a typo'd removal, a bad merge -- would make the guard above pass
    over an empty loop and look green while checking nothing. Also
    proves every path in it actually exists on disk, so a renamed file
    fails HERE with a clear reason rather than as an opaque
    `FileNotFoundError` from `ast.parse`'s own `.read_text()` call
    inside the guard it feeds."""
    assert _REGISTRATION_MODULES
    for relative in _REGISTRATION_MODULES:
        assert (REPO_ROOT / relative).is_file(), relative


# --- P3 Task 4: `agents/chat` reaches Conversation/Agent/Flow through -----
# `agents/visibility.py` only -----------------------------------------------


_VISIBILITY_MODELS = ("Conversation", "Agent", "Flow", "Share", "Workstream")

# The two modules `agents/chat` may reach these four managers through.
# `agents/visibility.py` lives at the COLUMN ROOT (its own docstring says
# why -- a future MCP edge and a future management command need the same
# answer, and neither is a chat view), not under `agents/chat/`, so it is
# never actually walked by the scan below (`git ls-files -- agents/chat`
# only lists paths under that directory). Named here anyway, as a CLOSED
# set rather than an implicit "everything not under agents/chat/", so a
# reviewer sees the sanctioned files without inferring them from the
# scan's own directory filter -- and so either one accidentally moved
# under `agents/chat/` later is still covered by this exclusion rather
# than silently failing the gate for the right reasons. `agents/
# shares.py` (IA-2) is the module that owns the `Share` manager, exactly
# as `visibility.py` owns the other three.
_VISIBILITY_MODULE_EXCLUSION = frozenset({"agents/visibility.py", "agents/shares.py"})


def _direct_model_access(source: str) -> list[str]:
    """Which of `_VISIBILITY_MODELS` `source` reaches via `<Model>.
    objects` -- `ast.Attribute(value=ast.Name(id=<Model>), attr=
    "objects")`, so it catches `Agent.objects` in a function body as
    readily as at module scope, and does not trip over the word
    "objects" in a docstring or over `type(self).objects` (P3-D... the
    `Flow.save()` slug-immutability check uses exactly that indirect
    form, deliberately outside this AST shape's reach)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute) and node.attr == "objects"
            and isinstance(node.value, ast.Name) and node.value.id in _VISIBILITY_MODELS
        ):
            hits.append(node.value.id)
    return hits


def test_no_chat_module_queries_the_three_owned_models_directly():
    """RULING 4c, extended by IA-2. `agents/visibility.py` and
    `agents/shares.py` are the two places `agents/chat` reaches
    `Conversation`, `Agent`, `Flow`, or `Share` rows.

    In open mode those functions return everything, so nothing about
    today's behaviour depends on this -- and that is the whole reason
    the rule needs a guard rather than a convention. When Identity &
    Auth adds a filter, it adds it in a handful of functions; a view
    that had grown its own `Agent.objects.filter(...)` would silently
    keep showing everybody everything, and would do it on the one page
    where that matters most.

    The exclusion is FLAT and PER-MODULE: `agents/visibility.py` and
    `agents/shares.py`, and nothing else. No carve-out for creates, no
    carve-out for "just this one query" -- `visibility.create_
    conversation` exists precisely so the rule needs none, and a guard
    with an exception is a guard somebody widens.
    """
    out = subprocess.run(["git", "ls-files", "--", "agents/chat"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    offenders: dict[str, list[str]] = {}
    for relative in out.stdout.splitlines():
        if not relative.endswith(".py") or _is_test_file(relative):
            continue
        if relative in _VISIBILITY_MODULE_EXCLUSION:
            continue
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        hits = _direct_model_access(source)
        if hits:
            offenders[relative] = hits
    assert offenders == {}, offenders


def test_the_visibility_module_exclusion_is_a_closed_set_of_two():
    """Anti-vacuous pin on `_VISIBILITY_MODULE_EXCLUSION` itself: exactly
    two files, and both really exist."""
    assert _VISIBILITY_MODULE_EXCLUSION == frozenset(
        {"agents/visibility.py", "agents/shares.py"}
    )
    for relative in _VISIBILITY_MODULE_EXCLUSION:
        assert (REPO_ROOT / relative).is_file(), relative


def test_the_chat_model_access_gate_would_actually_catch_a_violation():
    """Anti-vacuous pin: a gate that cannot fail proves nothing. A
    synthetic `Agent.objects.filter(x=1)` source really is flagged by
    the same walk this gate uses."""
    planted = "def f():\n    return Agent.objects.filter(x=1)\n"
    assert _direct_model_access(planted) == ["Agent"]


def test_the_stdlib_carve_out_does_not_hide_a_real_violation():
    """Anti-vacuous pin: `_is_allowed_registration_import` must still
    refuse a real service-layer import -- the stdlib carve-out is for
    `time`/`dataclasses`, not a blanket pass."""
    assert _is_allowed_registration_import("time")
    assert _is_allowed_registration_import("dataclasses")
    assert _is_allowed_registration_import("agents.contracts.tools")
    assert _is_allowed_registration_import("models.contracts.operations")
    assert _is_allowed_registration_import("foundation.settings_help")
    assert _is_allowed_registration_import("__future__")
    assert not _is_allowed_registration_import("tools.rag.retrieval")
    assert not _is_allowed_registration_import("tools.vision.services")
    assert not _is_allowed_registration_import("models.registry.bindings")
    assert not _is_allowed_registration_import("django.conf")
    assert not _is_allowed_registration_import("agents.models")
    assert not _is_allowed_registration_import("agents.runtime.loop")
    # NOT blanket `foundation`: `foundation.ops`/`foundation.setup`/
    # `foundation.landing` are Django apps, not pure leaves.
    assert not _is_allowed_registration_import("foundation.ops")


# --- IA-1: `AuditEvent.objects` is `identity/audit.py`'s alone ---------

_AUDIT_WRITER = frozenset({"identity/audit.py"})


def _audit_manager_access(source: str) -> int:
    """How many `AuditEvent.objects` attribute accesses `source` makes --
    `ast.Attribute(value=ast.Name(id="AuditEvent"), attr="objects")`, the
    SAME shape the chat `.objects` gate uses, so it catches the access in
    a function body as readily as at module scope and does not trip over
    the word "objects" in a docstring."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "objects"
        and isinstance(node.value, ast.Name) and node.value.id == "AuditEvent"
    )


def test_no_module_outside_audit_touches_auditevent_objects():
    """Append-only, enforced at the level it is actually a property of.

    `AuditEvent.save()` refuses a re-save, but a queryset `.update()` or
    `.delete()` never calls `save()`. So the manager itself is off
    limits everywhere but the one writer -- `.update()` and `.delete()`
    included, because those are the two operations that actually destroy
    an audit trail. Reads go through `identity.audit.recent`/
    `for_target`, so the audit page needs no exception either.
    """
    out = subprocess.run(
        ["git", "ls-files", "--", "agents", "tools", "models", "foundation", "identity",
         "config", "scripts"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    offenders: dict[str, int] = {}
    for relative in out.stdout.splitlines():
        if not relative.endswith(".py") or _is_test_file(relative):
            continue
        if relative in _AUDIT_WRITER:
            continue
        count = _audit_manager_access((REPO_ROOT / relative).read_text(encoding="utf-8"))
        if count:
            offenders[relative] = count
    assert offenders == {}, offenders


def test_the_audit_writer_exclusion_is_a_closed_set_of_one():
    """Anti-vacuous pin on the exclusion itself."""
    assert _AUDIT_WRITER == frozenset({"identity/audit.py"})
    for relative in _AUDIT_WRITER:
        assert (REPO_ROOT / relative).is_file(), relative


def test_the_audit_gate_catches_update_and_delete_not_only_create():
    """Anti-vacuous pin, and the case a `.create`-only guard would have
    waved through -- which is also the case that actually destroys an
    audit trail."""
    assert _audit_manager_access("AuditEvent.objects.create(action='x')\n") == 1
    assert _audit_manager_access("AuditEvent.objects.update(action='x')\n") == 1
    assert _audit_manager_access("AuditEvent.objects.all().delete()\n") == 1
    assert _audit_manager_access("def f():\n    return AuditEvent.objects.filter()\n") == 1
    assert _audit_manager_access('"""AuditEvent.objects is off limits."""\n') == 0


# --- IA-1 (spec section 4.3, row 5): `request.user` and `.is_superuser`/
# `.is_staff` are `identity/`'s to read, everywhere else ------------------
#
# The one-request-to-one-principal rule (`identity.request.
# principal_for_request`) and the one administer-question (`identity.
# access.is_admin`) both exist so that "who is this" and "may they
# administer" have exactly one answer each. A view in `tools/`, `models/`
# or `agents/` reading `request.user` (or `.is_superuser`/`.is_staff`)
# directly would be a second opinion that bypasses both -- correct today,
# by accident, only because `principal_for_request` and Django's own
# `request.user` currently agree; a future divergence (a service
# principal, an admin surface Django's session does not know about) would
# make the direct read silently wrong while the sweep says nothing.

# Files allowed to read one of the two patterns below, with a reason.
# EMPTY, DELIBERATELY: the sweep (below) finds nothing outside `identity/`
# doing this today, so there is nothing to exempt. A future entry here
# must name the file and say why the direct read is genuinely
# unavoidable -- e.g. a template context processor Django's own auth
# views populate, which this platform does not own the shape of -- never
# "it was easier".
_REQUEST_USER_ALLOWED: frozenset[str] = frozenset()

_REQUEST_USER_SCANNED_COLUMNS = ("tools", "models", "agents")


def _request_user_scanned_files() -> list[str]:
    """Every tracked, non-test `.py` file under `tools/`, `models/` and
    `agents/` -- extracted so the anti-vacuous pin below can assert on
    the FILE LIST rather than on the hit dict, which is empty when the
    gate is green."""
    out = subprocess.run(
        ["git", "ls-files", "--"] + list(_REQUEST_USER_SCANNED_COLUMNS),
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in out.stdout.splitlines()
            if line.endswith(".py") and not _is_test_file(line)]


def _is_request_chain(node: ast.AST) -> bool:
    """Whether `node` is the base of a `request.user`-shaped access --
    a bare `request` name, or `<anything>.request` (`self.request.user`,
    the shape a class-based view's `self.request` carries)."""
    if isinstance(node, ast.Name) and node.id == "request":
        return True
    return isinstance(node, ast.Attribute) and node.attr == "request"


def _request_user_access_count(source: str) -> int:
    """How many `request.user` attribute accesses (any depth of
    `self.`/`obj.` prefix before `request`) and `.is_superuser`/
    `.is_staff` attribute accesses (any base -- these two names mean the
    same thing in this codebase wherever they are read) `source` makes."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr == "user" and _is_request_chain(node.value):
            count += 1
        elif node.attr in ("is_superuser", "is_staff"):
            count += 1
    return count


def test_no_view_outside_identity_reads_request_user():
    """Spec section 4.3, row 5. Every access question -- who is this,
    are they an administrator -- goes through `identity.request.
    principal_for_request` / `identity.access.is_admin` (or a column's
    own visibility function built on top of them), never a direct read
    of `request.user`/`.is_superuser`/`.is_staff` in a view outside
    `identity/` itself.

    KNOWN BLIND SPOT, stated rather than left for a reader to assume
    this is airtight: `_request_user_access_count` walks an AST for the
    LITERAL attribute-access shape (`request.user`, `self.request.user`,
    `.is_superuser`/`.is_staff`). A DYNAMIC equivalent --
    `getattr(request, "user")`, `getattr(obj, "is_superuser")`, or any
    other indirection that reaches the same value without writing the
    attribute name where this walk looks -- is NOT caught. And the sweep
    reads TRACKED `.py` FILES ONLY (`_request_user_scanned_files`): a
    template reading `user.*` directly -- `foundation/templates/
    _shell.html`'s sign-out form is exactly this -- is out of scope
    entirely, not merely allowed through. Neither gap is exploited
    anywhere in this tree today; closing them (an AST walk that also
    flags `getattr`, and a sweep over `.html` sources) is future
    hardening, not something this test claims to already do.
    """
    offenders: dict[str, int] = {}
    for relative in _request_user_scanned_files():
        if relative in _REQUEST_USER_ALLOWED:
            continue
        count = _request_user_access_count((REPO_ROOT / relative).read_text(encoding="utf-8"))
        if count:
            offenders[relative] = count
    assert offenders == {}, offenders


def test_the_request_user_sweep_reaches_real_files():
    """Anti-vacuous pin: a broken `git ls-files` call, or a typo'd
    column name, would make the gate above pass by looking at nothing."""
    files = _request_user_scanned_files()
    assert len(files) > 50, len(files)
    assert "tools/rag/views.py" in files
    assert "agents/chat/views/thread.py" in files


def test_the_request_user_allowlist_is_empty_and_stays_honest():
    """Anti-vacuous pin on `_REQUEST_USER_ALLOWED` itself: it is empty
    today, and this pin fails the moment a future entry names a file
    that either does not exist or no longer contains the pattern it was
    exempted for -- an allowlist entry nobody checks is a bypass with
    extra steps."""
    assert _REQUEST_USER_ALLOWED == frozenset()
    for relative in _REQUEST_USER_ALLOWED:  # pragma: no cover - empty today
        assert (REPO_ROOT / relative).is_file(), relative


def test_the_request_user_gate_would_actually_catch_a_violation():
    """Anti-vacuous pin: a gate that cannot fail proves nothing. Four
    synthetic shapes -- a bare `request.user`, a class-based view's
    `self.request.user`, and a direct `.is_superuser`/`.is_staff`
    read -- are each flagged by the same walk this gate uses."""
    assert _request_user_access_count("request.user.is_authenticated\n") == 1
    assert _request_user_access_count(
        "class V:\n    def get(self, request):\n        return self.request.user\n"
    ) == 1
    assert _request_user_access_count("if user.is_superuser:\n    pass\n") == 1
    assert _request_user_access_count("if account.is_staff:\n    pass\n") == 1
    assert _request_user_access_count('"""request.user is off limits."""\n') == 0


# --- IA-1: `GenerationJob.objects` is `tools/vision/visibility.py`'s
# and `tools/vision/services.py`'s alone ------------------------------

_VISION_JOB_MODELS = ("GenerationJob",)

# A CLOSED SET OF TWO, named, with the reason for each written out:
# `visibility.py` is where every read of this manager belongs (the
# whole point of this task), and `services.py` legitimately CREATES
# rows -- `submit_job` is the one create surface, and a create is not a
# visibility question. No carve-out for "just this one query" beyond
# those two -- the same discipline `_VISIBILITY_MODULE_EXCLUSION`
# states for `agents/visibility.py`.
_VISION_JOB_MODULE_EXCLUSION = frozenset({
    "tools/vision/visibility.py",
    "tools/vision/services.py",
})


def _direct_vision_job_access(source: str) -> list[str]:
    """Which of `_VISION_JOB_MODELS` `source` reaches via `<Model>.
    objects` -- the same AST shape `_direct_model_access` uses above."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute) and node.attr == "objects"
            and isinstance(node.value, ast.Name) and node.value.id in _VISION_JOB_MODELS
        ):
            hits.append(node.value.id)
    return hits


def test_no_vision_module_queries_generationjob_directly():
    """IA-1. `tools/vision/visibility.py` is the one place that answers
    "may this principal see this generated image"; a view or a job
    handler that grew its own `GenerationJob.objects.filter(...)` would
    silently skip that answer, on the one table this task exists to
    stop leaking."""
    out = subprocess.run(["git", "ls-files", "--", "tools/vision"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    offenders: dict[str, list[str]] = {}
    for relative in out.stdout.splitlines():
        if not relative.endswith(".py") or _is_test_file(relative):
            continue
        if relative in _VISION_JOB_MODULE_EXCLUSION:
            continue
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        hits = _direct_vision_job_access(source)
        if hits:
            offenders[relative] = hits
    assert offenders == {}, offenders


def test_the_vision_job_module_exclusion_is_a_closed_set_of_two():
    """Anti-vacuous pin on `_VISION_JOB_MODULE_EXCLUSION`: exactly two
    files, and both really exist."""
    assert _VISION_JOB_MODULE_EXCLUSION == frozenset({
        "tools/vision/visibility.py", "tools/vision/services.py",
    })
    for relative in _VISION_JOB_MODULE_EXCLUSION:
        assert (REPO_ROOT / relative).is_file(), relative


def test_the_vision_job_gate_would_actually_catch_a_violation():
    """Anti-vacuous pin: a synthetic `GenerationJob.objects.filter(x=1)`
    source really is flagged by the same walk this gate uses."""
    planted = "def f():\n    return GenerationJob.objects.filter(x=1)\n"
    assert _direct_vision_job_access(planted) == ["GenerationJob"]


# --- IA-2 (spec section 4.3, row 8): the library reads documents through
# `tools/rag/access.py`, never off the raw manager -------------------------

def _document_objects_count(source: str) -> int:
    """How many `Document.objects` attribute accesses `source` makes --
    `ast.Attribute(value=ast.Name(id="Document"), attr="objects")`, the
    same shape the chat gate above uses."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "objects"
        and isinstance(node.value, ast.Name) and node.value.id == "Document"
    )


def test_rag_views_reads_documents_through_the_access_module():
    """COUNTS ARE A READING SURFACE TOO, and they are the easiest thing
    to forget because they render no document. A sidebar that says
    "Finance (14)" to a member who may open none of them leaks exactly
    the fact the labels exist to hide.

    `tools.rag.access.readable_documents` and `::listable_documents` are
    the only sanctioned readers, so the library totals, the category
    sidebar counts and the tabular-row count cannot drift back onto the
    raw manager and cannot pick the wrong one of the two.
    """
    source = (REPO_ROOT / "tools/rag/views.py").read_text(encoding="utf-8")
    assert _document_objects_count(source) == 0


def test_the_document_gate_would_actually_catch_a_violation():
    """Anti-vacuous pin: the walk really does find the shape it claims,
    and really does ignore an unrelated `.objects`."""
    assert _document_objects_count("Document.objects.count()\n") == 1
    assert _document_objects_count("readable_documents(p).count()\n") == 0


# --- Workstreams (WS-1, spec section 8.2): the pin table reads through
# `tools/rag/workstreams.py` and `tools/rag/access.py`, never off the raw
# manager -------------------------------------------------------------

_PIN_READERS = ("tools/rag/workstreams.py", "tools/rag/access.py")


def _pin_objects_count(source: str) -> int:
    """`WorkstreamPin.objects` in `source` — the same AST shape
    `_document_objects_count` uses, so it catches an in-function reach as
    readily as a module-scope one and does not trip over the word
    "objects" in a docstring."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "objects"
        and isinstance(node.value, ast.Name) and node.value.id == "WorkstreamPin"
    )


def test_the_pin_table_has_exactly_two_sanctioned_readers():
    """The same shape, and for the same reason, the `Document.objects`
    gate exists: two readers of "what is in this stream" is how two
    surfaces come to disagree. `tools/rag/workstreams.py` owns the
    writes; `tools/rag/access.py` may read for the visibility value it
    builds. Every other module asks one of them a question.
    """
    offenders = {}
    for relative in _scanned("tools", "agents", "models", "foundation"):
        if relative in _PIN_READERS:
            continue
        count = _pin_objects_count((REPO_ROOT / relative).read_text(encoding="utf-8"))
        if count:
            offenders[relative] = count
    assert offenders == {}, offenders


def test_the_pin_gate_would_actually_catch_a_violation():
    assert _pin_objects_count("WorkstreamPin.objects.count()\n") == 1
    assert _pin_objects_count("stream_documents(p, s).count()\n") == 0


# --- A contract's own tests live beside the contract (C-58) ---------------

# `tools/rag/tests/test_gateway_transcriber.py` reads as a violation by
# the text heuristic below -- it imports `models.contracts.gateway` and
# never spells "tools.rag" -- but it is not one. Unlike the modules this
# gate moved, it is `@pytest.mark.django_db` and imports
# `models.registry.models` (`ModelConnection`, `RoleBinding`): a DB-backed
# integration test of the resolve -> get_engine -> build seam, byte-
# parallel to `tools/vision/tests/test_gateway.py`'s
# `TestGetImageGenerator*` classes (which the docstring says outright, and
# which correctly stays in `tools/vision` for the identical reason). It
# only dodges the heuristic because it defines its own private
# `_clear_bindings()` rather than importing vision's shared
# `clear_bindings()` -- itself an established convention in this repo
# (see `models/registry/tests/_helpers.py::make_job_ctx`'s docstring: a
# small per-app duplicate over a cross-app test import), not an oversight
# to fix by importing across columns. `models/contracts/tests/`'s whole
# character (every module's own docstring says so) is DB-free; a
# DB-backed integration test does not belong there.
_NOT_A_CONTRACTS_ONLY_TEST = {
    "tools/rag/tests/test_gateway_transcriber.py":
        "DB-backed integration test of the gateway/registry seam, byte-parallel to "
        "tools/vision/tests/test_gateway.py which stays put for the same reason -- "
        "see the comment above this constant",
}


def test_a_contract_module_keeps_its_tests_beside_itself():
    """C-58. `models/contracts/` had no tests directory at all, and modules
    testing nothing but `models.contracts.*` lived in `tools/vision` and
    `tools/rag` -- in a consumer column, because that column happened to
    be the first consumer. The next consumer would not have found them."""
    assert (REPO_ROOT / "models/contracts/tests/__init__.py").is_file()
    for column in ("tools/vision", "tools/rag"):
        for path in (REPO_ROOT / column / "tests").glob("test_*.py"):
            relative = f"{column}/tests/{path.name}"
            if relative in _NOT_A_CONTRACTS_ONLY_TEST:
                continue
            text = path.read_text()
            imports_contracts = "from models.contracts" in text
            imports_own_column = column.replace("/", ".") in text
            assert not (imports_contracts and not imports_own_column), (
                f"{path} tests only `models.contracts` and belongs beside it")


# --- C-56: no test module grows past the in-repo split precedent ---------

# `tools/vision/tests/` is the in-repo precedent for splitting one views
# module's tests by feature area, and its largest file is 2,014 lines --
# so 2,100 is the threshold, chosen as "past the precedent" rather than
# as an aspiration nobody meets. `models/registry/tests/test_views.py`
# (7,606 lines, C-56a) and `tools/rag/tests/test_views.py` (4,915 lines,
# C-56b) both crossed it; both are split by this same plan, into six and
# four files respectively, each comfortably under it. This pin is what
# keeps either from silently growing back past it, one class at a time.
_TEST_MODULE_SPLIT_THRESHOLD = 2100

# KNOWN OVER, and deliberately not split by this plan: not named in C-56.
# ONLY files ACTUALLY over the threshold TODAY belong here --
# `test_the_split_threshold_exemption_is_not_stale` below asserts that for
# every entry, so an exemption that stops being true (the file shrinks
# back under, or is split, or is deleted) fails loudly instead of quietly
# exempting nothing. The brief's original four-file list (`test_jobs.py`,
# `test_media.py`, `models/queue/tests/test_worker.py`,
# `agents/chat/tests/test_sidebar.py`) are all now UNDER 2,100 lines and
# were removed from this set for exactly that reason (orchestrator ruling,
# review round on this task) -- if any of them crosses back over, it earns
# its own dated entry then, not a standing one kept "just in case".
_KNOWN_OVER_SPLIT_THRESHOLD = {
    # WP5's ingest-editing tasks (C-08, C-14, C-17, C-18, C-25, C-32,
    # C-46) grew `tools/rag/ingest.py` -- and its test module along with
    # it -- past the threshold between this plan's brief being written
    # and this task running. Not named in C-56, not this plan's to split.
    "tools/rag/tests/test_ingest.py",
    # PR #89 (hygiene sweep) merging PR #92 (the settings assistant):
    # `models/queue/tests/test_worker.py` was one of the four files a
    # prior split brought back under 2,100 (see this set's own header
    # comment) -- the merge concatenated its own `_evict_to_match_plan`
    # phase-decomposition tests with #92's independent `TestTheSingle
    # SettingsReadPerTick` class (S6), non-overlapping content neither
    # side could have split around the other not knowing it existed, and
    # that pushed it back over. Not this merge's to re-split.
    "models/queue/tests/test_worker.py",
    # `agents/chat/tests/test_assistant_panel.py` is PR #92's own new
    # module (the settings assistant), arriving already past the
    # threshold -- #92 was reviewed and merged with no knowledge of C-56,
    # so it is not itself a violation this merge introduced. Not this
    # merge's to split.
    "agents/chat/tests/test_assistant_panel.py",
    # `agents/chat/tests/test_thread.py` WAS HERE, added by chat-cluster
    # task 4 after task 3's context meter carried that module over the
    # threshold unnoticed. The whole-branch review (I-5) ruled the entry
    # out: AGENTS.md non-negotiable 2 ends "A dated exemption list gets
    # its exempted code removed, not grown", it carves out no exception
    # for a well-argued growth, and the branch that grew it is the branch
    # that re-wrapped that rule's own file. The module was split instead
    # -- `agents/chat/tests/test_thread_meter.py`, the meter's own edge --
    # and BOTH meter classes moved together, so task 4's plan decision
    # ("splitting the two halves across two modules is how they come to
    # disagree") is honoured rather than overruled.
}


def _tracked_test_modules() -> list[str]:
    """Every tracked `tests/test_*.py` module, via `git ls-files` -- not
    `Path.rglob`, which would also walk `.venv/`'s own vendored test
    suites (numpy, networkx, and regex each ship one well past 2,100
    lines) sitting inside REPO_ROOT. `git ls-files` only ever reports
    tracked files, the same discipline every other gate in this file
    already uses."""
    out = subprocess.run(
        ["git", "ls-files", "--", "*/tests/test_*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def _exceeds_split_threshold(text: str) -> bool:
    return len(text.splitlines()) > _TEST_MODULE_SPLIT_THRESHOLD


def test_no_test_module_grows_past_the_split_threshold():
    """C-56. See `_TEST_MODULE_SPLIT_THRESHOLD`'s own comment for where
    2,100 comes from and `_KNOWN_OVER_SPLIT_THRESHOLD`'s for the modules
    this plan deliberately leaves over it."""
    offenders = []
    for relative in _tracked_test_modules():
        if relative in _KNOWN_OVER_SPLIT_THRESHOLD:
            continue
        text = (REPO_ROOT / relative).read_text()
        if _exceeds_split_threshold(text):
            offenders.append(relative)
    assert not offenders, f"test modules past the split threshold: {offenders}"


def test_the_tracked_test_module_sweep_reaches_real_files():
    """Anti-vacuous pin: a broken `git ls-files` call would make the gate
    above pass by looking at nothing, and a stray `.venv/` hit would mean
    it is looking at the wrong thing."""
    modules = _tracked_test_modules()
    assert len(modules) > 150, len(modules)
    assert "tools/rag/tests/test_views_ask.py" in modules
    assert not any(".venv" in m for m in modules)


def test_the_split_threshold_exemption_is_a_closed_set_and_stays_honest():
    """Anti-vacuous pin on `_KNOWN_OVER_SPLIT_THRESHOLD` itself: every
    entry really exists, so a renamed or deleted file cannot go on
    silently exempting nothing."""
    assert _KNOWN_OVER_SPLIT_THRESHOLD == {
        "tools/rag/tests/test_ingest.py",
        "models/queue/tests/test_worker.py",
        "agents/chat/tests/test_assistant_panel.py",
    }
    for relative in _KNOWN_OVER_SPLIT_THRESHOLD:
        assert (REPO_ROOT / relative).is_file(), relative


def test_the_split_threshold_exemption_is_not_stale():
    """A stronger anti-vacuous pin than the one above: every exempted
    file must ACTUALLY be over the threshold today, not merely exist.
    This is what makes the exemption set self-correcting -- the review
    round on this task found the brief's original four-entry set had
    drifted (all four had fallen back under 2,100 lines and were kept
    anyway, exempting nothing); this pin is what would have caught that
    the moment it happened, rather than leaving it for the next reader
    to notice by hand."""
    stale = [
        relative for relative in _KNOWN_OVER_SPLIT_THRESHOLD
        if not _exceeds_split_threshold((REPO_ROOT / relative).read_text())
    ]
    assert not stale, f"no longer over the split threshold -- drop from the exemption set: {stale}"


def test_the_split_threshold_gate_would_actually_catch_a_violation():
    """Anti-vacuous pin: a gate that cannot fail proves nothing. A
    synthetic module 100 lines past the threshold is flagged; one exactly
    at the threshold is not."""
    assert _exceeds_split_threshold("\n" * (_TEST_MODULE_SPLIT_THRESHOLD + 100))
    assert not _exceeds_split_threshold("\n" * _TEST_MODULE_SPLIT_THRESHOLD)
