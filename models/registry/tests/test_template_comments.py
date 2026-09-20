"""Repo-wide static check: Django's `{# ... #}` comment tag is SINGLE-LINE
only -- unlike `{% comment %}...{% endcomment %}`, an unclosed `{#` does
not swallow subsequent lines as a comment. It swallows nothing: everything
after the first line renders as literal text.

`models/registry/templates/inference/_installed_row.html` shipped a
`{# ... #}` comment spanning three lines, which leaked its own commentary
(including a stray "T9:" note) into the rendered Capability column on
every installed-model row at /inference/ -- a live-visible defect. This
test sweeps every `*.html` template in the repo (mirroring
`grep -rn '{#' --include='*.html' .`, excluding `.venv`/`node_modules`)
and asserts every `{#` on a line is matched by a `#}` on that SAME line,
so this class of bug can't silently recur elsewhere.
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings

EXCLUDED_DIR_NAMES = {".venv", "node_modules", ".git"}


def _iter_repo_html_templates():
    repo_root = Path(settings.BASE_DIR)
    for path in repo_root.rglob("*.html"):
        if EXCLUDED_DIR_NAMES & set(path.parts):
            continue
        yield path


def test_every_django_comment_tag_closes_on_the_same_line():
    """`{#` must be closed by `#}` on the same line, everywhere in the repo.
    A multi-line `{# ... #}` silently renders its trailing lines as
    literal page content instead of being hidden as a comment."""
    violations = []
    for path in _iter_repo_html_templates():
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.count("{#") != line.count("#}"):
                violations.append(f"{path}:{lineno}: {line.strip()!r}")

    assert not violations, (
        "Found `{# #}` Django comment(s) not closed on the same line "
        "(use `{% comment %}...{% endcomment %}` for multi-line comments "
        "instead):\n" + "\n".join(violations)
    )


def test_sweep_finds_at_least_the_known_template_trees():
    """Sanity check on the walk itself: if this ever finds zero templates,
    the glob/exclusion logic broke silently and the test above would pass
    vacuously."""
    found = list(_iter_repo_html_templates())
    assert len(found) > 10
    assert any(p.name == "_installed_row.html" for p in found)
