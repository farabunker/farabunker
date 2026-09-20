"""`identity/access.py`'s three entitlement questions, and the two
picker readers.

IDENTITY ANSWERS ABOUT PRINCIPALS, NEVER ABOUT DOCUMENTS. It may not
import `tools/` or `agents/` (import-law rule 4), so it answers "which
entitlement ids" and lets each column turn that into a queryset of its
own rows.
"""
from __future__ import annotations

import pytest
from django.contrib.auth.models import Group

from identity.access import (
    grant_subjects, held_entitlement_ids, labelling_entitlements, may_see_unlabelled,
    owned_entitlement_ids, share_subjects,
)
from identity.contracts.postures import LIBRARY_LOCKED, POSTURE_ENTERPRISE, POSTURE_OPEN
from identity.contracts.principals import ANONYMOUS, OPEN_PRINCIPAL, SERVICE_PRINCIPAL
from identity.models import EntitlementGrant
from identity.tests._helpers import (
    grant, make_admin, make_entitlement, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestHeldEntitlementIds:
    def test_a_direct_grant_is_held(self):
        user = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=user)
        with posture(POSTURE_ENTERPRISE):
            assert held_entitlement_ids(user_principal(user)) == frozenset({finance.pk})

    def test_a_grant_through_a_group_is_held(self):
        """One query, and it reaches both ways in: `Q(user_id=...) |
        Q(group__user__id=...)`."""
        user = make_user()
        group = make_group(name="analysts")
        user.groups.add(group)
        finance = make_entitlement(name="Finance")
        grant(finance, group=group)
        with posture(POSTURE_ENTERPRISE):
            assert held_entitlement_ids(user_principal(user)) == frozenset({finance.pk})

    def test_a_non_user_principal_holds_nothing(self):
        """Grants attach to a user or a group, by the XOR constraint. A
        service principal therefore holds nothing -- permanently, until
        service-account tokens land -- and the open and anonymous
        principals never reach a grants join at all."""
        finance = make_entitlement(name="Finance")
        grant(finance, user=make_user())
        with posture(POSTURE_ENTERPRISE):
            assert held_entitlement_ids(SERVICE_PRINCIPAL) == frozenset()
            assert held_entitlement_ids(ANONYMOUS) == frozenset()
            assert held_entitlement_ids(OPEN_PRINCIPAL) == frozenset()

    def test_an_unparseable_user_key_answers_nothing_rather_than_raising(self):
        """`Principal.key` for a user is the primary key AS A STRING. A
        key that is not a decimal string -- from a hand-written payload,
        or a row written against an older schema -- would reach
        `filter(user_id=...)` and raise `ValueError` inside a request.
        A never-500 surface cannot afford that."""
        from identity.contracts.principals import Principal
        with posture(POSTURE_ENTERPRISE):
            assert held_entitlement_ids(Principal("user", "not-a-pk")) == frozenset()


class TestOwnedEntitlementIds:
    def test_only_owner_grants_are_owned_and_they_are_also_held(self):
        user = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=user, role=EntitlementGrant.Role.OWNER)
        grant(legal, user=user)
        with posture(POSTURE_ENTERPRISE):
            principal = user_principal(user)
            assert owned_entitlement_ids(principal) == frozenset({finance.pk})
            assert held_entitlement_ids(principal) == frozenset({finance.pk, legal.pk})

    def test_an_owner_grant_through_a_group_is_owned(self):
        user = make_user()
        group = make_group(name="stewards")
        user.groups.add(group)
        finance = make_entitlement(name="Finance")
        grant(finance, group=group, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE):
            assert owned_entitlement_ids(user_principal(user)) == frozenset({finance.pk})


class TestMaySeeUnlabelled:
    def test_open_posture_answers_true_without_a_query(self):
        assert may_see_unlabelled(OPEN_PRINCIPAL) is True

    def test_an_open_library_admits_anyone_who_is_not_anonymous(self):
        """Spec section 8.1 says "every signed-in principal"; sections
        5.3 and 8.3 both say a SERVICE principal answering from a shell
        sees unlabelled documents. Those only agree if the open-library
        branch admits the service principal -- which is the honest
        reading of a file somebody dropped in a shared inbox."""
        user = make_user()
        with posture(POSTURE_ENTERPRISE):
            assert may_see_unlabelled(user_principal(user)) is True
            assert may_see_unlabelled(SERVICE_PRINCIPAL) is True
            assert may_see_unlabelled(ANONYMOUS) is False

    def test_a_locked_library_answers_sees_all_content_only(self):
        member = make_user()
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            assert may_see_unlabelled(user_principal(member)) is False
            # The content setting is OFF by default, so even an admin is
            # refused the BYTES of an unlabelled document on a locked
            # library. They still see its ROW and can label it.
            assert may_see_unlabelled(user_principal(admin)) is False
            assert may_see_unlabelled(SERVICE_PRINCIPAL) is False

    def test_a_locked_library_admits_an_admin_once_the_content_setting_is_on(self):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED,
                     admin_sees_content=True):
            assert may_see_unlabelled(user_principal(admin)) is True

    def test_the_two_non_open_postures_answer_identically(self):
        """NO POSTURE BRANCH. `personal` and `enterprise` differ only in
        which pages exist."""
        from identity.contracts.postures import POSTURE_PERSONAL
        user = make_user()
        answers = []
        for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
            with posture(name, library_posture=LIBRARY_LOCKED):
                answers.append(may_see_unlabelled(user_principal(user)))
        assert answers[0] == answers[1] is False


class TestTheTwoPickerReaders:
    def test_labelling_entitlements_gives_an_admin_everything(self):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            assert labelling_entitlements(user_principal(admin)) == (
                (finance.pk, "Finance"), (legal.pk, "Legal"))

    def test_labelling_entitlements_gives_a_member_only_what_they_own(self):
        user = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=user, role=EntitlementGrant.Role.OWNER)
        grant(legal, user=user)
        with posture(POSTURE_ENTERPRISE):
            assert labelling_entitlements(user_principal(user)) == ((finance.pk, "Finance"),)

    def test_share_subjects_lists_active_accounts_and_groups(self):
        """The caller here is a THIRD account, deliberately distinct from
        both listed subjects: `share_subjects` excludes the caller (see
        the very next test), so a caller who was also one of the two
        subjects under test would make this test's own two assertions
        (an active account is listed; an inactive one is not) impossible
        to state independently of that exclusion."""
        caller = make_user(username="caller")
        user = make_user(username="ana")
        gone = make_user(username="zed")
        gone.is_active = False
        gone.save(update_fields=["is_active"])
        group = make_group(name="analysts")
        with posture(POSTURE_ENTERPRISE):
            subjects = share_subjects(user_principal(caller))
        assert (user.pk, "ana") in subjects["users"]
        assert all(name != "zed" for _pk, name in subjects["users"])
        assert subjects["groups"] == ((group.pk, "analysts"),)

    def test_share_subjects_never_offers_the_caller_themselves(self):
        """Sharing a row with yourself is a no-op the form should not
        offer: you already own it, or you already have it."""
        user = make_user(username="ana")
        make_user(username="bea")
        with posture(POSTURE_ENTERPRISE):
            names = [name for _pk, name in share_subjects(user_principal(user))["users"]]
        assert names == ["bea"]

    def test_grant_subjects_DOES_offer_the_caller_themselves(self):
        """The one line that makes the two readers two readers.
        Granting yourself an entitlement is not a no-op: an administrator
        with the content setting off reads nothing labelled, so holding
        the entitlement is the only way for them to read a document under
        it -- and a grant form built from `share_subjects` would leave
        them no UI path to do it."""
        admin = make_admin(username="ana")
        make_user(username="bea")
        with posture(POSTURE_ENTERPRISE):
            names = [name for _pk, name in grant_subjects(user_principal(admin))["users"]]
        assert names == ["ana", "bea"]

    def test_both_readers_answer_empty_in_open_posture(self):
        """An open box has no accounts to offer, and neither reader runs
        a query to find that out.

        PINNED to `POSTURE_OPEN` explicitly (repair, Task 5 review):
        this module's own `_settings` fixture calls `seed_sweep_posture`,
        so under `FARABUNKER_TEST_POSTURE=enterprise` this test used to
        run in enterprise posture and its "empty in open posture" claim
        was a claim about the wrong box -- a test that pins its own
        posture always wins that sweep (`identity.testing.
        seed_sweep_posture`'s own docstring), the same precedence every
        other open-posture pin in this module (and this file's own
        siblings above) already relies on."""
        with posture(POSTURE_OPEN):
            make_user()
            assert share_subjects(OPEN_PRINCIPAL) == {"users": (), "groups": ()}
            assert grant_subjects(OPEN_PRINCIPAL) == {"users": (), "groups": ()}


class TestEffectiveEntitlements:
    def test_it_names_direct_and_via_group_distinctly(self):
        from identity.access import effective_entitlements
        user = make_user()
        group = make_group(name="analysts")
        user.groups.add(group)
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        grant(finance, user=user)
        grant(legal, group=group)
        with posture(POSTURE_ENTERPRISE):
            rows = {row["name"]: row for row in effective_entitlements(user)}
        assert rows["Finance"]["via"] == ""
        assert rows["Legal"]["via"] == "analysts"

    def test_one_entitlement_held_both_ways_appears_once_as_direct(self):
        """A direct grant is the stronger fact -- it survives leaving the
        group -- so the row says so and does not appear twice."""
        from identity.access import effective_entitlements
        user = make_user()
        group = make_group(name="analysts")
        user.groups.add(group)
        finance = make_entitlement(name="Finance")
        grant(finance, user=user)
        grant(finance, group=group)
        with posture(POSTURE_ENTERPRISE):
            rows = [row for row in effective_entitlements(user) if row["name"] == "Finance"]
        assert len(rows) == 1
        assert rows[0]["via"] == ""

    def test_an_owner_grant_says_so(self):
        from identity.access import effective_entitlements
        from identity.models import EntitlementGrant
        user = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=user, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE):
            assert effective_entitlements(user)[0]["role"] == "owner"
