"""Repo-wide static check: `THE VISION-FLAG RULE` (T10 review, test-hygiene
enforcement gap) documented in `tools.rag.tests._helpers`'s own module
docstring was, until this file, only ever ENFORCED by hand-written test
discipline -- nothing actually caught a new test that silently reintroduced
the exact bug the rule exists to prevent.

The rule, in short: `config/urls.py` builds its `vision/` mount
CONDITIONALLY, ONCE, off whatever `settings.FARABUNKER_FEATURES` happens to
be at Django's first real URL resolution in a given test process -- it never
re-evaluates after that. A test that overrides `FARABUNKER_FEATURES` to a
set WITHOUT `"vision"` and ALSO triggers URL resolution (Django's test
`Client`, or a bare `reverse()` call) risks being the unlucky FIRST such
resolution in the whole run, permanently building `config.urls` without the
`vision/` mount -- poisoning every LATER test, in ANY app, that expects
`reverse("vision-create")` (or any vision URL) to resolve, for the rest of
that process. See `tools.rag.tests._helpers`'s own "THE VISION-FLAG RULE"
docstring for the fuller mechanism and its one documented escape hatch:
reload (and always restore) `config.urls` yourself under the override,
`tools.vision.tests.test_views_create`'s own pattern.

This sweeps every `test_*.py` file under `tools/`, `models/`, `foundation/`,
and `agents/` (`_APP_TREES` below; mirrors
`models.registry.tests.test_template_comments`'s own repo-wide sweep, just
over Python source instead of HTML templates) and, for any file that BOTH
(a) overrides `FARABUNKER_FEATURES` with a LITERAL `frozenset(...)` --
`settings.FARABUNKER_FEATURES = frozenset(...)` (the direct pytest
`settings`-fixture idiom) or `override_settings(FARABUNKER_FEATURES=
frozenset(...))` (the Django context-manager idiom); a `some_variable`-
driven override (e.g. `tools.vision.tests.test_apps._run_ready_with
(features)`'s own indirection) is not statically checkable from its call
site and is simply not matched -- and (b) touches Django's test `Client`/
`reverse()` ANYWHERE in the file, asserts every such literal contains
`"vision"` -- UNLESS the file also reloads `config.urls`
(`importlib.reload(config_urls)`), the documented escape hatch.

Deliberately FILE-scoped, not per-test-function: a file with ANY Client/
reverse usage anywhere is treated as "at risk" for ALL its own literal
overrides, even ones in a different test function that happens not to touch
Client/reverse itself -- coarser than `_helpers.py`'s own per-test judgment
call, but cheap and sound against the codebase as it stands today (every
compliant file already keeps `"vision"` in every override it makes,
Client-adjacent or not; a file that genuinely wants a narrower, per-test
carve-out can earn it the same way `test_config.py`/`test_views_create.py`
did). "Keep it simple" per the task's own instruction: a regex sweep over
source text, not an AST walk or per-function analysis.
"""
from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

EXCLUDED_DIR_NAMES = {".venv", "node_modules", ".git", "__pycache__"}

# `FARABUNKER_FEATURES = frozenset(...)` -- matches BOTH the direct
# attribute-assignment idiom (`settings.FARABUNKER_FEATURES = frozenset(
# {"media"})`) and the `override_settings(...)` keyword-argument idiom
# (`override_settings(FARABUNKER_FEATURES=frozenset({"media"}))`): both
# are syntactically "FARABUNKER_FEATURES = frozenset(...)" wherever they
# appear. Does NOT match a variable-driven override (`FARABUNKER_FEATURES
# =features`) -- there is no literal set at that call site for a regex to
# evaluate; see this module's own docstring for why that's the correct,
# deliberate scope, not a gap.
_OVERRIDE_RE = re.compile(r"FARABUNKER_FEATURES\s*=\s*frozenset\(([^)]*)\)")

# Django's test Client (`Client(`, or the pytest-django `client` fixture
# used as `client.get(...)`/`client.post(...)`) and `reverse(` (URL
# resolution, whether or not it's fed straight to a Client call) are BOTH
# the risk this rule guards against -- see `_helpers.py`'s own "AND
# performs an actual HTTP request or URL resolution" clause.
_CLIENT_OR_REVERSE_RE = re.compile(r"\breverse\(|\bClient\(")

# The documented escape hatch (`tools.vision.tests.test_config`/
# `test_views_create`'s own pattern): a file that reloads `config.urls`
# itself, under the override, and always restores it in a `finally` block
# is deliberately EXERCISING the poisoning risk under controlled
# conditions, not stumbling into it.
_RELOADS_CONFIG_URLS_RE = re.compile(r"reload\(\s*config_urls\s*\)")


# The repo's app trees. Named explicitly (not a bare repo-root rglob) so
# `.venv/`, `data/`, `docs/`, and `.git/` are never walked. This tuple is a
# FILESYSTEM-PATH LITERAL and it fails SILENTLY: a stale entry makes this
# whole sweep a no-op instead of an error, which is why
# `test_the_sweep_actually_finds_files` below exists. `agents` is listed
# ahead of P1 putting tests there -- a tree with no test files is skipped,
# not an error, so this needs no edit when they appear.
_APP_TREES = ("tools", "models", "foundation", "agents")


def _iter_repo_test_files():
    repo_root = Path(settings.BASE_DIR)
    for tree in _APP_TREES:
        tree_root = repo_root / tree
        if not tree_root.is_dir():
            continue
        for path in tree_root.rglob("test_*.py"):
            if EXCLUDED_DIR_NAMES & set(path.parts):
                continue
            yield path


def test_the_sweep_actually_finds_files():
    """`_APP_TREES` is a filesystem-path literal: a stale entry turns this
    whole module into a no-op that passes. This is the assertion that
    makes that impossible -- the sweep must find, at minimum, this file
    and one test module from each column it claims to walk."""
    found = list(_iter_repo_test_files())
    names = {path.name for path in found}
    assert "test_flag_hygiene.py" in names
    trees = {path.relative_to(Path(settings.BASE_DIR)).parts[0] for path in found}
    assert {"tools", "models", "foundation"} <= trees, sorted(trees)
    assert len(found) >= 50, len(found)


def test_every_farabunker_features_override_in_a_client_touching_file_keeps_vision():
    violations = []
    for path in _iter_repo_test_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _CLIENT_OR_REVERSE_RE.search(text):
            continue  # never touches Client/reverse -- not at risk, see module docstring
        if _RELOADS_CONFIG_URLS_RE.search(text):
            continue  # the documented escape hatch -- reloads (and restores) config.urls itself
        for match in _OVERRIDE_RE.finditer(text):
            literal = match.group(1)
            if "vision" not in literal:
                lineno = text.count("\n", 0, match.start()) + 1
                violations.append(f"{path}:{lineno}: FARABUNKER_FEATURES=frozenset({literal})")

    assert not violations, (
        "Found FARABUNKER_FEATURES override(s) missing \"vision\" in a file that also "
        "touches Django's test Client/reverse() -- this risks permanently poisoning the "
        "process-wide URLconf's vision/ mount if it happens to be the first URL "
        "resolution in the run (see tools.rag.tests._helpers's THE VISION-FLAG RULE "
        "docstring). Add \"vision\" to the set, or reload config.urls yourself "
        "(tools.vision.tests.test_views_create's pattern) if the override is "
        "deliberately testing the feature-off case:\n" + "\n".join(violations)
    )


def test_sweep_finds_at_least_the_known_test_trees():
    """Sanity check on the walk itself, mirroring `models.registry.tests.
    test_template_comments`'s own -- if this ever finds zero files, the
    glob/exclusion logic broke silently and the assertion above would pass
    vacuously."""
    found = list(_iter_repo_test_files())
    assert len(found) > 10
    # `tools/rag/tests/test_views.py` (C-56b) split into four files by
    # feature area; any one of them proves the walk still finds this tree.
    assert any(p.name == "test_views_ask.py" and p.parent.name == "tests" for p in found)


def test_override_regex_matches_both_assignment_idioms():
    """Sanity check on `_OVERRIDE_RE` itself against the two real shapes
    this codebase actually uses -- proves the regex isn't accidentally
    matching neither (which would make the assertion above pass vacuously
    for every file)."""
    direct = 'settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})'
    via_override_settings = 'with override_settings(FARABUNKER_FEATURES=frozenset()):'
    variable_driven = "override_settings(FARABUNKER_FEATURES=features)"

    direct_match = _OVERRIDE_RE.search(direct)
    override_match = _OVERRIDE_RE.search(via_override_settings)

    assert direct_match is not None and "vision" in direct_match.group(1)
    assert override_match is not None and override_match.group(1) == ""
    assert _OVERRIDE_RE.search(variable_driven) is None
