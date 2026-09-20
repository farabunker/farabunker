"""The five access questions -- and the difference between two of them.

ADMINISTERING IS NOT READING. `is_admin` answers "may this principal
reach an admin surface"; `sees_all_content` answers "may this principal
read somebody else's words, pixels or bytes". They were one function in
the design's first draft, which is exactly how "conversations are
private" quietly became "private from the people who are not
administrators". Half the assertions below exist to keep them apart.
"""
from __future__ import annotations

import pytest

from identity.access import accounts_on, is_admin, owner_fields, posture, sees_all_content
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL
from identity.contracts.principals import (
    ANONYMOUS, OPEN_PRINCIPAL, Principal, SERVICE_PRINCIPAL,
)
from identity.tests._helpers import make_admin, make_user, seed_sweep_posture, user_principal

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


class TestPosture:
    def test_the_default_is_open_and_that_is_todays_behaviour(self):
        # PINNED, not read raw: this module's `_sweep` autouse fixture
        # applies `FARABUNKER_TEST_POSTURE` when the sweep sets it, and a
        # test claiming "the DEFAULT is open" must say so under its own
        # pin -- `identity/tests/_helpers.py::posture` always wins over
        # the sweep, the same precedence the feature flags already use.
        from identity.tests._helpers import posture as pin
        with pin(POSTURE_OPEN):
            assert posture() == POSTURE_OPEN
            assert accounts_on() is False

    @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
    def test_every_other_posture_requires_accounts(self, name):
        from identity.tests._helpers import posture as pin
        with pin(name):
            assert accounts_on() is True


class TestIsAdmin:
    def test_the_open_principal_is_an_admin(self):
        """On a box with no accounts there is nobody for anything to be
        hidden from, and answering False would make every admin surface
        unreachable in the posture that is the default.

        PINNED: this is true only IN OPEN POSTURE (`is_admin`'s own
        `_accounts_on` branch) -- under the sweep, `OPEN_PRINCIPAL` is
        not a `user` principal at all, so a non-open settings row would
        make this False, which is correct there and not what this test
        is about."""
        from identity.tests._helpers import posture as pin
        with pin(POSTURE_OPEN):
            assert is_admin(OPEN_PRINCIPAL) is True

    def test_an_active_superuser_is_an_admin(self):
        from identity.tests._helpers import posture as pin
        admin = make_admin()
        with pin(POSTURE_PERSONAL):
            assert is_admin(user_principal(admin)) is True

    def test_an_ordinary_member_is_not(self):
        from identity.tests._helpers import posture as pin
        with pin(POSTURE_PERSONAL):
            assert is_admin(user_principal(make_user())) is False

    def test_a_deactivated_superuser_is_not(self):
        """`is_active` is half the predicate. A demoted-by-deactivation
        admin must stop reaching the console on their next request, not
        on their next login."""
        from identity.tests._helpers import posture as pin
        admin = make_admin(is_active=False)
        with pin(POSTURE_PERSONAL):
            assert is_admin(user_principal(admin)) is False

    def test_a_service_principal_is_never_an_admin(self):
        """A machine caller that could reach the model console or the
        posture page would make the shell a privilege-escalation path
        with no login behind it."""
        from identity.tests._helpers import posture as pin
        with pin(POSTURE_ENTERPRISE):
            assert is_admin(SERVICE_PRINCIPAL) is False

    def test_anonymous_is_never_an_admin(self):
        from identity.tests._helpers import posture as pin
        with pin(POSTURE_ENTERPRISE):
            assert is_admin(ANONYMOUS) is False

    def test_a_user_principal_whose_key_is_not_a_primary_key_answers_false(self):
        """NEVER a 500. `Principal.key` is the pk as a string; a key
        that is not a decimal string would reach
        `User.objects.filter(pk=key)` and raise `ValueError` inside a
        request. A hand-written payload or a row from an older schema
        can supply one, so the shape is checked before the query."""
        from identity.tests._helpers import posture as pin
        with pin(POSTURE_PERSONAL):
            assert is_admin(Principal("user", "not-a-number")) is False

    def test_a_user_principal_naming_no_row_answers_false(self):
        from identity.tests._helpers import posture as pin
        with pin(POSTURE_PERSONAL):
            assert is_admin(Principal("user", "99999999")) is False


class TestSeesAllContent:
    def test_open_sees_everything(self):
        """PINNED, same reason `TestIsAdmin::test_the_open_principal_is_an_admin`
        is: true because the box is OPEN, not because of anything about
        `OPEN_PRINCIPAL` itself."""
        from identity.tests._helpers import posture as pin
        with pin(POSTURE_OPEN):
            assert sees_all_content(OPEN_PRINCIPAL) is True

    @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
    def test_an_admin_with_the_setting_off_does_not(self, name):
        """THE DEFAULT, and the decision. An administrator runs the box
        without reading anybody's words."""
        from identity.tests._helpers import posture as pin
        admin = make_admin()
        with pin(name, admin_sees_content=False):
            assert is_admin(user_principal(admin)) is True
            assert sees_all_content(user_principal(admin)) is False

    @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
    def test_turning_the_setting_on_flips_it(self, name):
        from identity.tests._helpers import posture as pin
        admin = make_admin()
        with pin(name, admin_sees_content=True):
            assert sees_all_content(user_principal(admin)) is True

    def test_a_member_never_sees_all_content_however_the_setting_is_set(self):
        from identity.tests._helpers import posture as pin
        member = make_user()
        for value in (False, True):
            with pin(POSTURE_ENTERPRISE, admin_sees_content=value):
                assert sees_all_content(user_principal(member)) is False

    def test_the_two_non_open_postures_answer_identically(self):
        """THERE IS NO POSTURE BRANCH. `personal` and `enterprise`
        differ only in which pages exist; a predicate that read the
        posture would mean the same administrator saw different content
        on two boxes that had made the same choice."""
        from identity.tests._helpers import posture as pin
        admin, member = make_admin(), make_user()
        for setting in (False, True):
            answers = []
            for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
                with pin(name, admin_sees_content=setting):
                    answers.append((sees_all_content(user_principal(admin)),
                                    sees_all_content(user_principal(member))))
            assert answers[0] == answers[1]

    def test_anonymous_and_service_see_nothing(self):
        from identity.tests._helpers import posture as pin
        with pin(POSTURE_ENTERPRISE, admin_sees_content=True):
            assert sees_all_content(ANONYMOUS) is False
            assert sees_all_content(SERVICE_PRINCIPAL) is False


class TestOwnerFields:
    def test_it_is_the_two_columns_to_stamp(self):
        assert owner_fields(OPEN_PRINCIPAL) == {"owner_kind": "open", "owner_key": "box"}

    def test_a_user_is_stamped_by_primary_key_never_by_username(self):
        """A username is renameable; a rename must not orphan every row
        a person owns."""
        user = make_user(username="ann")
        assert owner_fields(user_principal(user)) == {
            "owner_kind": "user", "owner_key": str(user.pk)}


class TestRowTakingForms:
    """`accounts_on`/`is_admin`'s `settings_row=` keyword -- for
    `identity.middleware`, which fetches `IdentitySettings.get_solo()`
    once per request and threads the row through instead of letting each
    no-argument call re-fetch the singleton for itself."""

    def test_accounts_on_with_a_row_agrees_with_accounts_on(self):
        from identity.tests._helpers import posture as pin
        # PINNED OPEN for the first half: reading the row raw would take
        # whatever the sweep left it at, and this half is specifically
        # about the OPEN answer.
        with pin(POSTURE_OPEN) as row:
            assert accounts_on(settings_row=row) is False
        with pin(POSTURE_PERSONAL) as row:
            assert accounts_on(settings_row=row) is True

    def test_is_admin_with_a_row_agrees_with_is_admin(self):
        from identity.tests._helpers import posture as pin
        admin = make_admin()
        member = make_user()
        with pin(POSTURE_PERSONAL) as row:
            assert is_admin(user_principal(admin), settings_row=row) is True
            assert is_admin(user_principal(member), settings_row=row) is False

    def test_it_takes_no_extra_read_over_the_row_already_fetched(
            self, django_assert_num_queries):
        """The whole point: a caller holding the row already answers
        both questions off it with ZERO further reads of
        `IdentitySettings`."""
        from identity.models import IdentitySettings
        from identity.tests._helpers import posture as pin
        admin = make_admin()
        with pin(POSTURE_PERSONAL) as row:
            with django_assert_num_queries(0):
                accounts_on(settings_row=row)
            with django_assert_num_queries(1):  # the one `User` read `is_admin` needs
                is_admin(user_principal(admin), settings_row=row)


class TestTheOpenBranchIsFirst:
    def test_open_posture_asks_the_user_table_nothing(self, django_assert_num_queries):
        """Spec section 3.4, made structural rather than promised: every
        function tests `accounts_on()` first and returns before it
        touches a second table. The one primary-key read of
        `IdentitySettings` is not a permission query -- it is the query
        that answers WHICH POSTURE, and the box must ask it before it
        can skip anything else."""
        from identity.models import IdentitySettings
        from identity.tests._helpers import posture as pin
        with pin(POSTURE_OPEN):
            IdentitySettings.get_solo()      # warm the row so its creation is not counted
            with django_assert_num_queries(1):
                assert is_admin(OPEN_PRINCIPAL) is True
            with django_assert_num_queries(1):
                assert sees_all_content(OPEN_PRINCIPAL) is True

    def test_an_admins_content_check_reads_settings_once_not_three_times(
            self, django_assert_num_queries):
        """`sees_all_content` for a non-open admin answers `accounts_on`,
        `is_admin`, AND `admin_sees_content` off the SAME settings row --
        one read of `IdentitySettings`, not one per question -- plus the
        one `User` read `is_admin` needs to confirm the superuser. A
        function every column calls per content check must not triple
        its own cost answering one predicate."""
        from identity.tests._helpers import posture as pin
        admin = make_admin()
        with pin(POSTURE_PERSONAL, admin_sees_content=True):
            with django_assert_num_queries(2):
                assert sees_all_content(user_principal(admin)) is True
