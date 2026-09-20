"""C-16: one poll loop in this repository, not three.

`chat/conversation.html`, `rag/ask.html` and `vision/create.html` each
grew their own `fetch` + `setTimeout` retry loop, and the duplication was
self-acknowledged in-repo -- `conversation.html`'s own comment says its
timing constants are "copied from tools/rag/templates/rag/ask.html ...,
which is the poller this one mirrors", i.e. kept in sync BY HAND.

`foundation/templates/_poller.html` is the one loop now. This gate stops
a fourth from appearing and stops the two repointed pages from quietly
growing their own again.

`chat/conversation.html` IS EXEMPT AND SAID SO HERE: it is held for PR
#88 and is repointed in a follow-up once that merges (the exemption
below carries the date and the reason, matching this repo's own
exemption convention).

`chat/_assistant_panel.html` IS ALSO EXEMPT, for a DIFFERENT reason
(PR #92, the settings assistant, merged after C-16 landed): its poll
loop is fragment-swap-driven, not the shared helper's `{done: true}`
JSON contract -- each tick swaps in a freshly rendered `#assistant-body`
and decides whether to continue from a DOM marker
(`dataset.assistantPending`) on the SWAPPED-IN markup, not from the raw
`Response` `onResponse` receives before any swap happens. Converting it
onto `pollUntilTerminal` is plausible (do the swap inside `onResponse`
and return `{retry: true}` based on the post-swap marker) but is a
behavior-touching rewrite of an already-reviewed, shipped feature's JS,
which a merge is the wrong moment to attempt -- left for a follow-up
that can test it on its own, not held for a numbered PR the way
`conversation.html`'s entry is.
"""
from __future__ import annotations

import pytest

from foundation.ops.tests._helpers import REPO_ROOT
from foundation.ops.tests.test_css_ownership import _COMMENT_RE

_POLLER = "foundation/templates/_poller.html"

# HELD FOR PR #88 -- repointed in the C-16 follow-up once that branch
# merges. Not a permanent exemption: the follow-up deletes this entry.
#
# `_assistant_panel.html` (PR #92, the settings assistant): a DIFFERENT,
# fragment-swap poll shape the shared helper's JSON contract does not fit
# without a behavior-touching rewrite -- see this module's own docstring.
# Also not a permanent exemption: a follow-up that converts it, tested on
# its own, drops this entry the same way #88's follow-up drops the first.
_EXEMPT = {
    "agents/chat/templates/chat/conversation.html",
    "agents/chat/templates/chat/_assistant_panel.html",
}


def _text(rel: str) -> str:
    """The template at `rel` (repo-relative), `{% comment %}` regions
    stripped -- the same convention every pin in `test_css_ownership.py`
    uses, and load-bearing here: `_poller.html`'s own comment block
    explains the include-vs-shell decision and says the word `<script>`
    three times while doing it."""
    return _COMMENT_RE.sub("", (REPO_ROOT / rel).read_text())


def test_the_shared_helper_exists_and_defines_the_function_once():
    text = _text(_POLLER)
    assert text.count("<script>") == 1
    assert "window.pollUntilTerminal" in text
    assert "window.POLL_DEFAULTS" in text


@pytest.mark.parametrize("path", ["tools/rag/templates/rag/ask.html",
                                  "tools/vision/templates/vision/create.html"])
def test_a_repointed_page_defines_no_loop_of_its_own(path):
    """The teeth. A page that calls the helper must not ALSO keep a
    hand-rolled retry loop -- which is exactly how the three copies grew
    in the first place. `setInterval` is banned outright (no page in this
    repo has ever used it, and it is the other spelling of this bug);
    `setTimeout` is allowed only inside the helper, so its absence here
    is what proves the loop actually moved."""
    text = _text(path)
    assert '{% include "_poller.html" %}' in text
    assert "pollUntilTerminal(" in text
    assert "setInterval" not in text
    assert "setTimeout" not in text


def test_the_exemption_is_not_stale():
    """Self-correcting, the same discipline `test_column_boundaries.py`'s
    `test_the_split_threshold_exemption_is_not_stale` uses for its own
    exemption set: `_EXEMPT` is a held-for-PR-#88 claim that
    `conversation.html` still rolls its own `setTimeout`/poll loop, not a
    standing pass. Once #88 repoints the page onto `_poller.html`, the
    loop this asserts on disappears and this test fails loudly -- which is
    exactly the signal that entry is stale and must come out of `_EXEMPT`,
    rather than the repo-wide gate below silently exempting a page that no
    longer needs it."""
    for relative in _EXEMPT:
        text = _text(relative)
        assert "setTimeout" in text or "setInterval" in text, (
            f"{relative} no longer rolls its own poll loop -- drop it from _EXEMPT"
        )


def test_no_template_outside_the_helper_rolls_its_own_poll_loop():
    """The repo-wide half: every template, not just the two repointed
    ones. Deferred scheduling is the helper's job and nobody else's.

    MEASURED, not guessed: before this task `setInterval` appeared in
    ZERO templates repo-wide, and `setTimeout` in exactly three -- the
    three pollers C-16 is about. So the condition can be the strict one
    (either spelling, anywhere) rather than a compound heuristic about
    `fetch(` being nearby, and a page that grows a timer for some
    unrelated reason will fail this test and have to argue its case here
    by name. That is the intended strictness, not an accident."""
    offenders = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        parts = path.relative_to(REPO_ROOT).parts
        if ".claude" in parts or ".venv" in parts:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        if rel == _POLLER or rel in _EXEMPT:
            continue
        text = _COMMENT_RE.sub("", path.read_text())
        if "setInterval" in text or "setTimeout" in text:
            offenders.append(rel)
    assert offenders == [], offenders
