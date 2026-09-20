"""The isdecimal ruling, permanent (IA-2 whole-branch review, item 1).

`"²".isdigit()` is `True` -- Python's `str.isdigit()` accepts
superscript/subscript and other Unicode digit characters `int()` cannot
parse -- but `int("²")` raises `ValueError`. Two reproduced 500s
came from exactly this gap: `tools/vision/views.py`'s POST
`/vision/generate/` with `connection=²` (anonymous-reachable) and
`tools/rag/views.py`'s labels-bulk POST with `entitlements=²`
(member-reachable). Both are pinned by their own regression tests
(`tools/vision/tests/test_views_generate.py`,
`tools/rag/tests/test_views_upload_and_settings.py`).

THIS GATE IS THE STRUCTURAL HALF: every string a production module
checks before feeding it to `int()` must use `.isdecimal()` -- the
narrower predicate whose accepted set is exactly what `int()` can
parse -- and `.isdigit()` is banned OUTRIGHT rather than narrowed
call site by call site, because the failure mode is a 500 on whatever
surface a reviewer forgets to check next.

Lives beside `test_column_boundaries.py` and `test_import_law.py` for
the same reason both do: this is a repo-wide grep gate, not one
column's own rule, and `foundation.ops` is the app that already
reaches across every column by design. Pure file text; no Django ORM,
no database.
"""
from __future__ import annotations

import subprocess

from foundation.ops.tests._helpers import REPO_ROOT, _is_test_file

_NEEDLE = ".isdigit("

# A file may carry `.isdigit(` ONLY if listed here, with a reason. Seeded
# to exactly the one site the sweep deliberately LEFT alone rather than
# converting -- everywhere else the sweep found, in every column, is now
# `.isdecimal()`. A future `.isdigit()` anywhere else in production code
# has no entry here and fails this gate; the fix is to convert it, not
# to grow this dict.
ALLOWED: dict[str, str] = {
    # `connection` reaches this line only after `_plausible_connection_id`
    # (a few lines up in the SAME function) has already run `int(connection)`
    # inside its own real `try/except (TypeError, ValueError)` and returned
    # `False` -- refusing the turn with `UNREGISTERED_CONNECTION` -- for
    # anything `int()` cannot parse. By the time this `isdigit()` runs,
    # `connection` is already known to be a value `int()` accepts; a
    # non-decimal-but-isdigit string (`"²"`) never survives to reach
    # it. Converting it to `.isdecimal()` would be a harmless no-op, not a
    # bug fix -- left as `.isdigit()` on purpose, so this allowlist entry
    # documents WHY rather than hiding an unconverted site.
    "agents/runtime/preflight.py":
        "int(connection) already ran, and was already caught, in "
        "_plausible_connection_id's own try/except a few lines up in "
        "the same function -- nothing unparseable by int() reaches "
        "this isdigit() call",
}


def _production_py_files() -> list[str]:
    """Every tracked, non-test `.py` file in the repo."""
    out = subprocess.run(
        ["git", "ls-files", "--", "*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in out.stdout.splitlines() if line and not _is_test_file(line)]


def test_no_production_module_calls_isdigit():
    offenders: dict[str, int] = {}
    for relative in _production_py_files():
        if relative in ALLOWED:
            continue
        path = REPO_ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        count = text.count(_NEEDLE)
        if count:
            offenders[relative] = count
    assert offenders == {}, (
        "these production modules call .isdigit() with no ALLOWED entry -- "
        "the isdecimal ruling (IA-2 whole-branch review, item 1) bans it "
        "outright: a superscript/subscript digit passes .isdigit() but "
        "raises ValueError from int(), which is a 500 on whatever surface "
        f"forgets to guard it: {offenders}"
    )


def test_the_gate_is_reading_real_files():
    """Anti-vacuous pin: a broken `git ls-files` call would make the test
    above pass by looking at nothing."""
    tracked = _production_py_files()
    assert len(tracked) > 200, len(tracked)
    assert "config/settings.py" in tracked
    assert not any(_is_test_file(name) for name in tracked)


def test_the_gate_would_actually_catch_a_violation():
    """Pins the counting logic itself against a crafted sample, so a
    future edit to `_NEEDLE` or the count loop that quietly stopped
    matching real code would be caught here, not by a green suite that
    happens to have nothing left to find."""
    sample = "if raw.isdigit():\n    return int(raw)\n"
    assert sample.count(_NEEDLE) == 1
    assert "raw.isdecimal()".count(_NEEDLE) == 0


def test_the_one_allowed_file_still_needs_its_exception():
    """The allowlist names ONE file for ONE documented reason. If
    `agents/runtime/preflight.py` stops calling `.isdigit()` at all --
    converted, or the check removed -- this fails, which is the signal
    to delete the now-stale entry rather than let it quietly outlive the
    code it excuses."""
    assert list(ALLOWED) == ["agents/runtime/preflight.py"]
    path = REPO_ROOT / "agents/runtime/preflight.py"
    assert _NEEDLE in path.read_text(encoding="utf-8")
