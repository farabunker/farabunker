"""The shared app bar and the settings sidebar
(`foundation/templates/_shell.html`, `_settings.html`), UI-1 and UI-2.

THE SHELL RENDERS ON EVERY PAGE, so its own claims are asserted here once
rather than re-asserted per page: which entries appear, who sees the
account area, and what the settings sidebar holds. The per-page nav tests
(`tools/rag/tests/test_views_ask.py`, `agents/chat/tests/test_mount.py`,
`tools/vision/tests/test_views_create.py`, `models/registry/tests/
test_views_console_and_roles.py`, `foundation/setup/tests/test_views.py`)
keep their own job: that each page marks ITSELF current.

`/setup/` is the page these tests drive, and deliberately: it is the one
route classified PUBLIC, so the SAME url can be fetched by an anonymous
visitor, a member and an administrator on an accounts-on box. Anything
else would answer a redirect for one of the three and the shell would
never render at all. It is also a settings-area page, so the same fetch
carries the sidebar this module has to assert -- an anonymous visitor's
sidebar included, which no other url in the tree can show.
"""
from __future__ import annotations

import html
import importlib
import re

import pytest
from django.conf import settings as django_settings
from django.test import override_settings
from django.urls import clear_url_caches, reverse

from foundation.settings_area import first_entry, visible_entries
from foundation.tests._helpers import (
    bind_role, clear_bindings, make_admin, make_user, posture, seed_sweep_posture,
    sign_in,
)
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN
from models.contracts.roles import CHAT_CONVERSE_ROLE

pytestmark = pytest.mark.django_db

_ADMIN_ENTRIES = ("Accounts", "Groups", "Entitlements", "Tool access", "Agent access")


@pytest.fixture(autouse=True)
def _fresh_box(db, settings):
    """Nothing bound -- from the DB OR from the environment. The second
    half matters: `availability()` unions in the roles
    `LLM_MODEL`/`EMBED_MODEL` resolve, so a developer with either in
    their `.env` would otherwise turn `test_a_model_backed_entry_appears
    _only_once_its_role_is_bound` red for a reason that has nothing to do
    with the shell (the same ambient-dependency rule commit ea5157d
    applied to `FARABUNKER_FEATURES`)."""
    seed_sweep_posture()
    clear_bindings()
    settings.LLM_MODEL = None
    settings.EMBED_MODEL = None


_APP_BAR = re.compile(r'<header class="app-bar">.*?</header>', re.S)
_SIDEBAR = re.compile(r'<nav class="settings-nav" aria-label="Settings">.*?</nav>', re.S)
_LINK = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def _page(client, query: str = "") -> str:
    """`query` -- the one settings page these tests drive, fetched with a
    query string. Used only by the drift test's assistant-open leg: the
    panel's whole state is `?assistant=1` on the URL, so the sidebar's
    open-state rendering cannot be asserted without fetching one."""
    response = client.get(reverse("setup-index") + query)
    assert response.status_code == 200, response.status_code
    return response.content.decode()


def _bar(client, query: str = "") -> str:
    """JUST THE APP BAR, not the page it sits on.

    Sliced deliberately: `/setup/` prose links to Models by name, so a
    bare `">Models</a>" not in body` assertion would be answered by the
    page's own body copy rather than by the nav -- which is the one thing
    this module is about. Every assertion here is about the bar, so every
    assertion here reads only the bar.
    """
    match = _APP_BAR.search(_page(client, query))
    assert match is not None, "the shell rendered no app bar at all"
    return match.group(0)


def _sidebar(client, query: str = "") -> str:
    """JUST THE SETTINGS SIDEBAR, for the same reason `_bar` slices the
    bar: this page's own body links to half of what the sidebar lists."""
    match = _SIDEBAR.search(_page(client, query))
    assert match is not None, "the settings shell rendered no sidebar at all"
    return match.group(0)


class TestTheBrand:
    def test_it_links_home_from_every_page(self, client):
        with posture(POSTURE_OPEN):
            bar = _bar(client)
        assert f'href="{reverse("landing")}" class="brand ' in bar
        assert ">farabunker</a>" in bar

    def test_the_landing_page_marks_the_brand_current(self, client):
        """The brand IS the landing page's nav entry, so that is what it
        marks -- there is no second "Home" link beside it."""
        with posture(POSTURE_OPEN):
            body = client.get(reverse("landing")).content.decode()
        assert f'href="{reverse("landing")}" class="brand current"' in body


class TestTheCurrentSectionLinkStaysClickable:
    """ROUND-16 ADDENDUM (owner feedback, verbatim: "when I'm on chat I
    should still be able to select chat to go back to the main chat
    menue... it deactivates when I'm on chat"). `nav.top-nav a.current`
    used to carry `pointer-events: none` -- the `<a href>` markup was
    always real (every existing test in this class family already
    proved that, by asserting `href=...class="current"` together), but
    that CSS property made the current section's own top-bar link
    functionally unclickable while already inside that section. Fixed
    APP-WIDE (every section this bar names), NOT chat-specific, and
    scoped PURELY to the top bar: `nav.settings-nav a.current` (a
    DIFFERENT nav, the settings sidebar) keeps its own identical rule
    unchanged, pinned here in both directions so a future edit that
    widened this fix by accident -- or narrowed it -- is caught either
    way.
    """

    def test_the_top_nav_current_rule_no_longer_disables_the_link(self, client):
        with posture(POSTURE_OPEN):
            body = client.get(reverse("landing")).content.decode()
        start = body.index("nav.top-nav a.current {")
        rule = body[start:body.index("}", start)]
        assert "pointer-events" not in rule
        # Non-vacuous: the visual marking this rule exists for is still
        # there -- removing the WHOLE rule would also pass a bare
        # "pointer-events not in rule" check.
        assert "font-weight: 600;" in rule

    def test_the_settings_nav_current_rule_is_untouched(self, client):
        """SCOPE DISCIPLINE, THE OTHER DIRECTION: the addendum's own
        words, "purely the section links in the top bar; nothing else
        in the shell changes" -- the settings SIDEBAR's identical rule
        keeps its `pointer-events: none`, since clicking the settings
        page you are already reading is not the gesture this fix is
        about (and `.chat-nav-row.current`'s own docstring, `agents/
        chat/sidebar.py`, already makes the opposite call for the chat
        rail's OWN current row, on its own reasoning, unrelated to this
        fix)."""
        with posture(POSTURE_OPEN):
            body = client.get(reverse("landing")).content.decode()
        start = body.index("nav.settings-nav a.current {")
        rule = body[start:body.index("}", start)]
        assert "pointer-events: none;" in rule

    def test_the_current_chat_link_is_a_real_href_not_inert_text(self, client):
        """THE OWNER'S OWN EXACT SCENARIO: fetch a page where "Chat" is
        the current TOP-BAR entry (any page under `/chat/`, `agents.
        chat.templates.chat.base.html`'s own `nav_current_chat`
        override) and confirm the link still has a real, followable
        `href` -- `agents/chat/tests/test_mount.py`'s own `test_the_
        chat_page_has_the_standard_top_bar` pins the identical markup
        claim from the chat column's own side; this is the shell's own
        pin of the same fact, the "per-page nav tests keep their own
        job" split this module's docstring already states."""
        with posture(POSTURE_OPEN):
            bind_role(CHAT_CONVERSE_ROLE)
            body = client.get(reverse("chat-index")).content.decode()
        assert '<a href="/chat/" class="current">Chat</a>' in body


class TestTheAccountAreaOnAnOpenBox:
    """A household box has no accounts, so it has no account area -- and
    asking whether it did would be a query this box has promised not to
    run (`identity/tests/test_zero_queries.py`)."""

    def test_it_renders_nothing_at_all(self, client):
        """The MARKUP, not the stylesheet: the shell ships one inline
        `<style>` for every posture (that is what makes it one shell), so
        `.nav-account`'s rules are in the page either way. `_bar` reads
        only the bar, which is where the element would be."""
        with posture(POSTURE_OPEN):
            bar = _bar(client)
        assert "nav-account" not in bar
        assert "Sign in" not in bar
        assert "Sign out" not in bar
        assert "Your password" not in bar


class TestTheAccountAreaWhenAccountsAreOn:
    def test_a_signed_out_visitor_gets_a_sign_in_link_carrying_next(self, client):
        with posture(POSTURE_ENTERPRISE):
            bar = _bar(client)
        assert f'href="{reverse("identity-login")}?next={reverse("setup-index")}"' in bar
        assert "Sign out" not in bar

    def test_a_signed_in_person_is_named_legibly(self, client):
        """The username is the account area's only job, so it renders in
        its own element -- not folded into a muted run of link text where
        nobody can pick it out."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user(username="ada"))
            bar = _bar(client)
        assert '<span class="nav-username">ada</span>' in bar

    def test_your_password_lives_in_the_account_area(self, client):
        """Moved out of the main link row in UI-1: it is an account
        action, and it was the one entry in that row about the person
        rather than about the box."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            bar = _bar(client)
        head, _, tail = bar.partition('<div class="nav-account">')
        assert reverse("identity-password-change") in tail
        assert reverse("identity-password-change") not in head

    def test_signing_out_is_a_post_form_and_never_a_link(self, client):
        """RULING R-T8, unchanged by the rework: a GET logout is a URL
        anybody can put in an image tag."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            bar = _bar(client)
        assert f'<form method="post" action="{reverse("identity-logout")}"' in bar
        assert f'href="{reverse("identity-logout")}"' not in bar

    def test_a_signed_in_person_is_offered_no_sign_in_link(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            bar = _bar(client)
        assert ">Sign in</a>" not in bar


class TestTheBarIsOneRow:
    """UI-2: the Manage row is gone and its two member-facing halves have
    different homes. Queue came UP into the one row -- it is activity,
    not configuration -- and everything else went behind one `Settings`
    entry, which is a link to `/settings/` and not to any page in
    particular."""

    def test_there_is_no_second_row_left(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            bar = _bar(client)
        assert "manage-nav" not in bar
        assert ">Manage<" not in bar

    def test_queue_and_settings_are_in_the_one_row(self, client):
        with posture(POSTURE_OPEN):
            bar = _bar(client)
        assert f'href="{reverse("jobs-queue")}"' in bar
        assert ">Queue</a>" in bar
        assert f'href="{reverse("settings-index")}"' in bar
        assert ">Settings</a>" in bar

    @pytest.mark.parametrize("who", ["anonymous", "member", "admin"])
    def test_everybody_is_offered_settings_and_queue(self, client, who):
        """No standing gate on either. Queue is class R and shows
        everybody their own rows; `Settings` points at a redirect that
        resolves to the first section this viewer may open, so it can
        never be a link to a refusal."""
        with posture(POSTURE_ENTERPRISE):
            if who == "member":
                sign_in(client, make_user())
            elif who == "admin":
                sign_in(client, make_admin())
            bar = _bar(client)
        assert ">Queue</a>" in bar
        assert ">Settings</a>" in bar

    def test_the_bar_names_no_settings_page_directly(self, client):
        """The whole point of the collapse: an administrator's bar holds
        ONE door to the operator surfaces, not nine."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            bar = _bar(client)
        for entry in _ADMIN_ENTRIES + ("Models", "Library", "Install guides"):
            assert f">{entry}</a>" not in bar


class TestTheSettingsSidebar:
    """No sidebar entry is availability-gated -- settings is where an
    operator fixes "nothing is bound" FROM. Each is gated on STANDING
    instead, which is a different question, and the gates are the Manage
    row's own gates carried over unchanged:

      * Install guides (class P) for everybody;
      * Models and Library, the two class-S operator pages. ORCHESTRATOR
        RULING R1: a member clicking either gets a 403, and a nav entry
        that can only ever refuse is not navigation;
      * the six identity/chat admin pages, which need accounts to be ON
        as well as an administrator looking.

    `identity_is_admin` is True in the open posture, so a household box
    still sees the full operator set -- the first two tiers.
    """

    # "Engine files" is ALSO flag-guarded (`"vision" in farabunker_features`)
    # on top of the ADMIN gate the other two carry -- every test in this
    # class runs with the feature at its default-enabled value (no test
    # here touches `FARABUNKER_FEATURES`), so it belongs beside them.
    # `TestTheEngineFilesEntryIsFlagGuarded`, below, is the one class that
    # turns the flag off and asserts this entry -- and only this entry --
    # disappears.
    _ADMIN_ONLY_OPERATOR = (">Models</a>", ">Library</a>", ">Chat</a>",
                            ">Job execution</a>", ">Engine files</a>")

    def test_the_operator_entries_are_shown_on_an_open_box(self, client):
        with posture(POSTURE_OPEN):
            sidebar = _sidebar(client)
        for entry in (">Install guides</a>",) + self._ADMIN_ONLY_OPERATOR:
            assert entry in sidebar

    def test_an_open_box_gets_neither_access_nor_box(self, client):
        """`identity_is_admin` is True in the open posture -- everybody
        operates a household box -- so the posture, not the admin
        answer, is what keeps these off: there are no accounts to
        administer. The GROUP LABELS go with them: a group whose every
        entry is hidden renders nothing, not an orphan heading."""
        with posture(POSTURE_OPEN):
            sidebar = _sidebar(client)
        for entry in _ADMIN_ENTRIES + ("Identity &amp; security",):
            assert f">{entry}</a>" not in sidebar
        assert ">Access<" not in sidebar
        assert ">Box<" not in sidebar
        assert ">Setup<" in sidebar

    def test_an_administrator_gets_every_group(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            sidebar = _sidebar(client)
        for entry in _ADMIN_ENTRIES:
            assert f">{entry}</a>" in sidebar
        assert reverse("identity-settings") in sidebar
        assert ">Identity &amp; security</a>" in sidebar
        for entry in (">Install guides</a>",) + self._ADMIN_ONLY_OPERATOR:
            assert entry in sidebar
        for label in (">Setup<", ">Access<", ">Box<"):
            assert label in sidebar

    def test_a_member_gets_only_what_a_member_can_open(self, client):
        """R1. The two class-S operator entries go with the six identity
        ones: a member is refused at the gate on all eight, and offering
        the link anyway only turns a permission decision into a dead end
        they have to discover by clicking."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            sidebar = _sidebar(client)
        for entry in _ADMIN_ENTRIES:
            assert f">{entry}</a>" not in sidebar
        for entry in self._ADMIN_ONLY_OPERATOR:
            assert entry not in sidebar
        assert ">Install guides</a>" in sidebar

    def test_a_member_is_not_left_with_an_empty_sidebar(self, client):
        """Anti-vacuous companion to the test above: R1 removes entries,
        it does not remove the sidebar -- a member still reaches the
        install guidance, under a group heading that still reads. WHICH
        entries a member gets is the drift test's job below; this one is
        only about the sidebar still being there."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            sidebar = _sidebar(client)
        assert ">Setup<" in sidebar
        assert _LINK.findall(sidebar)

    def test_the_page_marks_its_own_sidebar_entry(self, client):
        """`/setup/` is the page these tests drive, so it is the one the
        sidebar must mark -- and it marks the SIDEBAR entry, not a bar
        entry: the bar's `Settings` is marked once by the settings shell
        for every page in the area."""
        with posture(POSTURE_OPEN):
            body = _page(client)
        assert f'<a href="{reverse("setup-index")}" class="current">Install guides</a>' in body
        assert f'<a href="{reverse("settings-index")}" class="current">Settings</a>' in body

    @pytest.mark.parametrize("assistant_open", [False, True],
                             ids=["panel-collapsed", "panel-open"])
    @pytest.mark.parametrize("vision_on", [True, False], ids=["vision-on", "vision-off"])
    @pytest.mark.parametrize("box,who,admin", [
        # `identity_is_admin` is True in the open posture -- everybody
        # operates a household box -- so the open cell's expected
        # standing is True with nobody signed in at all.
        (POSTURE_OPEN, "anonymous", True),
        (POSTURE_ENTERPRISE, "anonymous", False),
        (POSTURE_ENTERPRISE, "member", False),
        (POSTURE_ENTERPRISE, "admin", True),
    ])
    def test_the_sidebar_is_the_table_and_lands_where_settings_sends_you(
        self, client, settings, box, who, admin, vision_on, assistant_open,
    ):
        """THE DRIFT TEST. `foundation/settings_area.py` types the order,
        the gates AND THE LABELS once, for `/settings/` to redirect by;
        this sidebar renders the same table as template `{% if %}`s,
        because a context processor would run on every page in the box
        and cost a query the console's own count pin forbids. Two
        readers, one truth -- and this is what holds them together.

        THE WHOLE PAIR LIST, not just the first href (UI-2 review, Minor
        5): the labels are half of the one-name-per-page rule this pass
        exists to enforce, so a `Entry.label` that drifted from the
        literal in the template has to fail HERE rather than survive
        because only hrefs were compared. `html.unescape` because the
        template writes "Identity &amp; security" the way the house
        writes every ampersand, and the table holds the NAME.

        Then, on top of that: whatever the sidebar offers FIRST is
        exactly where the bar's one `Settings` entry lands.

        `vision_on` (peer condition 9, review round 2026-09-02): "Engine
        files" is the ONE entry gated on a feature flag as well as
        standing (`Entry.feature`, `foundation/settings_area.py`), and
        `_settings.html`'s own `{% if "vision" in farabunker_features %}`
        is a SECOND place that same gate is expressed. Sweeping this
        whole drift test across both feature states is what proves the
        template's flag check and the table's `Entry.feature` can never
        drift from EACH OTHER in either state -- not merely that each one
        independently hides the entry when off (`TestTheEngineFilesEntry
        IsFlagGuarded` already pins that half). No `importlib.reload` of
        `config.urls` needed here even with vision off: `visible_entries`
        already drops the entry before `reverse()` is ever called on its
        url_name, and the template's own `{% if %}` skips the `{% url %}`
        the same way -- neither side ever asks the URLconf about a route
        that might not be mounted.

        `assistant_open` (persistence round, owner report: "if I have the
        chat up and then I click on a new settings link, the menu goes
        back into hiding"). The settings assistant panel's whole state is
        `?assistant=1` on the URL, so a sidebar of bare hrefs closes it
        on every click. Sweeping BOTH states is what pins the two halves
        of that fix together: collapsed renders hrefs byte-identical to
        before it existed, and open appends the flag to every entry --
        never for a viewer the panel does not render for, which is why
        the suffix below is gated on `admin` as well.
        """
        settings.FARABUNKER_FEATURES = frozenset({"vision"}) if vision_on else frozenset()
        query = "?assistant=1" if assistant_open else ""
        with posture(box):
            if who == "member":
                sign_in(client, make_user())
            elif who == "admin":
                sign_in(client, make_admin())
            sidebar = _sidebar(client, query)
            landing = client.get(reverse("settings-index") + query)

        # The panel renders for an administrator and for nobody else
        # (`agents/chat/context_processors.py`'s second guard), so the
        # sidebar carries its flag on exactly the same terms.
        flag = "?assistant=1" if (assistant_open and admin) else ""
        rendered = [(href, html.unescape(label))
                    for href, label in _LINK.findall(sidebar)]
        expected = [(reverse(entry.url_name) + flag, entry.label)
                    for entry in visible_entries(
                        admin=admin, posture=box, features=django_settings.FARABUNKER_FEATURES)]
        assert rendered == expected
        # THE LANDING REDIRECT CARRIES THE FLAG IT WAS GIVEN, whoever
        # asked: it is a query-string passthrough, not a second standing
        # decision (`foundation/settings_area.py::settings_index`). So
        # the two still agree on the PAGE always -- which is the drift
        # this assertion has always been about -- and on the flag
        # wherever the sidebar writes one at all.
        assert rendered[0][0].partition("?")[0] == landing.headers["Location"].partition("?")[0]
        assert landing.headers["Location"] == reverse(
            first_entry(
                admin=admin, posture=box, features=django_settings.FARABUNKER_FEATURES
            ).url_name) + query


class TestTheEngineFilesEntryIsFlagGuarded:
    """`Entry.feature="vision"` (`foundation/settings_area.py`): with the
    feature off, `config/urls.py` never mounts `/vision/` at all, so
    `reverse("vision-engine-files")` would raise -- the sidebar must not
    even try. Models/Library stay ADMIN-only with no flag, so turning
    vision off must remove Engine files alone, not the whole Setup
    group.

    THE FLAG-OFF CASE RELOADS `config.urls` ITSELF, the escape hatch
    `tools/vision/tests/test_views_create.py::
    TestTheLinkIsGoneWhenTheFeatureIsOff` already established
    (`tools/rag/tests/test_flag_hygiene.py`'s own hygiene test requires
    it): a bare `settings.FARABUNKER_FEATURES = frozenset()` in a file
    that also touches the test `Client`/`reverse()` risks permanently
    poisoning the process-wide URLconf's `vision/` mount if this happens
    to be the first URL resolution the suite runs.
    """

    def test_an_administrator_loses_only_engine_files_with_the_flag_off(self, client):
        from config import urls as config_urls

        try:
            with override_settings(FARABUNKER_FEATURES=frozenset()):
                importlib.reload(config_urls)
                clear_url_caches()
                with posture(POSTURE_ENTERPRISE):
                    sign_in(client, make_admin())
                    sidebar = _sidebar(client)
        finally:
            # Restore AFTER `override_settings` has already put the real
            # feature set back -- reloading while still inside the `with`
            # would rebuild the URLconf from the OVERRIDDEN (empty)
            # settings and leave every later test without the vision/
            # mount.
            importlib.reload(config_urls)
            clear_url_caches()

        assert ">Engine files</a>" not in sidebar
        assert ">Models</a>" in sidebar
        assert ">Library</a>" in sidebar

    def test_an_administrator_gets_it_back_with_the_flag_on(self, client, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            sidebar = _sidebar(client)
        assert ">Engine files</a>" in sidebar

    def test_a_member_never_sees_it_either_way(self, client, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            sidebar = _sidebar(client)
        assert ">Engine files</a>" not in sidebar


class TestTheUseSurfaceRow:
    def test_storage_and_the_record_are_offered_with_nothing_bound(self, client):
        with posture(POSTURE_OPEN):
            bar = _bar(client)
        assert ">Document library</a>" in bar
        assert ">Ask history</a>" in bar

    def test_a_model_backed_entry_appears_only_once_its_role_is_bound(self, client):
        with posture(POSTURE_OPEN):
            assert ">Chat</a>" not in _bar(client)
        bind_role(CHAT_CONVERSE_ROLE)
        with posture(POSTURE_OPEN):
            assert ">Chat</a>" in _bar(client)


class TestTheTargetHighlight:
    """The settings area's deep-link highlight (spec §4.2).

    ASSERTED ON THE RENDERED SHELL, not on the template file: the rule
    has to reach a real settings page. It lives in `_shell.html` rather
    than in `_settings.html`'s own `extra_style` because that region is
    unconditional -- every page that extends `_shell.html` inherits it
    regardless of what a leaf's own `extra_style` override does -- which
    is what protected this rule when TWO settings leaves
    (`foundation/setup/templates/setup/index.html:16` and `models/
    registry/templates/inference/console.html:39`) overrode
    `extra_style` with no `{{ block.super }}` and would have silently
    dropped it otherwise: an undercount the final review caught (finding
    I1/m5), because the settings assistant panel's own CSS had been
    placed in `_settings.html`'s `extra_style` on the strength of this
    same docstring naming only one leaf, and shipped unstyled on both.
    Coherence Wave A fixed both leaves' own `{{ block.super }}`, but the
    rule stays here rather than moving to `_settings.html`, precisely
    because this region does not depend on a leaf page remembering
    anything. Coherence Wave B (N5 re-review): the method below now
    walks every `SETTINGS_GROUPS` leaf rather than naming only
    `setup-index` -- the earlier version's name claimed "every settings
    leaf" and checked one; `test_assistant_panel.
    py::TestThePanelOnThePage::test_the_panel_css_reaches_every_
    settings_leaf` -- not a docstring in this file -- carries the same
    walk for the assistant panel's own CSS (re-check, R2).
    """

    def test_the_target_rule_reaches_every_settings_leaf(self, client, settings):
        """M3 (Coherence Wave B review): PINS ITS OWN FEATURE STATE,
        matching its sibling `test_assistant_panel.py::TestThePanelOnThePage::
        test_the_panel_css_reaches_every_settings_leaf` -- the walk
        reaches `Entry("Engine files", "vision-engine-files", ADMIN,
        feature="vision")`, and without this it only passed because
        `config/settings.py` happens to default `FARABUNKER_FEATURES` to
        `"vision"`. Under `FARABUNKER_FEATURES=''` this would otherwise
        be a `NoReverseMatch`, with the count assertion unreachable."""
        from foundation.settings_area import SETTINGS_GROUPS

        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            swept = 0
            for _group, entries in SETTINGS_GROUPS:
                for entry in entries:
                    body = client.get(reverse(entry.url_name)).content.decode()
                    assert ".settings-main :target" in body, entry.url_name
                    assert "var(--target-wash)" in body, entry.url_name
                    assert "scroll-margin-top" in body, entry.url_name
                    swept += 1
        # I3 (Wave C review): compared against the GATED view of the
        # table, never against the loop's own `sum(len(entries) ...)` --
        # that earlier spelling re-derived the count from the very
        # structure the unconditional loop above walks, so it could not
        # fail under any change and the comment beside it claimed a
        # guarantee it structurally did not provide. `visible_entries`
        # applies `_may_see` (standing, posture and `Entry.feature`), so
        # this DOES disagree the moment the loop walks an entry this
        # viewer would not be offered -- which is exactly the case where
        # sweeping it proves nothing.
        assert swept == len(visible_entries(
            admin=True, posture=POSTURE_ENTERPRISE, features=frozenset({"vision"}))), swept

    def test_the_wash_token_is_declared_in_both_theme_blocks(self, client):
        """ONE declaration per theme, so the highlight follows whichever
        is in force instead of hard-coding a colour that is right in only
        one of them. Two occurrences of the DECLARATION, and the rule
        above uses it without redeclaring it."""
        with posture(POSTURE_OPEN):
            body = client.get(reverse("setup-index")).content.decode()
        assert body.count("--target-wash:") == 2

    def test_the_wash_is_not_plain_panel(self, client):
        """NOT `background: var(--panel)` (spec §4.2, decision 24):
        several settings sections are ALREADY painted `--panel` --
        `setup/index.html`'s own `.card`, `_settings.html`'s `.msg` -- so
        a `--panel` highlight is invisible on exactly the pages a person
        is most likely to be sent to, and §14's "highlighted" would be a
        criterion a human could not honestly sign off."""
        with posture(POSTURE_OPEN):
            body = client.get(reverse("setup-index")).content.decode()
        rule_start = body.index(".settings-main :target")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "background: var(--target-wash)" in rule
        assert "background: var(--panel)" not in rule
        assert "box-shadow" in rule

    def test_a_settings_page_fetched_with_a_fragment_still_carries_the_anchor(self, client):
        """The other half of the feature: the browser does the scrolling
        and the CSS says which thing it scrolled to, so the only thing
        the SERVER has to get right is that the id is on the page. A URL
        fragment is never sent to the server, which is exactly why this
        needs no view change at all."""
        with posture(POSTURE_OPEN):
            body = client.get(reverse("setup-index")).content.decode()
        assert 'id="how-models-reach-farabunker"' in body


class TestTheBarsSettingsEntryCarriesTheAssistantPanel:
    """REVIEW ROUND 1, I2. The bar is ON every settings page
    (`_settings.html` extends `_shell.html`), so its one `Settings`
    entry is as much a settings link as the sidebar's own twelve -- and
    it was the one link in the area still dropping the panel's open
    flag, which made `settings_index`'s own passthrough dead code: with
    nothing in the tree emitting `/settings/?assistant=1`, no request
    ever reached it carrying the flag.

    THE HREF IS TAKEN OUT OF THE RENDERED BAR, never built here: a test
    that assembles the url it expects proves the route, not the link,
    which is exactly how the hole survived a green suite.

    Off the settings area the bar renders on pages with no `assistant`
    context at all, so the variable is empty there and those pages stay
    byte-identical -- the second test pins that on `/chat/`.
    """

    _SETTINGS_LINK = re.compile(r'<a href="([^"]*)"[^>]*>Settings</a>')

    def _bar_settings_href(self, bar: str) -> str:
        match = self._SETTINGS_LINK.search(bar)
        assert match is not None, "the app bar rendered no Settings entry"
        return match.group(1)

    def test_it_carries_the_flag_on_an_open_settings_page(self, client):
        with posture(POSTURE_OPEN):
            bar = _bar(client, "?assistant=1")
        assert self._bar_settings_href(bar) == reverse("settings-index") + "?assistant=1"

    def test_it_is_bare_on_a_collapsed_settings_page(self, client):
        with posture(POSTURE_OPEN):
            bar = _bar(client)
        assert self._bar_settings_href(bar) == reverse("settings-index")

    def test_it_is_bare_off_the_settings_area_even_with_the_parameter_typed(self, client):
        """The panel has no context off the settings area, so the
        variable renders empty and the bar on every other page in the
        box is unchanged -- with or without somebody hand-typing the
        parameter."""
        with posture(POSTURE_OPEN):
            body = client.get(reverse("chat-index") + "?assistant=1").content.decode()
        bar = _APP_BAR.search(body).group(0)
        assert self._bar_settings_href(bar) == reverse("settings-index")


_GUARD_MARKER = "Unsaved settings input"


def _guard_script(body: str) -> str:
    """JUST THE GUARD'S OWN `<script>`, sliced off its distinctive lead
    comment rather than off "the first `<script>` in the page" -- the
    idiom `models/registry/tests/test_views_engine_and_remove.py::
    test_autofill_script_is_inline_and_guarded` already uses, for the
    same reason: a settings page legitimately carries other scripts (the
    assistant panel's poller, the shared composer's Enter-to-send, the
    models console's own endpoint autofill), and which of them comes
    first in document order is not this module's business."""
    marker_at = body.index(_GUARD_MARKER)
    start = body.rindex("<script>", 0, marker_at) + len("<script>")
    return body[start:body.index("</script>", marker_at)]


class TestTheUnsavedInputGuard:
    """THE DIRTY-FORM NAVIGATION GUARD (owner report, live, 2026-09-16):
    typing into a settings field and then clicking the assistant bubble,
    a sidebar link, a panel jump-link or `Close` is a full page load, and
    the input went with it -- silently.

    IT LIVES IN THE SETTINGS CHROME (`foundation/templates/_settings.html`),
    which is the whole of its scoping: every settings page extends that
    template and no other page in the box does, so the guard ships
    exactly where the settings chrome renders and nowhere else -- the
    same "declare it once in the chrome" rule the sidebar's own
    `open_query` suffix and `TestTheBarsSettingsEntryCarriesTheAssistant
    Panel` already follow.

    PROGRESSIVE ENHANCEMENT, AND STORAGE-FREE. With JavaScript off every
    settings page behaves exactly as it did before this existed; nothing
    is held on the client between loads, which is why the dirty check is
    a scan of the live DOM against the SERVER-RENDERED defaults, made at
    unload time, rather than a flag some `input` listener has been
    maintaining since page load.
    """

    def _script(self, client) -> str:
        with posture(POSTURE_OPEN):
            body = client.get(reverse("setup-index")).content.decode()
        return _guard_script(body)

    def test_every_settings_leaf_carries_it_exactly_once(self, client, settings):
        """SWEPT OFF `SETTINGS_GROUPS` ITSELF, the way the assistant
        panel's own reach is swept (`agents/chat/tests/test_assistant_
        panel.py::TestThePanelOnThePage`), so a settings page added later
        is covered the day it is registered rather than the day somebody
        remembers to extend a hand-typed list. PINS ITS OWN FEATURE STATE
        (Global Constraint 13): one entry is gated on a feature token."""
        from foundation.settings_area import SETTINGS_GROUPS

        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            swept = 0
            for _group, entries in SETTINGS_GROUPS:
                for entry in entries:
                    body = client.get(reverse(entry.url_name)).content.decode()
                    assert body.count(_GUARD_MARKER) == 1, entry.url_name
                    assert "beforeunload" in _guard_script(body), entry.url_name
                    swept += 1
        # Compared against the GATED view of the table, never against the
        # loop's own `sum(len(entries) ...)`, for the reason the panel's
        # own sweep states at length: re-deriving the count from the
        # structure the loop walks cannot fail under any change.
        assert swept == len(visible_entries(
            admin=True, posture=POSTURE_ENTERPRISE,
            features=frozenset({"vision"}))), swept

    def test_it_is_absent_from_every_page_outside_the_settings_area(self, client):
        """THE SCOPE HALF, and the one that makes "settings pages only" a
        fact rather than an intention: a page that does not extend the
        settings chrome is byte-identical to what it was before."""
        with posture(POSTURE_OPEN):
            for url_name in ("chat-index", "jobs-queue"):
                body = client.get(reverse(url_name)).content.decode()
                assert _GUARD_MARKER not in body, url_name
                assert "beforeunload" not in body, url_name

    def test_it_ships_on_a_collapsed_page_and_on_an_open_one(self, client):
        """A settings page needs the guard whether or not the assistant
        panel is open: the panel's own links (`Close`, the jump strip)
        are among the navigations that discard input, and the collapsed
        bubble is itself a link to a full page load."""
        for query in ("", "?assistant=1"):
            with posture(POSTURE_OPEN):
                body = client.get(reverse("setup-index") + query).content.decode()
            assert body.count(_GUARD_MARKER) == 1, query

    def test_it_compares_the_live_control_against_its_rendered_default(self, client):
        """STATELESS DIRTY DETECTION. Every comparison is against the
        DOM's own record of what the server rendered -- `defaultValue`
        for text inputs and textareas, `defaultChecked` for checkboxes
        and radios, `defaultSelected` for a select's options -- which is
        what makes a field the operator typed into and then reverted BY
        HAND read as clean, and what makes markup the panel's own XHR
        swapped in after page load covered with no re-binding at all."""
        script = self._script(client)
        assert "beforeunload" in script
        assert "defaultValue" in script
        assert "defaultChecked" in script
        assert "defaultSelected" in script

    def test_a_save_does_not_prompt_about_the_form_it_is_saving(self, client):
        """THE SUBMIT EXEMPTION: a document-delegated `submit` listener
        records the form being submitted, and the unload scan skips that
        one form -- its values are being SAVED, not lost. A DIFFERENT
        form that is also dirty still prompts, because that edit really
        would be discarded by the redirect."""
        script = self._script(client)
        assert 'addEventListener("submit"' in script
        assert "defaultPrevented" in script

    def test_it_excludes_the_controls_that_are_never_an_operator_edit(self, client):
        """Hidden inputs (a CSRF token is not somebody's typing), the
        buttons, and any disabled control.

        FIX ROUND 1, Nit 2: `"disabled" in script` did not pin what this
        method's name claims -- `defaultIndex`'s own `!options[index].
        disabled` satisfied it on its own, so deleting the control-level
        exclusion from `controlIsDirty` left this green. It is the
        control-level spelling that is asserted now."""
        script = self._script(client)
        assert '"hidden"' in script
        assert '"submit"' in script
        assert "control.disabled" in script

    def test_it_stores_nothing_on_the_client(self, client):
        """The same sweep list `agents/chat/tests/test_assistant_panel.py
        ::TestTheOneScript::test_it_stores_nothing_on_the_client` uses,
        `document.cookie` included. Persistence for a settings edit is
        the Save button and nothing else."""
        script = self._script(client)
        for banned in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
            assert banned not in script, banned

    def test_it_binds_with_addeventlistener_and_writes_no_inline_handler(self, client):
        script = self._script(client)
        assert "addEventListener" in script
        assert "onbeforeunload" not in script
        assert "onclick" not in script

    def test_it_rolls_no_timer_of_its_own(self, client):
        """`foundation/ops/tests/test_shared_poller.py::test_no_template_
        outside_the_helper_rolls_its_own_poll_loop` bans `setTimeout` and
        `setInterval` in every template but the shared poller, and its
        docstring says a template that grows a timer "will fail this test
        and have to argue its case here by name". This guard does not
        have to: the submit exemption is read off the submit event's own
        `defaultPrevented` and cleared by the unload scan that consumes
        it, so there is no deferred clear to schedule. Pinned here too,
        beside the behaviour it shapes, rather than only in the gate that
        would catch it."""
        script = self._script(client)
        assert "setTimeout" not in script
        assert "setInterval" not in script

    def test_it_ignores_a_submit_that_will_not_unload_this_document(self, client):
        """FIX ROUND 1, Important 1. The exemption is only ever spent by
        an unload, so a submit that does NOT unload this document must
        not record one -- a `<form target="_blank">` (or a named frame)
        fires `submit`, navigates somewhere else entirely, and would
        otherwise leave `submittingForm` set for the life of the page,
        silently disarming the guard for that one form. No settings-area
        form carries `target` today; this keeps it that way from the
        script's side, and `TestTheGuardsOrderingInvariant` keeps it that
        way from the markup's."""
        script = self._script(client)
        assert "event.target.target" in script

    def test_its_select_fallback_states_its_own_precondition(self, client):
        """FIX ROUND 1, Minor 1. The "first non-disabled option" fallback
        is the browser's reset behaviour only for a select whose DISPLAY
        SIZE is 1. A `<select size="2">` without `multiple` auto-selects
        nothing, so its `selectedIndex` is -1 on load, and a fallback that
        answered 0 would prompt on every navigation off that page for
        ever. The same algorithm treats an option inside a disabled
        `<optgroup>` as unselectable, which `option.disabled` alone does
        not report. Neither shape exists in the tree today -- both are
        handled here so that adding one is not a silent false prompt."""
        script = self._script(client)
        assert "select.size" in script
        assert "OPTGROUP" in script


class TestTheGuardsOrderingInvariant:
    """FIX ROUND 1, Important 1 -- THE INVARIANT THE EXEMPTION RESTS ON,
    pinned rather than merely reasoned about.

    The guard reads `event.defaultPrevented` at the DOCUMENT bubble. That
    is after every handler bound to the form itself (target phase always
    precedes the bubble, whatever order anybody registered in), which is
    why the assistant panel's cancelling handler -- bound to its own form
    -- is always visible as a cancellation and records no exemption. It
    is NOT after a listener delegated to `document` and registered LATER:
    the guard sits in `{% templatetag openblock %} block content
    {% templatetag closeblock %}` and `_shell.html` renders `content`
    before `scripts`, so any settings leaf's own `{% templatetag
    openblock %} block scripts {% templatetag closeblock %}` registers
    after it. A delegated `submit` + `preventDefault()` there would run
    after the guard had already recorded the form, and -- because nothing
    but an unload spends the record -- would leave that form exempt for
    the life of the page. That is the very defect this round closes,
    restored, with no test red.

    Document delegation is this repository's SANCTIONED script pattern
    (`chat/_enter_to_send.html`'s own m1 ruling), so this is a likely
    future move rather than an exotic one. Hence a gate, in the shape
    `foundation/ops/tests/test_shared_poller.py` already uses for its own
    repo-wide ban: read the template files, derive the settings area from
    `_settings.html` itself rather than from a hand-typed list, and fail
    the build on the pattern.
    """

    _DOC_SUBMIT = re.compile(r"""document\s*\.\s*addEventListener\s*\(\s*['"]submit['"]""")
    # `(?<![-\w])target`, not `\btarget` (re-review, Nit 6): a word
    # boundary also sits inside `data-target=` / `aria-controls-target=`,
    # so the looser spelling would fail this gate on an attribute that is
    # not a form target at all. A false red rather than a false green,
    # but still wrong, and nothing in the settings area has to earn it.
    _FORM_TARGET = re.compile(r"<form\b[^>]*(?<![-\w])target\s*=", re.I | re.S)
    _EXTENDS = re.compile(r"""{%\s*extends\s+['"]([^'"]+)['"]""")
    _INCLUDE = re.compile(r"""{%\s*include\s+['"]([^'"]+)['"]""")

    _GUARD = "foundation/templates/_settings.html"

    def _templates(self) -> dict[str, str]:
        """Every template in the tree, by the NAME Django loads it under
        (its path below the nearest `templates/` directory) -> repo
        relative path. Both spellings are needed: `{% templatetag
        openblock %} extends {% templatetag closeblock %}` names a
        template by the former, this module reports by the latter."""
        from foundation.ops.tests._helpers import REPO_ROOT

        found = {}
        for path in sorted(REPO_ROOT.rglob("*.html")):
            parts = path.relative_to(REPO_ROOT).parts
            if ".claude" in parts or ".venv" in parts or "templates" not in parts:
                continue
            below = parts[parts.index("templates") + 1:]
            found["/".join(below)] = str(path.relative_to(REPO_ROOT))
        return found

    def _settings_area(self) -> dict[str, str]:
        """THE SETTINGS AREA, DERIVED. Everything that renders as part of
        a settings page, reached from `_settings.html` -- so a settings
        page added later is covered the day it is registered, exactly as
        the panel's own sweeps are. Returns name -> repo relative path.

        THE WALK IS ASYMMETRIC, AND THAT ASYMMETRY IS THE WHOLE DESIGN
        (re-review, Minor 3). Downward -- templates that EXTEND a reached
        template, and templates a reached template INCLUDES -- every hop
        is another thing rendering inside a settings page, so each is
        also a seed for the next hop. Upward, `_settings.html`'s own
        `extends` ancestors (`_shell.html`, and anything it in turn
        extended) render on every settings page too and belong in the
        SCANNED set -- a `document`-delegated `submit` added to the box's
        shared shell would reach every settings page and defeat the
        exemption exactly as a leaf's own would. But an ancestor must
        NEVER become a seed for the who-extends-me search: `_shell.html`
        is extended by every page in the box, so seeding it would balloon
        this gate from the settings area to the whole application and
        start failing on `/chat/`'s own sanctioned scripts. Ancestors and
        their includes therefore go into `reached` and nowhere else.
        """
        from foundation.ops.tests.test_css_ownership import _COMMENT_RE
        from foundation.ops.tests._helpers import REPO_ROOT

        templates = self._templates()
        reached = {"_settings.html"}

        # UPWARD FIRST, into `reached` only -- never into `frontier`.
        ancestors = []
        current = "_settings.html"
        while True:
            parents = [name for name in self._EXTENDS.findall(self._text(templates[current]))
                       if name in templates and name not in reached]
            if not parents:
                break
            current = parents[0]
            reached.add(current)
            ancestors.append(current)
        # An ancestor's own includes render on a settings page just as
        # much as the ancestor does, so they are scanned too -- by the
        # same rule and with the same restriction.
        pending = list(ancestors)
        while pending:
            for included in self._INCLUDE.findall(self._text(templates[pending.pop()])):
                if included in templates and included not in reached:
                    reached.add(included)
                    pending.append(included)

        frontier = ["_settings.html"]
        while frontier:
            name = frontier.pop()
            for candidate, relative in templates.items():
                if candidate in reached:
                    continue
                text = _COMMENT_RE.sub("", (REPO_ROOT / relative).read_text())
                if name in self._EXTENDS.findall(text):
                    reached.add(candidate)
                    frontier.append(candidate)
            # `include` runs the other way round: this template pulls
            # those in, so they render inside a settings page too.
            text = _COMMENT_RE.sub("", (REPO_ROOT / templates[name]).read_text())
            for included in self._INCLUDE.findall(text):
                if included in templates and included not in reached:
                    reached.add(included)
                    frontier.append(included)
        return {name: templates[name] for name in sorted(reached)}

    def _text(self, relative: str) -> str:
        from foundation.ops.tests._helpers import REPO_ROOT
        from foundation.ops.tests.test_css_ownership import _COMMENT_RE

        return _COMMENT_RE.sub("", (REPO_ROOT / relative).read_text())

    def test_the_detector_finds_the_pattern_in_the_guard_itself(self):
        """THE POSITIVE CONTROL, the same self-proving discipline
        `test_shared_poller.py::test_the_exemption_is_not_stale` uses: a
        scanner that matched nothing would pass the two gates below
        vacuously for ever. The guard IS a document-delegated `submit`
        listener, so the pattern must be found here -- and the settings
        area must really have been derived, not left empty."""
        area = self._settings_area()
        assert self._DOC_SUBMIT.search(self._text(self._GUARD)) is not None
        # Derived, not guessed, and in BOTH directions: the twelve leaves
        # and their bases come back downward through `extends`, the panel
        # and composer fragments through `include`, and `_shell.html`
        # upward through this template's own `extends` -- the last of
        # those was missing until the re-review's Minor 3, while this
        # comment already claimed it (`"_shell.html" in area` was False),
        # which is why the claim is an assertion now and not prose.
        assert len(area) > 12, sorted(area)
        assert "chat/_assistant_panel.html" in area
        assert "inference/console.html" in area
        assert "_shell.html" in area, sorted(area)
        # The asymmetry holds: reaching the shell must not drag in every
        # page that extends it. `/chat/`'s own leaves are not settings
        # pages and are none of this gate's business.
        assert "chat/conversation.html" not in area, sorted(area)
        assert "rag/ask.html" not in area, sorted(area)

    def test_no_other_settings_area_template_delegates_submit_to_the_document(self):
        """THE TEETH. A settings-area script that needs to cancel a
        submit must cancel it ON THE FORM, the way the assistant panel
        already does -- never in a listener delegated to `document`,
        which this guard's `defaultPrevented` read cannot see when it is
        registered later. `chat/_assistant_panel.html` is the proof that
        the form-bound spelling is available and sufficient."""
        offenders = [
            relative for name, relative in self._settings_area().items()
            if name != "_settings.html" and self._DOC_SUBMIT.search(self._text(relative))
        ]
        assert offenders == [], offenders

    def test_no_settings_area_form_opens_its_submit_somewhere_else(self):
        """The other half of the same invariant, in markup: a `<form
        target="...">` fires `submit` and never unloads this document, so
        the exemption it records would never be spent. The script refuses
        to record one (`TestTheUnsavedInputGuard::test_it_ignores_a_
        submit_that_will_not_unload_this_document`); this keeps the shape
        out of the settings area in the first place."""
        offenders = [
            relative for name, relative in self._settings_area().items()
            if self._FORM_TARGET.search(self._text(relative))
        ]
        assert offenders == [], offenders
