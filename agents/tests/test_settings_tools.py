"""The settings assistant's two read-only tools (spec §5.2, §5.3, §10.3).

BOTH ARE READ-ONLY AND ONLY ONE IS GATED, deliberately -- see the module
under test for the reasoning. The gate that matters is asserted here as a
REFUSAL WITH NO VALUE IN IT, not merely as a non-200: a refusal that
leaked the posture in its message would be the same defect wearing an
exception.

`make_tool_ctx` (`agents/tests/_helpers.py:192-206`) IS THIS PACKAGE'S
`ToolContext` BUILDER and is used rather than re-derived. `ToolContext`'s
first five fields carry NO defaults (`agents/contracts/tools.py:292-296`
-- `conversation_id`, `principal`, `depth`, `budget`, `job`), so a
hand-rolled three-argument construction raises `TypeError` before any
assertion runs. Its default principal is `make_principal()`, kind
`"resident_agent"`, which `is_admin` answers False for -- which is
exactly the caller the refusal test needs.
"""
from __future__ import annotations

import pytest

from agents.contracts.tools import ToolRefused, all_tools, get_tool
from agents.contracts.toolschema import openai_tool_dict
from agents.settings_tools import SETTINGS_CARD, SETTINGS_OVERVIEW, run_card, run_overview
# ONE IMPORT LINE FOR THE WHOLE HELPER FAMILY: `agents/tests/_helpers.py:36-39`
# re-exports `make_admin`/`posture`/`seed_sweep_posture`/`user_principal` from
# `identity.testing` "for this package's tests", so naming both modules would
# split one family across two import statements for no reason.
from agents.tests._helpers import (
    make_admin, make_tool_ctx, make_user, posture, seed_sweep_posture, user_principal,
)
from foundation.settings_help import CARDS, CONTENT_HASH, card_for, page_choices
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN
from models.contracts.operations import ParamError

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _a_box(db):
    seed_sweep_posture()


class TestRegistration:
    def test_both_keys_resolve_through_the_platform_registry(self):
        keys = {spec.key for spec in all_tools()}
        assert {"settings.card", "settings.overview"} <= keys

    def test_both_runners_are_dotted_paths_that_resolve(self):
        """The runner is a STRING so `AppConfig.ready()` never imports
        the implementation. `models/registry/tests/test_registry_paths.py
        ::test_every_registered_dotted_path_resolves` sweeps this for the
        whole platform; asserted here too so a broken path fails in the
        module's own test rather than three columns away."""
        from django.utils.module_loading import import_string

        assert callable(import_string(get_tool("settings.card").runner))
        assert callable(import_string(get_tool("settings.overview").runner))

    def test_neither_tool_mutates(self):
        """GUIDE-ONLY IS STRUCTURAL (owner ruling 1). Neither spec
        declares `mutates`, so both default to False -- which is also
        what makes them grantable at all."""
        assert get_tool("settings.card").mutates is False
        assert get_tool("settings.overview").mutates is False

    def test_neither_tool_binds_a_role(self):
        """`models.status` already reports which model answers each role
        and this agent holds it; a second tool answering the same
        question is the duplication owner ruling 5 forbids."""
        assert get_tool("settings.card").roles == ()
        assert get_tool("settings.overview").roles == ()


class TestTheEnumIsTheIndex:
    def test_the_page_param_enumerates_every_card_in_table_order(self):
        """SPEC §5.2: the index rides the TOOL SCHEMA, not the prompt.
        `Param(kind="choice", choices=...)` becomes an `enum` in the JSON
        schema both wire adapters build, so it cannot go stale and
        `agents/runtime/prompt.py` needs no settings-shaped special
        case."""
        schema = openai_tool_dict(SETTINGS_CARD)
        assert schema["function"]["parameters"]["properties"]["page"]["enum"] == list(
            page_choices())

    def test_the_description_names_every_page_and_cites_the_content_version(self):
        for card in CARDS:
            assert card.route_name in SETTINGS_CARD.description
            assert card.title in SETTINGS_CARD.description
        assert CONTENT_HASH in SETTINGS_CARD.description

    def test_an_invented_page_is_a_param_error_not_a_confident_answer(self):
        """"The one failure class worth handing back for a retry"
        (`docs/EXTENDING.md`). A page name a model made up must not reach
        the runner as a lookup miss."""
        with pytest.raises(ParamError):
            run_card({"page": "settings-nonesuch"}, make_tool_ctx())

    def test_a_missing_page_is_a_param_error(self):
        with pytest.raises(ParamError):
            run_card({}, make_tool_ctx())


class TestTheCard:
    def test_it_returns_the_page_its_caller_asked_for(self):
        result = run_card({"page": "identity-settings"}, make_tool_ctx())
        card = card_for("identity-settings")
        assert card.title in result.text
        assert card.purpose in result.text
        for field in card.fields:
            assert field.name in result.text
            assert field.meaning in result.text
            assert field.effects in result.text

    def test_its_data_carries_the_page_the_hash_and_the_links(self):
        result = run_card({"page": "identity-settings"}, make_tool_ctx())
        assert result.data["page"] == "identity-settings"
        assert result.data["content_hash"] == CONTENT_HASH
        assert result.data["links"] == [
            {"route": "identity-settings", "anchor": field.anchor, "label": field.name}
            for field in card_for("identity-settings").fields
        ]

    def test_its_links_are_route_anchor_pairs_and_never_urls(self):
        """SPEC §4.3: the model never produces a link, and neither does
        this. A URL built here would be a URL the panel had to trust; a
        `route`/`anchor` pair is one the panel re-validates against
        `CARDS` before it reverses anything."""
        for entry in run_card({"page": "rag-settings"}, make_tool_ctx()).data["links"]:
            assert set(entry) == {"route", "anchor", "label"}
            assert "://" not in entry["anchor"]
            assert "/" not in entry["anchor"]

    def test_it_reads_no_database_at_all(self, django_assert_num_queries):
        """Its cost is CONSTANT in the number of settings pages and ZERO
        in rows -- which is the whole point of an index-plus-on-demand
        shape. It reads `foundation.settings_help` only: no database, no
        request, no live value."""
        with django_assert_num_queries(0):
            run_card({"page": "chat-settings"}, make_tool_ctx())

    def test_it_answers_a_member_exactly_as_it_answers_an_administrator(self):
        """DELIBERATELY UNGATED (spec §5.3's last paragraph, decision
        23), and asserted rather than left to a docstring: this content
        is platform-authored help text about pages, and it reports no
        value about this box. It is also what keeps a member-facing
        variant possible."""
        member = user_principal(make_user())
        with posture(POSTURE_ENTERPRISE):
            admin_answer = run_card(
                {"page": "rag-settings"},
                make_tool_ctx(principal=user_principal(make_admin())))
            member_answer = run_card({"page": "rag-settings"},
                                     make_tool_ctx(principal=member))
        assert member_answer.text == admin_answer.text
        assert member_answer.data == admin_answer.data


class TestTheOverview:
    def test_an_administrator_gets_this_boxs_own_configuration(self):
        with posture(POSTURE_ENTERPRISE):
            result = run_overview({}, make_tool_ctx(principal=user_principal(make_admin())))
        assert result.data["posture"] == POSTURE_ENTERPRISE
        assert result.data["admin_sees_content"] is False
        assert result.data["time_aware"] is True
        assert result.data["content_hash"] == CONTENT_HASH
        assert "enterprise" in result.text

    def test_the_open_posture_is_reported_as_the_open_posture(self):
        """DRILL 1b AND DRILL 2 BOTH DEPEND ON THIS VALUE BEING REAL.
        The honest answer to "how do I make the library admin-only?" on
        an open box is sourced from here, not guessed."""
        with posture(POSTURE_OPEN):
            result = run_overview({}, make_tool_ctx())
        assert result.data["posture"] == POSTURE_OPEN

    def test_a_member_is_refused_and_the_refusal_names_no_value(self):
        """SPEC §5.3, DECISION 23 -- THE TOOL'S OWN GATE, not the
        surface's. A grant is per-AGENT, so an operator adding this key
        to another agent's `tool_keys` would otherwise hand a member this
        box's posture and security configuration. `ToolRefused`
        classifies as `refused` -- no retry -- which is the honest ending
        for a question no rephrasing fixes.

        THE MESSAGE IS ASSERTED TOO: a refusal that leaked the value in
        its own text would be the same defect wearing an exception.

        THE LEAK LIST INCLUDES `"open"` AND THAT IS THE POINT -- it is
        the posture value drills 1b and 2 turn on. The refusal copy in
        `agents/settings_tools.py` is worded around this list rather than
        the other way round; if this assertion goes red, fix the
        sentence, never the list."""
        member = user_principal(make_user())
        with posture(POSTURE_ENTERPRISE):
            with pytest.raises(ToolRefused) as caught:
                run_overview({}, make_tool_ctx(principal=member))
        message = str(caught.value).lower()
        for leak in ("enterprise", "personal", "locked", "open", "true", "false"):
            assert leak not in message, message

    def test_it_reads_the_settings_row_once_not_once_per_value(
            self, django_assert_num_queries):
        """The per-call reuse norm `agents/entitlements.py:30-36` states,
        applied to the ROW: every value is read off ONE fetched instance,
        not one fetch per value.

        THREE, AND EACH ONE IS NAMED, because a number nobody can account
        for is a number that drifts: (1) `settings_row()` ->
        `IdentitySettings.get_solo()`; (2) the GATE's own `_user_row`
        read of `auth_user` inside `is_admin` -- memoised on the
        settings-row INSTANCE (`identity/access.py:100-103`), and this
        runner fetches a fresh instance every call, so that memo is
        always cold; (3) `ChatSettings.get_solo()`.

        THE SECOND ONE IS THE COST OF BEING A RUNNER RATHER THAN A VIEW.
        A view threads the request-scoped row the gate middleware already
        stashed and pays nothing for the gate; a tool runner has no
        request to thread, so it pays for its own. That is a fact about
        where the seam is, not an N+1 to fix -- and it is FLAT: it does
        not grow with anything.

        Pinned by EQUALITY. A `<=` here would hide a fourth read the day
        somebody adds a value without reading it off `row`."""
        with posture(POSTURE_ENTERPRISE):
            principal = user_principal(make_admin())
            run_overview({}, make_tool_ctx(principal=principal))   # prime the singletons
            with django_assert_num_queries(3):
                run_overview({}, make_tool_ctx(principal=principal))

    def test_it_declares_no_params_so_any_argument_is_a_bug(self):
        assert SETTINGS_OVERVIEW.params == ()
        with pytest.raises(ParamError):
            run_overview({"page": "rag-settings"}, make_tool_ctx())

    def test_it_reports_no_free_text_user_supplied_value(self):
        """SPEC §9: every value it reports is an ENUMERATED or NUMERIC
        column. No account name, group name, entitlement name, workstream
        name or conversation title appears -- which is also why it
        reports no counts and no name lists."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin(username="a-very-distinctive-name")
            result = run_overview({}, make_tool_ctx(principal=user_principal(admin)))
        assert "a-very-distinctive-name" not in result.text
        assert set(result.data) == {
            "content_hash", "posture", "library_posture", "admin_sees_content",
            "session_idle_minutes", "time_aware", "unreported_settings",
        }

    def test_it_names_library_and_queue_as_not_reported_grouped_by_page(self):
        """S4 (Coherence Wave B; count corrected at N1, Coherence Wave B
        review): the backend audit's own complaint was that this tool
        reported 5 of 16 settings fields "as if it were the
        configuration" -- silently. `agents/` may not IMPORT
        `RagSettings`/`JobSettings` directly (see the module's own
        docstring for the full, corrected reasoning -- not reporting
        their live values today is narrower than "cannot"), so the fix
        is not reporting their live values, it is NAMING that gap in the
        tool's own output rather than leaving it implicit."""
        with posture(POSTURE_ENTERPRISE):
            result = run_overview({}, make_tool_ctx(principal=user_principal(make_admin())))
        assert "Identity & security:" in result.text
        assert "Chat:" in result.text
        assert "Not reported here" in result.text
        assert "Library --" in result.text
        # F1 (Coherence Wave C): grouped under the PAGE these four fields
        # are edited on, which is "Job execution" now that they no longer
        # live on the Queue page.
        assert "Job execution --" in result.text
        assert set(result.data["unreported_settings"]) == {"Library", "Job execution"}


class TestTheOverviewFieldCoverage:
    """S4 (Coherence Wave B): every concrete field on the four settings
    singletons the backend audit's Dimension 1 table names -- `Identity
    Settings`, `ChatSettings`, `RagSettings`, `JobSettings` -- must be
    either REPORTED by `run_overview` or NAMED in `UNREPORTED_SETTINGS_
    FIELDS` with a reason. This is the one place in the tree that imports
    all four models together for introspection -- a TEST file, exempt
    from the import-law sweep (`foundation.ops.tests._helpers.
    _is_test_file`) the production module itself is permanently bound by.
    A field added to any of the four in the future and left off both
    structures goes red here."""

    def _concrete_field_names(self, model) -> frozenset[str]:
        return frozenset(
            f.name for f in model._meta.get_fields()
            if getattr(f, "concrete", False) and not f.auto_created and f.name != "id"
        )

    def test_every_concrete_field_is_reported_or_named_excluded(self):
        from agents.models import ChatSettings
        from agents.settings_tools import REPORTED_SETTINGS_FIELDS, UNREPORTED_SETTINGS_FIELDS
        from identity.models import IdentitySettings
        from models.queue.models import JobSettings
        from tools.rag.models import RagSettings

        models = {
            "IdentitySettings": IdentitySettings, "ChatSettings": ChatSettings,
            "RagSettings": RagSettings, "JobSettings": JobSettings,
        }
        for model_name, model in models.items():
            accounted = (
                REPORTED_SETTINGS_FIELDS.get(model_name, frozenset())
                | frozenset(UNREPORTED_SETTINGS_FIELDS.get(model_name, {}))
            )
            for field_name in self._concrete_field_names(model):
                assert field_name in accounted, (model_name, field_name)

    def test_no_field_is_both_reported_and_named_excluded(self):
        from agents.settings_tools import REPORTED_SETTINGS_FIELDS, UNREPORTED_SETTINGS_FIELDS

        for model_name in UNREPORTED_SETTINGS_FIELDS:
            overlap = REPORTED_SETTINGS_FIELDS.get(model_name, frozenset()) & frozenset(
                UNREPORTED_SETTINGS_FIELDS[model_name]
            )
            assert overlap == set(), (model_name, overlap)

    def test_the_eighteen_the_audit_counted_are_exactly_these_eighteen(self):
        """Pinned against the backend audit's own Dimension 1 count plus
        round-3 hardening's one addition and the one-timeout task's own
        (7 + 5 + 4 + 1 + 1 = 18 OPERATOR-EDITABLE fields, as of
        2026-09-17), so a future field silently changes this number
        rather than the accounting above. The two `updated_at`
        bookkeeping timestamps are excluded from this count on purpose --
        they are not operator-editable settings at all -- but still
        accounted for in the OTHER two tests below, so
        `test_every_concrete_field_is_reported_or_named_excluded` stays
        green.

        SIXTEEN BECAME SEVENTEEN when C-7 (round-3 hardening) added
        `JobSettings.max_queued_per_principal`. It joins the import-law
        unreported set with its siblings, for the unchanged reason:
        `agents/` may not read `models.queue`'s table.

        SEVENTEEN BECAME EIGHTEEN when the one-timeout task (2026-09-17)
        added `JobSettings.response_timeout_seconds`, for the identical
        reason -- import law, not a policy choice about this one field."""
        from agents.settings_tools import REPORTED_SETTINGS_FIELDS, UNREPORTED_SETTINGS_FIELDS

        reported_count = sum(len(fields) for fields in REPORTED_SETTINGS_FIELDS.values())
        import_law_unreported = sum(
            len(fields) for model, fields in UNREPORTED_SETTINGS_FIELDS.items()
            if model in ("RagSettings", "JobSettings")
        )
        assert reported_count == 5
        assert import_law_unreported == 13
        assert reported_count + import_law_unreported == 18

    def test_bookkeeping_fields_are_named_separately_from_import_law_fields(self):
        from agents.settings_tools import UNREPORTED_SETTINGS_FIELDS

        assert UNREPORTED_SETTINGS_FIELDS["IdentitySettings"] == {
            "updated_at": UNREPORTED_SETTINGS_FIELDS["IdentitySettings"]["updated_at"],
        }
        assert UNREPORTED_SETTINGS_FIELDS["ChatSettings"] == {
            "updated_at": UNREPORTED_SETTINGS_FIELDS["ChatSettings"]["updated_at"],
        }
        assert "auto_now" in UNREPORTED_SETTINGS_FIELDS["IdentitySettings"]["updated_at"]

    def test_every_unreported_field_carries_a_real_reason(self):
        from agents.settings_tools import UNREPORTED_SETTINGS_FIELDS

        for model_name, fields in UNREPORTED_SETTINGS_FIELDS.items():
            for field_name, reason in fields.items():
                assert reason, (model_name, field_name)


class TestThePromptIsUntouched:
    def test_the_prompt_builder_names_no_settings_identifier(self):
        """A PHASE PIN WHOSE OWN DOCSTRING SAYS TO DELETE IT (spec §10.3).

        Unlike `models/registry/tools.py:23-27`, whose text guard protects
        a call that module must NEVER make, this one protects a DECISION
        (spec §5.2, decision 6): the page index rides the tool schema, so
        `agents/runtime/prompt.py` gets no settings-shaped special case
        and no slug conditional inside the one function every agent on
        this platform goes through. A later phase's retrieval leg
        (§15.1) could legitimately revisit that. WHEN IT DOES, DELETE
        THIS TEST -- do not weaken it, and do not add an exception to it.
        """
        import pathlib

        import agents.runtime.prompt as prompt

        source = pathlib.Path(prompt.__file__).read_text()
        for identifier in ("settings_help", "settings-helper", "settings.card",
                           "settings.overview", "SETTINGS_CARD", "SETTINGS_OVERVIEW"):
            assert identifier not in source, identifier


class TestTheCatalogueEntry:
    def test_the_settings_assistant_is_in_the_catalogue_and_is_not_a_tool(self):
        """`as_tool=False` IS DELIBERATE: an `agent.settings-helper` tool
        key would put this agent inside other agents' reach, which is the
        opposite of settings-only."""
        from agents.defaults import DEFAULT_AGENTS

        spec = next(s for s in DEFAULT_AGENTS if s.slug == "settings-helper")
        assert spec.name == "Settings assistant"
        assert spec.as_tool is False
        assert spec.tool_keys == ("settings.card", "settings.overview", "models.status")

    def test_every_tool_it_holds_is_registered_and_none_of_them_mutates(self):
        """SPEC §5.5 -- BELT OVER THE STRUCTURAL BRACES. `Agent.save()`
        would already refuse a row naming a mutating key, but the ruling
        is the owner's, and a structural guarantee nobody restates is a
        guarantee somebody removes."""
        from agents.contracts.tools import get_tool
        from agents.defaults import DEFAULT_AGENTS

        spec = next(s for s in DEFAULT_AGENTS if s.slug == "settings-helper")
        for key in spec.tool_keys:
            assert get_tool(key).mutates is False, key

    def test_its_system_prompt_carries_doctrine_and_never_lists_the_pages(self):
        """SPEC §5.1: the index rides the tool SCHEMA. A row is a COPY,
        and a copy goes stale until somebody re-runs `install_defaults
        --reset` -- which is exactly the staleness the owner asked about.
        So no route name and no page title appears in the prompt."""
        from agents.defaults import DEFAULT_AGENTS
        from foundation.settings_help import CARDS

        prompt = next(s for s in DEFAULT_AGENTS if s.slug == "settings-helper").system_prompt
        for card in CARDS:
            assert card.route_name not in prompt, card.route_name
        assert "settings.card" in prompt
        assert "settings.overview" in prompt

    def test_its_system_prompt_requires_a_card_per_page_named_not_one_call_and_done(self):
        """The flagship-drill gap (2026-09-11): a run that called `settings.card`
        once, for one page, then named a CONTROL on a second page from memory --
        the second page's own card was never fetched, even though the first
        card's own text pointed straight at it. Pinned NON-VACUOUSLY: this is
        the exact clause that closes that gap, not merely 'the tool is mentioned
        somewhere' (the prior assertions above already passed on the prompt that
        produced the wrong answer, which is why this test exists as its own
        method rather than folded into the one above).

        WORDING, AND WHY IT IS SHORT (drill follow-up, same date): a first
        attempt stated this as its own third paragraph -- 'One `settings.card`
        call covers ONE page... THAT control's OWN page... If a card's own
        text points you at a different page...' -- and a live drill against
        the installed model found it produced an EMPTY answer with NO tool
        call at all, reproducibly, every time it was inserted as a standalone
        paragraph in that spot. The one-sentence form below, folded into the
        existing `settings.card` paragraph instead of added as a new one,
        does not reproduce that failure (drilled clean across 11 live runs)
        and still measurably changes behaviour: a `settings.card` call for a
        SECOND page, when the first page's own card redirected there, which
        never happened before this sentence existed.

        PLACEMENT IS PART OF THE PIN, NOT ONLY THE WORDING (post-READY
        review, P1): a substring check against the WHOLE prompt passes just
        as happily if this exact sentence is lifted back out into its own
        paragraph -- the precise shape already shown to break the installed
        model. So this asserts against the `settings.card` PARAGRAPH alone,
        found by splitting on the same blank-line join every paragraph in
        this prompt uses, never against the prompt as a whole."""
        from agents.defaults import DEFAULT_AGENTS

        prompt = next(s for s in DEFAULT_AGENTS if s.slug == "settings-helper").system_prompt
        # Per-page discipline AND the redirect case, both in one short,
        # imperative sentence appended to the existing `settings.card`
        # paragraph (never a new paragraph -- see the docstring above).
        # Scoped to that ONE paragraph, not the whole prompt, so lifting the
        # sentence back out into its own paragraph turns this red.
        card_para = next(p for p in prompt.split("\n\n") if "Call `settings.card`" in p)
        assert "A card naming another page is a page you have not read yet" in card_para
        assert "get its card too before you describe what is on it" in card_para

    def test_its_system_prompt_covers_multi_part_and_negative_claims_too(self):
        """PLACEMENT-ROUND RULING 2 (owner, 2026-09-11), closing the
        resilience battery's own gap in the fold-in sentence above. That
        sentence is worded around a CARD's own text redirecting the model
        to a second page (the flagship drill's exact shape); the battery
        found two OTHER directions the same discipline gap recurs from:

          * Q8 -- a MULTI-PART USER QUESTION that names a second page
            itself ("create a group and give it access to only the
            finance documents"), never a card's own redirect at all. The
            model named a specific, fabricated control on the second
            page's own card without ever calling it.
          * Q4 -- a NEGATIVE CLAIM ("no such setting exists... none of
            the available settings pages... include a control for
            managing API keys") about pages it never called `settings.
            card` for this turn either -- the identical gap applied to
            "X is not there" instead of "X is there".

        One more sentence, in the SAME paragraph as the first fold-in
        (never a new one -- the model's own proven sensitivity to a new
        mid-prompt paragraph, drilled above, is the reason this test scopes
        to the paragraph rather than the whole prompt, exactly as the test
        above does)."""
        from agents.defaults import DEFAULT_AGENTS

        prompt = next(s for s in DEFAULT_AGENTS if s.slug == "settings-helper").system_prompt
        card_para = next(p for p in prompt.split("\n\n") if "Call `settings.card`" in p)
        assert "before you name any page in a multi-part answer" in card_para
        assert "say a page lacks a control" in card_para
        assert "get that page's card this turn too" in card_para

    def test_its_system_prompt_covers_the_mechanism_that_owns_visibility_too(self):
        """FINAL-CODE BATTERY RULING, Q10 (owner, 2026-09-13). Asked "Why
        can't my teammate see the Settings area?", the model called
        `settings.card(page='identity-entitlements')` only and answered
        that Settings-area visibility is gated by entitlements --
        verified against `foundation/settings_area.py` to be false: every
        settings-area entry is gated through `identity.access.is_admin`,
        and entitlements play no role in whether the Settings sidebar
        itself renders. The same discipline gap as the two tests above --
        naming a mechanism on a page never queried this turn -- in a
        third shape: confusing WHICH of several plausible mechanisms
        (entitlements vs. administrator status) actually controls
        something, because only one candidate page was ever read.

        One more sentence, in the SAME `settings.card` paragraph as the
        two fold-ins above (never a new paragraph -- the model's own
        proven sensitivity to a new mid-prompt paragraph, drilled and
        pinned by the two tests above, is why this test scopes to the
        paragraph rather than the whole prompt, exactly as they do).

        RE-FOLD (owner-sanctioned single retry, 2026-09-13): the first
        wording's own live re-drill got the MECHANISM right (administrator
        status, no entitlements fabrication) but then recommended
        "the Accounts page" as where to act on it without ever calling
        `settings.card(page='identity-users')` this turn -- the identical
        gap one step later, at the ACTION rather than the DIAGNOSIS. The
        sentence now also names "where someone would go to act on it",
        not only "which mechanism controls it"."""
        from agents.defaults import DEFAULT_AGENTS

        prompt = next(s for s in DEFAULT_AGENTS if s.slug == "settings-helper").system_prompt
        card_para = next(p for p in prompt.split("\n\n") if "Call `settings.card`" in p)
        assert "before you say who can see or access something" in card_para
        assert "which mechanism controls it" in card_para
        assert "where someone would go to act on it" in card_para
        assert "fetch the card of that owning page first" in card_para

    def test_its_system_prompt_asks_for_concise_answers(self):
        """PLACEMENT-ROUND RULING 3 (owner, 2026-09-11): short prose, no
        markdown headers, no emoji, direct answer first, numbered steps
        only where steps exist. Folded into the CLOSING paragraph (never a
        new one, same law as ruling 2's own fold-in) -- scoped to that
        paragraph so a future edit that lifted this back out into its own
        paragraph, the shape already shown to break the installed model,
        turns this red."""
        from agents.defaults import DEFAULT_AGENTS

        prompt = next(s for s in DEFAULT_AGENTS if s.slug == "settings-helper").system_prompt
        closing_para = next(p for p in prompt.split("\n\n") if "Do not write out a link" in p)
        assert "no markdown headers, no emoji" in closing_para
        assert "leading with the direct answer" in closing_para
        assert "numbered steps only where there are real steps to number" in closing_para

    def test_its_system_prompt_asks_for_the_pages_human_name_only(self):
        """OWNER FEEDBACK, LIVE, SCREENSHOT-VERIFIED, 2026-09-13: the
        finale answer "reads glitched" -- it spoke in internal route
        names ("chat-tool-entitlements", "identity-settings
        (identity-settings)") instead of a page's own human name. The
        prompt half of a two-part fix (the platform half,
        `agents/chat/context_processors.py::_linkify_named_pages`, makes
        whatever the model writes clickable in place regardless). One
        more sentence, folded into the EXISTING page-naming paragraph
        ("Quote the page's own words...", not the `settings.card` one and
        not the closing one) -- scoped to that paragraph, same law and
        same reason as the two pins above."""
        from agents.defaults import DEFAULT_AGENTS

        prompt = next(s for s in DEFAULT_AGENTS if s.slug == "settings-helper").system_prompt
        naming_para = next(
            p for p in prompt.split("\n\n") if "Quote the page's own words" in p)
        assert "Refer to a page by its human name only" in naming_para
        assert "never its internal route name or a parenthesised slug" in naming_para

    def test_no_row_appears_just_because_the_catalogue_has_an_entry(self):
        """RULING 2 (`agents/defaults.py:14-18`): `install_default` is
        create-if-absent and is the ONE way a row appears. The panel
        renders the offer; a deploy installs nothing."""
        from agents.models import Agent

        assert not Agent.objects.filter(slug="settings-helper").exists()

    def test_installing_it_creates_exactly_one_row_and_is_idempotent(self):
        from agents.defaults import install_default
        from agents.models import Agent
        from identity.contracts.principals import OPEN_PRINCIPAL

        install_default("agent", "settings-helper", OPEN_PRINCIPAL)
        install_default("agent", "settings-helper", OPEN_PRINCIPAL)
        assert Agent.objects.filter(slug="settings-helper").count() == 1


class TestTheSurfaceSlugSet:
    def test_every_slug_in_the_set_names_a_real_catalogue_entry(self):
        """SPEC §10.3, "slug set is honest". The frozenset is one line
        beside the spec it names, and this is what keeps it from
        outliving it."""
        from agents.defaults import DEFAULT_AGENTS, SETTINGS_SURFACE_SLUGS

        catalogue = {spec.slug for spec in DEFAULT_AGENTS}
        assert SETTINGS_SURFACE_SLUGS <= catalogue
        assert SETTINGS_SURFACE_SLUGS == {"settings-helper"}

    def test_it_is_a_frozenset_so_no_caller_can_widen_it_in_place(self):
        from agents.defaults import SETTINGS_SURFACE_SLUGS

        assert isinstance(SETTINGS_SURFACE_SLUGS, frozenset)
