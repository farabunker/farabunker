"""`Agent.box_wide` -- the audience column, distinct from `resident`
the origin marker -- and the writers that read it."""
from __future__ import annotations

import pytest

from agents.models import Agent
from agents.tests._helpers import make_agent, posture
from agents.visibility import installed_agent_slugs, visible_agents
from identity.contracts.postures import POSTURE_PERSONAL
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
