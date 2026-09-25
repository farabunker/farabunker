"""Every page's title and heading agree with what the nav calls it (UI-1
work item E).

BEFORE THIS, THE SAME PAGE HAD THREE NAMES. The nav said "Model setup",
the heading said "Model management console", the tab said "farabunker —
Model management console"; Ask's heading was "farabunker — Ask your
knowledge base" while the nav said "Ask". A person cannot navigate a box
whose pages will not agree on what they are called, and the brand belongs
in the app bar rather than glued to the front of every heading.

THE RULE, and it is asserted below rather than described:
  * `<title>` is "X — farabunker", where X is the page's own name. The
    landing page is the one exception and the honest one: its name IS the
    brand, so its title is just "farabunker".
  * `<h1>` is exactly that same X -- or, in one recorded case, an obvious
    expansion of the nav's label for the surface (see `_EXPANSIONS`).

EVERY PAGE THE APP BAR OR THE SETTINGS SIDEBAR LINKS TO, not a
convenient subset. The six identity/chat admin pages are swept too, and
they are why `_viewing` exists: each is class S or A, so it needs a
signed-in administrator on an accounts-on box before it will render at
all. Sweeping only the pages that answer an anonymous open box is how
"Tool entitlements" survived under a nav entry that says "Tool access"
-- the exact three-names-for-one-page defect this module exists to
close.

FOUR NAMES CHANGED IN UI-2, when the Manage row became a settings area
with a sidebar that already says SETUP above its first group: "Model
setup" -> Models, "Library settings" -> Library, "Setup" -> Install
guides, and the identity posture page's "Settings" -> Identity &
security, which could not go on sharing its name with the app bar's
entry into the whole area. Display names only: no url name, path or
Python identifier moved.

The rest are driven in the OPEN posture with every role bound, so each
renders its real body rather than a redirect or an unavailable-model
banner.
"""
from __future__ import annotations

import html
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.urls import reverse

from foundation.settings_area import SETTINGS_GROUPS
from foundation.tests._helpers import (
    bind_role, clear_bindings, make_admin, posture, seed_sweep_posture, sign_in,
)
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN
from models.contracts.roles import (
    CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE, RAG_EMBED_ROLE, VISION_GENERATE_ROLE,
)

pytestmark = pytest.mark.django_db

# url name -> the page's own name, as the nav and the heading both say it.
_NAMES = {
    "chat-index": "Chat",
    "rag-ask-page": "Ask",
    "rag-search": "Search",
    "rag-documents": "Document library",
    "rag-history": "Ask history",
    "rag-settings": "Library",
    "chat-settings": "Chat",
    "jobs-queue": "Queue",
    # F1 (Coherence Wave C): the settings page the four `JobSettings`
    # forms moved to. A DIFFERENT page from `jobs-queue` above, with a
    # different name on purpose -- one is the activity surface, one is
    # the policy that governs it.
    "jobs-settings": "Job execution",
    "inference-console": "Models",
    # The agent library (chat cluster, feature B). ADMIN-gated, not
    # ACCOUNTS_ADMIN, so it is NOT in `_ADMIN_ONLY` below: `is_admin`
    # answers True for everybody on an open box, exactly as it does for
    # `inference-console` and `jobs-settings` above, and this page
    # renders its real body there.
    "settings-agents": "Agent library",
    "setup-index": "Install guides",
    "identity-login": "Sign in",
    # The settings sidebar's identity/chat admin pages. Each needs an
    # administrator on an accounts-on box -- see `_ADMIN_ONLY` below.
    "identity-users": "Accounts",
    "identity-groups": "Groups",
    "identity-entitlements": "Entitlements",
    "chat-tool-entitlements": "Tool access",
    "chat-agent-entitlements": "Agent access",
    "identity-settings": "Identity & security",
}
if "vision" in django_settings.FARABUNKER_FEATURES:
    _NAMES["vision-create"] = "Image generation"
    _NAMES["vision-engine-files"] = "Engine files"

# The pages an anonymous visitor on an open box cannot render: class S (or
# A) surfaces that need a signed-in administrator and accounts turned on.
_ADMIN_ONLY = frozenset({
    "identity-users", "identity-groups", "identity-entitlements",
    "chat-tool-entitlements", "chat-agent-entitlements", "identity-settings",
})

# The one heading that is not word-for-word its nav entry. "Images" names
# the whole /vision/ surface -- Generate and Gallery both live under it --
# and "Image generation" is what THIS page inside it does. An obvious
# expansion, recorded here so it stays a decision rather than a drift.
_EXPANSIONS = {"vision-create": "Images"}

_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)


def _named(pattern: re.Pattern, body: str) -> str:
    """The page's own name, as a READER sees it.

    `html.unescape` because one page name contains an ampersand
    ("Identity & security") and the template writes it the way the house
    writes every other one, as `&amp;`. Comparing the raw source instead
    would make this module's table a transcription of markup rather than
    a table of names, and the next name with an apostrophe or a dash
    would have to be encoded here too.
    """
    return html.unescape(pattern.search(body).group(1).strip())


@contextmanager
def _viewing(client, name: str):
    """The posture and principal `name` needs in order to render.

    An open box for everything the nav offers a household; a signed-in
    administrator on an accounts-on box for the six that only exist once
    accounts do."""
    if name in _ADMIN_ONLY:
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            yield
    else:
        with posture(POSTURE_OPEN):
            yield


def _page(client, name: str) -> str:
    response = client.get(reverse(name))
    assert response.status_code == 200, (name, response.status_code)
    return response.content.decode()


@pytest.fixture(autouse=True)
def _every_surface_available(db):
    seed_sweep_posture()
    clear_bindings()
    for role in (CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE, RAG_EMBED_ROLE, VISION_GENERATE_ROLE):
        bind_role(role)


@pytest.mark.parametrize("name", sorted(_NAMES))
def test_the_title_is_the_page_name_then_the_brand(client, name):
    with _viewing(client, name):
        body = _page(client, name)
    assert _named(_TITLE, body) == f"{_NAMES[name]} — farabunker"


@pytest.mark.parametrize("name", sorted(_NAMES))
def test_the_heading_is_the_page_name(client, name):
    with _viewing(client, name):
        body = _page(client, name)
    assert _named(_H1, body) == _NAMES[name]


def test_the_landing_pages_name_is_the_brand_itself(client):
    """The one page whose title is not "X — farabunker": its name IS the
    brand, and "farabunker — farabunker" would be absurd."""
    with posture(POSTURE_OPEN):
        body = _page(client, "landing")
    assert _named(_TITLE, body) == "farabunker"
    assert _named(_H1, body) == "farabunker"


def test_no_heading_still_carries_the_brand_as_a_prefix(client):
    """The regression this pass exists to close: the brand lives in the
    app bar now, so no page glues it to the front of its own heading."""
    for name in sorted(_NAMES):
        with _viewing(client, name):
            body = _page(client, name)
        assert not _named(_H1, body).startswith("farabunker")


@pytest.mark.parametrize("name", sorted(_EXPANSIONS))
def test_a_heading_that_expands_its_nav_label_is_recorded_as_such(client, name):
    """Anti-vacuous: an entry in `_EXPANSIONS` must name a label the nav
    really renders, or the carve-out is describing nothing."""
    with _viewing(client, name):
        body = _page(client, name)
    assert f">{_EXPANSIONS[name]}</a>" in body
    assert _NAMES[name].startswith(_EXPANSIONS[name].rstrip("s"))


def test_names_covers_every_settings_sidebar_entry():
    """THE SETTINGS-SIDEBAR HALF of the module docstring's own claim --
    "EVERY PAGE THE APP BAR OR THE SETTINGS SIDEBAR LINKS TO, not a
    convenient subset" -- stated as an assertion rather than left as a
    claim nothing checks. `chat-settings` and `vision-engine-files` were
    both added to `SETTINGS_GROUPS` after this module's own `_NAMES`
    table and neither made it in; nothing here went red when that
    happened, because nothing tied the two tables together. This does,
    for every page `SETTINGS_GROUPS` registers. It does NOT close the
    app-bar half of the same claim (review N2): there is no equivalent
    table this module can walk for the app bar's own links the way
    `SETTINGS_GROUPS` gives it one for the sidebar, so that half stays a
    claim the docstring makes and this file does not check.

    An entry gated on a `FARABUNKER_FEATURES` token this test run does
    not have on is exempt, the same carve-out `_NAMES`'s own
    `if "vision" in django_settings.FARABUNKER_FEATURES:` block above
    already makes for `vision-create`/`vision-engine-files` -- there is
    no route to reverse and no page to sweep for one, so a name for it
    would describe a page that does not exist in this run.
    """
    missing = [
        entry.url_name
        for _group_label, entries in SETTINGS_GROUPS
        for entry in entries
        if entry.url_name not in _NAMES
        and (entry.feature is None or entry.feature in django_settings.FARABUNKER_FEATURES)
    ]
    assert not missing, (
        f"SETTINGS_GROUPS route(s) missing from _NAMES: {missing} -- add each one's "
        f"real <h1>/<title> name to the table above."
    )


_RETIRED_SETTINGS_LINK_RE = re.compile(
    r"""\{%\s*url\s+'identity-settings'\s*%\}[^<]*>\s*Settings\s*<"""
)


def test_no_template_calls_identity_settings_by_its_retired_name():
    """F3's own guard (review M2): five templates called `identity-settings`
    "Settings" in their link text -- the name UI-2 retired specifically
    because "Settings" now names the whole area (see this module's own
    docstring, "FOUR NAMES CHANGED IN UI-2"). All five were hand-fixed;
    nothing stopped a sixth. A plain `git ls-files` grep, the same idiom
    `foundation/ops/tests/test_no_accounts_required.py` uses for its own
    retired-name sweep, rather than a Django render: the defect is text a
    template AUTHOR writes, not something that only exists once rendered.
    """
    repo_root = Path(django_settings.BASE_DIR)
    tracked = subprocess.run(
        ["git", "ls-files", "*.html"], cwd=repo_root,
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    offenders = []
    for relative in tracked:
        path = repo_root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError, IsADirectoryError):
            continue
        if _RETIRED_SETTINGS_LINK_RE.search(text):
            offenders.append(relative)

    assert not offenders, (
        f"template(s) link to identity-settings by its retired name \"Settings\": "
        f"{offenders} -- call it \"Identity & security\" instead."
    )


def test_the_retired_link_gate_is_reading_real_files():
    """Anti-vacuous pin: a broken `git ls-files` call, or a broken regex,
    would make the test above pass by finding nothing to check."""
    repo_root = Path(django_settings.BASE_DIR)
    tracked = subprocess.run(
        ["git", "ls-files", "*.html"], cwd=repo_root,
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert len(tracked) > 20, "the tracked-template list looks suspiciously short"
    assert _RETIRED_SETTINGS_LINK_RE.search(
        "<a href=\"{% url 'identity-settings' %}\">Settings</a>"
    ), "the pattern does not even match the shape it exists to catch"
