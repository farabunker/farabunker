"""Unit tests for foundation/settings_bounds.py (S1, Coherence Wave B) --
pure functions, no Django DB, no fixtures needed, same shape as
`foundation/tests/test_format.py`.
"""
from foundation.settings_bounds import (
    BIGINT_FIELD_MAX, POSITIVE_INT_FIELD_MAX, exceeds_field_ceiling,
)


class TestFieldCeilingConstants:
    def test_bigint_ceiling_is_the_real_postgres_bigint_max(self):
        assert BIGINT_FIELD_MAX == 2**63 - 1

    def test_positive_int_ceiling_is_the_real_postgres_integer_max(self):
        """A Django `PositiveIntegerField` is a plain signed `integer`
        column plus a `>= 0` CHECK constraint -- NOT unsigned -- so the
        true ceiling is `2**31-1`, not `2**32-1`."""
        assert POSITIVE_INT_FIELD_MAX == 2**31 - 1


class TestExceedsFieldCeiling:
    def test_a_value_within_the_ceiling_does_not_exceed_it(self):
        assert exceeds_field_ceiling(100, max_stored=POSITIVE_INT_FIELD_MAX) is False

    def test_a_value_exactly_at_the_ceiling_does_not_exceed_it(self):
        assert exceeds_field_ceiling(POSITIVE_INT_FIELD_MAX, max_stored=POSITIVE_INT_FIELD_MAX) is False

    def test_a_value_one_past_the_ceiling_exceeds_it(self):
        assert exceeds_field_ceiling(POSITIVE_INT_FIELD_MAX + 1, max_stored=POSITIVE_INT_FIELD_MAX) is True

    def test_an_arbitrary_precision_int_far_past_the_ceiling_exceeds_it(self):
        """The motivating case (T10 review MINOR 5): a 40-digit
        whole-number string parses cleanly via plain `int()` -- Python
        ints have no size limit -- so the ceiling has to be an explicit
        comparison, not something the parse step itself would ever catch."""
        astronomically_large = int("9" + "0" * 39)
        assert exceeds_field_ceiling(astronomically_large, max_stored=BIGINT_FIELD_MAX) is True

    def test_works_with_a_narrower_field_specific_ceiling(self):
        """Callers may pass a real, meaningful range ceiling narrower than
        either column constant above (e.g. `RagSettings.
        RETRIEVAL_TOP_K_MAX`), not only the two Postgres-column ceilings."""
        assert exceeds_field_ceiling(51, max_stored=50) is True
        assert exceeds_field_ceiling(50, max_stored=50) is False
