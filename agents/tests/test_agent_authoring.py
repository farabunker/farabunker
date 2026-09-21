"""`Agent.box_wide` -- the audience column, distinct from `resident`
the origin marker -- and the writers that read it."""
from __future__ import annotations

import pytest

from agents.models import Agent
from agents.tests._helpers import (
    make_admin, make_agent, make_entitlement, make_principal, make_user, posture,
    user_principal,
)
from agents.visibility import installed_agent_slugs, visible_agents
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_PERSONAL
from identity.contracts.principals import Principal

pytestmark = pytest.mark.django_db


class TestTheAudienceColumn:
    def test_a_new_agent_is_private_by_default(self):
        assert make_agent(slug="private-one").box_wide is False

    def test_box_wide_is_independent_of_resident(self):
        """`resident` records ORIGIN and keeps its other reader
        (`resident_agent_tool_keys`'s shell-path warning). `box_wide`
        records AUDIENCE. An administrator may make a shipped default
        private, or put an agent they wrote in front of everybody,
        without either act being a statement about where it came from."""
        shipped_but_private = make_agent(slug="shipped-private", resident=True,
                                         box_wide=False)
        home_grown_but_public = make_agent(slug="home-public", resident=False,
                                           box_wide=True)
        assert shipped_but_private.resident is True
        assert shipped_but_private.box_wide is False
        assert home_grown_but_public.resident is False
        assert home_grown_but_public.box_wide is True

    def test_visible_agents_reaches_a_box_wide_row_for_a_stranger(self):
        """`with posture(POSTURE_PERSONAL)` is a deviation from the
        brief's verbatim test (task-5-brief.md): under this box's
        AMBIENT default posture (open), `sees_all_content` short-circuits
        `visible_agents` to every row regardless of `box_wide`
        (`identity.access.sees_all_content`'s open branch), so the
        assertion as literally written would pass whether or not the
        `box_wide` swap in `agents/visibility.py` were correct -- the
        unsatisfiable half is the SIBLING assertion below, and this one
        is fixed to the same honest, non-vacuous posture for the same
        reason. Precedent: `agents/chat/tests/test_visibility.py`'s own
        `test_a_box_wide_agent_is_visible_to_everybody` pins
        `POSTURE_PERSONAL` for an identical stranger-visibility check."""
        make_agent(slug="everyones", box_wide=True,
                   owner_kind="user", owner_key="99")
        stranger = Principal("user", "1")
        with posture(POSTURE_PERSONAL):
            assert "everyones" in set(
                visible_agents(stranger).values_list("slug", flat=True))

    def test_visible_agents_does_NOT_reach_a_resident_row_that_is_not_box_wide(self):
        """The leg swapped, and it really swapped: a row marked only as
        a shipped default no longer reaches everybody by origin alone.

        `with posture(POSTURE_PERSONAL)` is a deviation from the brief's
        verbatim test (task-5-brief.md): as literally written, against
        this box's AMBIENT default posture (open), the assertion is
        UNSATISFIABLE -- `identity.access.sees_all_content`'s open branch
        returns True unconditionally, so `visible_agents` returns every
        row including this one, whatever `box_wide` holds. The fix is to
        the test's posture, matching `agents/chat/tests/
        test_visibility.py`'s own precedent for a stranger-visibility
        check (`test_a_box_wide_agent_is_visible_to_everybody`), never to
        `agents/visibility.py` -- the swap itself is correct, as this
        assertion proves once a non-open posture is actually in force."""
        make_agent(slug="origin-only", resident=True, box_wide=False,
                   owner_kind="user", owner_key="99")
        stranger = Principal("user", "1")
        with posture(POSTURE_PERSONAL):
            assert "origin-only" not in set(
                visible_agents(stranger).values_list("slug", flat=True))

    def test_installed_agent_slugs_moved_with_it(self):
        """`with posture(POSTURE_PERSONAL)` -- the same deviation as
        `test_visible_agents_reaches_a_box_wide_row_for_a_stranger`
        above, and for the identical reason: `installed_agent_slugs`
        also starts with a `sees_all_content` short-circuit."""
        make_agent(slug="everyones-too", box_wide=True, enabled=False,
                   owner_kind="user", owner_key="99")
        stranger = Principal("user", "1")
        with posture(POSTURE_PERSONAL):
            assert "everyones-too" in set(installed_agent_slugs(stranger))


class TestTheShippedCatalogueIsThePlatformsOffer:
    def test_installing_a_default_stamps_box_wide(self):
        from agents.defaults import DEFAULT_AGENTS, install_default

        slug = DEFAULT_AGENTS[0].slug
        row, created = install_default("agent", slug, Principal("open", "box"))
        assert created is True
        assert row.box_wide is True
        assert row.resident is True

    def test_a_reset_re_stamps_it_along_with_every_other_field(self):
        """`install_default`'s own rule: a reset is a FRESH ADOPTION of
        the shipped text, not a partial patch -- so every field a fresh
        install would set is set again, this one included."""
        from agents.defaults import DEFAULT_AGENTS, install_default

        slug = DEFAULT_AGENTS[0].slug
        row, _created = install_default("agent", slug, Principal("open", "box"))
        Agent.objects.filter(pk=row.pk).update(box_wide=False)
        again, created = install_default("agent", slug, Principal("open", "box"),
                                         reset=True)
        assert created is False
        assert again.box_wide is True

    def test_installing_a_flow_default_still_works(self):
        """`box_wide` is an AGENT column. `install_default`'s flow branch
        must not learn about it."""
        from agents.defaults import DEFAULT_FLOWS, install_default

        row, created = install_default("flow", DEFAULT_FLOWS[0].slug,
                                       Principal("open", "box"))
        assert created is True
        assert not hasattr(row, "box_wide")


class TestTheMigrationPreservesTodaysBehaviour:
    def test_the_data_migration_function_copies_resident_onto_box_wide(self):
        """Called directly against the live models rather than through a
        migration executor, so it stays a unit test: the function's
        contract is 'every row's audience becomes what its origin used
        to imply', which is what makes landing day byte-identical."""
        from agents.migrations import _0012_helpers

        make_agent(slug="was-resident", resident=True, box_wide=False)
        make_agent(slug="was-not", resident=False, box_wide=False)

        class _Apps:
            @staticmethod
            def get_model(app_label, model_name):
                assert (app_label, model_name) == ("agents", "Agent")
                return Agent

        _0012_helpers.copy_resident_to_box_wide(_Apps, None)
        assert Agent.objects.get(slug="was-resident").box_wide is True
        assert Agent.objects.get(slug="was-not").box_wide is False


class TestMayManageAgent:
    def test_the_rows_own_owner_may(self):
        from agents.visibility import may_manage_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="mine", **owner_fields(user_principal(owner)))
            assert may_manage_agent(user_principal(owner), agent) is True

    def test_a_stranger_may_not(self):
        from agents.visibility import may_manage_agent

        owner, stranger = make_user(), make_user(username="other")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="theirs", **owner_fields(user_principal(owner)))
            assert may_manage_agent(user_principal(stranger), agent) is False

    def test_an_administrator_may_edit_any_agent(self):
        from agents.visibility import may_manage_agent

        owner, admin = make_user(), make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="somebodys", **owner_fields(user_principal(owner)))
            assert may_manage_agent(user_principal(admin), agent) is True

    def test_a_box_wide_row_refuses_even_its_own_non_admin_owner(self):
        """Editing a row EVERYBODY on the box can use is an
        administrator's act, whoever originally created it -- and
        `box_wide` short-circuits AHEAD of the ownership branch, which is
        what makes that true."""
        from agents.visibility import may_manage_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="everyones-row", box_wide=True,
                               **owner_fields(user_principal(owner)))
            assert may_manage_agent(user_principal(owner), agent) is False

    def test_on_an_open_box_whoever_is_at_the_keyboard_edits_everything(self):
        """Not a fallback: `is_admin` answers True for everybody on a box
        with no accounts, and that is the true statement about a
        household box.

        No `with posture(...)`: this module defines no sweep-seeding
        autouse fixture, so every test here that does not pin one runs
        against the shipped default posture (`open`) whatever
        `FARABUNKER_TEST_POSTURE` says -- which is exactly the box this
        test is about."""
        from agents.visibility import may_manage_agent
        from identity.contracts.principals import OPEN_PRINCIPAL

        agent = make_agent(slug="open-box-row", box_wide=True)
        assert may_manage_agent(OPEN_PRINCIPAL, agent) is True


class TestEditableAgents:
    def test_it_lists_the_principals_own_and_excludes_box_wide(self):
        from agents.visibility import editable_agents

        owner, other = make_user(), make_user(username="other")
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="a-mine", **owner_fields(user_principal(owner)))
            make_agent(slug="b-theirs", **owner_fields(user_principal(other)))
            make_agent(slug="c-everyones", box_wide=True,
                       **owner_fields(user_principal(owner)))
            slugs = set(editable_agents(user_principal(owner))
                        .values_list("slug", flat=True))
        assert slugs == {"a-mine"}

    def test_on_an_open_box_the_list_and_the_predicate_agree(self):
        """Spec review M6. `owned_rows_q` has NO open-posture widening,
        so without the `sees_all_content` short-circuit a row stamped
        with a `user` principal is ABSENT from the list while
        `may_manage_agent` answers True for it -- the list and the
        predicate disagreeing about the same row."""
        from agents.visibility import editable_agents, may_manage_agent
        from identity.contracts.principals import OPEN_PRINCIPAL

        stamped = make_agent(slug="stamped-user", owner_kind="user", owner_key="7")
        assert "stamped-user" in set(
            editable_agents(OPEN_PRINCIPAL).values_list("slug", flat=True))
        assert may_manage_agent(OPEN_PRINCIPAL, stamped) is True

    def test_it_costs_zero_permission_queries_on_an_open_box(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from agents.visibility import editable_agents
        from identity.contracts.principals import OPEN_PRINCIPAL

        make_agent(slug="open-cost")
        with CaptureQueriesContext(connection) as captured:
            list(editable_agents(OPEN_PRINCIPAL))
        grant_reads = [q for q in captured.captured_queries
                       if "entitlementgrant" in q["sql"].lower()]
        assert grant_reads == []

    def test_an_administrator_with_the_content_setting_off_sees_their_own_rows_only(self):
        """Deliberate, and pinned so it is not read as a bug later:
        `/chat/agents/` is "the agents I work on"; `/settings/agents/` is
        the box-wide view, one click away."""
        from agents.visibility import editable_agents

        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="admins-own", **owner_fields(user_principal(admin)))
            make_agent(slug="members-own", **owner_fields(user_principal(member)))
            slugs = set(editable_agents(user_principal(admin))
                        .values_list("slug", flat=True))
        assert slugs == {"admins-own"}


class TestCreateAgent:
    def _fields(self, **overrides):
        base = dict(name="My helper", description="", system_prompt="be helpful",
                    max_steps=4, enabled=True)
        base.update(overrides)
        return base

    def test_it_stamps_the_creator_as_the_owner_and_derives_a_slug(self):
        from agents.visibility import create_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            row, errors = create_agent(user_principal(owner), self._fields())
        assert errors == {}
        assert row.slug == "my-helper"
        assert row.owner_kind == "user"
        assert row.box_wide is False
        assert row.resident is False
        assert row.tool_keys == []

    def test_a_blank_name_is_refused_and_writes_nothing(self):
        from agents.models import Agent
        from agents.visibility import AGENT_NAME_REQUIRED, create_agent

        before = Agent.objects.count()
        row, errors = create_agent(make_principal(), self._fields(name="   "))
        assert row is None
        assert errors == {"name": AGENT_NAME_REQUIRED}
        assert Agent.objects.count() == before

    def test_a_colliding_slug_is_uniquified_case_insensitively(self):
        from agents.visibility import create_agent

        make_agent(slug="My-Helper")
        row, errors = create_agent(make_principal(), self._fields())
        assert errors == {}
        assert row.slug == "my-helper-2"

    def test_a_slug_colliding_with_the_shipped_catalogue_is_uniquified_too(self):
        """A row whose slug collides with a catalogue entry would be
        silently overwritten by `install_defaults --reset <slug>`."""
        from agents.defaults import DEFAULT_AGENTS
        from agents.visibility import create_agent

        shipped = DEFAULT_AGENTS[0].slug
        row, errors = create_agent(make_principal(),
                                   self._fields(name=shipped.replace("-", " ")))
        assert errors == {}
        assert row.slug != shipped

    def test_a_punctuation_only_name_still_gets_a_usable_slug(self):
        from agents.visibility import create_agent

        row, errors = create_agent(make_principal(), self._fields(name="!!! ???"))
        assert errors == {}
        assert row.slug.startswith("agent-")

    def test_a_name_past_the_column_is_refused_and_writes_nothing(self):
        """`AGENT_NAME_TOO_LONG` is one of the three refusal sentences
        this task ships and the `elif len(name) > 255` branch had no
        test at all (review finding 3). 255 is `Agent.name`'s own
        `max_length`, so a 256th character is the first one the column
        could not hold -- refused at the writer rather than reaching a
        database error."""
        from agents.models import Agent
        from agents.visibility import AGENT_NAME_TOO_LONG, create_agent

        before = Agent.objects.count()
        row, errors = create_agent(make_principal(), self._fields(name="x" * 256))
        assert row is None
        assert errors == {"name": AGENT_NAME_TOO_LONG}
        assert Agent.objects.count() == before

    def test_the_creation_route_is_not_an_audience_escape_hatch(self):
        """Review finding 3. `create_agent` hard-codes `box_wide=False`
        and pops the key out of `clean`, so even an ADMINISTRATOR -- and
        on this open box every principal is one, which is what lets the
        field reach `clean` at all -- cannot create a box-wide row
        straight from the form. Removing the pop today would raise
        `TypeError: got multiple values`, not create a box-wide row, so
        there is no live hole; this test is what stops a future tidy-up
        that drops the explicit `box_wide=False` in favour of a plain
        `**clean` from quietly opening one."""
        from agents.visibility import create_agent

        row, errors = create_agent(make_principal(), self._fields(box_wide=True))
        assert errors == {}
        assert row.box_wide is False

    def test_max_steps_is_refused_outside_the_declared_range(self):
        from agents.limits import MAX_STEPS_CEILING
        from agents.visibility import AGENT_MAX_STEPS_OUT_OF_RANGE, create_agent

        low, low_errors = create_agent(make_principal(), self._fields(max_steps=0))
        high, high_errors = create_agent(
            make_principal(), self._fields(max_steps=MAX_STEPS_CEILING + 1))
        assert low is None and high is None
        assert low_errors == {"max_steps": AGENT_MAX_STEPS_OUT_OF_RANGE}
        assert high_errors == {"max_steps": AGENT_MAX_STEPS_OUT_OF_RANGE}

    def test_it_writes_one_audit_row_naming_the_action(self):
        from identity.contracts import actions
        from identity.models import AuditEvent
        from agents.visibility import create_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            create_agent(user_principal(owner), self._fields())
            assert AuditEvent.objects.filter(action=actions.AGENT_CREATED).count() == 1


class TestUpdateAgent:
    def test_it_writes_the_editable_fields(self):
        from agents.visibility import update_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="editable", **owner_fields(user_principal(owner)))
            errors = update_agent(user_principal(owner), agent, dict(
                name="Renamed", description="d", system_prompt="new prompt",
                max_steps=3, enabled=False))
        agent.refresh_from_db()
        assert errors == {}
        assert (agent.name, agent.system_prompt, agent.max_steps, agent.enabled) == (
            "Renamed", "new prompt", 3, False)

    def test_the_slug_cannot_be_changed_and_the_attempt_is_not_a_500(self):
        """`Agent.save()` refuses a slug change because a slug is a KEY
        -- `flow.run` resolves one, an `agent.<slug>` grant names one,
        `--reset` matches on one. The form never offers the field, and
        this writer ignores it rather than reaching that refusal."""
        from agents.visibility import update_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="keep-me", **owner_fields(user_principal(owner)))
            errors = update_agent(user_principal(owner), agent, dict(
                name="n", description="", system_prompt="", max_steps=2, enabled=True,
                slug="something-else"))
        agent.refresh_from_db()
        assert errors == {}
        assert agent.slug == "keep-me"

    def test_a_non_admin_cannot_set_the_role_even_by_forging_the_field(self):
        from agents.visibility import update_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="role-locked", **owner_fields(user_principal(owner)))
            was = agent.llm_role
            update_agent(user_principal(owner), agent, dict(
                name="n", description="", system_prompt="", max_steps=2, enabled=True,
                llm_role="some.other.role"))
        agent.refresh_from_db()
        assert agent.llm_role == was

    def test_an_administrator_can_change_the_role(self):
        """The brief's own version of this test set `llm_role` to the
        value the row ALREADY HELD -- `CHAT_CONVERSE_ROLE` on both sides,
        which is also `Agent.llm_role`'s field default -- so it passed
        with the admin branch deleted, inverted, or applied to everybody:
        it proved the role was not CLOBBERED, never that an
        administrator may WRITE one (review finding 2). A real second
        chat-capability role exists and needs no registration of its
        own: `tools/rag/apps.py` registers `RAG_ANSWER_ROLE` with
        capability "chat" unconditionally, with no feature-flag guard,
        so it is in `all_roles()` on every gate run including the
        flag-off ones. Writing it is what makes this the other half of
        decision 7 -- the permission, beside the two siblings' refusal
        -- and it is also the only test that reaches `llm_role` through
        the `changed`/audit path."""
        from agents.visibility import update_agent
        from models.contracts.roles import CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE

        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="role-open", llm_role=CHAT_CONVERSE_ROLE,
                               **owner_fields(user_principal(admin)))
            errors = update_agent(user_principal(admin), agent, dict(
                name="n", description="", system_prompt="", max_steps=2, enabled=True,
                llm_role=RAG_ANSWER_ROLE))
        agent.refresh_from_db()
        assert errors == {}
        assert agent.llm_role == RAG_ANSWER_ROLE

    def test_a_non_admin_cannot_set_box_wide_even_by_forging_the_field(self):
        from agents.visibility import update_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="reach-locked", **owner_fields(user_principal(owner)))
            update_agent(user_principal(owner), agent, dict(
                name="n", description="", system_prompt="", max_steps=2, enabled=True,
                box_wide=True))
        agent.refresh_from_db()
        assert agent.box_wide is False

    def test_the_reach_write_touches_no_labels_at_all(self):
        """Control 1 and control 2 are independent, each gated by its own
        authority. Reach cannot widen anything past a label that is
        standing, because it writes no label."""
        from agents.labels import agent_label_ids, set_agent_labels
        from agents.visibility import update_agent

        admin = make_admin()
        entitlement = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="reach-and-labels",
                               **owner_fields(user_principal(admin)))
            set_agent_labels(user_principal(admin), agent, {entitlement.pk})
            update_agent(user_principal(admin), agent, dict(
                name="n", description="", system_prompt="", max_steps=2, enabled=True,
                box_wide=True))
        agent.refresh_from_db()
        assert agent.box_wide is True
        assert agent_label_ids(agent) == frozenset({entitlement.pk})

    def test_a_stranger_writes_nothing(self):
        from agents.visibility import update_agent

        owner, stranger = make_user(), make_user(username="other")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="not-yours", **owner_fields(user_principal(owner)))
            errors = update_agent(user_principal(stranger), agent, dict(
                name="Hijacked", description="", system_prompt="", max_steps=2,
                enabled=True))
        agent.refresh_from_db()
        assert errors != {}
        assert agent.name != "Hijacked"

    def test_the_audit_row_names_which_fields_changed_and_not_their_contents(self):
        """A system prompt is the operator's text, and an audit trail is
        not the place to copy it."""
        from identity.contracts import actions
        from identity.models import AuditEvent
        from agents.visibility import update_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="audited", system_prompt="old secret",
                               **owner_fields(user_principal(owner)))
            update_agent(user_principal(owner), agent, dict(
                name=agent.name, description=agent.description,
                system_prompt="new secret", max_steps=agent.max_steps, enabled=True))
            row = AuditEvent.objects.filter(action=actions.AGENT_EDITED).get()
        serialized = str(row.__dict__)
        assert "system_prompt" in serialized
        assert "new secret" not in serialized
        assert "old secret" not in serialized
        # THE KEY, NOT ONLY THE VALUE (review finding 1). `was=` already
        # means THE PREVIOUS VALUE in `rename_workstream`, eighty lines
        # below the writer under test; pinning `fields` here is what
        # stops the two meanings sharing one key in one trail again.
        assert row.detail == {"fields": "system_prompt"}
