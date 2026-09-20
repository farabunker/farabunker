"""The posture vocabulary -- pure names and one pure predicate.

The DECISION about which posture this box is in lives in a database row
(`identity.models.IdentitySettings`, Task 2) and is read by
`identity.access.posture()`. This module holds only the names, so a
column that needs to say "personal" in a template or a form does not
have to import a Django model to do it.
"""
from identity.contracts.postures import (
    LIBRARY_CHOICES, LIBRARY_LOCKED, LIBRARY_OPEN, POSTURE_CHOICES, POSTURE_ENTERPRISE,
    POSTURE_OPEN, POSTURE_PERSONAL, POSTURES, SESSION_IDLE_MINUTES_DEFAULT,
    accounts_required,
)


class TestPostures:
    def test_there_are_exactly_three_in_order_of_strictness(self):
        assert POSTURES == (POSTURE_OPEN, POSTURE_PERSONAL, POSTURE_ENTERPRISE)
        assert (POSTURE_OPEN, POSTURE_PERSONAL, POSTURE_ENTERPRISE) == (
            "open", "personal", "enterprise")

    def test_the_choices_cover_every_posture(self):
        assert [value for value, _label in POSTURE_CHOICES] == list(POSTURES)

    def test_open_is_the_only_posture_without_accounts(self):
        assert accounts_required(POSTURE_OPEN) is False
        assert accounts_required(POSTURE_PERSONAL) is True
        assert accounts_required(POSTURE_ENTERPRISE) is True


class TestLibraryPosture:
    def test_two_values_and_open_is_the_default_name(self):
        assert (LIBRARY_OPEN, LIBRARY_LOCKED) == ("open", "locked")
        assert [value for value, _label in LIBRARY_CHOICES] == [LIBRARY_OPEN, LIBRARY_LOCKED]


class TestSessionIdle:
    def test_the_default_is_twelve_hours_in_minutes(self):
        """Long enough that a person working through a document library
        is not logged out mid-task; short enough that a browser left
        open on a shared desk is not a standing session."""
        assert SESSION_IDLE_MINUTES_DEFAULT == 720
